"""Dò vùng sub cứng và quét OCR ra danh sách câu (có thời gian)."""
import collections
import concurrent.futures as cf
import difflib
import os
import re
import subprocess
import tempfile
import threading

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
        r = subprocess.run([tess, lst, "stdout", "-l", lang, "--psm", "7"] + _OEM + ["tsv"], capture_output=True, env=env,
                           creationflags=util.NOWINDOW)
        if r.returncode != 0 and _OEM:
            _OEM = []  # bản Tesseract/dữ liệu ngôn ngữ không hỗ trợ --oem 1: chạy lại như thường
            r = subprocess.run([tess, lst, "stdout", "-l", lang, "--psm", "7", "tsv"], capture_output=True, env=env,
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


def run_ocr(video, info, bands, primary, fps=6.0, t0=0.0, t1=None, thr=210, min_conf=40.0, seg_min=55.0, task=None, cb=None, debug=None, ref=True):
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

    nb = len(bands)
    ncpu = max(1, min(8, os.cpu_count() or 2))
    jobs = []          # (band_index, [png các phiên bản])
    frames = []        # mỗi khung: [chỉ số job hoặc None cho từng dòng]
    last_key = [None] * nb
    last_job = [None] * nb
    res = {}           # job -> (chữ, tin cậy)
    lock = threading.Lock()
    prog = {"done": 0}
    BATCH = 16

    def analyse(img):
        """Tính mặt nạ chữ cho mọi dòng của một khung (chạy song song nhiều khung)."""
        out = []
        for bi, (a, z) in enumerate(px):
            if not ref and bi != primary:  # bỏ dòng đối chiếu cho nhanh
                out.append(None)
                continue
            sub = img[a - ymin: z - ymin]
            mask, soft = _text_mask(sub, thr)
            if mask.sum() < sub.shape[0] * sub.shape[1] * 0.002:
                out.append(False)
                continue
            key = cv2.resize(mask, (max(1, W // 3), max(1, sub.shape[0] // 3)), interpolation=cv2.INTER_AREA).astype(np.float32)
            out.append((mask, soft, key, sub))
        return out

    def run_jobs(idx):
        """Đọc một lô job: vòng 1 cho tất cả, chỉ job chưa đủ chắc mới thử phiên bản ảnh kế tiếp (gom lô theo ngôn ngữ)."""
        best = {j: ("", 0.0) for j in idx}
        pending = list(idx)
        for rnd in range(3):
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
                        if (last_key[bi] is not None and last_key[bi].shape == key.shape
                                and float(np.abs(key - last_key[bi]).sum()) / max(1.0, float(max(key.sum(), last_key[bi].sum()))) < 0.35):
                            row.append(last_job[bi])
                        else:
                            pngs = []
                            white = (sub.min(2) >= thr).astype(np.uint8)  # chữ trắng tuyệt đối: sạch nét nhất khi nền không sáng
                            if 0.002 < white.mean() < 0.25:
                                pngs.append(_prep_png(white))
                            pngs += [_prep_png(mask), _prep_png(mask, soft)]
                            jobs.append((bi, pngs))
                            last_job[bi] = len(jobs) - 1
                            backlog.append(last_job[bi])
                            row.append(last_job[bi])
                        last_key[bi] = key
                frames.append(row)
            window.clear()
        while len(backlog) >= BATCH:  # đủ một lô thì cho chạy Tesseract ngay, trong lúc vẫn đọc tiếp khung hình
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
                    cb(0.9 * min(1.0, idx / total), f"Đọc khung hình {idx}/{total} · đã nhận dạng {prog['done']}/{len(jobs)} chữ")
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
                cb(0.9 + 0.1 * n / max(1, len(futs)), f"Nhận dạng chữ {prog['done']}/{len(jobs)}")
    finally:
        mask_pool.shutdown(wait=False, cancel_futures=True)
        ocr_pool.shutdown(wait=False, cancel_futures=True)
        if p.poll() is None:
            p.kill()
    if task is not None and task.cancel:
        raise Cancelled()
    results = [res.get(j, ("", 0.0)) for j in range(len(jobs))]

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


def probe_frame(video, info, bands, primary, t, thr=210, min_conf=40.0, seg_min=55.0):
    """Chẩn đoán một khung hình: với từng dải trả về ảnh cắt, ảnh chữ tách được, chữ đọc ra, độ tin cậy và lý do bị bỏ (nếu có)."""
    import base64
    tess = util.tesseract_bin()
    if not tess:
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
            pngs = []
            white = (sub.min(2) >= thr).astype(np.uint8)
            if 0.002 < white.mean() < 0.25:
                pngs.append(_prep_png(white))
            pngs += [_prep_png(mask), _prep_png(mask, soft)]
            text, conf = _ocr_best(pngs, b["lang"], tess, enough=101.0)
            item["text"], item["conf"] = _clean(text), round(conf, 1)
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
    if letters < 2 or letters / max(1, len(core)) < 0.7:
        return False
    words = re.findall(r"[^\W\d_]+", t)
    good = [w for w in words if (len(w) >= 2 and re.search(r"[aeiouyàáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữự]", w.lower())) or w.lower() in ("i", "a")]
    return len(good) >= max(1, int(0.6 * len(words)))


def deep_scan(video, info, bands, primary, t0, t1, thr=210, task=None, cb=None):
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
                           min_conf=25.0, seg_min=35.0, ref=False, task=task)
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
