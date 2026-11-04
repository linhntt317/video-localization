"""Render video: xoá sub cũ (tô nền + nhoè), logo, watermark, sub Việt, xuất chuẩn YouTube."""
import copy
import os
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import util
from .util import Cancelled

DEFAULTS = {
    "old": {"y0": 0.862, "fade": 0.07, "blur": 1.0},
    "logo": {"enabled": True, "pos": "tr", "width": 0.0885, "opacity": 1.0, "margin": 0.0175, "file": "logo.png"},
    "corner": {"enabled": True, "text": "ĐẬU XANH STUDIO", "pos": "tl", "size": 0.0556, "opacity": 0.5,
               "serif": True, "margin": 0.0175},
    "fly": {"enabled": True, "text": "ĐẬU XANH STUDIO", "size": 0.05, "opacity": 0.3, "speed": 1.0, "serif": True},
    "sub": {"font": "DejaVu Sans", "size": 0.048, "outline": 3.2, "shadow": 1.2, "margin_v": 0.044,
            "margin_h": 0.083, "color": "#FFFFFF", "outline_color": "#000000"},
    "export": {"quality": "balanced", "resolution": "source", "audio_kbps": 192},
}

QUALITY = {  # (preset, crf)
    "fast": ("veryfast", 21),
    "balanced": ("fast", 19),
    "best": ("slow", 17),
}


def merged(user):
    cfg = copy.deepcopy(DEFAULTS)
    for k, v in (user or {}).items():
        if isinstance(v, dict) and k in cfg:
            cfg[k].update(v)
    return cfg


def list_fonts():
    """Danh sách tên font (family) trong thư mục fonts/."""
    names = {}
    if os.path.isdir(util.FONT_DIR):
        for f in sorted(os.listdir(util.FONT_DIR)):
            if f.lower().endswith((".ttf", ".otf")):
                try:
                    fam = ImageFont.truetype(os.path.join(util.FONT_DIR, f), 20).getname()[0]
                    names[fam] = f
                except Exception:
                    pass
    return names


def _font_path(serif):
    pref = "DejaVuSerif-Bold.ttf" if serif else "DejaVuSans-Bold.ttf"
    p = os.path.join(util.FONT_DIR, pref)
    if os.path.exists(p):
        return p
    for c in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            ImageFont.truetype(c, 20)
            return c
        except Exception:
            continue
    return None


def make_text_png(path, text, px, serif=True):
    fp = _font_path(serif)
    f = ImageFont.truetype(fp, px) if fp else ImageFont.load_default()
    d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    b = d.textbbox((0, 0), text, font=f)
    pad = max(6, px // 3)
    w, h = b[2] - b[0] + pad * 2, b[3] - b[1] + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    off = max(2, px // 20)
    d.text((pad + off, pad - b[1] + off), text, font=f, fill=(0, 0, 0, 150))
    d.text((pad, pad - b[1]), text, font=f, fill=(255, 255, 255, 255))
    img.save(path)
    return img.size


def _odd(n):
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


class Cleaner:
    """Xoá chữ cứng: dò pixel chữ, tô lại bằng màu nền xung quanh rồi làm nhoè mềm."""

    def __init__(self, W, strip_h, scale, blur=1.0):
        self.W, self.H = W, strip_h
        s = max(0.5, scale)
        self.ko = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(15 * s), _odd(15 * s)))
        self.kd = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(11 * s), _odd(11 * s)))
        self.s1, self.s2, self.s3 = 9 * s * blur, 18 * s * blur, 6 * s * blur

    def __call__(self, img):
        W, H = self.W, self.H
        s = cv2.resize(img, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
        mx = s.max(2)
        th = mx - cv2.morphologyEx(mx, cv2.MORPH_OPEN, self.ko)
        g = s.min(2)
        bh = cv2.morphologyEx(g, cv2.MORPH_CLOSE, self.ko) - g
        b, gg, r = [s[:, :, i].astype(np.int16) for i in range(3)]
        red = (r > 150) & (gg < 100) & (b < 115)
        hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
        colored = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 150)).astype(np.uint8)  # chữ nổi bật màu cam/hồng
        colored = cv2.dilate(colored, self.kd, iterations=2)
        m = (((th > 26) | (bh > 38) | red).astype(np.uint8) | colored)
        m = cv2.dilate(m, self.kd)
        w = (1 - m).astype(np.float32)
        f = s.astype(np.float32)
        num = cv2.GaussianBlur(f * w[:, :, None], (0, 0), self.s1)
        den = cv2.GaussianBlur(w, (0, 0), self.s1)[:, :, None]
        plain = cv2.GaussianBlur(f, (0, 0), self.s2)
        fill = np.where(den > 0.04, num / np.maximum(den, 1e-3), plain)
        out = np.where(m[:, :, None] > 0, fill, f)
        out = cv2.GaussianBlur(out, (0, 0), self.s3)
        out = cv2.resize(out, (W, H), interpolation=cv2.INTER_CUBIC)
        return np.clip(out, 0, 255).astype(np.uint8)


def geometry(cfg, info):
    W, H = info["width"], info["height"]
    top = int(max(0, (cfg["old"]["y0"] - cfg["old"]["fade"]) * H))
    top -= top % 2
    sh = H - top
    sh -= sh % 2
    return W, H, top, sh


def intervals(cues, pad=0.12):
    iv = []
    for c in sorted(cues, key=lambda c: c["start"]):
        a, b = c["start"] - pad, c["end"] + pad
        if iv and a - iv[-1][1] < 0.5:
            iv[-1][1] = max(iv[-1][1], b)
        else:
            iv.append([a, b])
    return iv


def build_strip(video, info, cues, cfg, out_path, task=None, cb=None):
    """Tạo video dải đáy đã xoá sub cũ (chỉ xử lý ở các đoạn có sub)."""
    W, H, top, sh = geometry(cfg, info)
    fps = info["fps_str"]
    iv = intervals([c for c in cues if (c.get("start") is not None)])
    cleaner = Cleaner(W, sh, H / 1080.0, cfg["old"]["blur"])
    rd = subprocess.Popen(
        [util.need_ffmpeg(), "-v", "error", "-nostdin", "-i", video, "-vf", f"fps={fps},crop={W}:{sh}:0:{top}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=util.NOWINDOW)
    wr = subprocess.Popen(
        [util.need_ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{sh}",
         "-r", fps, "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-pix_fmt", "yuv420p",
         out_path], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=util.NOWINDOW)
    fr = info["fps"] or 30.0
    total = max(1, int(info["duration"] * fr))
    fs = W * sh * 3
    n = 0
    k = 0
    try:
        while True:
            if task is not None and task.cancel:
                raise Cancelled()
            buf = rd.stdout.read(fs)
            if len(buf) < fs:
                break
            t = n / fr
            n += 1
            while k < len(iv) and t > iv[k][1]:
                k += 1
            if k < len(iv) and iv[k][0] <= t <= iv[k][1]:
                buf = cleaner(np.frombuffer(buf, np.uint8).reshape(sh, W, 3)).tobytes()
            wr.stdin.write(buf)
            if cb and n % 30 == 0:
                cb(min(1.0, n / total))
    except Cancelled:
        rd.kill()
        wr.kill()
        raise
    finally:
        try:
            wr.stdin.close()
        except Exception:
            pass
    wr.wait()
    rd.wait()


def make_mask(path, W, sh, fade_px):
    y = np.arange(sh, dtype=np.float32)[:, None] * np.ones((1, W), np.float32)
    m = np.clip(y / max(1.0, fade_px) * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(m, "L").save(path)


def _hex_to_ass(h, alpha="00"):
    h = h.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def _ass_t(t):
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, c = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def write_ass(path, cues, cfg, info, only_at=None):
    W, H = info["width"], info["height"]
    sc = H / 1080.0
    st = cfg["sub"]
    style = (
        f"Style: Default,{st['font']},{max(8, round(st['size'] * H))},{_hex_to_ass(st['color'])},&H000000FF,"
        f"{_hex_to_ass(st['outline_color'])},&H80000000,1,0,0,0,100,100,0,0,1,{st['outline'] * sc:.2f},"
        f"{st['shadow'] * sc:.2f},2,{round(st['margin_h'] * W)},{round(st['margin_h'] * W)},"
        f"{round(st['margin_v'] * H)},1")
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n" + style + "\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    for c in cues:
        txt = (c.get("vi") or "").strip()
        if not txt:
            continue
        txt = txt.replace("{", "(").replace("}", ")").replace("\r", "").replace("\n", "\\N")
        a, b = c["start"], c["end"]
        if only_at is not None:
            if not (a - 0.01 <= only_at <= b + 0.01):
                continue
            a, b = 0.0, 5.0
        ev.append(f"Dialogue: 0,{_ass_t(a)},{_ass_t(b)},Default,,0,0,0,,{txt}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")


def _pos(pos, mx, my):
    return {
        "tl": (f"{mx}", f"{my}"),
        "tr": (f"W-w-{mx}", f"{my}"),
        "bl": (f"{mx}", f"H-h-{my}"),
        "br": (f"W-w-{mx}", f"H-h-{my}"),
    }.get(pos, (f"{mx}", f"{my}"))


def prepare_assets(jobdir, cfg, info, project_dir):
    """Tạo các PNG overlay (logo, chữ góc, chữ bay) trong jobdir. Trả về dict tên file và kích thước."""
    W, H = info["width"], info["height"]
    assets = {}
    lg = cfg["logo"]
    if lg["enabled"]:
        src = None
        name = os.path.basename(lg.get("file") or "")
        if name == "dauxanh_logo.png":  # tên cũ của logo mặc định
            name = "logo.png"
        for cand in (os.path.join(project_dir, "assets", name),
                     os.path.join(util.ASSET_DIR, name)):
            if name and os.path.isfile(cand):
                src = cand
                break
        if src:
            im = Image.open(src).convert("RGBA")
            tw = max(8, int(lg["width"] * W))
            im = im.resize((tw, max(8, int(tw * im.height / im.width))), Image.LANCZOS)
            im.save(os.path.join(jobdir, "ov_logo.png"))
            assets["logo"] = im.size
    co = cfg["corner"]
    if co["enabled"] and co["text"].strip():
        assets["corner"] = make_text_png(os.path.join(jobdir, "ov_corner.png"), co["text"], max(8, int(co["size"] * H)), co["serif"])
    fl = cfg["fly"]
    if fl["enabled"] and fl["text"].strip():
        assets["fly"] = make_text_png(os.path.join(jobdir, "ov_fly.png"), fl["text"], max(8, int(fl["size"] * H)), fl["serif"])
    return assets


def build_graph(cfg, info, assets, strip_idx, mask_idx, idx_map, enable_expr, t_off=0.0):
    """Trả về chuỗi filter_complex. idx_map: {'logo': i, 'corner': i, 'fly': i} chỉ mục input."""
    W, H, top, sh = geometry(cfg, info)
    fps = info["fps_str"]
    L = [f"[0:v]fps={fps}[b0]",
         f"[{strip_idx}:v]format=yuv420p[strip]",
         f"[{mask_idx}:v]format=gray[mask]",
         "[strip][mask]alphamerge[stripa]",
         f"[b0][stripa]overlay=0:{top}:enable='{enable_expr}'[v1]"]
    cur = "v1"
    k = 1
    if "logo" in idx_map:
        lg = cfg["logo"]
        m = int(lg["margin"] * H)
        x, y = _pos(lg["pos"], m, m)
        L.append(f"[{idx_map['logo']}:v]format=rgba,colorchannelmixer=aa={lg['opacity']:.3f}[logo]")
        k += 1
        L.append(f"[{cur}][logo]overlay={x}:{y}[v{k}]")
        cur = f"v{k}"
    if "corner" in idx_map:
        co = cfg["corner"]
        m = int(co["margin"] * H)
        x, y = _pos(co["pos"], m, max(2, m - int(0.012 * H)))
        L.append(f"[{idx_map['corner']}:v]format=rgba,colorchannelmixer=aa={co['opacity']:.3f}[wc]")
        k += 1
        L.append(f"[{cur}][wc]overlay={x}:{y}[v{k}]")
        cur = f"v{k}"
    if "fly" in idx_map:
        fl = cfg["fly"]
        sp = max(0.1, float(fl["speed"]))
        ymin = int(0.12 * H)
        ymax = int(max(0.2, cfg["old"]["y0"] - 0.1) * H)
        L.append(f"[{idx_map['fly']}:v]format=rgba,colorchannelmixer=aa={fl['opacity']:.3f}[wf]")
        k += 1
        tt = f"(t+{t_off:.3f})"
        L.append(f"[{cur}][wf]overlay=x='(W-w)*(0.5+0.5*sin(2*PI*{tt}/{24 / sp:.3f}))':"
                 f"y='{ymin}+({ymax}-{ymin}-h)*(0.5+0.5*sin(2*PI*{tt}/{17 / sp:.3f}+1.2))'[v{k}]")
        cur = f"v{k}"
    k += 1
    L.append(f"[{cur}]ass=sub.ass:fontsdir=fonts[v{k}]")
    cur = f"v{k}"
    res = cfg["export"]["resolution"]
    tail = "format=yuv420p"
    if res in ("1080", "720") and H != int(res):
        tail = f"scale=-2:{res}:flags=lanczos,format=yuv420p"
    L.append(f"[{cur}]{tail}[out]")
    return ";\n".join(L)


def _prep_jobdir(jobdir):
    os.makedirs(jobdir, exist_ok=True)
    fd = os.path.join(jobdir, "fonts")
    if os.path.isdir(fd):
        shutil.rmtree(fd)
    shutil.copytree(util.FONT_DIR, fd)


def render_final(video, info, cues, user_cfg, project_dir, out_name="final.mp4", task=None, cb=None):
    cfg = merged(user_cfg)
    job = os.path.join(project_dir, "render")
    _prep_jobdir(job)
    W, H, top, sh = geometry(cfg, info)
    cb = cb or (lambda p, m=None: None)

    cb(0.01, "Xoá sub cũ và làm nhoè nền")
    build_strip(video, info, cues, cfg, os.path.join(job, "strip.mp4"), task=task,
                cb=lambda p: cb(0.01 + 0.39 * p, "Xoá sub cũ và làm nhoè nền"))
    make_mask(os.path.join(job, "mask.png"), W, sh, cfg["old"]["fade"] * H)
    assets = prepare_assets(job, cfg, info, project_dir)
    write_ass(os.path.join(job, "sub.ass"), cues, cfg, info)

    iv = intervals(cues)
    enable = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in iv) or "0"
    inputs = ["-i", video, "-i", "strip.mp4", "-loop", "1", "-framerate", info["fps_str"], "-i", "mask.png"]
    idx_map = {}
    nxt = 3
    for name in ("logo", "corner", "fly"):
        if name in assets:
            inputs += ["-loop", "1", "-i", f"ov_{name}.png"]
            idx_map[name] = nxt
            nxt += 1
    graph = build_graph(cfg, info, assets, 1, 2, idx_map, enable)
    with open(os.path.join(job, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(graph)

    preset, crf = QUALITY.get(cfg["export"]["quality"], QUALITY["balanced"])
    fr = info["fps"] or 30.0
    gop = max(12, int(round(fr * 2)))
    args = inputs + ["-filter_complex_script", "graph.txt", "-map", "[out]"]
    if info["has_audio"]:
        args += ["-map", "0:a:0"]
    args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-g", str(gop), "-bf", "2", "-movflags", "+faststart", "-t", f"{info['duration']:.3f}"]
    if info["has_audio"]:
        args += ["-c:a", "aac", "-b:a", f"{int(cfg['export']['audio_kbps'])}k", "-ar", "48000", "-ac", "2"]
    out_abs = os.path.abspath(os.path.join(project_dir, out_name))
    args += [out_abs]
    cb(0.4, "Ghép logo, watermark, phụ đề và xuất video")
    util.run_ffmpeg(args, duration=info["duration"], cwd=job, task=task,
                    cb=lambda p: cb(0.4 + 0.6 * p, "Ghép logo, watermark, phụ đề và xuất video"))
    cb(1.0, "Hoàn tất")
    return out_abs


def render_preview(video, info, cues, user_cfg, project_dir, t, width=1280):
    """Xem thử đúng như bản xuất, tại thời điểm t. Trả về đường dẫn JPEG."""
    cfg = merged(user_cfg)
    job = os.path.join(project_dir, "preview")
    _prep_jobdir(job)
    W, H, top, sh = geometry(cfg, info)
    cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
           "-vf", f"crop={W}:{sh}:0:{top}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=util.NOWINDOW).stdout
    if len(raw) < W * sh * 3:
        raise RuntimeError("Không lấy được khung hình để xem thử.")
    img = np.frombuffer(raw[:W * sh * 3], np.uint8).reshape(sh, W, 3)
    active = any(c["start"] - 0.12 <= t <= c["end"] + 0.12 for c in cues)
    if active:
        img = Cleaner(W, sh, H / 1080.0, cfg["old"]["blur"])(np.ascontiguousarray(img))
    cv2.imwrite(os.path.join(job, "strip.png"), img)
    make_mask(os.path.join(job, "mask.png"), W, sh, cfg["old"]["fade"] * H)
    assets = prepare_assets(job, cfg, info, project_dir)
    write_ass(os.path.join(job, "sub.ass"), cues, cfg, info, only_at=t)
    inputs = ["-ss", f"{t:.3f}", "-i", video, "-loop", "1", "-i", "strip.png", "-loop", "1", "-i", "mask.png"]
    idx_map, nxt = {}, 3
    for name in ("logo", "corner", "fly"):
        if name in assets:
            inputs += ["-loop", "1", "-i", f"ov_{name}.png"]
            idx_map[name] = nxt
            nxt += 1
    graph = build_graph(cfg, info, assets, 1, 2, idx_map, "1", t_off=t)
    with open(os.path.join(job, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(graph)
    out = os.path.join(project_dir, "preview.jpg")
    args = inputs + ["-filter_complex_script", "graph.txt", "-map", "[out]", "-frames:v", "1", "-q:v", "2", out]
    util.run_ffmpeg(args, cwd=job)
    return out


def check_youtube(path):
    """Kiểm tra file xuất có hợp chuẩn đăng YouTube không."""
    info = util.probe(path)
    fp = util.ffprobe_bin()
    import json
    out = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_streams", path],
                         capture_output=True, text=True, creationflags=util.NOWINDOW)
    streams = json.loads(out.stdout).get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video"), {})
    a = next((s for s in streams if s["codec_type"] == "audio"), None)
    faststart = False
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
        mo, md = head.find(b"moov"), head.find(b"mdat")
        faststart = mo != -1 and (md == -1 or mo < md)
    except Exception:
        pass
    checks = [
        ("Codec video H.264", v.get("codec_name") == "h264", v.get("codec_name", "?")),
        ("Định dạng pixel yuv420p", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt", "?")),
        ("Độ phân giải", info["height"] >= 720, f"{info['width']}x{info['height']}"),
        ("Tốc độ khung hình", 23 <= info["fps"] <= 61, f"{info['fps']} fps"),
        ("Âm thanh AAC", (a or {}).get("codec_name") == "aac", (a or {}).get("codec_name", "không có âm thanh")),
        ("Faststart (xem ngay khi đang tải lên)", faststart, "moov ở đầu file" if faststart else "moov ở cuối file"),
        ("Dung lượng", info["size"] < 256 * 1024 ** 3, f"{info['size'] / 1024 / 1024:.1f} MB"),
    ]
    return [{"name": n, "ok": bool(ok), "value": val} for n, ok, val in checks]
