"""Nhận dạng giọng nói bằng Whisper (faster-whisper) để đối chiếu với kết quả OCR: chỗ có người nói mà không có câu nào là nghi bị sót."""
import importlib.util
import os
import subprocess
import tempfile

from . import util
from .util import Cancelled

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
_CACHE = {}


def available():
    return importlib.util.find_spec("faster_whisper") is not None


def device():
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa
        return "cpu"


def default_model():
    return "medium" if device() == "cuda" else "small"


def _load(name, dev):
    """Nạp mô hình (tải về lần đầu), giữ lại để lần sau khỏi nạp lại."""
    key = (name, dev)
    if key not in _CACHE:
        from faster_whisper import WhisperModel
        _CACHE.clear()
        _CACHE[key] = WhisperModel(name, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    return _CACHE[key]


LOADER = _load  # cho phép thay thế khi kiểm thử


def _extract_audio(video, wav):
    cmd = [util.need_ffmpeg(), "-y", "-v", "error", "-nostdin", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", wav]
    r = subprocess.run(cmd, capture_output=True, creationflags=util.NOWINDOW)
    if r.returncode != 0 or not os.path.isfile(wav) or os.path.getsize(wav) < 1000:
        raise RuntimeError("Không lấy được âm thanh từ video (video có thể không có tiếng).")


def transcribe(video, duration, model="small", lang=None, task=None, cb=None):
    """Trả về (danh sách {start,end,text}, ngôn ngữ nhận ra)."""
    if not available() and LOADER is _load:
        raise RuntimeError("Chưa cài faster-whisper. Chạy: pip install -r requirements-asr.txt")
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        if cb:
            cb(0.0, "Đang lấy âm thanh")
        _extract_audio(video, wav)
        if cb:
            cb(0.02, f"Đang nạp mô hình {model} (lần đầu phải tải về, có thể mất vài phút)")
        m = LOADER(model, device())
        segs, info = m.transcribe(wav, language=(lang or None), vad_filter=True,
                                  vad_parameters={"min_silence_duration_ms": 300}, beam_size=5,
                                  condition_on_previous_text=False)
        out = []
        for s in segs:
            if task is not None and task.cancel:
                raise Cancelled()
            t = (s.text or "").strip()
            if t:
                out.append({"start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": t})
            if cb:
                cb(min(0.99, 0.05 + 0.94 * float(s.end) / max(1.0, duration)), f"Đang nghe: {len(out)} đoạn")
        return out, getattr(info, "language", lang or "")
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
