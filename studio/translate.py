"""Dịch phụ đề sang tiếng Việt bằng Claude API (chỉ dùng thư viện chuẩn) và nhập SRT dịch sẵn."""
import json
import re
import time
import urllib.error
import urllib.request

from . import srt
from .util import Cancelled

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

DEFAULT_RULES = (
    "Dịch tự nhiên, ngắn gọn, đọc kịp trên màn hình.\n"
    "Hạn chế dùng ký tự gạch ngang \"-\" trong phụ đề.\n"
    "Tên người Hàn viết cách từng âm tiết, ví dụ Won Jun Seo, Baek Yun Hee (không viết liền hay có gạch nối).\n"
    "Xưng hô theo ngữ cảnh phim, thống nhất xuyên suốt."
)

SYSTEM = """Bạn là dịch giả phụ đề phim chuyên nghiệp, dịch sang tiếng Việt.
Bạn nhận một danh sách câu sub được quét bằng OCR nên có thể sai chính tả hoặc thiếu chữ: hãy sửa theo ngữ cảnh.
Mỗi mục có "id", "text" (ngôn ngữ gốc) và "ref" (cùng câu ở ngôn ngữ khác, chỉ để đối chiếu, có thể rỗng).
Yêu cầu:
- Mỗi câu dịch tối đa khoảng 42 ký tự mỗi dòng, có thể xuống dòng bằng \\n nếu dài.
- Không giải thích, không thêm ghi chú.
- Chỉ trả về MỘT JSON object duy nhất dạng {"<id>": "<bản dịch>"} cho tất cả id được đưa.
Quy tắc của người dùng:
{rules}
Bảng tên và thuật ngữ (nguồn = dịch):
{glossary}
"""


def _call(api_key, model, system, user, max_tokens=4096, retries=4):
    body = json.dumps({
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    delay = 3
    for attempt in range(retries + 1):
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.loads(r.read().decode("utf-8"))
            return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            if e.code == 401:
                raise RuntimeError("API key không hợp lệ (401). Kiểm tra lại key Claude.")
            raise RuntimeError(f"Claude API lỗi {e.code}: {msg[:300]}")
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError("Không kết nối được Claude API: " + str(e.reason))
    raise RuntimeError("Claude API không phản hồi.")


def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Phản hồi không có JSON")
    return json.loads(m.group(0))


def translate_cues(cues, api_key, model=None, rules="", glossary="", only_empty=True, batch=30, task=None, cb=None):
    model = model or DEFAULT_MODEL
    system = SYSTEM.replace("{rules}", rules.strip() or DEFAULT_RULES).replace("{glossary}", glossary.strip() or "(không có)")
    todo = [c for c in cues if (c.get("src") or "").strip() and (not only_empty or not (c.get("vi") or "").strip())]
    missing = 0
    for i in range(0, len(todo), batch):
        if task is not None and task.cancel:
            raise Cancelled()
        chunk = todo[i:i + batch]
        ctx = [c for c in cues if (c.get("vi") or "").strip() and c["id"] < chunk[0]["id"]][-5:]
        user = ""
        if ctx:
            user += "Các câu liền trước đã dịch (để giữ mạch và xưng hô, KHÔNG dịch lại):\n"
            user += json.dumps([{"text": c["src"], "vi": c["vi"]} for c in ctx], ensure_ascii=False) + "\n\n"
        user += "Dịch các mục sau:\n" + json.dumps(
            [{"id": str(c["id"]), "text": c["src"], "ref": c.get("ref", "")} for c in chunk], ensure_ascii=False)
        out = None
        for attempt in range(2):
            try:
                out = _parse_json(_call(api_key, model, system, user))
                break
            except ValueError:
                continue
        if out is None:
            raise RuntimeError("Claude trả về dữ liệu không đọc được, thử lại sau.")
        for c in chunk:
            v = out.get(str(c["id"]))
            if isinstance(v, str) and v.strip():
                c["vi"] = v.strip()
            else:
                missing += 1
        if cb:
            cb(min(1.0, (i + len(chunk)) / max(1, len(todo))), f"Đã dịch {min(i + batch, len(todo))}/{len(todo)} câu")
    return missing


def apply_srt(cues, text):
    """Gán bản dịch từ SRT nhập vào: ưu tiên khớp theo thời gian, nếu số câu bằng nhau thì khớp theo thứ tự."""
    items = srt.parse(text)
    if not items:
        raise ValueError("SRT trống hoặc sai định dạng.")
    if len(items) == len(cues):
        for c, it in zip(cues, items):
            c["vi"] = it["text"]
        return len(items), "theo thứ tự"
    used = 0
    for c in cues:
        best, bo = None, 0.0
        for it in items:
            ov = min(c["end"], it["end"]) - max(c["start"], it["start"])
            if ov > bo:
                best, bo = it, ov
        if best is not None and bo > 0.05:
            c["vi"] = best["text"]
            used += 1
    return used, "theo thời gian"


SHORTEN_SYSTEM = """Bạn là biên tập viên phụ đề lồng tiếng tiếng Việt.
Bạn nhận các câu thoại tiếng Việt đã dịch nhưng giọng đọc không kịp trong khung thời gian. Hãy RÚT GỌN từng câu cho đọc vừa
khung thời gian, giữ đúng ý chính, xưng hô và tên riêng, văn nói tự nhiên.
Mỗi mục có "id", "text" (câu hiện tại), "source" (câu gốc để đối chiếu, có thể rỗng) và "max_chars" (số ký tự tối đa gợi ý, tính cả dấu cách).
Yêu cầu: câu rút gọn KHÔNG dài hơn max_chars quá 10%, không giải thích, không thêm ghi chú.
Chỉ trả về MỘT JSON object duy nhất dạng {"<id>": "<câu rút gọn>"} cho tất cả id được đưa."""


def shorten_cues(items, api_key, model=None, batch=30, task=None, cb=None):
    """items: [{id, text, source, max_chars}]. Trả về {id: câu rút gọn}."""
    out_all = {}
    for i in range(0, len(items), batch):
        if task is not None and task.cancel:
            raise Cancelled()
        chunk = items[i:i + batch]
        out = None
        for attempt in range(2):
            try:
                out = _parse_json(_call(api_key, model or DEFAULT_MODEL, SHORTEN_SYSTEM, json.dumps(chunk, ensure_ascii=False)))
                break
            except ValueError:
                continue
        if out is None:
            raise RuntimeError("Claude trả về dữ liệu không đọc được, thử lại sau.")
        for c in chunk:
            v = out.get(str(c["id"]))
            if isinstance(v, str) and v.strip():
                out_all[str(c["id"])] = v.strip()
        if cb:
            cb(min(1.0, (i + len(chunk)) / max(1, len(items))), f"Đã rút gọn {min(i + batch, len(items))}/{len(items)} câu")
    return out_all
