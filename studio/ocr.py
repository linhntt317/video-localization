"""Dò vùng sub cứng và quét OCR ra danh sách câu (có thời gian)."""
import collections
import concurrent.futures as cf
import difflib
import os
import re
import subprocess
import tempfile
import threading
import traceback

import cv2
import numpy as np

from . import util
from .util import Cancelled

CJK_NOSPACE = {"chi_sim", "chi_tra", "jpn"}

# ---------------------------------------------------------------- engine RapidOCR (PP-OCR qua onnxruntime)
# Đọc chữ Trung tốt hơn Tesseract nhiều (độ tin cậy cao và ổn định nên ít câu bị loại vì "tin cậy thấp"),
# cài bằng pip, không cần phần mềm hệ thống. Mô hình mặc định đọc Trung giản/phồn + Anh; ngôn ngữ khác dùng Tesseract.
RAPID_LANGS = {"chi_sim", "chi_tra", "eng"}
_RAPID = {"engine": None, "error": None}
_RAPID_LOCK = threading.Lock()


def rapid_available():
    """True nếu dùng được RapidOCR (gói rapidocr_onnxruntime hoặc rapidocr). Khởi tạo một lần rồi giữ lại."""
    with _RAPID_LOCK:
        if _RAPID["engine"] is None and _RAPID["error"] is None:
            try:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _RAPID["v2"] = False
                except ImportError:
                    from rapidocr import RapidOCR  # gói mới (>= 2.x)
                    _RAPID["v2"] = True
                _RAPID["engine"] = RapidOCR()
            except Exception as e:  # noqa  (chưa cài / onnxruntime lỗi -> dùng Tesseract)
                _RAPID["error"] = str(e)
        return _RAPID["engine"] is not None


def band_engine(lang, engine="auto"):
    """Engine dùng cho một dải: 'rapid' hoặc 'tesseract'."""
    engine = (engine or "auto").lower()
    ok = set(lang.split("+")) <= RAPID_LANGS
    if engine == "tesseract" or not ok:
        return "tesseract"
    if rapid_available():
        return "rapid"
    if engine == "rapid":
        raise RuntimeError("Chưa dùng được RapidOCR (" + str(_RAPID["error"]) + "). Cài: pip install rapidocr-onnxruntime")
    return "tesseract"


def _rapid_pngs(sub, mask=None):
    """Các phiên bản ảnh cho RapidOCR (mã hoá PNG cho gọn bộ nhớ), đọc lần lượt tới khi chắc chắn. Bộ dò chữ của RapidOCR rất
    nhạy với cách cắt: đo trên video thật, cùng một khung mà thêm 12px lề thì đọc ra rỗng, để nguyên thì đúng (0.91) và
    ngược lại; cắt sát chữ thì tách 1 dòng thành các ô chồng nhau, mất chữ đầu câu. Nên không dựa vào một lần đọc."""
    out = []
    for img in (sub, cv2.copyMakeBorder(sub, 12, 12, 12, 12, cv2.BORDER_REPLICATE),
                cv2.resize(sub, None, fx=min(1.0, 1280.0 / sub.shape[1]), fy=min(1.0, 1280.0 / sub.shape[1]),
                           interpolation=cv2.INTER_AREA)):
        ok, buf = cv2.imencode(".png", img)
        out.append(buf.tobytes())
    return out


def _rapid_best(pngs, lang, enough=75.0):
    best = ("", 0.0)
    for png in pngs:
        r = _rapid_read(png, lang)
        if r[0] and r[1] > best[1]:
            best = r
        if best[1] >= enough:
            break
    return best


def _join_boxes(line, sep):
    """Nối các ô chữ trên 1 dòng (đã xếp trái->phải). Ô chồng lên ô trước thì bỏ phần chữ trùng ở chỗ nối."""
    out, prev_x1 = "", None
    for b in line:
        t = b[3]
        if prev_x1 is not None and b[1] < prev_x1 - 0.3 * b[2] and out:
            k = max((k for k in range(1, min(len(out), len(t)) + 1) if out.endswith(t[:k])), default=0)
            out += t[k:]
        else:
            out += (sep if out else "") + t
        prev_x1 = max(prev_x1 or 0, b[5])
    return out


def _rapid_read(png, lang):
    """Đọc 1 dải bằng RapidOCR -> (chữ, độ tin cậy 0..100). Các ô chữ được xếp theo dòng rồi trái->phải."""
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    with _RAPID_LOCK:  # onnxruntime đã tự dùng nhiều nhân; gọi song song chỉ làm tranh CPU
        eng = _RAPID["engine"]
        if _RAPID.get("v2"):
            r = eng(img)
            parts = [getattr(r, k, None) for k in ("boxes", "txts", "scores")]
            items = [] if any(x is None for x in parts) else list(zip(*parts))
        else:
            r, _ = eng(img)
            items = r or []
    boxes = []
    for b, t, s in items:
        t = (t or "").strip()
        if not t or float(s) < 0.3:
            continue
        ys = [p[1] for p in b]
        xs = [p[0] for p in b]
        boxes.append((sum(ys) / len(ys), min(xs), max(max(ys) - min(ys), 1.0), t, float(s), max(xs)))
    if not boxes:
        return "", 0.0
    boxes.sort()
    lines = [[boxes[0]]]
    for bx in boxes[1:]:
        cy = sum(x[0] for x in lines[-1]) / len(lines[-1])
        if abs(bx[0] - cy) <= 0.6 * max(bx[2], lines[-1][0][2]):
            lines[-1].append(bx)
        else:
            lines.append([bx])
    sep = "" if lang.split("+")[0] in CJK_NOSPACE else " "
    text = " ".join(_join_boxes(sorted(ln, key=lambda x: x[1]), sep) for ln in lines)
    n = sum(len(x[3]) for x in boxes)
    conf = 100.0 * sum(x[4] * len(x[3]) for x in boxes) / max(1, n)
    return text, conf


def _raw_frames(video, t0, t1, fps, y0, h, w):
    cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{t0:.3f}", "-i", video, "-t", f"{t1 - t0:.3f}",
           "-vf", f"fps={fps},crop={w}:{h}:0:{y0}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=util.NOWINDOW)
    return p


def _detect_bands_rapid(video, info, samples=24, y_from=0.55):
    """Dò dòng sub bằng chính bộ OCR (cách của bản desktop): đọc các khung mẫu ở phần dưới khung hình, bỏ chữ đứng yên
    (logo/watermark: cùng chữ cùng chỗ ở >= nửa số khung) và chữ dọc (tên nhân vật), gom các ô chữ theo dòng; dòng xuất
    hiện ở nhiều khung nhất là sub. Không bị nền sáng đánh lừa như cách đếm điểm sáng. Trả về [] nếu không đủ bằng chứng."""
    W, H, dur = info["width"], info["height"], float(info["duration"])
    if dur <= 0 or not rapid_available():
        return []
    y_start = int(H * y_from)
    crop_h = H - y_start
    crop_h -= crop_h % 2
    sw = min(1280, W)
    sc = sw / W
    cand, n = [], 0
    for i in range(samples):
        t = dur * (0.03 + 0.94 * (i + 0.5) / samples)
        cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
               "-vf", f"crop={W}:{crop_h}:0:{y_start},scale={sw}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, capture_output=True, creationflags=util.NOWINDOW).stdout
        h = len(raw) // (sw * 3)
        if h < 8:
            continue
        img = np.frombuffer(raw[: h * sw * 3], np.uint8).reshape(h, sw, 3)
        with _RAPID_LOCK:
            if _RAPID.get("v2"):
                r = _RAPID["engine"](img)
                parts = [getattr(r, k, None) for k in ("boxes", "txts", "scores")]
                items = [] if any(x is None for x in parts) else list(zip(*parts))
            else:
                items = _RAPID["engine"](img)[0] or []
        n += 1
        for b, txt, s in items:
            txt = (txt or "").strip()
            if float(s) < 0.6 or not re.search(r"[A-Za-zÀ-ỹ" + _CJK_CH + "]", txt):
                continue
            ys = [q[1] for q in b]
            xs = [q[0] for q in b]
            y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
            bh = (y1 - y0) / sc
            if not (0.015 * H <= bh <= 0.12 * H) or ((x1 - x0) < 1.2 * (y1 - y0) and len(txt) > 1):
                continue  # quá nhỏ/quá to, hoặc chữ dọc
            cand.append({"fi": i, "txt": txt, "y0": y_start + y0 / sc, "y1": y_start + y1 / sc, "h": bh,
                         "cx": (x0 + x1) / 2 / sc, "cjk": len(re.findall(rf"[{_CJK_CH}]", txt)) >= 0.5 * len(txt)})
    if n < 6 or not cand:
        return []
    seen = collections.defaultdict(set)
    for c in cand:
        seen[(c["txt"], round(c["cx"] / 40), round((c["y0"] + c["y1"]) / 80))].add(c["fi"])
    static = {k for k, v in seen.items() if len(v) >= max(4, 0.5 * n)}
    cand = [c for c in cand if (c["txt"], round(c["cx"] / 40), round((c["y0"] + c["y1"]) / 80)) not in static]
    if not cand:
        return []
    med_h = float(np.median([c["h"] for c in cand]))
    cand.sort(key=lambda c: (c["y0"] + c["y1"]) / 2)
    clusters = []
    for c in cand:
        cy = (c["y0"] + c["y1"]) / 2
        if clusters and cy - clusters[-1]["cy"] <= 0.6 * med_h:
            cl = clusters[-1]
            cl["items"].append(c)
            cl["cy"] = float(np.mean([(x["y0"] + x["y1"]) / 2 for x in cl["items"]]))
        else:
            clusters.append({"items": [c], "cy": cy})
    lines = []
    for cl in clusters:
        frames_hit = len({x["fi"] for x in cl["items"]})
        if frames_hit < max(3, int(0.12 * n)):
            continue
        it = cl["items"]
        hh = float(np.median([x["h"] for x in it]))
        y0 = float(np.percentile([x["y0"] for x in it], 5)) - 0.15 * hh
        y1 = float(np.percentile([x["y1"] for x in it], 95)) + 0.15 * hh
        cjk = sum(x["cjk"] for x in it) >= 0.5 * len(it)
        lines.append({"y0": round(max(0.0, y0 / H), 4), "y1": round(min(1.0, y1 / H), 4),
                      "score": round(frames_hit / n, 3), "lang": "chi_sim" if cjk else "eng"})
    lines.sort(key=lambda l: -l["score"])
    lines = lines[:3]
    lines.sort(key=lambda l: l["y0"])
    return lines


def detect_bands(video, info, samples=20, engine="tesseract"):
    """Tự dò các dòng sub cứng ở nửa dưới khung hình. Trả về danh sách dòng (y0,y1 theo tỉ lệ chiều cao).
    engine != 'tesseract' và có RapidOCR thì dò bằng OCR (kèm đoán ngôn ngữ); còn lại đếm điểm chữ sáng như cũ."""
    try:
        lines = _detect_bands_rapid(video, info) if (engine or "tesseract") != "tesseract" else []
    except Exception:  # noqa
        traceback.print_exc()
        lines = []
    if lines:
        return lines
    W, H, dur = info["width"], info["height"], info["duration"]
    y_start = int(H * 0.60)
    crop_h = H - y_start
    acc_presence = None
    acc_pixels = None
    n = 0
    k9 = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    for i in range(samples):
        t = dur * (0.03 + 0.94 * i / max(1, samples - 1))
        cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
               "-vf", f"crop={W}:{crop_h}:0:{y_start},scale=960:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, capture_output=True, creationflags=util.NOWINDOW).stdout
        if not raw:
            continue
        h = len(raw) // (960 * 3)
        img = np.frombuffer(raw[: h * 960 * 3], np.uint8).reshape(h, 960, 3)
        g = cv2.medianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 3)
        top = g.astype(np.int16) - cv2.morphologyEx(g, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(np.int16)
        mask = ((top >= 45) & (g >= 90)).astype(np.uint8)
        blobs = cv2.dilate(cv2.erode(mask, k9), k9)
        text = mask & (1 - blobs)
        rows = text.sum(1).astype(np.float32)
        if acc_presence is None:
            acc_presence = np.zeros(h, np.float32)
            acc_pixels = np.zeros(h, np.float32)
        hh = min(h, len(acc_presence))
        acc_presence[:hh] += (rows[:hh] >= 6)
        acc_pixels[:hh] += rows[:hh]
        n += 1
    if not n or acc_presence is None:
        return []
    freq = np.convolve(acc_presence / n, np.ones(5) / 5, mode="same")
    pix = np.convolve(acc_pixels / n, np.ones(5) / 5, mode="same")
    sc = 960 / W
    active = freq >= 0.12
    runs, start, gap = [], None, 0
    for r, a in enumerate(active):
        if a:
            if start is None:
                start = r
            last, gap = r, 0
        elif start is not None:
            gap += 1
            if gap > 5:
                runs.append((start, last))
                start = None
    if start is not None:
        runs.append((start, last))
    lines = []
    for a, b in runs:
        height_frac = (b - a) / sc / H
        if height_frac < 0.012:
            continue
        # Nền nhiễu hay làm cả nửa dưới thành 1 run dài; dòng sub thật là đỉnh mật độ chữ rõ rệt.
        # Chỉ giữ các đoạn có mật độ >= 30% đỉnh của run (gộp khe <= 3 hàng).
        floor = 0.3 * float(pix[a:b + 1].max())
        cores, s0, last = [], None, None
        for r in range(a, b + 1):
            if pix[r] >= floor and pix[r] > 0:
                if s0 is not None and r - last > 3:
                    cores.append((s0, last))
                    s0 = None
                if s0 is None:
                    s0 = r
                last = r
        if s0 is not None:
            cores.append((s0, last))
        pieces, todo = [], list(cores)
        while todo:  # nhiều dòng dính nhau: chỉ tách khi giữa có khe thật (thấp hơn nửa đỉnh hai bên)
            x, y = todo.pop()
            if (y - x) / sc / H > 0.085 and len(pieces) + len(todo) < 6:
                lo, hi = x + int((y - x) * 0.3), x + int((y - x) * 0.7)
                cut = lo + int(np.argmin(pix[lo:hi + 1]))
                if pix[cut] < 0.5 * min(pix[x:cut + 1].max(), pix[cut:y + 1].max()):
                    todo += [(x, cut), (cut, y)]
                    continue
            if (y - x) / sc / H >= 0.012:
                pieces.append((x, y))
        for x, y in pieces:
            lines.append({
                "y0": round(max(0.0, (y_start + x / sc) / H - 0.012), 4),
                "y1": round(min(1.0, (y_start + y / sc) / H + 0.012), 4),
                "score": round(float(freq[x:y + 1].mean()), 3),
            })
    lines.sort(key=lambda l: -l["score"])
    lines = lines[:3]
    lines.sort(key=lambda l: l["y0"])
    return lines


def _ocr_best(pngs, lang, tess, enough=86.0):
    """Đọc lần lượt các phiên bản ảnh, dừng sớm khi đã đủ chắc chắn (tiết kiệm phần lớn thời gian quét)."""
    best = ("", 0.0)
    for png in pngs:
        r = _ocr_png(png, lang, tess)
        if r[0] and r[1] > best[1]:
            best = r
        if best[1] >= enough and len(best[0]) >= 3:
            break
    return best


_OEM = ["--oem", "1"]  # chỉ dùng LSTM: nhanh hơn ~1,5 lần; tự bỏ nếu bản Tesseract không hỗ trợ
# Xuất TSV bằng tham số thay vì tên cấu hình "tsv": cấu hình đó là file tessdata/configs/tsv, KHÔNG có khi TESSDATA_PREFIX
# trỏ vào thư mục chỉ chứa *.traineddata (setup.bat) -> Tesseract in chữ thường, TSV rỗng -> quét ra 0 câu.
_TSV = ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]


def _ocr_batch(pngs, lang, tess):
    """Đọc nhiều ảnh trong MỘT lần chạy Tesseract (nhanh hơn nhiều so với gọi từng ảnh vì khỏi khởi động lại). Trả về [(chữ, độ tin cậy)]."""
    global _OEM
    out = [("", 0.0)] * len(pngs)
    if not pngs:
        return out
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, png in enumerate(pngs):
            fp = os.path.join(d, f"{i:05d}.png")
            with open(fp, "wb") as f:
                f.write(png)
            paths.append(fp)
        lst = os.path.join(d, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            f.write("\n".join(paths))
        r = subprocess.run([tess, lst, "stdout", "-l", lang, "--psm", "7"] + _OEM + _TSV, capture_output=True, env=env,
                           creationflags=util.NOWINDOW)
        if r.returncode != 0 and _OEM:
            _OEM = []  # bản Tesseract/dữ liệu ngôn ngữ không hỗ trợ --oem 1: chạy lại như thường
            r = subprocess.run([tess, lst, "stdout", "-l", lang, "--psm", "7"] + _TSV, capture_output=True, env=env,
                               creationflags=util.NOWINDOW)
    pages = {}
    for ln in r.stdout.decode("utf-8", "ignore").splitlines()[1:]:
        p = ln.split("\t")
        if len(p) >= 12 and p[0] == "5":
            try:
                pg, c = int(p[1]), float(p[10])
            except ValueError:
                continue
            w = p[11].strip()
            if c >= 0 and w:
                pages.setdefault(pg, ([], []))[0].append(w)
                pages[pg][1].append(c)
    for pg, (words, confs) in pages.items():
        if 1 <= pg <= len(pngs):
            text = ("".join(words) if lang.split("+")[0] in CJK_NOSPACE else " ".join(words)).strip()
            out[pg - 1] = (text, float(np.mean(confs)))
    return out


def _ocr_png(png, lang, tess):
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    out = subprocess.run([tess, "stdin", "stdout", "-l", lang, "--psm", "7"] + _TSV, input=png,
                         capture_output=True, env=env, creationflags=util.NOWINDOW)
    words, confs = [], []
    for ln in out.stdout.decode("utf-8", "ignore").splitlines()[1:]:
        p = ln.split("\t")
        if len(p) >= 12 and p[0] == "5":
            try:
                c = float(p[10])
            except ValueError:
                continue
            w = p[11].strip()
            if c >= 0 and w:
                words.append(w)
                confs.append(c)
    if not words:
        return "", 0.0
    text = ("".join(words) if lang.split("+")[0] in CJK_NOSPACE else " ".join(words)).strip()
    return text, float(np.mean(confs))


def _text_mask(sub, thr):
    """Mặt nạ chữ sáng theo độ tương phản cục bộ (top-hat), không phụ thuộc chữ phải trắng tuyệt đối:
    chữ hơi xám, đang mờ dần, bị nén hay nằm trên nền sáng vẫn được giữ; mảng sáng lớn của nền bị loại.
    thr (mặc định 210) càng thấp càng nhạy. Không làm mờ trung vị vì nó xoá mất nét chữ mỏng 1 pixel."""
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    k = max(7, int(round(sub.shape[0] * 0.2)) | 1)
    top = g.astype(np.int16) - cv2.morphologyEx(g, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))).astype(np.int16)
    t = int(np.clip(thr - 165, 15, 100))
    cand = ((top >= t) & (g >= 90)).astype(np.uint8)
    if cand.sum() < 12:
        return cand, cand * 255
    # trong vùng khoanh được, chỉ giữ ruột sáng nhất của nét chữ (bỏ viền sáng/mờ quanh chữ) bằng ngưỡng Otsu
    reg = cv2.dilate(cand, np.ones((3, 3), np.uint8)) > 0
    vals = g[reg].reshape(-1, 1)
    o, _ = cv2.threshold(vals, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    o = max(float(o), float(np.percentile(vals, 95)) - 30.0)  # ruột chữ sáng gần bằng mức sáng cao nhất của chữ
    m = (reg & (g >= o)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    small = np.isin(lab, [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] <= 4])
    m[small] = 0  # bỏ đốm nhiễu
    return m, np.clip(top * (255.0 / max(60.0, float(top[m > 0].max()) if m.any() else 60.0)), 0, 255).astype(np.uint8)


def _prep_gray(sub):
    """Ảnh xám gốc phóng to + kéo giãn tương phản: hợp chữ rất mỏng/viền mỏng trên nền sáng (Tesseract tự đọc nét viền tối)."""
    g = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    f = max(1, int(np.ceil(120.0 / g.shape[0])))
    if f > 1:
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(g, (2, 98))
    g = np.clip((g.astype(np.float32) - lo) * (255.0 / max(1.0, hi - lo)), 0, 255).astype(np.uint8)
    g = cv2.copyMakeBorder(g, 12, 12, 16, 16, cv2.BORDER_REPLICATE)
    ok, buf = cv2.imencode(".png", g)
    return buf.tobytes()


def _prep_png(mask, soft=None):
    """Ảnh chữ đen nền trắng cho Tesseract. soft: giữ độ mịn nét chữ (hợp chữ nhỏ/mờ); không có thì nhị phân."""
    if soft is not None:
        img = 255 - (soft * cv2.dilate(mask, np.ones((3, 3), np.uint8))).astype(np.uint8)
        if img.shape[0] < 90:
            f = 2 if img.shape[0] >= 45 else 3
            img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    else:
        img = np.where(mask > 0, 0, 255).astype(np.uint8)
    if soft is None and img.shape[0] < 70:
        f = 2 if img.shape[0] >= 40 else 3
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        img = np.where(img < 140, 0, 255).astype(np.uint8)
    img = cv2.copyMakeBorder(img, 12, 12, 16, 16, cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def _clean(t):
    toks = t.split()
    keep = lambda w: len(w) > 1 or w in ("I", "A", "a", "i") or re.match(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", w)
    while toks and not keep(toks[0]):
        toks.pop(0)
    while toks and not keep(toks[-1]):
        toks.pop()
    out = " ".join(toks)
    return re.sub(r"^[^\w\u3040-\u9fff\uac00-\ud7af'\"“¿¡(（《「『]+", "", out).strip()


def _norm(s):
    return re.sub(r"[\W_]+", "", s.lower())


_CJK_CH = r"぀-ヿ㐀-鿿가-힯"
_CJK_PUNCT = r"，。！？、；：…“”‘’《》「」『』～·?!,.…"


def _clean_cjk(t):
    """Dọn chữ Trung/Nhật/Hàn đọc ra: bỏ khoảng trắng giữa các chữ (OCR hay tách ô), '-' ở đầu/giữa chữ Hán là '一' đọc nhầm,
    bỏ rác ngắn (1-2 ký tự không phải chữ) ở hai đầu: '19下姐姐教我CE' -> '下姐姐教我', '我我是苏晚棠（（' -> '我我是苏晚棠'."""
    t = re.sub(rf"(?<=[{_CJK_CH}])\s+(?=[{_CJK_CH}\-—_])", "", t)
    t = re.sub(rf"(?<=[\-—_])\s+(?=[{_CJK_CH}])", "", t)
    t = re.sub(rf"(^|(?<=[{_CJK_CH}]))[-—_](?=[{_CJK_CH}])", "一", t)
    edge = rf"[^{_CJK_CH}{_CJK_PUNCT}]{{1,2}}"
    for _ in range(2):
        t = re.sub(rf"^\s*{edge}(?=[{_CJK_CH}])", "", t)
        t = re.sub(rf"(?<=[{_CJK_CH}{_CJK_PUNCT}]){edge}$", "", t.strip())
        t = re.sub(r"[（(《「『“]+$", "", t.strip())       # ngoặc mở ở cuối câu = rác
        t = re.sub(r"^[）)》」』”]+", "", t)                # ngoặc đóng ở đầu câu = rác
        for o, c in ("（）", "()"):
            if t.endswith(c) and o not in t:
                t = t.rstrip(c)
            if t.startswith(o) and c not in t:
                t = t.lstrip(o)
    return t.strip()


def _clean_lang(t, lang):
    if lang.split("+")[0] not in CJK_NOSPACE:
        return _clean(t)
    t = re.sub(rf"(^|(?<=[{_CJK_CH}]))\s*[-—_]\s*(?=[{_CJK_CH}])", "一", t.strip())  # trước _clean (nó bỏ '-' đầu câu)
    return _clean_cjk(_clean(t))


def _looks_like_text(mask):
    """Mặt nạ có nét cao cỡ chữ không (mảng liền cao >= 25% dải, tổng diện tích >= 0,035·h²)?
    Nền sáng/nhiễu hay lọt mặt nạ nhưng toàn đốm thấp; RapidOCR đọc mỗi ảnh như vậy cả 3 phiên bản (đo trên video
    thật: 62% lượt đọc là rỗng). Câu 1 chữ mảnh nhất đo được (不) ~0,058·h²."""
    h = mask.shape[0]
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return False
    tall = st[1:, cv2.CC_STAT_HEIGHT] >= 0.25 * h
    return bool(tall.any()) and float(st[1:, cv2.CC_STAT_AREA][tall].sum()) >= 0.035 * h * h


def _key_diff(a, b):
    """Độ khác nhau giữa 2 mặt nạ chữ đã thu nhỏ (0 = giống hệt). Dùng để biết sub đổi câu hay chưa."""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.abs(a - b).sum()) / max(1.0, float(max(a.sum(), b.sum())))


def _mask_key(mask, W):
    return cv2.resize(mask, (max(1, W // 3), max(1, mask.shape[0] // 3)), interpolation=cv2.INTER_AREA).astype(np.float32)


def _cue_ok(text, conf, lang):
    """Lọc rác của luồng quét chính: chữ phải giống chữ thật (plausible). Riêng chữ Hán cho phép câu 1 chữ (嗯/好/啊)
    nếu đọc rất chắc."""
    first = lang.split("+")[0]
    if first in CJK_NOSPACE or first == "kor":
        core = re.sub(r"[^\w]", "", text)
        cjk = len(re.findall(rf"[{_CJK_CH}]", core))
        # chữ "thật": bỏ các chữ chỉ gồm nét ngang/dọc (一 二 丨) - viền sáng, vạch nền, dấu '-' đều hay bị đọc thành chúng
        real = len(re.findall(rf"[{_CJK_CH}]", re.sub(r"[一二三丨丶]", "", core)))
        if real == 0:
            return False
        if cjk == 1 and len(core) == 1:
            return conf >= 75
        return cjk >= 2 and real >= 0.5 * cjk and cjk >= 0.6 * len(core)
    return plausible(text, lang)


def _refine_times(video, info, cues, a, z, W, thr, fps, t0, t1, task=None):
    """Tinh chỉnh mốc start/end theo TỪNG KHUNG ở fps gốc (cách của bản desktop): mốc thô chỉ chính xác tới 1/fps quét;
    giải mã 1 lượt dải chữ chính, chỉ xét khung nằm trong cửa sổ quanh mỗi mốc, 'có sub' = mặt nạ chữ giống mặt nạ tham
    chiếu của câu. Sửa tại chỗ, trả về số mốc đã dời."""
    nf = float(info.get("fps") or 0)
    if not (5.0 <= nf <= 120.0) or not cues:
        return 0
    step = 1.0 / nf
    win = []
    for i, c in enumerate(cues):
        if c.get("_k0") is not None:
            win.append((c["start"] - 1.0 / fps - 2 * step, c["start"] + 2 * step, i, "s"))
        if c.get("_k1") is not None:
            win.append((c["end"] - 1.0 / fps - 2 * step, c["end"] + 2 * step, i, "e"))
    if not win:
        return 0
    win.sort(key=lambda w: w[0])
    h = (z - a) - (z - a) % 2
    lo = max(0.0, win[0][0] - 0.1)
    hi = min(t1, max(w[1] for w in win) + 0.2)
    cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{lo:.3f}", "-i", video, "-t", f"{hi - lo:.3f}",
           "-an", "-sn", "-vf", f"crop={W}:{h}:0:{a}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=util.NOWINDOW)
    fsize = W * h * 3
    obs, active, wi, k = {}, [], 0, 0
    try:
        while True:
            if task is not None and task.cancel:
                raise Cancelled()
            buf = p.stdout.read(fsize)
            if len(buf) < fsize:
                break
            t = lo + k * step
            k += 1
            while wi < len(win) and win[wi][0] <= t:
                active.append(win[wi])
                wi += 1
            active = [w for w in active if w[1] >= t]
            if not active:
                if wi >= len(win):
                    break
                continue
            sub = np.frombuffer(buf, np.uint8).reshape(h, W, 3)
            mask, _ = _text_mask(sub, thr)
            key = _mask_key(mask, W) if mask.sum() >= mask.size * 0.002 else None
            for _, _, i, kind in active:
                ref_key = cues[i]["_k0" if kind == "s" else "_k1"]
                obs.setdefault((i, kind), []).append((t, key is not None and _key_diff(ref_key, key) < 0.5))
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()
    moved = 0
    for i, c in enumerate(cues):
        o = obs.get((i, "s"))
        if o and not o[0][1]:
            vis = [t for t, v in o if v]
            if vis:
                moved += abs(vis[0] - c["start"]) >= step * 0.99
                c["start"] = vis[0]
        o = obs.get((i, "e"))
        if o and not o[-1][1]:
            vis = [t for t, v in o if v]
            if vis:
                ne = vis[-1] + step
                moved += abs(ne - c["end"]) >= step * 0.99
                c["end"] = ne
    for i, c in enumerate(cues):  # không để chồng lấn / quá ngắn sau khi dời mốc
        if c["end"] - c["start"] < 0.1:
            c["end"] = c["start"] + 0.1
        if i and c["start"] < cues[i - 1]["end"]:
            mid = (c["start"] + cues[i - 1]["end"]) / 2
            cues[i - 1]["end"] = max(mid, cues[i - 1]["start"] + 0.1)
            c["start"] = cues[i - 1]["end"]
    return moved


def _merge_adjacent(cues, fps):
    """Gộp câu liền kề bị tách đôi (sub chớp do nén, đọc lệch 1 chữ: '听话有交大' | '听话有奖大') và mảnh mờ dần ngắn nằm
    trọn trong câu bên cạnh. Giữ chữ của phần đọc được nhiều khung hơn."""
    out = []
    gap_max = 1.5 / fps + 0.02
    for c in cues:
        if out:
            g = out[-1]
            gap = c["start"] - g["end"]
            a, b = _norm(g["src"]), _norm(c["src"])
            same = difflib.SequenceMatcher(None, a, b).ratio() >= 0.8
            frag = (min(len(a), len(b)) >= 1 and (a in b or b in a)
                    and min(g["end"] - g["start"], c["end"] - c["start"]) <= 2.1 / fps)
            if gap <= gap_max and (same or frag):
                if frag and not same:
                    keep_c = len(b) > len(a)
                else:
                    keep_c = (c["_n"] * c["conf"], len(b)) > (g["_n"] * g["conf"], len(a))
                if keep_c:
                    g["src"], g["ref"] = c["src"], c["ref"]
                g["conf"] = round((g["conf"] * g["_n"] + c["conf"] * c["_n"]) / max(1, g["_n"] + c["_n"]))
                g["_n"] += c["_n"]
                g["end"], g["_k1"] = c["end"], c["_k1"]
                continue
        out.append(c)
    return out


def run_ocr(video, info, bands, primary, fps=6.0, t0=0.0, t1=None, thr=210, min_conf=40.0, seg_min=55.0, task=None, cb=None,
            debug=None, ref=True, engine="tesseract", refine=True):
    """bands: [{name,y0,y1,lang}]  (y theo tỉ lệ 0..1). primary: chỉ số dòng dùng để chia câu & dịch.
    engine: 'tesseract' (mặc định) | 'rapid' (RapidOCR, thử nghiệm) | 'auto' (RapidOCR cho Trung/Anh nếu đã cài).
    refine: tinh chỉnh mốc start/end theo từng khung ở fps gốc của video (thêm 1 lượt giải mã dải chữ chính)."""
    use = [band_engine(b["lang"], engine) if (ref or bi == primary) else None for bi, b in enumerate(bands)]
    tess = None
    if "tesseract" in use:
        tess = util.tesseract_bin()
        if not tess:
            raise RuntimeError("Không tìm thấy Tesseract OCR. Hãy cài Tesseract (xem README), "
                               "hoặc pip install rapidocr-onnxruntime để đọc chữ Trung/Anh.")
        have = set(util.tesseract_langs())
        for b, e in zip(bands, use):
            for l in (b["lang"].split("+") if e == "tesseract" else []):
                if l not in have:
                    raise RuntimeError(f"Tesseract chưa có ngôn ngữ '{l}'. Cài thêm gói ngôn ngữ (xem README).")
    W, H = info["width"], info["height"]
    t1 = min(t1 or info["duration"], info["duration"])
    px = []
    for b in bands:
        a = int(round(b["y0"] * H))
        z = int(round(b["y1"] * H))
        pad = max(2, int(round((z - a) * 0.12)))
        a = max(0, min(H - 4, a - pad))
        z = max(a + 4, min(H, z + pad))
        px.append((a, z))
    ymin = min(a for a, _ in px)
    ymax = max(z for _, z in px)
    crop_h = ymax - ymin
    crop_h -= crop_h % 2
    p = _raw_frames(video, t0, t1, fps, ymin, crop_h, W)
    fsize = W * crop_h * 3
    total = max(1, int((t1 - t0) * fps))

    nb = len(bands)
    ncpu = max(1, min(8, os.cpu_count() or 2))
    jobs = []          # (band_index, [ảnh các phiên bản]) — ảnh được bỏ ngay khi đọc xong cho nhẹ bộ nhớ
    job_key = []       # mặt nạ thu nhỏ lúc tạo job (tham chiếu để tinh chỉnh mốc thời gian)
    job_prev = []      # job đọc lại (cùng câu) -> job trước đó; đọc lại ra rỗng thì dùng kết quả cũ, không cắt đôi câu
    frames = []        # mỗi khung: [chỉ số job hoặc None cho từng dòng]
    last_key = [None] * nb
    last_job = [None] * nb
    job_px = [0.0] * nb  # lượng điểm chữ lúc tạo job đang dùng của từng dòng
    # RapidOCR đọc 1 lần/job và hay trả rỗng ở khung chữ đang hiện dần -> cả câu dùng lại kết quả rỗng đó và mất câu.
    # Như bản desktop: câu còn hiện thì đọc lại sau ~0,5s (tối đa 2 lần/câu cho đỡ chậm) (gom câu lấy chữ xuất hiện nhiều nhất).
    recheck = max(2, int(round(0.5 * fps)))
    job_age = [0] * nb
    job_reads = [0] * nb
    res = {}           # job -> (chữ, tin cậy)
    lock = threading.Lock()
    prog = {"done": 0}
    BATCH = 16

    def analyse(img):
        """Tính mặt nạ chữ cho mọi dòng của một khung (chạy song song nhiều khung)."""
        out = []
        for bi, (a, z) in enumerate(px):
            if use[bi] is None:  # bỏ dòng đối chiếu cho nhanh
                out.append(None)
                continue
            sub = img[a - ymin: z - ymin]
            mask, soft = _text_mask(sub, thr)
            if mask.sum() < mask.size * 0.002 or (use[bi] == "rapid" and not _looks_like_text(mask)):
                out.append(False)
                continue
            out.append((mask, soft, _mask_key(mask, W), sub))
        return out

    def run_jobs(idx):
        """Đọc một lô job. RapidOCR: 1 lần/ảnh. Tesseract: vòng 1 cho tất cả, chỉ job chưa đủ chắc mới thử phiên bản
        ảnh kế tiếp (gom lô theo ngôn ngữ)."""
        best = {j: ("", 0.0) for j in idx}
        pending = []
        for j in idx:
            if use[jobs[j][0]] == "rapid":
                if task is not None and task.cancel:
                    return
                best[j] = _rapid_best(jobs[j][1], bands[jobs[j][0]]["lang"])
            else:
                pending.append(j)
        for rnd in range(4):
            if task is not None and task.cancel:
                return
            pending = [j for j in pending if rnd < len(jobs[j][1]) and not (best[j][1] >= 86.0 and len(best[j][0]) >= 3)]
            if not pending:
                break
            by_lang = {}
            for j in pending:
                by_lang.setdefault(bands[jobs[j][0]]["lang"], []).append(j)
            for lang, js in by_lang.items():
                for j, r in zip(js, _ocr_batch([jobs[j][1][rnd] for j in js], lang, tess)):
                    if r[0] and r[1] > best[j][1]:
                        best[j] = r
        with lock:
            res.update(best)
            prog["done"] += len(idx)
            for j in idx:
                jobs[j] = (jobs[j][0], None)

    mask_pool = cf.ThreadPoolExecutor(ncpu)
    ocr_pool = cf.ThreadPoolExecutor(ncpu)
    futs, window, backlog, idx = [], [], [], 0

    def flush():
        nonlocal backlog
        if window:
            for img, an in zip(window, list(mask_pool.map(analyse, window))):
                row = []
                for bi in range(nb):
                    a = an[bi]
                    if a is None:
                        row.append(None)
                    elif a is False:
                        row.append(None)
                        last_key[bi] = None
                    else:
                        mask, soft, key, sub = a
                        ks = float(key.sum())
                        # cùng câu (hình chữ gần như không đổi) -> dùng lại chữ đã đọc; NHƯNG nếu chữ đậm/đủ hơn hẳn lúc
                        # đọc (đang hiện dần: lần đọc đầu thiếu chữ, vd '是热的我是活' thiếu '人') thì đọc lại
                        same = _key_diff(last_key[bi], key) < 0.35 and ks <= 1.2 * job_px[bi]
                        again = same and use[bi] == "rapid" and job_age[bi] >= recheck and job_reads[bi] < 2
                        if same and not again:
                            row.append(last_job[bi])
                            job_age[bi] += 1
                        else:
                            job_reads[bi] = job_reads[bi] + 1 if again else 1
                            job_age[bi] = 1
                            if use[bi] == "rapid":
                                pngs = _rapid_pngs(sub, mask)
                            else:
                                pngs = []
                                white = (sub.min(2) >= thr).astype(np.uint8)  # chữ trắng tuyệt đối: sạch nét nhất khi nền không sáng
                                if 0.002 < white.mean() < 0.25:
                                    pngs.append(_prep_png(white))
                                pngs += [_prep_png(mask), _prep_png(mask, soft)]
                                pngs.append(_prep_gray(sub))  # cách đọc dự phòng cho chữ rất mỏng/viền mỏng
                            with lock:
                                jobs.append((bi, pngs))
                                job_key.append(key if bi == primary else None)
                                job_prev.append(last_job[bi] if again else None)
                                last_job[bi] = len(jobs) - 1
                            job_px[bi] = ks
                            backlog.append(last_job[bi])
                            row.append(last_job[bi])
                        last_key[bi] = key
                frames.append(row)
            window.clear()
        while len(backlog) >= BATCH:  # đủ một lô thì cho chạy OCR ngay, trong lúc vẫn đọc tiếp khung hình
            futs.append(ocr_pool.submit(run_jobs, backlog[:BATCH]))
            backlog = backlog[BATCH:]

    try:
        while True:
            if task is not None and task.cancel:
                p.kill()
                raise Cancelled()
            buf = p.stdout.read(fsize)
            if len(buf) < fsize:
                break
            window.append(np.frombuffer(buf, np.uint8).reshape(crop_h, W, 3))
            idx += 1
            if len(window) >= 24:
                flush()
                if cb:
                    cb(0.85 * min(1.0, idx / total), f"Đọc khung hình {idx}/{total} · đã nhận dạng {prog['done']}/{len(jobs)} chữ")
        flush()
        p.wait()
        if backlog:
            futs.append(ocr_pool.submit(run_jobs, backlog))
            backlog = []
        for n, f in enumerate(cf.as_completed(futs), 1):
            if task is not None and task.cancel:
                raise Cancelled()
            f.result()
            if cb:
                cb(0.85 + 0.07 * n / max(1, len(futs)), f"Nhận dạng chữ {prog['done']}/{len(jobs)}")
    finally:
        mask_pool.shutdown(wait=False, cancel_futures=True)
        ocr_pool.shutdown(wait=False, cancel_futures=True)
        if p.poll() is None:
            p.kill()
    if task is not None and task.cancel:
        raise Cancelled()
    results = [res.get(j, ("", 0.0)) for j in range(len(jobs))]
    cleaned = {}

    def read_job(j, bi):
        if j not in cleaned:
            t, c = results[j]
            t = _clean_lang(t, bands[bi]["lang"])
            cleaned[j] = (t, c) if (c >= min_conf and len(_norm(t)) >= 1) else ("", 0.0)
            if not cleaned[j][0] and job_prev[j] is not None:  # đọc lại ra rỗng -> giữ chữ của lần đọc trước
                cleaned[j] = read_job(job_prev[j], bi)
        return cleaned[j]

    def text_at(fi, bi):
        j = frames[fi][bi]
        if j is None or results[j] is None:
            return "", 0.0
        return read_job(j, bi)

    if debug is not None:
        for fi in range(len(frames)):
            debug.append((round(t0 + fi / fps, 2), frames[fi][primary], *(results[frames[fi][primary]] if frames[fi][primary] is not None and results[frames[fi][primary]] else ("", 0.0))))
    # Gom khung hình liên tiếp thành câu, dựa trên dòng chính
    segs, cur, empty_run = [], None, 0
    for fi in range(len(frames)):
        txt, conf = text_at(fi, primary)
        if not txt:
            empty_run += 1
            if cur is not None and empty_run >= 2:
                segs.append(cur)
                cur = None
            continue
        empty_run = 0
        n = _norm(txt)
        if cur is not None:
            best = collections.Counter(cur["norms"]).most_common(1)[0][0]
            if difflib.SequenceMatcher(None, best, n).ratio() >= 0.72:
                cur["end_i"] = fi
                cur["texts"].append((txt, conf))
                cur["norms"].append(n)
                cur["frames"].append(fi)
                continue
            segs.append(cur)
        cur = {"start_i": fi, "end_i": fi, "texts": [(txt, conf)], "norms": [n], "frames": [fi]}
    if cur is not None:
        segs.append(cur)

    plang = bands[primary]["lang"]
    cues = []
    drop = {"ngắn": 0, "độ tin cậy thấp": 0, "chữ không hợp lệ": 0}
    drop_ex = []  # vài chữ bị loại để người dùng biết vì sao (vd. chọn nhầm ngôn ngữ dải)
    for s in segs:
        seg_conf = sum(c for _, c in s["texts"]) / len(s["texts"])
        counter = collections.Counter(t for t, _ in s["texts"])
        top = max(counter.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        # câu chỉ thấy ở 1 khung (< 0,25s ở fps 6) trước đây luôn bị bỏ -> mất câu ngắn; nay giữ nếu đọc chắc chắn
        if (s["end_i"] - s["start_i"] + 1) / fps < 0.25 and seg_conf < max(seg_min, 70.0):
            drop["ngắn"] += 1
            continue
        if seg_conf < seg_min or not _cue_ok(top, seg_conf, plang):
            drop["độ tin cậy thấp" if seg_conf < seg_min else "chữ không hợp lệ"] += 1
            if len(drop_ex) < 3:
                drop_ex.append(f"{top[:14]} ({round(seg_conf)})")
            continue
        refs = []
        for bi in range(len(bands)):
            if bi == primary:
                continue
            c2 = collections.Counter()
            for fi in s["frames"]:
                t, _ = text_at(fi, bi)
                if t:
                    c2[t] += 1
            if c2:
                refs.append(max(c2.items(), key=lambda kv: (kv[1], len(kv[0])))[0])
        cues.append({
            "start": t0 + s["start_i"] / fps,
            "end": t0 + (s["end_i"] + 1) / fps,
            "src": top,
            "ref": " | ".join(refs),
            "vi": "",
            "conf": round(seg_conf),
            "_n": len(s["texts"]),
            "_k0": job_key[frames[s["start_i"]][primary]],
            "_k1": job_key[frames[s["end_i"]][primary]],
        })
    cues = _merge_adjacent(cues, fps)
    if refine and cues:
        if cb:
            cb(0.93, "Tinh chỉnh mốc thời gian theo từng khung hình")
        try:
            _refine_times(video, info, cues, px[primary][0], px[primary][1], W, thr, fps, t0, t1, task=task)
        except Cancelled:
            raise
        except Exception:  # noqa  (lỗi tinh chỉnh không được làm mất kết quả quét)
            traceback.print_exc()
    for i, c in enumerate(cues, 1):
        for k in ("_n", "_k0", "_k1"):
            c.pop(k, None)
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)
        c["id"] = i
    if cb:
        lost = sum(drop.values())
        why = ""
        if lost:
            why = f" · đã loại {lost} đoạn chữ (" + ", ".join(f"{v} {k}" for k, v in drop.items() if v) + ")"
            if drop_ex:
                why += ", ví dụ: " + "; ".join(drop_ex)
        cb(1.0, f"Xong: {len(cues)} câu{why}")
    return cues


def probe_frame(video, info, bands, primary, t, thr=210, min_conf=40.0, seg_min=55.0, engine="tesseract"):
    """Chẩn đoán một khung hình: với từng dải trả về ảnh cắt, ảnh chữ tách được, chữ đọc ra, độ tin cậy và lý do bị bỏ (nếu có)."""
    import base64
    use = [band_engine(b["lang"], engine) for b in bands]
    tess = util.tesseract_bin() if "tesseract" in use else None
    if "tesseract" in use and not tess:
        raise RuntimeError("Không tìm thấy Tesseract OCR.")
    W, H = info["width"], info["height"]
    cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", video, "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=util.NOWINDOW).stdout
    if len(raw) < W * H * 3:
        raise RuntimeError("Không đọc được khung hình tại thời điểm này.")
    img = np.frombuffer(raw[: W * H * 3], np.uint8).reshape(H, W, 3)
    out = []
    for bi, b in enumerate(bands):
        a, z = int(round(b["y0"] * H)), int(round(b["y1"] * H))
        pad = max(2, int(round((z - a) * 0.12)))
        a = max(0, min(H - 4, a - pad))
        z = max(a + 4, min(H, z + pad))
        sub = img[a:z]
        mask, soft = _text_mask(sub, thr)
        pres = float(mask.mean())
        item = {"name": b["name"], "lang": b["lang"], "primary": bi == primary, "presence": round(pres * 100, 2), "text": "", "conf": 0.0}

        def enc(arr, w=720):
            f = min(1.0, w / arr.shape[1])
            if f < 1:
                arr = cv2.resize(arr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".png", arr)
            return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()
        item["crop"] = enc(sub)
        item["mask"] = enc(np.where(mask > 0, 0, 255).astype(np.uint8))
        if pres < 0.002:
            item["reason"] = "no_text"
        else:
            if use[bi] == "rapid":
                text, conf = _rapid_best(_rapid_pngs(sub, mask), b["lang"])
            else:
                pngs = []
                white = (sub.min(2) >= thr).astype(np.uint8)
                if 0.002 < white.mean() < 0.25:
                    pngs.append(_prep_png(white))
                pngs += [_prep_png(mask), _prep_png(mask, soft), _prep_gray(sub)]
                text, conf = _ocr_best(pngs, b["lang"], tess, enough=101.0)
            item["engine"] = use[bi]
            item["text"], item["conf"] = _clean_lang(text, b["lang"]), round(conf, 1)
            if not item["text"]:
                item["reason"] = "unreadable"
            elif conf < min_conf:
                item["reason"] = "low_conf"
            else:
                item["reason"] = "ok"
        out.append(item)
    return out


def plausible(text, lang):
    """Chữ đọc ra có giống chữ thật không (loại các chuỗi rác như 'aaa ~ e 4.')."""
    t = (text or "").strip()
    core = re.sub(r"\s+", "", t)
    if len(_norm(t)) < 2:
        return False
    first = lang.split("+")[0]
    chars = re.sub(r"[^\w]", "", core)
    if first in CJK_NOSPACE or first == "kor":
        cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", core))
        return cjk >= max(1, int(0.6 * len(chars)))
    letters = sum(ch.isalpha() for ch in core)
    body = re.sub(r"[.,!?'\"\u2019\u201c\u201d:;\u2026\-]", "", core)  # dấu câu thường gặp không phải rác ('Go!' = 2/3 chữ cái)
    if letters < 2 or letters / max(1, len(body)) < 0.7:
        return False
    words = re.findall(r"[^\W\d_]+", t)
    good = [w for w in words if (len(w) >= 2 and re.search(r"[aeiouyàáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữự]", w.lower())) or w.lower() in ("i", "a")]
    return len(good) >= max(1, int(0.6 * len(words)))


def deep_scan(video, info, bands, primary, t0, t1, thr=210, task=None, cb=None, engine="tesseract"):
    """OCR thật kỹ một đoạn ngắn để xác thực: nhiều mức độ nhạy, tốc độ khung cao, thử lần lượt từng dòng làm dòng chính.
    Chỉ giữ câu có chữ hợp lệ (plausible) và độ tin cậy đủ. Trả về danh sách câu (đã gỡ trùng)."""
    dur = float(info["duration"])
    t0, t1 = max(0.0, t0), min(dur, t1)
    if t1 - t0 < 0.3:
        return []
    plans = [(thr, 12), (thr - 25, 12), (thr - 45, 15), (thr + 15, 12)]
    order = [primary] + [i for i in range(len(bands)) if i != primary]
    cands, total, step = [], len(plans) * len(order), 0
    for bi in order:
        lang = bands[bi]["lang"]
        strong = False
        for th, fps in plans:
            if task is not None and task.cancel:
                raise Cancelled()
            cues = run_ocr(video, info, bands, bi, fps=fps, t0=t0, t1=t1, thr=int(max(120, min(254, th))),
                           min_conf=25.0, seg_min=35.0, ref=False, task=task, engine=engine, refine=False)
            for c in cues:
                if c.get("conf", 0) >= 45 and plausible(c["src"], lang):
                    c["_band"] = bi
                    cands.append(c)
                    strong = strong or c["conf"] >= 70
            step += 1
            if cb:
                cb(step / total)
            if strong:
                break  # đã thấy chữ chắc chắn ở dòng này, khỏi thử các mức còn lại
        if strong:
            break  # ưu tiên dòng chính (rồi mới tới dòng khác)
    cands.sort(key=lambda c: (c["_band"] != primary, -c["conf"]))
    chosen = []
    for c in cands:
        d = max(0.05, c["end"] - c["start"])
        if not any(min(c["end"], o["end"]) - max(c["start"], o["start"]) > 0.4 * min(d, max(0.05, o["end"] - o["start"])) for o in chosen):
            chosen.append(c)
    chosen.sort(key=lambda c: c["start"])
    for c in chosen:
        bi = c.pop("_band")
        c["deep"] = True
        if bi != primary:
            c["from_band"] = bands[bi]["name"]
        c["ref"] = ""
    return chosen
