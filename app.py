"""Video localization: web app chạy trên máy để làm video sub Việt chuẩn đăng YouTube."""
import io
import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
import webbrowser

from flask import Flask, Response, abort, jsonify, request, send_file

from studio import asr, ocr, render, srt, translate, util
from studio.util import Cancelled

ROOT = util.ROOT
PROJ_DIR = os.path.join(ROOT, "projects")
os.makedirs(PROJ_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="")
LOCK = threading.Lock()

# ---------------------------------------------------------------- truy cập từ trang web bên ngoài
# ALLOWED_ORIGINS: danh sách origin được gọi API, cách nhau bằng dấu phẩy ("*" = mọi nơi, cần ACCESS_TOKEN).
# ACCESS_TOKEN: nếu đặt, mọi lệnh /api phải kèm mã (header X-Access-Token hoặc ?token=).
# MEDIA_DIR: thư mục chứa video có sẵn trên máy xử lý (hiện danh sách để chọn, không cần tải lên).
# EXPORT_DIR: video xuất xong được chép thêm vào đây (vd. thư mục Google Drive).
# BACKUP_DIR: mỗi lần lưu, project.json được sao lưu vào đây và tự khôi phục khi khởi động lại.
MEDIA_DIR = os.environ.get("MEDIA_DIR", "").strip()
MEDIA_SRC = os.environ.get("MEDIA_SRC", "").strip()  # nguồn (vd. Drive): tự chép video mới sang MEDIA_DIR cho đọc nhanh
EXPORT_DIR = os.environ.get("EXPORT_DIR", "").strip()
BACKUP_DIR = os.environ.get("BACKUP_DIR", "").strip()
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".flv", ".wmv")
ALLOWED = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
TOKEN = os.environ.get("ACCESS_TOKEN", "")


@app.before_request
def _guard():
    if request.method == "OPTIONS":
        return Response(status=204)
    if TOKEN and request.path.startswith("/api/"):
        got = request.headers.get("X-Access-Token") or request.args.get("token") or ""
        if got != TOKEN:
            return jsonify({"error": "Sai hoặc thiếu mã truy cập."}), 401


@app.after_request
def _cors(resp):
    o = (request.headers.get("Origin") or "").rstrip("/")
    if o and ("*" in ALLOWED or o in ALLOWED):
        resp.headers["Access-Control-Allow-Origin"] = o
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Access-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        resp.headers["Access-Control-Max-Age"] = "600"
    return resp


# ---------------------------------------------------------------- dự án
def pdir(pid):
    if not re.fullmatch(r"[a-f0-9]{10}", pid or ""):
        abort(404)
    d = os.path.join(PROJ_DIR, pid)
    if not os.path.isdir(d):
        abort(404)
    return d


def load(pid):
    with open(os.path.join(pdir(pid), "project.json"), encoding="utf-8") as f:
        return json.load(f)


def save(p):
    with LOCK:
        tmp = os.path.join(PROJ_DIR, p["id"], "project.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(PROJ_DIR, p["id"], "project.json"))
    if BACKUP_DIR:
        threading.Thread(target=_backup, args=(p["id"],), daemon=True).start()


def _backup(pid):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with LOCK:
            shutil.copyfile(os.path.join(PROJ_DIR, pid, "project.json"), os.path.join(BACKUP_DIR, pid + ".json"))
    except Exception:  # noqa
        pass


def restore_backups():
    """Khôi phục dự án từ BACKUP_DIR (dùng khi máy xử lý bị reset, ví dụ Colab ngắt kết nối)."""
    if not BACKUP_DIR or not os.path.isdir(BACKUP_DIR):
        return
    for fn in os.listdir(BACKUP_DIR):
        pid = fn[:-5]
        if fn.endswith(".json") and re.fullmatch(r"[a-f0-9]{10}", pid) and not os.path.isdir(os.path.join(PROJ_DIR, pid)):
            try:
                os.makedirs(os.path.join(PROJ_DIR, pid, "assets"), exist_ok=True)
                shutil.copyfile(os.path.join(BACKUP_DIR, fn), os.path.join(PROJ_DIR, pid, "project.json"))
                print("  Đã khôi phục dự án", pid)
            except Exception:  # noqa
                pass


def media_path(rel):
    """Đổi tên file trong MEDIA_DIR thành đường dẫn thật, chặn thoát khỏi thư mục."""
    if not MEDIA_DIR:
        return None
    base = os.path.realpath(MEDIA_DIR)
    f = os.path.realpath(os.path.join(base, rel))
    return f if f.startswith(base + os.sep) and os.path.isfile(f) else None


def err(msg, code=400):
    return jsonify({"error": msg}), code


# ---------------------------------------------------------------- tác vụ nền
class Task:
    def __init__(self, kind):
        self.kind, self.state, self.progress, self.message = kind, "running", 0.0, ""
        self.error, self.cancel, self.started, self.result = None, False, time.time(), None

    def set(self, p, msg=None):
        self.progress = max(0.0, min(1.0, p))
        if msg:
            self.message = msg

    def dump(self):
        return {"kind": self.kind, "state": self.state, "progress": round(self.progress, 4),
                "message": self.message, "error": self.error, "elapsed": round(time.time() - self.started, 1),
                "result": self.result}


TASKS = {}


def start_task(pid, kind, fn):
    t = TASKS.get(pid)
    if t and t.state == "running":
        return None
    task = Task(kind)
    TASKS[pid] = task

    def runner():
        try:
            fn(task)
            task.state = "cancelled" if task.cancel else "done"
            if task.state == "done":
                task.progress = 1.0
        except Cancelled:
            task.state = "cancelled"
        except Exception as e:  # noqa
            traceback.print_exc()
            task.state, task.error = "error", str(e)

    threading.Thread(target=runner, daemon=True).start()
    return task


# ---------------------------------------------------------------- trang & trạng thái
@app.get("/")
def index():
    # Giao diện do chính máy xử lý phục vụ: báo cho app.js biết để gọi API cùng địa chỉ.
    html = open(os.path.join(app.static_folder, "index.html"), encoding="utf-8").read()
    html = html.replace("<head>", "<head>\n<script>window.VL_EMBEDDED=1</script>", 1)
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/logo.png")
def default_logo():
    return send_file(os.path.join(util.ASSET_DIR, "logo.png"), mimetype="image/png")


@app.get("/api/status")
def status():
    langs = util.tesseract_langs()
    return jsonify({
        "ffmpeg": bool(util.ffmpeg_bin()), "ffprobe": bool(util.ffprobe_bin()),
        "tesseract": bool(util.tesseract_bin()), "langs": langs, "libass": util.has_filter("ass"),
        "fonts": sorted(render.list_fonts().keys()), "defaults": render.DEFAULTS,
        "default_rules": translate.DEFAULT_RULES, "default_model": translate.DEFAULT_MODEL,
        "media": bool(MEDIA_DIR), "export_dir": EXPORT_DIR,
        "asr": {"available": asr.available(), "device": asr.device(), "default_model": asr.default_model(), "models": asr.MODELS},
    })


SYNC = {"running": False, "file": "", "done": 0, "total": 0, "error": ""}


def _sync_media():
    SYNC.update(running=True, file="", done=0, total=0, error="")
    try:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        todo = []
        for root, _, files in os.walk(MEDIA_SRC):
            if root[len(MEDIA_SRC):].count(os.sep) > 2:
                continue
            for fn in files:
                if fn.lower().endswith(VIDEO_EXT):
                    src = os.path.join(root, fn)
                    rel = os.path.relpath(src, MEDIA_SRC)
                    dst = os.path.join(MEDIA_DIR, rel)
                    if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                        todo.append((src, dst))
        SYNC["total"] = sum(os.path.getsize(a) for a, _ in todo)
        for src, dst in todo:
            SYNC["file"] = os.path.basename(src)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, "rb") as fi, open(dst + ".part", "wb") as fo:
                while True:
                    b = fi.read(16 << 20)
                    if not b:
                        break
                    fo.write(b)
                    SYNC["done"] += len(b)
            os.replace(dst + ".part", dst)
    except Exception as e:  # noqa
        traceback.print_exc()
        SYNC["error"] = str(e)
    SYNC["running"] = False


def start_sync():
    if MEDIA_SRC and MEDIA_DIR and not SYNC["running"]:
        SYNC["running"] = True
        threading.Thread(target=_sync_media, daemon=True).start()


@app.get("/api/media")
def list_media():
    out = []
    if MEDIA_DIR and os.path.isdir(MEDIA_DIR):
        base = os.path.realpath(MEDIA_DIR)
        for root, _, files in os.walk(base):
            if root[len(base):].count(os.sep) > 2:
                continue
            for fn in files:
                if fn.lower().endswith(VIDEO_EXT):
                    f = os.path.join(root, fn)
                    out.append({"name": os.path.relpath(f, base), "size": os.path.getsize(f), "mtime": os.path.getmtime(f)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"files": out, "sync": SYNC, "can_sync": bool(MEDIA_SRC)})


@app.post("/api/media/sync")
def media_sync():
    start_sync()
    return jsonify(SYNC)


@app.get("/api/projects")
def list_projects():
    out = []
    for d in sorted(os.listdir(PROJ_DIR), key=lambda x: os.path.getmtime(os.path.join(PROJ_DIR, x)), reverse=True):
        f = os.path.join(PROJ_DIR, d, "project.json")
        if os.path.isfile(f):
            try:
                p = json.load(open(f, encoding="utf-8"))
                out.append({"id": p["id"], "name": p["name"], "info": p["info"], "cues": len(p.get("cues", [])),
                            "translated": sum(1 for c in p.get("cues", []) if (c.get("vi") or "").strip()),
                            "has_final": os.path.isfile(os.path.join(PROJ_DIR, d, "final.mp4"))})
            except Exception:
                pass
    return jsonify(out)


def _project_dict(pid, name, path, info):
    d_bands = [
        {"name": "Dòng 1", "y0": 0.865, "y1": 0.935, "lang": "chi_sim"},
        {"name": "Dòng 2", "y0": 0.935, "y1": 0.995, "lang": "eng"},
    ]
    return {"id": pid, "name": os.path.splitext(name)[0], "video": path, "info": info, "bands": d_bands, "primary": 1,
            "ocr": {"fps": 6, "t0": 0, "t1": None, "thr": 210, "min_conf": 40}, "cues": [], "settings": {},
            "translate": {"model": translate.DEFAULT_MODEL, "rules": translate.DEFAULT_RULES, "glossary": ""}}


# ---------------------------------------------------------------- tải video từ link (link trực tiếp hoặc Google Drive)
DL = {"state": "idle", "message": "", "done": 0, "total": 0, "pid": None, "error": "", "cancel": False}


def _download(url):
    import urllib.parse
    import urllib.request
    DL.update(state="running", message="Đang bắt đầu tải", done=0, total=0, pid=None, error="", cancel=False)
    pid = uuid.uuid4().hex[:10]
    d = os.path.join(PROJ_DIR, pid)
    os.makedirs(os.path.join(d, "assets"), exist_ok=True)
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
            import gdown
            DL["message"] = "Đang tải từ Google Drive (không hiện tiến độ)"
            out = gdown.download(url=url, output=os.path.join(d, "source_dl"), quiet=True, fuzzy=True)
            if not out or not os.path.isfile(out):
                raise RuntimeError("Không tải được từ Google Drive. Hãy đặt file ở chế độ 'Bất kỳ ai có đường liên kết'.")
            name = os.path.basename(out)
            path = out
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                DL["total"] = int(r.headers.get("Content-Length") or 0)
                cd = r.headers.get("Content-Disposition") or ""
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
                name = os.path.basename(urllib.parse.unquote(m.group(1))) if m else os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)) or "video.mp4"
                ext = os.path.splitext(name)[1].lower()
                if ext not in VIDEO_EXT:
                    name = (os.path.splitext(name)[0] or "video") + ".mp4"
                path = os.path.join(d, "source" + os.path.splitext(name)[1].lower())
                DL["message"] = "Đang tải video về máy xử lý"
                with open(path, "wb") as fo:
                    while True:
                        if DL["cancel"]:
                            raise RuntimeError("Đã huỷ")
                        b = r.read(8 << 20)
                        if not b:
                            break
                        fo.write(b)
                        DL["done"] += len(b)
        info = util.probe(path)
        save(_project_dict(pid, name, path, info))
        DL.update(state="done", pid=pid, message="Xong")
    except Exception as e:  # noqa
        shutil.rmtree(d, ignore_errors=True)
        DL.update(state="error", error=str(e) or "Tải lỗi")


@app.post("/api/import_url")
def import_url():
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not re.match(r"https?://", url, re.I):
        return err("Link phải bắt đầu bằng http:// hoặc https://")
    if DL["state"] == "running":
        return err("Đang tải một video khác.", 409)
    DL["state"] = "running"
    threading.Thread(target=_download, args=(url,), daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/import_url")
def import_url_status():
    return jsonify({k: v for k, v in DL.items() if k != "cancel"})


@app.post("/api/import_url/cancel")
def import_url_cancel():
    DL["cancel"] = True
    return jsonify({"ok": True})


@app.post("/api/projects")
def create_project():
    pid = uuid.uuid4().hex[:10]
    d = os.path.join(PROJ_DIR, pid)
    os.makedirs(os.path.join(d, "assets"), exist_ok=True)
    try:
        if "file" in request.files:
            f = request.files["file"]
            name = os.path.basename(f.filename or "video.mp4")
            ext = os.path.splitext(name)[1].lower() or ".mp4"
            path = os.path.join(d, "source" + ext)
            f.save(path)
        else:
            body = request.get_json(silent=True) or {}
            path = body.get("path", "").strip().strip('"')
            if body.get("media"):
                path = media_path(body["media"]) or ""
            if not path or not os.path.isfile(path):
                shutil.rmtree(d, ignore_errors=True)
                return err("Không tìm thấy file video theo đường dẫn đã nhập.")
            name = os.path.basename(path)
        info = util.probe(path)
    except Exception as e:  # noqa
        shutil.rmtree(d, ignore_errors=True)
        return err(str(e))
    p = _project_dict(pid, name, path, info)
    save(p)
    return jsonify(p)


@app.get("/api/p/<pid>")
def get_project(pid):
    return jsonify(load(pid))


@app.delete("/api/p/<pid>")
def delete_project(pid):
    shutil.rmtree(pdir(pid), ignore_errors=True)
    TASKS.pop(pid, None)
    if BACKUP_DIR:
        try:
            os.remove(os.path.join(BACKUP_DIR, pid + ".json"))
        except OSError:
            pass
    return jsonify({"ok": True})


@app.post("/api/p/<pid>/save")
def save_project(pid):
    p = load(pid)
    body = request.get_json(force=True)
    for k in ("name", "bands", "primary", "ocr", "cues", "settings", "translate"):
        if k in body:
            p[k] = body[k]
    save(p)
    return jsonify({"ok": True})


@app.get("/api/p/<pid>/frame")
def frame(pid):
    p = load(pid)
    t = float(request.args.get("t", 0))
    w = int(request.args.get("w", 960))
    try:
        return Response(util.frame_jpeg(p["video"], t, w), mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa
        return err(str(e), 500)


@app.post("/api/p/<pid>/detect")
def detect(pid):
    p = load(pid)
    try:
        lines = ocr.detect_bands(p["video"], p["info"])
    except Exception as e:  # noqa
        return err(str(e), 500)
    return jsonify({"lines": lines})


# ---------------------------------------------------------------- OCR
def _mc(o):
    """Ngưỡng độ tin cậy từng khung. Dự án cũ lưu mặc định 55 (quá chặt, hay làm rớt câu) thì dùng 40."""
    v = float(o.get("min_conf", 40))
    return 40.0 if v == 55 else v


@app.post("/api/p/<pid>/ocr")
def run_ocr(pid):
    p = load(pid)
    body = request.get_json(force=True)
    bands = body.get("bands") or p["bands"]
    primary = int(body.get("primary", p.get("primary", 0)))
    o = body.get("ocr") or p["ocr"]
    p["bands"], p["primary"], p["ocr"] = bands, primary, o
    save(p)

    def work(task):
        task.set(0.0, "Bắt đầu quét")
        cues = ocr.run_ocr(p["video"], p["info"], bands, primary, fps=float(o.get("fps", 6)),
                           t0=float(o.get("t0") or 0), t1=(float(o["t1"]) if o.get("t1") else None),
                           thr=int(o.get("thr", 210)), min_conf=_mc(o),
                           seg_min=float(o.get("seg_min", 55)), ref=bool(o.get("ref", True)),
                           task=task, cb=task.set)
        q = load(pid)
        q["cues"] = cues
        save(q)
        task.result = {"cues": len(cues)}

    if not start_task(pid, "ocr", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


@app.post("/api/p/<pid>/probe")
def probe_ocr(pid):
    p = load(pid)
    body = request.get_json(force=True)
    o = p["ocr"]
    try:
        res = ocr.probe_frame(p["video"], p["info"], p["bands"], p.get("primary", 0), float(body.get("t", 0)),
                              thr=int(o.get("thr", 210)), min_conf=_mc(o), seg_min=float(o.get("seg_min", 55)))
    except Exception as e:  # noqa
        return err(str(e), 500)
    return jsonify({"bands": res, "min_conf": _mc(o), "seg_min": float(o.get("seg_min", 55))})


def merge_cues(old, new):
    """Thêm các câu mới không trùng thời gian với câu cũ (trùng > 40% độ dài câu ngắn hơn thì bỏ). Trả về (danh sách, số câu thêm)."""
    out, added = list(old), 0
    for n in new:
        dn = max(0.05, n["end"] - n["start"])
        dup = False
        for e in old:
            ov = min(e["end"], n["end"]) - max(e["start"], n["start"])
            if ov > 0.4 * min(dn, max(0.05, e["end"] - e["start"])):
                dup = True
                break
        if not dup:
            out.append(n)
            added += 1
    out.sort(key=lambda c: c["start"])
    for i, c in enumerate(out, 1):
        c["id"] = i
    return out, added


@app.post("/api/p/<pid>/rescan")
def run_rescan(pid):
    """Quét lại OCR cho một hoặc nhiều đoạn thời gian, chỉ bổ sung câu mới, không xoá câu cũ."""
    p = load(pid)
    body = request.get_json(force=True)
    dur = float(p["info"]["duration"])
    if body.get("ranges"):
        rs = [(max(0.0, float(a) - 0.3), min(dur, float(b) + 0.3)) for a, b in body["ranges"][:300]]
    else:
        t0 = max(0.0, float(body.get("t0", 0)))
        rs = [(t0, min(float(body.get("t1", t0 + 3)), dur))]
    rs = [r for r in rs if r[1] - r[0] >= 0.3]
    if not rs:
        return err("Đoạn cần quét quá ngắn.")
    o = p["ocr"]
    if "cues" in body:
        p["cues"] = body["cues"]
    save(p)

    def work(task):
        found = []
        for i, (a, b) in enumerate(rs):
            found += ocr.run_ocr(p["video"], p["info"], p["bands"], p.get("primary", 0), fps=max(6.0, float(o.get("fps", 6))),
                                 t0=a, t1=b, thr=int(body.get("thr", o.get("thr", 210))),
                                 min_conf=float(body.get("min_conf", 40)), seg_min=float(body.get("seg_min", 55)),
                                 ref=bool(o.get("ref", True)), task=task,
                                 cb=(lambda pr, msg, i=i: task.set((i + pr) / len(rs), f"Đoạn {i + 1}/{len(rs)}: {msg}")))
        q = load(pid)
        q["cues"], added = merge_cues(q["cues"], found)
        save(q)
        task.result = {"found": len(found), "added": added, "cues": len(q["cues"])}

    if not start_task(pid, "rescan", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


@app.post("/api/p/<pid>/asr")
def run_asr(pid):
    """Nghe toàn bộ video bằng Whisper, lưu các đoạn có tiếng nói vào dự án để đối chiếu với câu OCR."""
    p = load(pid)
    if not p["info"].get("has_audio", True):
        return err("Video này không có âm thanh.")
    body = request.get_json(force=True)
    model = body.get("model") if body.get("model") in asr.MODELS else asr.default_model()
    lang = (body.get("lang") or "").strip() or None

    def work(task):
        segs, got = asr.transcribe(p["video"], float(p["info"]["duration"]), model, lang, task=task, cb=task.set)
        q = load(pid)
        q["asr"] = {"model": model, "lang": got, "segments": segs}
        save(q)
        task.result = {"segments": len(segs), "lang": got}

    if not start_task(pid, "asr", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- dịch
@app.post("/api/p/<pid>/translate")
def run_translate(pid):
    p = load(pid)
    body = request.get_json(force=True)
    key = (body.get("api_key") or "").strip()
    if not key:
        return err("Chưa nhập API key Claude.")
    tr = {"model": body.get("model") or translate.DEFAULT_MODEL, "rules": body.get("rules", ""),
          "glossary": body.get("glossary", "")}
    p["translate"] = tr
    if "cues" in body:
        p["cues"] = body["cues"]
    save(p)
    only_empty = bool(body.get("only_empty", True))

    def work(task):
        q = load(pid)
        miss = translate.translate_cues(q["cues"], key, tr["model"], tr["rules"], tr["glossary"], only_empty,
                                        task=task, cb=task.set)
        save(q)
        task.result = {"missing": miss}

    if not start_task(pid, "translate", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


@app.post("/api/p/<pid>/import_srt")
def import_srt(pid):
    p = load(pid)
    body = request.get_json(force=True)
    if "cues" in body:
        p["cues"] = body["cues"]
    try:
        n, how = translate.apply_srt(p["cues"], body.get("text", ""))
    except ValueError as e:
        return err(str(e))
    save(p)
    return jsonify({"matched": n, "how": how, "cues": p["cues"]})


@app.get("/api/p/<pid>/srt/<kind>")
def export_srt(pid, kind):
    p = load(pid)
    if kind == "vi":
        txt, name = srt.dump(p["cues"], "vi"), p["name"] + "_vi.srt"
    elif kind == "src":
        txt, name = srt.dump(p["cues"], "src"), p["name"] + "_goc.srt"
    else:
        abort(404)
    return Response(txt, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{__import__('urllib.parse').parse.quote(name)}"})


# ---------------------------------------------------------------- tài nguyên (logo)
@app.post("/api/p/<pid>/asset")
def upload_asset(pid):
    d = pdir(pid)
    f = request.files.get("file")
    if not f:
        return err("Thiếu file.")
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        return err("Logo phải là PNG, JPG hoặc WEBP.")
    name = "logo_" + uuid.uuid4().hex[:6] + ext
    f.save(os.path.join(d, "assets", name))
    return jsonify({"file": name})


@app.get("/api/p/<pid>/asset/<name>")
def get_asset(pid, name):
    d = pdir(pid)
    name = os.path.basename(name)
    if name == "dauxanh_logo.png":  # tên cũ của logo mặc định
        name = "logo.png"
    for base in (os.path.join(d, "assets"), util.ASSET_DIR):
        f = os.path.join(base, name)
        if os.path.isfile(f):
            return send_file(f)
    abort(404)


# ---------------------------------------------------------------- xem thử & render
@app.post("/api/p/<pid>/preview")
def preview(pid):
    p = load(pid)
    body = request.get_json(force=True)
    cues = body.get("cues", p["cues"])
    try:
        path = render.render_preview(p["video"], p["info"], cues, body.get("settings", p["settings"]),
                                     pdir(pid), float(body.get("t", 0)))
    except Exception as e:  # noqa
        return err(str(e), 500)
    return send_file(path, mimetype="image/jpeg", max_age=0)


@app.post("/api/p/<pid>/render")
def run_render(pid):
    p = load(pid)
    body = request.get_json(force=True)
    if "cues" in body:
        p["cues"] = body["cues"]
    if "settings" in body:
        p["settings"] = body["settings"]
    save(p)
    if not any((c.get("vi") or "").strip() for c in p["cues"]):
        return err("Chưa có bản dịch nào. Hãy dịch hoặc nhập SRT tiếng Việt trước.")

    def work(task):
        q = load(pid)
        out = render.render_final(q["video"], q["info"], q["cues"], q["settings"], pdir(pid),
                                  task=task, cb=task.set)
        open(os.path.join(pdir(pid), "subs_vi.srt"), "w", encoding="utf-8").write(srt.dump(q["cues"], "vi"))
        open(os.path.join(pdir(pid), "subs_src.srt"), "w", encoding="utf-8").write(srt.dump(q["cues"], "src"))
        task.result = {"file": os.path.basename(out), "size": os.path.getsize(out)}
        if EXPORT_DIR:
            task.result["export"] = export_copy(q, pdir(pid), out, task)

    if not start_task(pid, "render", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


def export_copy(p, d, out, task):
    """Chép video và SRT đã xuất sang EXPORT_DIR (vd. Google Drive). Lỗi chép không làm hỏng bản xuất."""
    try:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", p["name"]).strip() or p["id"]
        dst = os.path.join(EXPORT_DIR, safe + "_vi.mp4")
        os.makedirs(EXPORT_DIR, exist_ok=True)
        total = os.path.getsize(out)
        done = 0
        task.set(0.99, "Đang chép video vào thư mục lưu trữ...")
        with open(out, "rb") as fi, open(dst, "wb") as fo:
            while True:
                b = fi.read(8 << 20)
                if not b:
                    break
                fo.write(b)
                done += len(b)
                task.set(0.99, f"Đang chép vào thư mục lưu trữ {done * 100 // max(1, total)}%")
        for name, suffix in (("subs_vi.srt", "_vi.srt"), ("subs_src.srt", "_goc.srt")):
            f = os.path.join(d, name)
            if os.path.isfile(f):
                shutil.copyfile(f, os.path.join(EXPORT_DIR, safe + suffix))
        return dst
    except Exception as e:  # noqa
        traceback.print_exc()
        return ""


@app.get("/api/p/<pid>/task")
def task_status(pid):
    pdir(pid)
    t = TASKS.get(pid)
    return jsonify(t.dump() if t else {"kind": None, "state": "idle", "progress": 0, "message": ""})


@app.post("/api/p/<pid>/task/cancel")
def task_cancel(pid):
    t = TASKS.get(pid)
    if t and t.state == "running":
        t.cancel = True
    return jsonify({"ok": True})


@app.get("/api/p/<pid>/final")
def final_file(pid):
    f = os.path.join(pdir(pid), "final.mp4")
    if not os.path.isfile(f):
        abort(404)
    p = load(pid)
    dl = request.args.get("download")
    return send_file(f, mimetype="video/mp4", conditional=True, as_attachment=bool(dl),
                     download_name=p["name"] + "_vi.mp4")


@app.get("/api/p/<pid>/check")
def check(pid):
    f = os.path.join(pdir(pid), "final.mp4")
    if not os.path.isfile(f):
        return err("Chưa có video đã xuất.", 404)
    return jsonify({"checks": render.check_youtube(f)})


def main():
    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")
    if host != "127.0.0.1" and not TOKEN:
        raise SystemExit("Mở server ra ngoài (HOST khác 127.0.0.1) thì bắt buộc đặt ACCESS_TOKEN.")
    url = f"http://127.0.0.1:{port}"
    restore_backups()
    start_sync()
    print("\n  Video localization đang chạy tại", url, "\n  (đóng cửa sổ này để tắt)\n")
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
