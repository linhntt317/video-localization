"""Lồng tiếng: tạo giọng đọc cho từng câu (mặc định Edge TTS), tính độ khớp với khung thời gian, ghép thành một track âm thanh."""
import asyncio
import concurrent.futures as cf
import hashlib
import importlib.util
import os
import re
import subprocess
import wave

import numpy as np

from . import util
from .util import Cancelled

SR = 24000
VOICES_FALLBACK = [
    {"id": "vi-VN-HoaiMyNeural", "name": "Hoài My (nữ)", "lang": "vi-VN"},
    {"id": "vi-VN-NamMinhNeural", "name": "Nam Minh (nam)", "lang": "vi-VN"},
]
DEFAULT_VOICE = "vi-VN-HoaiMyNeural"
MAX_SPEED = 1.35  # tăng tốc tối đa khi giọng dài hơn khung thời gian
MIN_SPEED = 0.8


class EdgeEngine:
    """Edge TTS (dịch vụ đọc của Microsoft Edge, không chính thức). Cần mạng."""
    name = "edge"

    def available(self):
        return importlib.util.find_spec("edge_tts") is not None

    def voices(self):
        import edge_tts
        vs = asyncio.run(edge_tts.list_voices())
        out = [{"id": v["ShortName"], "name": f"{v['ShortName'].split('-', 2)[-1].replace('Neural', '')} ({'nữ' if v.get('Gender') == 'Female' else 'nam'})",
                "lang": v["Locale"]} for v in vs if v["Locale"].startswith("vi-")]
        return out or VOICES_FALLBACK

    def synth(self, text, voice, out_path):
        import edge_tts
        last = None
        for attempt in range(3):
            try:
                asyncio.run(edge_tts.Communicate(text, voice).save(out_path))
                if os.path.isfile(out_path) and os.path.getsize(out_path) > 200:
                    return
                last = RuntimeError("Dịch vụ giọng đọc trả về file rỗng")
            except Exception as e:  # noqa
                last = e
            try:
                os.remove(out_path)
            except OSError:
                pass
        raise RuntimeError("Không tạo được giọng đọc (Edge TTS cần kết nối mạng và đôi khi bị chặn): " + str(last))


ENGINE = None  # thay được khi kiểm thử hoặc khi đổi nguồn giọng


def engine():
    return ENGINE or EdgeEngine()


def available():
    return engine().available()


def voices():
    try:
        return engine().voices()
    except Exception:  # noqa
        return VOICES_FALLBACK


def speech_text(vi):
    """Chữ đưa cho giọng đọc: bỏ xuống dòng, ký tự trang trí."""
    t = re.sub(r"\s*\n\s*", " ", vi or "").strip()
    t = re.sub(r"[♪♫♬*_#~]+", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def key_for(voice, text):
    return hashlib.sha1(f"{engine().name}|{voice}|{speech_text(text)}".encode("utf-8")).hexdigest()[:16]


def audio_path(project_dir, key):
    return os.path.join(project_dir, "tts", key + ".mp3")


def audio_duration(path):
    fp = util.ffprobe_bin()
    r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=util.NOWINDOW)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def synth_cue(project_dir, voice, text, force=False):
    """Tạo (hoặc dùng lại) file giọng cho một câu. Trả về dict {key, dur, voice, text}."""
    t = speech_text(text)
    if not t:
        return None
    key = key_for(voice, t)
    path = audio_path(project_dir, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if force or not os.path.isfile(path):
        engine().synth(t, voice, path)
    return {"key": key, "dur": round(audio_duration(path), 3), "voice": voice, "text": text}


def slot_of(cues, i):
    """Khung thời gian dành cho giọng của câu i: độ dài câu + một chút khoảng trống trước câu kế tiếp."""
    c = cues[i]
    gap = (cues[i + 1]["start"] - c["end"]) if i + 1 < len(cues) else 0.5
    return max(0.2, (c["end"] - c["start"]) + min(0.25, max(0.0, gap)))


def fit(cue, slot, max_speed=MAX_SPEED):
    """(tốc độ áp dụng, trạng thái) với trạng thái: ok (khớp), fast (cần tăng tốc), long (vẫn quá dài), none (chưa có giọng).
    Giống hệt logic hiển thị ở giao diện."""
    d = cue.get("dub")
    if not d or not d.get("dur"):
        return 1.0, "none"
    user = d.get("user")
    speed = float(user) if user else (1.0 if d["dur"] <= slot * 1.05 else min(max_speed, d["dur"] / slot))
    speed = max(MIN_SPEED, min(2.0, speed))
    eff = d["dur"] / speed
    if eff > slot * 1.05:
        return round(speed, 2), "long"
    return round(speed, 2), ("ok" if speed <= 1.05 else "fast")


def build_track(cues, project_dir, duration, volume=1.0, max_speed=MAX_SPEED, task=None, cb=None):
    """Ghép giọng của mọi câu vào MỘT file WAV (mono 24 kHz) dài bằng video. Trả về (đường dẫn, số câu đã ghép)."""
    n = int(duration * SR) + SR
    buf = np.zeros(n, np.int16)
    placed = 0
    ffm = util.need_ffmpeg()
    todo = [i for i, c in enumerate(cues) if c.get("dub") and os.path.isfile(audio_path(project_dir, c["dub"]["key"]))]
    for k, i in enumerate(todo):
        if task is not None and task.cancel:
            raise Cancelled()
        c = cues[i]
        speed, _ = fit(c, slot_of(cues, i), max_speed)
        af = []
        s = speed
        while s > 2.0:
            af.append("atempo=2.0"); s /= 2.0
        while s < 0.5:
            af.append("atempo=0.5"); s /= 0.5
        af.append(f"atempo={s:.4f}")
        af.append(f"volume={volume:.3f}")
        r = subprocess.run([ffm, "-v", "error", "-nostdin", "-i", audio_path(project_dir, c["dub"]["key"]), "-af", ",".join(af),
                            "-ar", str(SR), "-ac", "1", "-f", "s16le", "-"], capture_output=True, creationflags=util.NOWINDOW)
        seg = np.frombuffer(r.stdout, np.int16).astype(np.int32)
        if not len(seg):
            continue
        a = int(c["start"] * SR)
        b = min(n, a + len(seg))
        if a < n and b > a:
            buf[a:b] = np.clip(buf[a:b].astype(np.int32) + seg[: b - a], -32768, 32767).astype(np.int16)
            placed += 1
        if cb:
            cb((k + 1) / max(1, len(todo)))
    out = os.path.join(project_dir, "tts", "track.wav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(buf.tobytes())
    return out, placed


def synth_many(project_dir, cues, idxs, voice, force=False, workers=4, task=None, cb=None):
    """Tạo giọng cho nhiều câu song song. Cập nhật cue['dub'] tại chỗ. Trả về số câu lỗi."""
    jobs = [i for i in idxs if speech_text(cues[i].get("vi"))]
    failed, done = [], 0

    def one(i):
        return i, synth_cue(project_dir, voice, cues[i]["vi"], force)

    with cf.ThreadPoolExecutor(max(1, workers)) as ex:
        futs = [ex.submit(one, i) for i in jobs]
        for f in cf.as_completed(futs):
            if task is not None and task.cancel:
                ex.shutdown(cancel_futures=True)
                raise Cancelled()
            try:
                i, d = f.result()
            except Exception as e:  # noqa
                failed.append(str(e))
                d = None
            else:
                if d:
                    old = cues[i].get("dub") or {}
                    d["user"] = old.get("user") if old.get("text") == d["text"] else None
                    cues[i]["dub"] = d
            done += 1
            if cb:
                cb(done / max(1, len(jobs)), f"Đã tạo giọng {done}/{len(jobs)} câu")
    if failed and len(failed) == len(jobs):
        raise RuntimeError(failed[0])
    return len(failed)
