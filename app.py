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

from studio import ocr, render, srt, translate, util
from studio.util import Cancelled

ROOT = util.ROOT
PROJ_DIR = os.path.join(ROOT, "projects")
os.makedirs(PROJ_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="")
LOCK = threading.Lock()

# ---------------------------------------------------------------- truy cập từ trang web bên ngoài
# ALLOWED_ORIGINS: danh sách origin được gọi API, cách nhau bằng dấu phẩy ("*" = mọi nơi, cần ACCESS_TOKEN).
# ACCESS_TOKEN: nếu đặt, mọi lệnh /api phải kèm mã (header X-Access-Token hoặc ?token=).
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
    return app.send_static_file("index.html")


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
    })


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
            path = (request.get_json(silent=True) or {}).get("path", "").strip().strip('"')
            if not path or not os.path.isfile(path):
                shutil.rmtree(d, ignore_errors=True)
                return err("Không tìm thấy file video theo đường dẫn đã nhập.")
            name = os.path.basename(path)
        info = util.probe(path)
    except Exception as e:  # noqa
        shutil.rmtree(d, ignore_errors=True)
        return err(str(e))
    d_bands = [
        {"name": "Dòng 1", "y0": 0.865, "y1": 0.935, "lang": "chi_sim"},
        {"name": "Dòng 2", "y0": 0.935, "y1": 0.995, "lang": "eng"},
    ]
    p = {"id": pid, "name": os.path.splitext(name)[0], "video": path, "info": info, "bands": d_bands, "primary": 1,
         "ocr": {"fps": 6, "t0": 0, "t1": None, "thr": 210, "min_conf": 55}, "cues": [], "settings": {},
         "translate": {"model": translate.DEFAULT_MODEL, "rules": translate.DEFAULT_RULES, "glossary": ""}}
    save(p)
    return jsonify(p)


@app.get("/api/p/<pid>")
def get_project(pid):
    return jsonify(load(pid))


@app.delete("/api/p/<pid>")
def delete_project(pid):
    shutil.rmtree(pdir(pid), ignore_errors=True)
    TASKS.pop(pid, None)
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
                           thr=int(o.get("thr", 210)), min_conf=float(o.get("min_conf", 55)),
                           seg_min=float(o.get("seg_min", 72)),
                           task=task, cb=task.set)
        q = load(pid)
        q["cues"] = cues
        save(q)
        task.result = {"cues": len(cues)}

    if not start_task(pid, "ocr", work):
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

    if not start_task(pid, "render", work):
        return err("Đang có tác vụ khác chạy.", 409)
    return jsonify({"ok": True})


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
    print("\n  Video localization đang chạy tại", url, "\n  (đóng cửa sổ này để tắt)\n")
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
