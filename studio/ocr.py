"""Dò vùng sub cứng và quét OCR ra danh sách câu (có thời gian)."""
import collections
import concurrent.futures as cf
import difflib
import os
import re
import subprocess

import cv2
import numpy as np

from . import util
from .util import Cancelled

CJK_NOSPACE = {"chi_sim", "chi_tra", "jpn"}


def _raw_frames(video, t0, t1, fps, y0, h, w):
    cmd = [util.need_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{t0:.3f}", "-i", video, "-t", f"{t1 - t0:.3f}",
           "-vf", f"fps={fps},crop={w}:{h}:0:{y0}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=util.NOWINDOW)
    return p


def detect_bands(video, info, samples=20):
    """Tự dò các dòng sub cứng ở nửa dưới khung hình. Trả về danh sách dòng (y0,y1 theo tỉ lệ chiều cao)."""
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
        pieces = [(a, b)]
        if height_frac > 0.085:  # có thể là 2 dòng dính nhau: cắt ở chỗ ít chữ nhất
            lo, hi = a + int((b - a) * 0.3), a + int((b - a) * 0.7)
            cut = lo + int(np.argmin(pix[lo:hi + 1]))
            pieces = [(a, cut), (cut, b)]
        for x, y in pieces:
            lines.append({
                "y0": round(max(0.0, (y_start + x / sc) / H - 0.006), 4),
                "y1": round(min(1.0, (y_start + y / sc) / H + 0.006), 4),
                "score": round(float(freq[x:y + 1].mean()), 3),
            })
    lines.sort(key=lambda l: -l["score"])
    lines = lines[:3]
    lines.sort(key=lambda l: l["y0"])
    return lines


def _ocr_best(pngs, lang, tess):
    best = ("", 0.0)
    for png in pngs:
        r = _ocr_png(png, lang, tess)
        if r[0] and r[1] > best[1]:
            best = r
    return best


def _ocr_png(png, lang, tess):
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    out = subprocess.run([tess, "stdin", "stdout", "-l", lang, "--psm", "7", "tsv"], input=png,
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
    thr (mặc định 210) càng thấp càng nhạy."""
    g = cv2.medianBlur(cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY), 3)  # giảm nhiễu nén
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
    return re.sub(r"^[^\w\u3040-\u9fff\uac00-\ud7af'\"“¿¡]+", "", out).strip()


def _norm(s):
    return re.sub(r"[\W_]+", "", s.lower())


def run_ocr(video, info, bands, primary, fps=6.0, t0=0.0, t1=None, thr=210, min_conf=40.0, seg_min=55.0, task=None, cb=None, debug=None):
    """bands: [{name,y0,y1,lang}]  (y theo tỉ lệ 0..1). primary: chỉ số dòng dùng để chia câu & dịch."""
    tess = util.tesseract_bin()
    if not tess:
        raise RuntimeError("Không tìm thấy Tesseract OCR. Hãy cài Tesseract (xem README).")
    have = set(util.tesseract_langs())
    for b in bands:
        for l in b["lang"].split("+"):
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

    jobs = []          # (band_index, png)
    frames = []        # per frame: [job index or None per band]
    last_key = [None] * len(bands)
    last_job = [None] * len(bands)
    idx = 0
    while True:
        if task is not None and task.cancel:
            p.kill()
            raise Cancelled()
        buf = p.stdout.read(fsize)
        if len(buf) < fsize:
            break
        img = np.frombuffer(buf, np.uint8).reshape(crop_h, W, 3)
        row = []
        for bi, (a, z) in enumerate(px):
            sub = img[a - ymin: z - ymin]
            mask, soft = _text_mask(sub, thr)
            if mask.sum() < sub.shape[0] * sub.shape[1] * 0.002:
                row.append(None)
                last_key[bi] = None
                continue
            key = cv2.resize(mask, (max(1, W // 3), max(1, sub.shape[0] // 3)), interpolation=cv2.INTER_AREA).astype(np.float32)
            if (last_key[bi] is not None and last_key[bi].shape == key.shape
                    and float(np.abs(key - last_key[bi]).sum()) / max(1.0, float(max(key.sum(), last_key[bi].sum()))) < 0.35):
                row.append(last_job[bi])
            else:
                pngs = [_prep_png(mask), _prep_png(mask, soft)]
                white = (sub.min(2) >= thr).astype(np.uint8)  # cách cũ: chữ trắng tuyệt đối, sạch nét khi nền không sáng
                if 0.002 < white.mean() < 0.25:
                    pngs.append(_prep_png(white))
                jobs.append((bi, pngs))
                last_job[bi] = len(jobs) - 1
                row.append(last_job[bi])
            last_key[bi] = key
        frames.append(row)
        idx += 1
        if cb and idx % 5 == 0:
            cb(0.45 * min(1.0, idx / total), f"Đọc khung hình {idx}/{total}")
    p.wait()

    results = [None] * len(jobs)
    done = 0
    workers = max(1, min(8, (os.cpu_count() or 2)))
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_ocr_best, pngs, bands[bi]["lang"], tess): j for j, (bi, pngs) in enumerate(jobs)}
        for f in cf.as_completed(futs):
            if task is not None and task.cancel:
                ex.shutdown(cancel_futures=True)
                raise Cancelled()
            results[futs[f]] = f.result()
            done += 1
            if cb and done % 3 == 0:
                cb(0.45 + 0.55 * done / max(1, len(jobs)), f"Nhận dạng chữ {done}/{len(jobs)}")

    def text_at(fi, bi):
        j = frames[fi][bi]
        if j is None or results[j] is None:
            return "", 0.0
        t, c = results[j]
        t = _clean(t)
        return (t, c) if (c >= min_conf and len(_norm(t)) >= 1) else ("", 0.0)

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

    cues = []
    for s in segs:
        dur = (s["end_i"] - s["start_i"] + 1) / fps
        if dur < 0.25:
            continue
        seg_conf = sum(c for _, c in s["texts"]) / len(s["texts"])
        if seg_conf < seg_min or len(_norm(max((t for t, _ in s["texts"]), key=len))) < 2:
            continue
        counter = collections.Counter(t for t, _ in s["texts"])
        top = max(counter.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
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
            "start": round(t0 + s["start_i"] / fps, 2),
            "end": round(t0 + (s["end_i"] + 1) / fps, 2),
            "src": top,
            "ref": " | ".join(refs),
            "vi": "",
            "conf": round(seg_conf),
        })
    for i, c in enumerate(cues, 1):
        c["id"] = i
    if cb:
        cb(1.0, f"Xong: {len(cues)} câu")
    return cues
