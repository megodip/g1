# -*- coding: utf-8 -*-
"""Серверный HTTP-клиент: скачивание внешних страниц и расшифровка кодировок.

Используется поиском (search.py), читалкой (reader.py) и книгами (books.py).
"""

import re
import urllib.request

from app import config


def fetch_url(url, timeout, max_bytes):
    """Скачивает URL, возвращает (bytes, content_type). Больше max_bytes не читает."""
    req = urllib.request.Request(url, headers={"User-Agent": config.UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        buf = r.read(max_bytes + 1)
    return buf[:max_bytes], ctype


def decode_html(data, ctype):
    """bytes -> str. Кодировку берёт из Content-Type или <meta charset>. Фолбэк cp1251."""
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    enc = m.group(1) if m else None
    if not enc:
        head = data[:4096].decode("ascii", "ignore")
        mm = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
        if mm:
            enc = mm.group(1)
    for e in filter(None, [enc, "utf-8", "cp1251"]):
        try:
            return data.decode(e)
        except Exception:
            pass
    return data.decode("utf-8", "replace")
