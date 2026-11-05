"""Công cụ riêng: ghép video, cắt video, tách MP3 (ffmpeg)."""
import os
import shutil
import tempfile

from . import util
from .util import Cancelled  # noqa: F401

COPY_OK_EXT = (".mp4", ".mov", ".m4v")


def _esc(path):
    return path.replace("'", "'\\''")


def _out_ext(paths):
    """Gộp/cắt không mã hoá lại: mp4 nếu mọi file là mp4/mov/m4v, ngược lại mkv (chứa được mọi codec)."""
    return ".mp4" if all(os.path.splitext(p)[1].lower() in COPY_OK_EXT for p in paths) else ".mkv"


def concat_copy(paths, out, duration, task=None, cb=None):
    """Nối các file cùng định dạng mà không mã hoá lại (rất nhanh, giữ nguyên chất lượng)."""
    with tempfile.TemporaryDirectory() as d:
        lst = os.path.join(d, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in paths:
                f.write(f"file '{_esc(os.path.abspath(p))}'\n")
        args = ["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy"]
        if out.lower().endswith(".mp4"):
            args += ["-movflags", "+faststart"]
        util.run_ffmpeg(args + [out], duration, cb, task=task)


def _sig(i):
    return (i["codec"], i["width"], i["height"], i["pix_fmt"], round(i["fps"], 2), i["has_audio"],
            i.get("acodec"), i.get("a_rate"), i.get("a_channels"))


def merge(paths, out_base, mode="auto", height=0, task=None, cb=None):
    """Ghép nhiều video theo thứ tự. mode: auto (tự chọn), copy (không mã hoá lại), reencode (đồng bộ lại cho mọi video).
    height: 0 = theo video đầu, hoặc 720/1080... khi mã hoá lại. Trả về đường dẫn file kết quả."""
    if len(paths) < 2:
        raise RuntimeError("Cần ít nhất 2 video để ghép.")
    infos = [util.probe(p) for p in paths]
    total = sum(i["duration"] for i in infos) or 1.0
    same = len({_sig(i) for i in infos}) == 1
    use_copy = (mode == "copy") or (mode == "auto" and same and height in (0, None))
    if mode == "copy" and not same:
        raise RuntimeError("Các video khác định dạng (codec, độ phân giải, fps hoặc âm thanh) nên không thể ghép nhanh. "
                           "Hãy chọn 'Mã hoá lại' hoặc 'Tự động'.")
    if use_copy:
        out = out_base + _out_ext(paths)
        concat_copy(paths, out, total, task, cb)
        return out
    out = out_base + ".mp4"
    first = infos[0]
    H = int(height) if height else first["height"]
    W = int(round(first["width"] * H / max(1, first["height"]) / 2.0)) * 2
    H -= H % 2
    fps = min(60.0, first["fps"] or 30.0)
    args, parts, extra = [], [], []
    for p in paths:
        args += ["-i", p]
    n = len(paths)
    ai = n
    for i, inf in enumerate(infos):
        parts.append(f"[{i}:v:0]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                     f"setsar=1,fps={fps:.3f},format=yuv420p[v{i}]")
        if inf["has_audio"]:
            parts.append(f"[{i}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:  # video không có tiếng: chèn đoạn im lặng cho đúng độ dài
            extra += ["-f", "lavfi", "-t", f"{inf['duration']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            parts.append(f"[{ai}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
            ai += 1
    parts.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    args += extra
    args += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out]
    util.run_ffmpeg(args, total, cb, task=task)
    return out


def cut_one(src, start, end, out, exact, task=None, cb=None):
    dur = max(0.05, end - start)
    args = ["-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}"]
    if exact:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    if out.lower().endswith(".mp4"):
        args += ["-movflags", "+faststart"]
    util.run_ffmpeg(args + [out], dur, cb, task=task)


def cut(src, segments, out_base, exact=False, join=False, task=None, cb=None):
    """Cắt một hoặc nhiều đoạn [(bắt đầu, kết thúc)] giây. exact=False: không mã hoá lại (nhanh, điểm cắt có thể lệch về keyframe trước đó).
    join=True: nối các đoạn thành một file. Trả về danh sách file kết quả."""
    info = util.probe(src)
    segs = []
    for a, b in segments:
        a, b = max(0.0, float(a)), min(float(b), info["duration"])
        if b - a < 0.1:
            raise RuntimeError(f"Đoạn cắt không hợp lệ ({a:.1f}s đến {b:.1f}s): điểm cuối phải sau điểm đầu.")
        segs.append((a, b))
    if not segs:
        raise RuntimeError("Chưa có đoạn nào để cắt.")
    ext = ".mp4" if exact else _out_ext([src])
    total = sum(b - a for a, b in segs)
    done = [0.0]

    def sub(i, a, b):
        return lambda p: cb and cb((done[0] + p * (b - a)) / total)

    if join and len(segs) > 1:
        with tempfile.TemporaryDirectory() as d:
            parts = []
            for i, (a, b) in enumerate(segs):
                f = os.path.join(d, f"p{i}{ext}")
                cut_one(src, a, b, f, exact, task, sub(i, a, b))
                done[0] += b - a
                parts.append(f)
            out = out_base + ext
            concat_copy(parts, out, total, task)
        return [out]
    outs = []
    for i, (a, b) in enumerate(segs, 1):
        out = out_base + (f"_{i}" if len(segs) > 1 else "") + ext
        cut_one(src, a, b, out, exact, task, sub(i, a, b))
        done[0] += b - a
        outs.append(out)
    return outs


def extract_mp3(src, out, bitrate=192, start=None, end=None, task=None, cb=None):
    info = util.probe(src)
    if not info["has_audio"]:
        raise RuntimeError("Video này không có âm thanh để tách.")
    a = max(0.0, float(start or 0.0))
    b = min(float(end), info["duration"]) if end else info["duration"]
    if b - a < 0.1:
        raise RuntimeError("Khoảng thời gian cần tách quá ngắn.")
    args = []
    if a > 0:
        args += ["-ss", f"{a:.3f}"]
    args += ["-i", src]
    if end or a > 0:
        args += ["-t", f"{b - a:.3f}"]
    args += ["-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", f"{int(bitrate)}k", "-id3v2_version", "3", out]
    util.run_ffmpeg(args, b - a, cb, task=task)
    return out
