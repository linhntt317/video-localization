"""Đọc / ghi file SRT."""
import re


def ts(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_ts(s):
    s = s.strip().replace(".", ",")
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", s)
    if not m:
        raise ValueError("Sai định dạng thời gian: " + s)
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000


def dump(cues, key):
    out = []
    n = 0
    for c in cues:
        txt = (c.get(key) or "").strip()
        if not txt:
            continue
        n += 1
        out.append(f"{n}\n{ts(c['start'])} --> {ts(c['end'])}\n{txt}\n")
    return "\n".join(out)


def parse(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    blocks = re.split(r"\n\s*\n", text.strip())
    res = []
    for b in blocks:
        lines = [l for l in b.split("\n")]
        if len(lines) < 2:
            continue
        i = 0
        if "-->" not in lines[0]:
            i = 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, c = lines[i].split("-->")[:2]
        try:
            st, en = parse_ts(a), parse_ts(c.split()[0])
        except ValueError:
            continue
        res.append({"start": st, "end": en, "text": "\n".join(lines[i + 1:]).strip()})
    return res
