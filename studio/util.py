"""Tiện ích chung: tìm ffmpeg/tesseract, probe video, chạy ffmpeg có tiến độ."""
import json
import os
import shutil
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "fonts")
ASSET_DIR = os.path.join(ROOT, "assets")
IS_WIN = sys.platform.startswith("win")
NOWINDOW = 0x08000000 if IS_WIN else 0


class Cancelled(Exception):
    pass


def _find(name, extras=()):
    env = os.environ.get(name.upper() + "_BIN")
    if env and os.path.exists(env):
        return env
    p = shutil.which(name)
    if p:
        return p
    for c in extras:
        if c and os.path.exists(c):
            return c
    return None


def ffmpeg_bin():
    return _find("ffmpeg", [r"C:\ffmpeg\bin\ffmpeg.exe", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"])


def ffprobe_bin():
    p = _find("ffprobe", [r"C:\ffmpeg\bin\ffprobe.exe", "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"])
    if p:
        return p
    f = ffmpeg_bin()
    if f:
        cand = os.path.join(os.path.dirname(f), "ffprobe.exe" if IS_WIN else "ffprobe")
        if os.path.exists(cand):
            return cand
    return None


def tesseract_bin():
    return _find("tesseract", [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"])


def tesseract_langs():
    t = tesseract_bin()
    if not t:
        return []
    try:
        out = subprocess.run([t, "--list-langs"], capture_output=True, text=True,
                             creationflags=NOWINDOW, timeout=20)
        lines = (out.stdout or out.stderr).splitlines()[1:]
        return sorted(l.strip() for l in lines if l.strip() and l.strip() != "osd")
    except Exception:
        return []


def has_filter(name):
    f = ffmpeg_bin()
    if not f:
        return False
    try:
        out = subprocess.run([f, "-hide_banner", "-filters"], capture_output=True, text=True, creationflags=NOWINDOW, timeout=20)
        return any(l.split()[1:2] == [name] for l in out.stdout.splitlines() if len(l.split()) > 2)
    except Exception:
        return False


def need_ffmpeg():
    f = ffmpeg_bin()
    if not f:
        raise RuntimeError("Không tìm thấy ffmpeg. Hãy cài ffmpeg rồi mở lại (xem README).")
    return f


def probe(path):
    fp = ffprobe_bin()
    if not fp:
        raise RuntimeError("Không tìm thấy ffprobe (đi kèm ffmpeg).")
    out = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                         capture_output=True, text=True, creationflags=NOWINDOW)
    if out.returncode != 0:
        raise RuntimeError("Không đọc được video: " + (out.stderr or "").strip()[:300])
    j = json.loads(out.stdout)
    vs = [s for s in j.get("streams", []) if s.get("codec_type") == "video"]
    if not vs:
        raise RuntimeError("File không có luồng video.")
    v = vs[0]
    has_audio = any(s.get("codec_type") == "audio" for s in j.get("streams", []))

    def rate(s):
        try:
            a, b = s.split("/")
            a, b = float(a), float(b)
            return (a / b) if b else 0.0
        except Exception:
            return 0.0

    fps_str = v.get("avg_frame_rate", "0/0")
    if rate(fps_str) <= 0:
        fps_str = v.get("r_frame_rate", "30/1")
    if rate(fps_str) <= 0:
        fps_str = "30/1"
    dur = float(j.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "width": int(v["width"]), "height": int(v["height"]),
        "fps": round(rate(fps_str), 3), "fps_str": fps_str,
        "duration": dur, "has_audio": has_audio,
        "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "bit_rate": int(j.get("format", {}).get("bit_rate") or 0),
        "size": int(j.get("format", {}).get("size") or 0),
    }


def run_ffmpeg(args, duration=None, cb=None, cwd=None, task=None):
    """Chạy ffmpeg, báo tiến độ 0..1 qua cb, hỗ trợ huỷ qua task.cancel."""
    cmd = [need_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-progress", "pipe:1"] + args
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         creationflags=NOWINDOW)
    err = []

    def drain():
        for line in p.stderr:
            err.append(line)

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    for line in p.stdout:
        if task is not None and getattr(task, "cancel", False):
            p.kill()
            raise Cancelled()
        line = line.strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=")[1])
                if duration and cb:
                    cb(min(1.0, max(0.0, us / 1e6 / duration)))
            except ValueError:
                pass
    p.wait()
    th.join(timeout=2)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg lỗi: " + "".join(err)[-1500:])


def frame_jpeg(video, t, width=960):
    """Lấy 1 khung hình JPEG tại thời điểm t."""
    cmd = [need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", video,
           "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"]
    out = subprocess.run(cmd, capture_output=True, creationflags=NOWINDOW)
    if not out.stdout:
        raise RuntimeError("Không lấy được khung hình tại " + str(t))
    return out.stdout
