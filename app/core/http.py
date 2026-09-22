# -*- coding: utf-8 -*-
"""
HTTP-примитивы: Request (чтение запроса) и Response (описание ответа).

Слой намеренно тонкий и без магии:
- Request только читает данные из BaseHTTPRequestHandler (путь, query, куки, форму).
- Response только ОПИСЫВАЕТ ответ (статус, заголовки, тело или файл).
  Отправкой занимается core/server.py.
"""

import re
import urllib.parse

from app import config


def _fix_latin1(raw):
    """HTTP-строка приходит как latin-1; если в query были сырые байты UTF-8
    (старые телефоны не всегда процент-кодируют), восстанавливаем их."""
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


# ============================== ЗАПРОС =======================================
class Request:
    """Обёртка над входящим HTTP-запросом."""

    def __init__(self, handler):
        self.method = handler.command                      # GET / POST / HEAD
        parsed = urllib.parse.urlparse(handler.path)
        # Нормализация: "/chat/" -> "/chat" (старые браузеры любят слеши).
        self.path = parsed.path.rstrip("/") or "/"
        self._qs = urllib.parse.parse_qs(_fix_latin1(parsed.query),
                                         keep_blank_values=True)
        self._handler = handler
        self._form = None                                  # кэш формы POST

    # --- GET-параметры -----------------------------------------------------
    def q(self, name, default=""):
        """Первое значение GET-параметра: /?a=1 -> req.q("a") == "1"."""
        vals = self._qs.get(name)
        return vals[-1] if vals else default

    # --- Куки ---------------------------------------------------------------
    def cookie(self, name):
        for part in self._handler.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return urllib.parse.unquote(v)
        return ""

    # --- Форма POST ----------------------------------------------------------
    @property
    def form(self):
        """Поля POST-формы (urlencoded или multipart). Кэшируется."""
        if self._form is None:
            ct = self._handler.headers.get("Content-Type", "")
            body = self._read_body()
            if "multipart/form-data" in ct:
                self._form = parse_multipart(body, ct)
            else:
                self._form = {
                    k: v[0] for k, v in urllib.parse.parse_qs(
                        body.decode("utf-8", "replace"),
                        keep_blank_values=True).items()
                }
        return self._form

    def _read_body(self):
        try:
            n = int(self._handler.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        limit = config.MAX_UPLOAD_MB * 1024 * 1024 + 65536
        return self._handler.rfile.read(min(n, limit))


# ============================== ОТВЕТЫ =======================================
class Response:
    """Описание ответа сервера. Создавай через класс-методы ниже."""

    def __init__(self, status=200, ctype="text/html; charset=utf-8",
                 body=b"", headers=None, stream_path=None, download_name=None):
        self.status = status
        self.ctype = ctype
        self.body = body
        self.headers = headers or []       # список строк "Заголовок: значение"
        self.stream_path = stream_path     # если задан — отдаём файл с диска
        self.download_name = download_name # имя в Content-Disposition

    # --- HTML-страница -------------------------------------------------------
    @classmethod
    def html(cls, text, status=200, cookies=(), cache=None):
        headers = ["Set-Cookie: %s" % c for c in cookies if c]
        if cache:
            headers.append("Cache-Control: %s" % cache)
        return cls(status=status, body=text.encode("utf-8"), headers=headers)

    # --- Редирект --------------------------------------------------------------
    @classmethod
    def redirect(cls, location, cookies=()):
        headers = ["Location: %s" % location]
        headers += ["Set-Cookie: %s" % c for c in cookies if c]
        return cls(status=302, ctype="text/html; charset=utf-8",
                   body=b"", headers=headers)

    # --- Бинарные данные (картинки и т.п.) --------------------------------------
    @classmethod
    def binary(cls, data, ctype, status=200, cache=None):
        headers = ["Cache-Control: %s" % cache] if cache else []
        return cls(status=status, ctype=ctype, body=data, headers=headers)

    # --- Файл на скачивание -------------------------------------------------------
    @classmethod
    def file(cls, path, name):
        """Файл с диска как attachment (не читается в память целиком)."""
        return cls(stream_path=path, download_name=name)


# ============================== КУКИ =========================================
def make_cookie(name, value, max_age=31536000):
    return "%s=%s; Path=/; Max-Age=%d" % (name, urllib.parse.quote(value), max_age)


def del_cookie(name):
    return "%s=; Path=/; Max-Age=0" % name


# ============================== MULTIPART ====================================
def parse_multipart(body, ctype):
    """Разбор multipart/form-data без внешних библиотек.

    Возвращает dict: имя поля -> строка (обычное поле)
    или {"filename": str, "data": bytes} (файл).
    """
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return {}
    boundary = m.group(1).encode("utf-8")
    fields = {}
    for part in body.split(b"--" + boundary):
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        nm = re.search(rb'name="([^"]*)"', head)
        fn = re.search(rb'filename="([^"]*)"', head)
        key = nm.group(1).decode("utf-8", "replace") if nm else "f%d" % len(fields)
        if fn and fn.group(1):
            fields[key] = {"filename": fn.group(1).decode("utf-8", "replace"),
                           "data": data}
        else:
            fields[key] = data.decode("utf-8", "replace")
    return fields
