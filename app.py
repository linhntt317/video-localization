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

from studio import asr, ocr, render, srt, tools, translate, tts, util
from studio.util import Cancelled

ROOT = util.ROOT


def load_local_env():
    """Đọc file local.env (mỗi dòng KEY=VALUE, # là ghi chú) để đặt cấu hình mà không phải gõ biến môi trường mỗi lần.
    Biến môi trường có sẵn luôn được ưu tiên."""
    f = os.path.join(ROOT, "local.env")
    if not os.path.isfile(f):
        return
    for ln in open(f, encoding="utf-8-sig").read().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


load_local_env()
PROJ_DIR = os.path.join(ROOT, "projects")
os.makedirs(PROJ_DIR, exist_ok=True)

import mimetypes  # noqa: E402
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")

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
    if request.path.startswith("/vendor/"):
        resp.headers["Cache-Control"] = "public, max-age=2592000"  # bộ ffmpeg.wasm ~32MB, trình duyệt giữ lại để lần sau khỏi tải
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


def video_error(p):
    """Nếu file video của dự án không còn (vd. Colab vừa khởi động lại), trả về thông báo rõ ràng (và tự chép lại từ Drive nếu có thể)."""
    v = p.get("video") or ""
    if os.path.isfile(v):
        return None
    name = os.path.basename(v)
    if MEDIA_SRC and MEDIA_DIR and os.path.realpath(v).startswith(os.path.realpath(MEDIA_DIR) + os.sep):
        rel = os.path.relpath(os.path.realpath(v), os.path.realpath(MEDIA_DIR))
        if os.path.isfile(os.path.join(MEDIA_SRC, rel)):
            start_sync()
            if SYNC["running"]:
                tot = SYNC["total"] or 1
                return (f"Video '{name}' đang được chép từ Drive về máy xử lý ({SYNC['done'] * 100 // tot}%). "
                        "Chờ chép xong rồi thử lại (có thể mất vài phút với video lớn).")
            return f"Đã bắt đầu chép lại video '{name}' từ Drive. Chờ một lát rồi thử lại."
    return (f"Không tìm thấy file video '{name}' trên máy xử lý. Có thể máy xử lý (Colab) vừa khởi động lại. "
            "Hãy để video trong Drive ▸ video-localization ▸ videos rồi bấm 'Làm mới danh sách' ở bước 1, hoặc tạo dự án mới.")


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
        "tts": {"available": tts.available(), "default_voice": tts.DEFAULT_VOICE, "max_speed": tts.MAX_SPEED},
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
DL = {"state": "idle", "message": "", "done": 0, "total": 0, "pid": None, "input": None, "error": "", "cancel": False}


def _fetch(url, d, stem):
    """Tải video từ link (trực tiếp hoặc Google Drive) vào thư mục d, cập nhật DL. Trả về (đường dẫn, tên file)."""
    import urllib.parse
    import urllib.request
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
        import gdown
        DL["message"] = "Đang tải từ Google Drive (không hiện tiến độ)"
        out = gdown.download(url=url, output=os.path.join(d, stem + "_dl"), quiet=True, fuzzy=True)
        if not out or not os.path.isfile(out):
            raise RuntimeError("Không tải được từ Google Drive. Hãy đặt file ở chế độ 'Bất kỳ ai có đường liên kết'.")
        return out, os.path.basename(out)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        DL["total"] = int(r.headers.get("Content-Length") or 0)
        cd = r.headers.get("Content-Disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
        name = os.path.basename(urllib.parse.unquote(m.group(1))) if m else os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)) or "video.mp4"
        if os.path.splitext(name)[1].lower() not in VIDEO_EXT:
            name = (os.path.splitext(name)[0] or "video") + ".mp4"
        path = os.path.join(d, stem + os.path.splitext(name)[1].lower())
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
    return path, name


def _download(url, to="project"):
    DL.update(state="running", message="Đang bắt đầu tải", done=0, total=0, pid=None, input=None, error="", cancel=False)
    if to == "tools":
        os.makedirs(TOOLS_IN, exist_ok=True)
        try:
            tmp = os.path.join(TOOLS_IN, "dl_" + uuid.uuid4().hex[:6])
            os.makedirs(tmp)
            path, name = _fetch(url, tmp, "src")
            final = unique_path(TOOLS_IN, safe_name(name))
            os.replace(path, final)
            shutil.rmtree(tmp, ignore_errors=True)
            DL.update(state="done", input=os.path.basename(final), message="Xong")
        except Exception as e:  # noqa
            shutil.rmtree(tmp, ignore_errors=True)
            DL.update(state="error", error=str(e) or "Tải lỗi")
        return
    pid = uuid.uuid4().hex[:10]
    d = os.path.join(PROJ_DIR, pid)
    os.makedirs(os.path.join(d, "assets"), exist_ok=True)
    try:
        path, name = _fetch(url, d, "source")
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
    threading.Thread(target=_download, args=(url, (request.get_json(silent=True) or {}).get("to") or "project"), daemon=True).start()
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
    p = load(pid)
    p["_video_error"] = video_error(p)  # chỉ để giao diện hiển thị, không lưu vào project.json
    return jsonify(p)


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
    for k in ("name", "bands", "primary", "ocr", "cues", "settings", "translate", "dub"):
        if k in body:
            p[k] = body[k]
    save(p)
    return jsonify({"ok": True})


@app.get("/api/p/<pid>/frame")
def frame(pid):
    p = load(pid)
    ve = video_error(p)
    if ve:
        return err(ve, 503)
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


def asr_uncovered(cues, segs):
    """Đoạn có tiếng nói (theo Whisper) mà không có câu nào tương ứng (dung sai 0,4s). Giống logic ở giao diện."""
    out = []
    for g in segs or []:
        d = g["end"] - g["start"]
        if d < 0.4 or len((g.get("text") or "").strip()) < 2:
            continue
        need = min(0.3, d * 0.5)
        if not any(min(c["end"] + 0.4, g["end"]) - max(c["start"] - 0.4, g["start"]) >= need for c in cues):
            out.append(g)
    return out


def deep_ranges(p, ranges, task, p0, p1, pad=0.4):
    """Quét OCR thật kỹ nhiều đoạn (xác thực chỗ Whisper nghe thấy tiếng). Tiến độ trải từ p0 đến p1."""
    o, found = p["ocr"], []
    thr = int(o.get("thr", 210))
    for i, (a, b) in enumerate(ranges):
        got = ocr.deep_scan(p["video"], p["info"], p["bands"], p.get("primary", 0), a, b, thr=thr, task=task,
                            cb=(lambda pr, i=i: task.set(p0 + (p1 - p0) * (i + pr) / len(ranges), f"Quét kỹ đoạn nghi sót {i + 1}/{len(ranges)}")))
        ca, cb_ = a + pad, max(a + pad + 0.2, b - pad)  # lõi của đoạn (bỏ phần đệm hai đầu, dễ dính câu của đoạn bên cạnh)
        found += [c for c in got if min(c["end"], cb_) - max(c["start"], ca) >= min(0.25, 0.5 * (cb_ - ca))]
    return found


@app.post("/api/p/<pid>/ocr")
def run_ocr(pid):
    p = load(pid)
    ve = video_error(p)
    if ve:
        return err(ve, 503)
    body = request.get_json(force=True)
    bands = body.get("bands") or p["bands"]
    primary = int(body.get("primary", p.get("primary", 0)))
    o = body.get("ocr") or p["ocr"]
    p["bands"], p["primary"], p["ocr"] = bands, primary, o
    save(p)

    wh = (o.get("asr") or {}) if isinstance(o.get("asr"), dict) else {}
    use_asr = bool(wh.get("on")) and asr.available() and p["info"].get("has_audio", True)

    def work(task):
        task.set(0.0, "Bắt đầu quét")
        sc = 0.5 if use_asr else 1.0  # phần tiến độ dành cho OCR chính
        cues = ocr.run_ocr(p["video"], p["info"], bands, primary, fps=float(o.get("fps", 6)),
                           t0=float(o.get("t0") or 0), t1=(float(o["t1"]) if o.get("t1") else None),
                           thr=int(o.get("thr", 210)), min_conf=_mc(o),
                           seg_min=float(o.get("seg_min", 55)), ref=bool(o.get("ref", True)),
                           task=task, cb=lambda pr, msg: task.set(pr * sc, msg))
        q = load(pid)
        q["cues"] = cues
        save(q)
        task.result = {"cues": len(cues)}
        if not use_asr:
            if wh.get("on"):
                task.result["asr_error"] = ("Máy xử lý chưa cài Whisper (faster-whisper)." if not asr.available() else "Video không có âm thanh.")
            return
        # --- đối chiếu bằng Whisper ngay sau khi quét ---
        try:
            task.set(0.5, "Whisper đang nghe video để đối chiếu")
            model = wh.get("model") if wh.get("model") in asr.MODELS else asr.default_model()
            segs, got = asr.transcribe(p["video"], float(p["info"]["duration"]), model, (wh.get("lang") or None), task=task,
                                       cb=lambda pr, msg: task.set(0.5 + 0.3 * pr, "Whisper: " + msg))
            q = load(pid)
            q["asr"] = {"model": model, "lang": got, "segments": segs}
            miss = asr_uncovered(q["cues"], segs)
            task.result.update(segments=len(segs), missing=len(miss), rescanned=0)
            if miss:  # xác thực: OCR lại thật kỹ chỗ Whisper nghe thấy tiếng, chỉ thêm câu khi OCR thấy chữ thật
                dur = float(p["info"]["duration"])
                found = deep_ranges(q, [(max(0.0, g["start"] - 0.4), min(dur, g["end"] + 0.4)) for g in miss[:300]], task, 0.8, 0.97)
                q["cues"], added = merge_cues(q["cues"], found)
                task.result["rescanned"] = added
                miss = asr_uncovered(q["cues"], segs)
                task.result["missing"] = len(miss)
            task.result["cues"] = len(q["cues"])
            save(q)
        except Cancelled:
            raise
        except Exception as e:  # noqa  (lỗi Whisper không được làm mất kết quả OCR đã có)
            traceback.print_exc()
            task.result["asr_error"] = str(e)

    if not start_task(pid, "ocr", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


@app.post("/api/p/<pid>/probe")
def probe_ocr(pid):
    p = load(pid)
    ve = video_error(p)
    if ve:
        return err(ve, 503)
    body = request.get_json(force=True)
    o = p["ocr"]
    try:
        res = ocr.probe_frame(p["video"], p["info"], p["bands"], p.get("primary", 0), float(body.get("t", 0)),
                              thr=int(o.get("thr", 210)), min_conf=_mc(o), seg_min=float(o.get("seg_min", 55)))
    except Exception as e:  # noqa
        return err(str(e), 500)
    return jsonify({"bands": res, "min_conf": _mc(o), "seg_min": float(o.get("seg_min", 55))})


def merge_cues(old, new):
    """Thêm các câu mới không trùng thời gian với câu đang có (trùng > 40% độ dài câu ngắn hơn thì bỏ), kể cả trùng giữa
    các câu mới với nhau (ưu tiên câu dài hơn, rồi tin cậy cao hơn). Trả về (danh sách, số câu thêm)."""
    out, added = list(old), 0
    for n in sorted(new, key=lambda c: (-(c["end"] - c["start"]), -(c.get("conf") or 0))):
        dn = max(0.05, n["end"] - n["start"])
        if not any(min(e["end"], n["end"]) - max(e["start"], n["start"]) > 0.4 * min(dn, max(0.05, e["end"] - e["start"])) for e in out):
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
    ve = video_error(p)
    if ve:
        return err(ve, 503)
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
        if body.get("deep"):
            found = deep_ranges(p, rs, task, 0.0, 1.0, pad=0.7)
        for i, (a, b) in enumerate([] if body.get("deep") else rs):
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
    ve = video_error(p)
    if ve:
        return err(ve, 503)
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
    ve = video_error(p)
    if ve:
        return err(ve, 503)
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
    ve = video_error(p)
    if ve:
        return err(ve, 503)
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



# ---------------------------------------------------------------- lồng tiếng (giọng đọc cho từng câu)
_VOICES = {}


@app.get("/api/tts/voices")
def tts_voices():
    if "v" not in _VOICES:
        _VOICES["v"] = tts.voices()
    return jsonify({"available": tts.available(), "voices": _VOICES["v"], "default": tts.DEFAULT_VOICE})


def _need_tts():
    if not tts.available():
        return "Máy xử lý chưa cài edge-tts (giọng đọc). Colab: chạy lại ô cài đặt mới nhất; máy tính: pip install edge-tts."
    return None


@app.post("/api/p/<pid>/tts/synth")
def tts_synth(pid):
    """Tạo giọng đọc cho nhiều câu (mặc định mọi câu có bản dịch). Câu đã có giọng giống hệt thì dùng lại, trừ khi force."""
    e = _need_tts()
    if e:
        return err(e)
    p = load(pid)
    body = request.get_json(force=True)
    voice = body.get("voice") or tts.DEFAULT_VOICE
    if "cues" in body:
        p["cues"] = body["cues"]
    p["dub"] = {**(p.get("dub") or {}), "voice": voice}
    save(p)
    idxs, force = body.get("indices"), bool(body.get("force"))

    def work(task):
        q = load(pid)
        idx = [int(i) for i in idxs] if idxs is not None else list(range(len(q["cues"])))
        failed = tts.synth_many(pdir(pid), q["cues"], idx, voice, force, task=task, cb=task.set)
        save(q)
        task.result = {"failed": failed, "cues": sum(1 for c in q["cues"] if c.get("dub"))}

    if not start_task(pid, "tts", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


@app.post("/api/p/<pid>/tts/cue")
def tts_cue(pid):
    """Tạo lại giọng cho một câu (nút 'Tạo lại giọng')."""
    e = _need_tts()
    if e:
        return err(e)
    p = load(pid)
    body = request.get_json(force=True)
    i = int(body.get("i", -1))
    if not (0 <= i < len(p["cues"])):
        return err("Câu không tồn tại.", 404)
    voice = body.get("voice") or (p.get("dub") or {}).get("voice") or tts.DEFAULT_VOICE
    try:
        d = tts.synth_cue(pdir(pid), voice, p["cues"][i].get("vi", ""), force=bool(body.get("force", True)))
    except Exception as ex:  # noqa
        return err(str(ex), 500)
    if not d:
        return err("Câu này chưa có bản dịch để đọc.")
    old = p["cues"][i].get("dub") or {}
    d["user"] = old.get("user") if old.get("text") == d["text"] else None
    p["cues"][i]["dub"] = d
    save(p)
    return jsonify(d)


@app.get("/api/p/<pid>/tts/audio/<key>")
def tts_audio(pid, key):
    if not re.fullmatch(r"[a-f0-9]{16}", key):
        abort(404)
    f = tts.audio_path(pdir(pid), key)
    if not os.path.isfile(f):
        abort(404)
    return send_file(f, mimetype="audio/mpeg", conditional=True)


@app.post("/api/p/<pid>/shorten")
def run_shorten(pid):
    """Nhờ Claude rút gọn các câu có giọng đọc quá dài so với khung thời gian, rồi tạo lại giọng cho các câu đó."""
    p = load(pid)
    body = request.get_json(force=True)
    key = (body.get("api_key") or "").strip()
    if not key:
        return err("Chưa nhập API key Claude.")
    e = _need_tts()
    if e:
        return err(e)
    if "cues" in body:
        p["cues"] = body["cues"]
    save(p)
    model = body.get("model") or translate.DEFAULT_MODEL
    max_speed = float(((p.get("settings") or {}).get("dub") or {}).get("max_speed", tts.MAX_SPEED))
    voice = (p.get("dub") or {}).get("voice") or tts.DEFAULT_VOICE

    def work(task):
        q = load(pid)
        cs, items = q["cues"], []
        for i, c in enumerate(cs):
            sp, st = tts.fit(c, tts.slot_of(cs, i), max_speed)
            d = c.get("dub")
            if st != "long" or not d or d.get("text") != c.get("vi"):
                continue
            rate = len(tts.speech_text(c["vi"])) / max(0.2, d["dur"])  # ký tự mỗi giây của giọng này ở tốc độ thường
            items.append({"id": str(i), "text": c["vi"], "source": c.get("src", ""),
                          "max_chars": max(4, int(tts.slot_of(cs, i) * max_speed * 0.95 * rate))})
        task.result = {"shortened": 0, "long": len(items)}
        if not items:
            return
        out = translate.shorten_cues(items, key, model, task=task, cb=lambda pr, msg: task.set(pr * 0.5, msg))
        changed = []
        for it in items:
            v = out.get(it["id"])
            if v and v != cs[int(it["id"])]["vi"]:
                cs[int(it["id"])]["vi"] = v
                cs[int(it["id"])]["dub"] = None
                changed.append(int(it["id"]))
        save(q)
        if changed:
            tts.synth_many(pdir(pid), cs, changed, voice, True, task=task, cb=lambda pr, msg: task.set(0.5 + 0.5 * pr, msg))
            save(q)
        task.result = {"shortened": len(changed), "long": len(items)}

    if not start_task(pid, "shorten", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- công cụ riêng: ghép video, cắt video, tách MP3
TOOLS_DIR = os.path.join(ROOT, "tools")
TOOLS_IN = os.path.join(TOOLS_DIR, "in")
TOOLS_OUT = os.path.join(TOOLS_DIR, "out")
os.makedirs(TOOLS_IN, exist_ok=True)
os.makedirs(TOOLS_OUT, exist_ok=True)
SRC_REG = {}  # sid -> {"path","name","info"}


def safe_name(name, default="video"):
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", os.path.basename(name or "")).strip(" .")
    return n or default


def unique_path(d, name):
    base, ext = os.path.splitext(name)
    f, i = os.path.join(d, name), 2
    while os.path.exists(f):
        f = os.path.join(d, f"{base}_{i}{ext}")
        i += 1
    return f


def resolve_source(src):
    """Đổi mô tả nguồn video ({media|project|input|path}) thành đường dẫn thật, kiểm tra an toàn."""
    src = src or {}
    if src.get("media"):
        f = media_path(src["media"])
    elif src.get("project"):
        f = load(src["project"]).get("video")
    elif src.get("input"):
        f = os.path.join(TOOLS_IN, os.path.basename(src["input"]))
    elif src.get("path"):
        f = str(src["path"]).strip().strip('"')
    else:
        f = None
    if not f or not os.path.isfile(f):
        raise ValueError("Không tìm thấy file video đã chọn (có thể máy xử lý vừa khởi động lại). Hãy chọn lại.")
    return f


def reg_source(src):
    f = resolve_source(src)
    info = util.probe(f)
    sid = uuid.uuid5(uuid.NAMESPACE_URL, os.path.realpath(f)).hex[:12]
    SRC_REG[sid] = {"path": f, "name": os.path.basename(f), "info": info}
    return sid, SRC_REG[sid]


def src_by_sid(sid):
    r = SRC_REG.get(sid)
    if not r or not os.path.isfile(r["path"]):
        raise ValueError("Video đã chọn không còn. Hãy chọn lại.")
    return r


def src_view(sid, r):
    i = r["info"]
    return {"sid": sid, "name": r["name"], "duration": i["duration"], "width": i["width"], "height": i["height"],
            "has_audio": i["has_audio"], "codec": i["codec"], "size": i["size"] or os.path.getsize(r["path"])}


@app.get("/api/tools/sources")
def tools_sources():
    media = []
    if MEDIA_DIR and os.path.isdir(MEDIA_DIR):
        base = os.path.realpath(MEDIA_DIR)
        for root, _, files in os.walk(base):
            if root[len(base):].count(os.sep) > 2:
                continue
            for fn in files:
                if fn.lower().endswith(VIDEO_EXT):
                    f = os.path.join(root, fn)
                    media.append({"name": os.path.relpath(f, base), "size": os.path.getsize(f), "mtime": os.path.getmtime(f)})
    media.sort(key=lambda x: -x["mtime"])
    projects = []
    for d in os.listdir(PROJ_DIR):
        f = os.path.join(PROJ_DIR, d, "project.json")
        if os.path.isfile(f):
            try:
                pj = json.load(open(f, encoding="utf-8"))
                if os.path.isfile(pj.get("video", "")):
                    projects.append({"id": pj["id"], "name": pj["name"], "size": os.path.getsize(pj["video"]), "mtime": os.path.getmtime(f)})
            except Exception:  # noqa
                pass
    projects.sort(key=lambda x: -x["mtime"])
    inputs = [{"name": fn, "size": os.path.getsize(os.path.join(TOOLS_IN, fn)), "mtime": os.path.getmtime(os.path.join(TOOLS_IN, fn))}
              for fn in os.listdir(TOOLS_IN) if os.path.isfile(os.path.join(TOOLS_IN, fn))]
    inputs.sort(key=lambda x: -x["mtime"])
    return jsonify({"media": media, "projects": projects, "inputs": inputs, "has_media": bool(MEDIA_DIR)})


@app.post("/api/tools/source")
def tools_source():
    try:
        sid, r = reg_source((request.get_json(silent=True) or {}).get("src"))
    except ValueError as e:
        return err(str(e), 404)
    except Exception as e:  # noqa
        return err(str(e))
    return jsonify(src_view(sid, r))


@app.get("/api/tools/frame/<sid>")
def tools_frame(sid):
    try:
        r = src_by_sid(sid)
        return Response(util.frame_jpeg(r["path"], float(request.args.get("t", 0)), int(request.args.get("w", 960))),
                        mimetype="image/jpeg", headers={"Cache-Control": "no-store"})
    except ValueError as e:
        return err(str(e), 404)
    except Exception as e:  # noqa
        return err(str(e), 500)


@app.post("/api/tools/upload")
def tools_upload():
    f = request.files.get("file")
    if not f:
        return err("Thiếu file.")
    dest = unique_path(TOOLS_IN, safe_name(f.filename))
    f.save(dest)
    try:
        util.probe(dest)
    except Exception as e:  # noqa
        os.remove(dest)
        return err("File không phải video/âm thanh đọc được: " + str(e))
    return jsonify({"input": os.path.basename(dest)})


def tools_job(kind, fn):
    if not start_task("_tools", kind, fn):
        return err("Đang có một tác vụ công cụ khác chạy.", 409)
    return jsonify({"ok": True})


def finish_outputs(task, outs):
    """Ghi kết quả vào task; chép thêm sang EXPORT_DIR (vd. Drive) nếu có."""
    names = [os.path.basename(o) for o in outs]
    exported = []
    if EXPORT_DIR:
        try:
            os.makedirs(EXPORT_DIR, exist_ok=True)
            for i, o in enumerate(outs):
                task.set(0.99, f"Đang chép vào thư mục lưu trữ ({i + 1}/{len(outs)})")
                dst = unique_path(EXPORT_DIR, os.path.basename(o))
                shutil.copyfile(o, dst)
                exported.append(dst)
        except Exception:  # noqa
            traceback.print_exc()
    task.result = {"files": names, "export": exported}


def out_base(name, default):
    return os.path.join(TOOLS_OUT, os.path.splitext(safe_name(name, default))[0] if name else default)


def free_base(base, exts=(".mp4", ".mkv", ".mp3")):
    b, i = base, 2
    while any(os.path.exists(b + e) or os.path.exists(f"{b}_1{e}") for e in exts):
        b = f"{base}_{i}"
        i += 1
    return b


@app.post("/api/tools/merge")
def tools_merge():
    body = request.get_json(force=True)
    try:
        paths = [src_by_sid(x)["path"] for x in body.get("sids", [])]
    except ValueError as e:
        return err(str(e), 404)
    if len(paths) < 2:
        return err("Cần chọn ít nhất 2 video để ghép.")
    base = free_base(out_base(body.get("name"), "ghep"))
    mode = body.get("mode") if body.get("mode") in ("auto", "copy", "reencode") else "auto"
    height = int(body.get("height") or 0)

    def work(task):
        out = tools.merge(paths, base, mode, height, task=task, cb=lambda p: task.set(p * 0.98, "Đang ghép video"))
        finish_outputs(task, [out])

    return tools_job("merge", work)


@app.post("/api/tools/cut")
def tools_cut():
    body = request.get_json(force=True)
    try:
        r = src_by_sid(body.get("sid"))
    except ValueError as e:
        return err(str(e), 404)
    try:
        segs = [(float(a), float(b)) for a, b in body.get("segments", [])]
    except (TypeError, ValueError):
        return err("Thời gian cắt không hợp lệ.")
    if not segs:
        return err("Chưa có đoạn nào để cắt.")
    base = free_base(out_base(body.get("name"), os.path.splitext(r["name"])[0] + "_cat"))
    exact, join = bool(body.get("exact")), bool(body.get("join"))

    def work(task):
        outs = tools.cut(r["path"], segs, base, exact=exact, join=join, task=task, cb=lambda p: task.set(p * 0.98, "Đang cắt video"))
        finish_outputs(task, outs)

    return tools_job("cut", work)


@app.post("/api/tools/audio")
def tools_audio():
    body = request.get_json(force=True)
    try:
        r = src_by_sid(body.get("sid"))
    except ValueError as e:
        return err(str(e), 404)
    if not r["info"]["has_audio"]:
        return err("Video này không có âm thanh để tách.")
    br = int(body.get("bitrate") or 192)
    br = br if br in (96, 128, 160, 192, 256, 320) else 192
    a = body.get("start")
    b = body.get("end")
    base = free_base(out_base(body.get("name"), os.path.splitext(r["name"])[0]))

    def work(task):
        out = tools.extract_mp3(r["path"], base + ".mp3", br, float(a) if a not in (None, "") else None,
                                float(b) if b not in (None, "") else None, task=task, cb=lambda p: task.set(p * 0.98, "Đang tách MP3"))
        finish_outputs(task, [out])

    return tools_job("audio", work)


@app.get("/api/tools/task")
def tools_task():
    t = TASKS.get("_tools")
    return jsonify(t.dump() if t else {"kind": None, "state": "idle", "progress": 0, "message": ""})


@app.post("/api/tools/task/cancel")
def tools_task_cancel():
    t = TASKS.get("_tools")
    if t and t.state == "running":
        t.cancel = True
    return jsonify({"ok": True})


@app.get("/api/tools/outputs")
def tools_outputs():
    out = []
    for fn in os.listdir(TOOLS_OUT):
        f = os.path.join(TOOLS_OUT, fn)
        if os.path.isfile(f):
            out.append({"name": fn, "size": os.path.getsize(f), "mtime": os.path.getmtime(f), "audio": fn.lower().endswith((".mp3", ".m4a", ".wav"))})
    out.sort(key=lambda x: -x["mtime"])
    return jsonify(out)


def _tool_file(name):
    f = os.path.join(TOOLS_OUT, os.path.basename(name))
    if not os.path.isfile(f):
        abort(404)
    return f


@app.get("/api/tools/file/<path:name>")
def tools_file(name):
    f = _tool_file(name)
    return send_file(f, conditional=True, as_attachment=bool(request.args.get("download")), download_name=os.path.basename(f))


@app.delete("/api/tools/file/<path:name>")
def tools_file_delete(name):
    os.remove(_tool_file(name))
    return jsonify({"ok": True})


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
