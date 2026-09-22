#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  МИНИ-ПОРТАЛ для кнопочного телефона (браузер без JavaScript)
#  Вкладки: Поиск | Загрузки | Книги | Чат | Архив | Вега (ИИ-агент с доступом к серверу)
#  Запуск:  python3 server.py     ->  http://IP_СЕРВЕРА:8000
#  Зависимости: только Python 3.7+
# ============================================================================

import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from html import escape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================== НАСТРОЙКИ ===================================
HOST = "0.0.0.0"
PORT = 8000

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "data")
DL_DIR       = os.path.join(DATA_DIR, "downloads")     # файлы для скачивания
ARCHIVE_DIR  = os.path.join(DATA_DIR, "archive")       # записи архива
CHAT_FILE    = os.path.join(DATA_DIR, "chat.jsonl")

MAX_UPLOAD_MB = 60        # лимит закачки файла с телефона
MAX_PAGE_KB   = 2000      # лимит страницы для прокси-читалки
FETCH_TIMEOUT = 25        # сек, загрузка страниц

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# --- Вега (ИИ-агент). Модель подключаешь СВОЮ: ------------------------------
# LLM_STYLE = "openai" -> любой OpenAI-совместимый endpoint (LLM_URL + ключ)
# LLM_STYLE = "ollama" -> локальная Ollama (LLM_URL = http://127.0.0.1:11434/api/chat)
# Если LLM_MODEL пуст и стиль openai — модель определится автоматически (/models).
LLM_STYLE     = "openai"
LLM_URL       = "https://api.provod.ai/v1"
LLM_KEY       = "sk_9093a5d227fdf4f3bfe1a9182fe2dec365b65734fff99928"
LLM_MODEL     = ""        # пусто = автоопределение (для Ollama заполни обязательно!)
VEGA_CONFIRM  = True      # True = команды shell только после подтверждения
CMD_TIMEOUT   = 30        # сек, таймаут shell-команды

# --- Книги (вкладка «Книги»): поиск и скачивание с Flibusta -----------------
FLIBUSTA_HOSTS  = ["flibusta.is", "flibusta.site"]  # зеркала; можно дописать своё
BOOK_DL_TIMEOUT = 90      # сек на скачивание файла книги
BOOK_MAX_MB     = 40      # макс. размер файла книги
# ============================================================================

LOCK = threading.Lock()
TABS = [("search", "Поиск"), ("downloads", "Загрузки"), ("books", "Книги"),
        ("chat", "Чат"), ("archive", "Архив"), ("vega", "Вега")]

def ensure_dirs():
    for d in (DATA_DIR, DL_DIR, ARCHIVE_DIR):
        os.makedirs(d, exist_ok=True)

def fmt_size(n):
    x = float(n)
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if x < 1024:
            return ("%d %s" if u == "Б" else "%.1f %s") % (x, u)
        x /= 1024
    return "%.1f ТБ" % x

def safe_name(name):
    name = os.path.basename(str(name).replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ]", "_", name, flags=re.UNICODE)
    return name.strip(". ")[:80]

def nl2br(s):
    return s.replace("\n", "<br>")

def cookie(name, val):
    return "%s=%s; Path=/; Max-Age=31536000" % (name, urllib.parse.quote(val))

# ----------------------------- HTTP-клиент ----------------------------------
def fetch_url(url, timeout, max_bytes):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        buf = r.read(max_bytes + 1)
    return buf[:max_bytes], ctype

def decode_html(data, ctype):
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

def reader_url(u, imgs):
    return "/reader?url=" + urllib.parse.quote(u, safe="") + ("&imgs=1" if imgs else "&imgs=0")

def img_proxy_url(u):
    return "/img?url=" + urllib.parse.quote(u, safe="")

# ------------------------- Парсер страниц (читалка) -------------------------
class Extractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "iframe", "svg", "object", "embed",
            "template", "head", "form", "input", "button", "select", "textarea",
            "label", "option", "nav", "aside", "video", "audio", "canvas", "map"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "table", "ul", "ol", "section", "article", "blockquote", "pre",
             "td", "th", "hr", "center", "figure", "figcaption", "dl", "dt", "dd",
             "header", "main", "footer"}

    def __init__(self, base, imgs):
        super().__init__(convert_charrefs=True)
        self.base, self.imgs = base, imgs
        self.out, self.skip, self.href = [], 0, None

    def abs_url(self, h):
        h = (h or "").strip()
        if h.startswith("data:"):
            return ""
        return urllib.parse.urljoin(self.base, h)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in self.BLOCK:
            self.out.append("\n")
        d = dict(attrs)
        if tag == "a":
            self.href = self.abs_url(d.get("href")) if d.get("href") else None
        elif tag == "img":
            src = d.get("src") or d.get("data-src") or ""
            alt = (d.get("alt") or "").strip()
            if src:
                u = self.abs_url(src)
                if u.startswith("http"):
                    if self.imgs:
                        self.out.append(' <img src="%s" alt="%s"> '
                                        % (escape(img_proxy_url(u)), escape(alt)))
                    elif alt:
                        self.out.append(" [рис: %s] " % escape(alt))

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a":
            self.href = None

    def handle_data(self, data):
        if self.skip:
            return
        t = data.strip()
        if not t:
            return
        if self.href and self.href.startswith("http"):
            self.out.append('<a href="%s">%s</a> '
                            % (escape(reader_url(self.href, self.imgs)), escape(t)))
        else:
            self.out.append(escape(t) + " ")

# --------------------------- Поиск: Google / DDG -----------------------------
class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.href, self.buf = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            text = " ".join("".join(self.buf).split())
            self.links.append((self.href, text))
            self.href = None

    def handle_data(self, d):
        if self.href is not None:
            self.buf.append(d)

SKIP_FRAGMENTS = ("google.", "gstatic.", "googleusercontent", "accounts.",
                  "policies.", "support.google", "webcache.", "translate.",
                  "/search?", "/preferences", "duckduckgo.com", "yandex.",
                  "docs.google", "drive.google", "play.google", "books.google")

def clean_results(links):
    out, seen = [], set()
    for href, text in links:
        if not href:
            continue
        if href.startswith("/url?"):                       # Google-редирект
            p = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = p.get("q", p.get("url", [href]))[0]
        if href.startswith("//"):
            href = "https:" + href
        if "uddg=" in href:                                # DDG-редирект
            p = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" in p:
                href = urllib.parse.unquote(p["uddg"][0])
        if not href.startswith("http") or not text:
            continue
        low = href.lower()
        if any(s in low for s in SKIP_FRAGMENTS):
            continue
        key = href.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append((href, text))
        if len(out) >= 15:
            break
    return out

def google_search(q):
    url = "https://www.google.com/search?hl=ru&num=20&q=" + urllib.parse.quote(q)
    data, ctype = fetch_url(url, FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
    ap = AnchorParser()
    ap.feed(decode_html(data, ctype))
    ap.close()
    res = clean_results(ap.links)
    if not res:
        raise RuntimeError("Google не дал результатов")
    return res

def ddg_search(q):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    data, ctype = fetch_url(url, FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
    ap = AnchorParser()
    ap.feed(decode_html(data, ctype))
    ap.close()
    return clean_results(ap.links)

def web_search(q):
    try:
        r = google_search(q)
        if r:
            return r, "Google"
    except Exception:
        pass
    return ddg_search(q), "DuckDuckGo"

# --------------------------------- Чат --------------------------------------
def chat_load():
    try:
        with LOCK, open(CHAT_FILE, "r", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except Exception:
        return []

def chat_append(nick, text):
    with LOCK:
        with open(CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": nick, "x": text, "t": time.time()},
                               ensure_ascii=False) + "\n")
        try:
            with open(CHAT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > 400:
                with open(CHAT_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-200:])
        except Exception:
            pass

# --------------------------------- Вега -------------------------------------
def vega_hist_path(sid):
    return os.path.join(DATA_DIR, "vega_%s.json" % re.sub(r"\W", "", sid))

def vega_load(sid):
    try:
        with open(vega_hist_path(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def vega_save(sid, hist):
    with LOCK, open(vega_hist_path(sid), "w", encoding="utf-8") as f:
        json.dump(hist[-60:], f, ensure_ascii=False)

def vega_pending_path(sid):
    return os.path.join(DATA_DIR, "vega_%s.pending" % re.sub(r"\W", "", sid))

def vega_get_pending(sid):
    try:
        with open(vega_pending_path(sid), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def vega_save_pending(sid, cmd):
    with open(vega_pending_path(sid), "w", encoding="utf-8") as f:
        f.write(cmd)

def vega_pop_pending(sid):
    cmd = vega_get_pending(sid)
    try:
        os.remove(vega_pending_path(sid))
    except Exception:
        pass
    return cmd

VEGA_SYSTEM = (
    "Ты — Вега, ИИ-агент, работающий на Linux-сервере пользователя. "
    "Ты можешь выполнять команды оболочки. Чтобы выполнить команду, добавь "
    "В КОНЕЦ ответа отдельной строкой метку вида:\n@@RUN команда\n"
    "После выполнения ты получишь вывод команды и сможешь продолжить работу. "
    "Если команда не нужна — просто отвечай текстом. Отвечай кратко, по-русски."
)

_MODEL_CACHE = {"name": LLM_MODEL or None}

def vega_pick_model():
    """Если LLM_MODEL не задана (стиль openai) — берём первую модель
    из списка эндпоинта {LLM_URL}/models. Результат кэшируется."""
    if _MODEL_CACHE["name"]:
        return _MODEL_CACHE["name"]
    if LLM_STYLE != "openai" or not LLM_URL:
        return None
    url = LLM_URL.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    if LLM_KEY:
        req.add_header("Authorization", "Bearer " + LLM_KEY)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        items = data.get("data") or data.get("models") or []
        ids = [m.get("id") or m.get("name") for m in items if isinstance(m, dict)]
        ids = [i for i in ids if i]
        if ids:
            _MODEL_CACHE["name"] = ids[0]
    except Exception:
        pass
    return _MODEL_CACHE["name"]

def vega_call(history):
    if not LLM_URL:
        raise RuntimeError("LLM не настроен: заполни LLM_URL / LLM_MODEL / LLM_KEY "
                           "в начале server.py и перезапусти сервер")
    msgs = [{"role": "system", "content": VEGA_SYSTEM}] + history[-20:]
    if LLM_STYLE == "ollama":
        if not LLM_MODEL:
            raise RuntimeError("Для Ollama обязательно укажи LLM_MODEL в начале server.py")
        url, payload = LLM_URL, {"model": LLM_MODEL, "messages": msgs, "stream": False}
    else:
        url = LLM_URL if "/chat/completions" in LLM_URL \
            else LLM_URL.rstrip("/") + "/chat/completions"
        payload = {"messages": msgs, "temperature": 0.4}
        model = vega_pick_model()
        if model:
            payload["model"] = model
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    if LLM_KEY:
        req.add_header("Authorization", "Bearer " + LLM_KEY)
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    if LLM_STYLE == "ollama":
        return resp["message"]["content"]
    return resp["choices"][0]["message"]["content"]

def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           errors="replace", timeout=CMD_TIMEOUT, cwd=DATA_DIR)
        out = (r.stdout or "")
        if r.stderr:
            out += "\n[stderr]\n" + r.stderr
    except subprocess.TimeoutExpired:
        out = "Команда превысила таймаут %d сек" % CMD_TIMEOUT
    except Exception as e:
        out = "Ошибка запуска: %s" % e
    out = out.strip() or "(пустой вывод)"
    return out[:4000]

def vega_step(sid, max_iters=4):
    """Диалог с моделью: пока Вега просит команды — выполняем (или ставим на
    подтверждение) и кормим её выводом, максимум max_iters шагов."""
    hist = vega_load(sid)
    for _ in range(max_iters):
        try:
            reply = vega_call(hist)
        except Exception as e:
            hist.append({"role": "system", "content": "Ошибка связи с моделью: %s" % e})
            break
        hist.append({"role": "assistant", "content": reply})
        m = re.search(r"@@RUN[ \t]+(.+)", reply)
        if not m:
            break
        cmd = m.group(1).strip()[:500]
        if VEGA_CONFIRM:
            vega_save_pending(sid, cmd)
            break
        hist.append({"role": "system",
                     "content": "Вывод команды «%s»:\n%s" % (cmd, run_cmd(cmd))})
    vega_save(sid, hist)

# ----------------------- Книги: Flibusta (OPDS/HTML) ------------------------
ATOM = "http://www.w3.org/2005/Atom"
BOOK_FMT_ORDER = ["fb2", "epub", "mobi", "pdf", "djvu", "txt", "doc", "rtf", "html"]

def qparam(s):
    return urllib.parse.quote(s or "", safe="")

def fb_fetch(host, path, timeout, max_bytes):
    url = "https://%s%s" % (host, path)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError("файл больше лимита %d МБ" % (max_bytes // (1024 * 1024)))
    return data, ctype

def fb_get(path, timeout, max_bytes, hosts=None):
    """Пробует зеркала по очереди; возвращает (host, data, ctype)."""
    hosts = hosts or FLIBUSTA_HOSTS
    last = None
    for h in hosts:
        try:
            data, ctype = fb_fetch(h, path, timeout, max_bytes)
            return h, data, ctype
        except Exception as e:
            last = e
    raise RuntimeError("Зеркала Flibusta не ответили (%s)" % last)

def fmt_sort(fs):
    order = {f: i for i, f in enumerate(BOOK_FMT_ORDER)}
    return sorted(set(fs), key=lambda f: order.get(f, 99))

def fb_search_opds(q):
    """Поиск через OPDS-каталог Flibusta (стабильнее HTML)."""
    host, data, ctype = fb_get("/opds/search?searchTerm=" + qparam(q),
                               FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
    root = ET.fromstring(data)
    res = []
    for e in root.findall("{%s}entry" % ATOM):
        title = (e.findtext("{%s}title" % ATOM) or "").strip()
        if not title:
            continue
        authors = []
        for a in e.findall("{%s}author" % ATOM):
            nm = (a.findtext("{%s}name" % ATOM) or "").strip()
            if nm:
                authors.append(nm)
        fmts, bid = [], 0
        for l in e.findall("{%s}link" % ATOM):
            href = l.get("href") or ""
            rel = l.get("rel") or ""
            if rel and "acquisition" not in rel:
                continue
            m = re.search(r"/b/(\d+)/(\w+)", href)
            if m:
                bid = int(m.group(1))
                fmts.append(m.group(2).lower())
        if bid and fmts:
            res.append({"title": title, "authors": ", ".join(authors), "id": bid,
                        "host": host, "fmts": fmt_sort(fmts)})
    return res

def fb_search_html(q):
    """Фолбэк: парсим HTML-страницу поиска Flibusta."""
    host, data, ctype = fb_get("/booksearch?ask=" + qparam(q),
                               FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
    ap = AnchorParser()
    ap.feed(decode_html(data, ctype))
    ap.close()
    res, seen = [], set()
    for href, text in ap.links:
        m = re.match(r"^/b/(\d+)$", (href or "").strip())
        if not m or not text:
            continue
        bid = int(m.group(1))
        if bid in seen:
            continue
        seen.add(bid)
        res.append({"title": text, "authors": "", "id": bid, "host": host,
                    "fmts": ["fb2", "epub"]})
    return res

def books_search(q):
    try:
        res = fb_search_opds(q)
        if res:
            return res
    except Exception:
        pass
    return fb_search_html(q)

def books_download(bid, fmt, fname, host=""):
    """Скачивает книгу в data/downloads, возвращает имя файла."""
    hosts = ([host] if host in FLIBUSTA_HOSTS else []) + \
            [h for h in FLIBUSTA_HOSTS if h != host]
    path = "/b/%d/%s" % (bid, fmt)
    last_err = None
    for h in hosts:
        try:
            _, data, ctype = fb_get(path, BOOK_DL_TIMEOUT, BOOK_MAX_MB * 1024 * 1024,
                                    hosts=[h])
        except Exception as e:
            last_err = e
            continue
        if "text/html" in (ctype or "").lower():
            txt = re.sub(r"\s+", " ",
                         re.sub(r"<[^>]+>", " ",
                                decode_html(data[:4000], ctype))).strip()
            last_err = RuntimeError("зеркало %s вернуло страницу вместо файла "
                                    "(капча/ошибка): %s" % (h, txt[:160]))
            continue
        name = safe_name(fname) or ("book_%d" % bid)
        fn = "%s.%s" % (name, fmt)
        with open(os.path.join(DL_DIR, fn), "wb") as f:
            f.write(data)
        return fn
    raise last_err or RuntimeError("не удалось скачать файл книги")

# ---------------------------- Каркас страницы --------------------------------
def page(title, active, body, refresh=0):
    nav = ""
    for key, name in TABS:
        if key == active:
            nav += "[<b>%s</b>] " % name
        else:
            nav += '[<a href="/%s">%s</a>] ' % (key, name)
    meta = '<meta http-equiv="refresh" content="%d">' % refresh if refresh else ""
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">%(meta)s
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
body{background:#f2efe6;color:#111;font:14px/1.5 Arial,Helvetica,sans-serif;margin:6px;max-width:760px}
a{color:#0a4d68}
h1{font-size:19px;margin:6px 0}
h2{font-size:16px;margin:10px 0 4px}
.tabs{border-bottom:2px solid #0a4d68;padding:2px 0 6px;margin-bottom:8px}
input[type=text],textarea,select,input[type=file]{width:95%%;margin:3px 0}
textarea{height:160px}
hr{border:0;border-top:1px solid #b9b3a2;margin:10px 0}
.small{color:#555;font-size:12px}
.msg{margin:3px 0;padding:3px 5px;background:#e9e5d8}
pre{white-space:pre-wrap;background:#e9e5d8;padding:6px;overflow:auto}
img{max-width:100%%}
form{margin:6px 0}
</style></head><body>
<div class="tabs">%(nav)s</div>
%(body)s
</body></html>""" % {"meta": meta, "title": escape(title), "nav": nav, "body": body}

def parse_multipart(body, ctype):
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return {}
    b = m.group(1).encode("utf-8")
    fields = {}
    for part in body.split(b"--" + b):
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

# =============================== HTTP-сервер =================================
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # --- служебное ---
    def send_html(self, code, text, cookies=()):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for c in cookies:
            if c:
                self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, loc, cookies=()):
        self.send_response(302)
        self.send_header("Location", loc)
        for c in cookies:
            if c:
                self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def get_cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return urllib.parse.unquote(v)
        return ""

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        return self.rfile.read(min(n, MAX_UPLOAD_MB * 1024 * 1024 + 65536))

    def send_file(self, path, name):
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"; filename*=UTF-8\'\'%s'
                         % (name.encode("ascii", "replace").decode(),
                            urllib.parse.quote(name)))
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ---------------------------- GET ------------------------------------
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path, qs = p.path, urllib.parse.parse_qs(p.query)
        try:
            if path in ("", "/"):
                self.redirect("/search")
            elif path == "/search":
                self.pg_search(qs)
            elif path == "/reader":
                self.pg_reader(qs)
            elif path == "/img":
                self.pg_img(qs)
            elif path == "/downloads":
                self.pg_downloads()
            elif path == "/dl":
                self.pg_dl(qs)
            elif path == "/books":
                self.pg_books(qs)
            elif path == "/books/get":
                self.pg_books_get(qs)
            elif path == "/chat":
                self.pg_chat(qs)
            elif path == "/archive":
                self.pg_archive()
            elif path == "/archive/view":
                self.pg_archive_view(qs)
            elif path == "/archive/raw":
                self.pg_archive_raw(qs)
            elif path == "/vega":
                self.pg_vega()
            else:
                self.send_html(404, page("404", "",
                                         'Страница не найдена. <a href="/search">На главную</a>', 0))
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self.send_html(500, page("Ошибка", "",
                                         "<b>Ошибка:</b> %s<br><a href=\"/search\">На главную</a>"
                                         % escape(str(e)), 0))
            except Exception:
                pass

    # ---------------------------- POST -----------------------------------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/chat":
                self.po_chat()
            elif path == "/downloads":
                self.po_upload()
            elif path == "/archive/save":
                self.po_archive_save()
            elif path == "/archive/del":
                self.po_archive_del()
            elif path == "/vega/send":
                self.po_vega_send()
            elif path == "/vega/exec":
                self.po_vega_exec()
            elif path == "/vega/clear":
                self.po_vega_clear()
            else:
                self.send_html(404, page("404", "", "Не найдено", 0))
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self.send_html(500, page("Ошибка", "",
                                         "<b>Ошибка:</b> %s" % escape(str(e)), 0))
            except Exception:
                pass

    def form_fields(self):
        body = self.read_body()
        ct = self.headers.get("Content-Type", "")
        if "multipart/form-data" in ct:
            return parse_multipart(body, ct)
        return {k: v[0] for k, v in urllib.parse.parse_qs(
            body.decode("utf-8", "replace"), keep_blank_values=True).items()}

    # ------------------------- Поиск --------------------------------------
    def pg_search(self, qs):
        q = (qs.get("q", [""])[0] or "").strip()
        imgs = qs.get("imgs", ["1"])[-1] == "1"
        body = ('<h1>Поиск</h1>'
                '<form method="get" action="/search">'
                '<input type="text" name="q" value="%s"> '
                '<input type="hidden" name="imgs" value="0">'
                '<label><input type="checkbox" name="imgs" value="1"%s> картинки</label> '
                '<input type="submit" value="Найти"></form>'
                % (escape(q), " checked" if imgs else ""))
        body += ('<form method="get" action="/reader">Открыть сайт по адресу: '
                 '<input type="text" name="url" placeholder="https://..."> '
                 '<input type="hidden" name="imgs" value="%d">'
                 '<input type="submit" value="Открыть"></form>' % (1 if imgs else 0))
        if q:
            body += "<hr><h2>Результаты</h2>"
            try:
                res, engine = web_search(q)
            except Exception as e:
                res, engine = None, ""
                body += '<div class="msg">Ошибка поиска: %s</div>' % escape(str(e))
            if res is not None:
                if not res:
                    body += '<div class="msg">Ничего не найдено.</div>'
                else:
                    body += "<ol>" + "".join(
                        '<li><a href="%s">%s</a><br><span class="small">%s</span></li>'
                        % (escape(reader_url(href, imgs)), escape(text[:120]),
                           escape(href[:100])) for href, text in res) + "</ol>"
                    body += '<div class="small">Источник: %s. Страницы открываются без JavaScript.</div>' % engine
        self.send_html(200, page("Поиск", "search", body, 0))

    # ------------------------- Читалка ------------------------------------
    def pg_reader(self, qs):
        url = qs.get("url", [""])[0]
        imgs = qs.get("imgs", ["1"])[-1] == "1"
        if not url.startswith(("http://", "https://")):
            self.send_html(200, page("Читалка", "search",
                                     'Нужен адрес http(s)://... <a href="/search">Назад</a>', 0))
            return
        try:
            data, ctype = fetch_url(url, FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
        except Exception as e:
            self.send_html(200, page("Читалка", "search",
                                     'Не удалось загрузить страницу: %s<br><a href="/search">Назад к поиску</a>'
                                     % escape(str(e)), 0))
            return
        raw = decode_html(data, ctype)
        t = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = re.sub(r"\s+", " ", t.group(1)).strip()[:120] if t else url
        ex = Extractor(url, imgs)
        ex.feed(raw)
        ex.close()
        content = "".join(ex.out).strip() or "(не удалось извлечь текст)"
        toggle = '<a href="%s">%s</a>' % (escape(reader_url(url, not imgs)),
                                          "только текст" if imgs else "с картинками")
        top = ('<div class="small">Источник: %s<br>Режим: %s · '
               '<a href="/search">← к поиску</a></div><hr>'
               % (escape(url[:100]), toggle))
        self.send_html(200, page(title, "search", top + nl2br(content), 0))

    # ------------------------- Прокси картинок ----------------------------
    def pg_img(self, qs):
        u = qs.get("url", [""])[0]
        ok = False
        if u.startswith(("http://", "https://")):
            try:
                data, ctype = fetch_url(u, 15, 3 * 1024 * 1024)
                if not ctype.startswith("image/"):
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        ctype = "image/png"
                    elif data[:3] == b"\xff\xd8\xff":
                        ctype = "image/jpeg"
                    elif data[:6] in (b"GIF87a", b"GIF89a"):
                        ctype = "image/gif"
                    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                        ctype = "image/webp"
                    else:
                        ctype = ""
                if ctype:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    ok = True
            except Exception:
                pass
        if not ok:
            self.send_html(404, "not found")

    # ------------------------- Загрузки -----------------------------------
    def pg_downloads(self):
        rows = []
        if os.path.isdir(DL_DIR):
            for n in sorted(os.listdir(DL_DIR)):
                p = os.path.join(DL_DIR, n)
                if os.path.isfile(p):
                    rows.append('<div class="msg"><b>%s</b> <span class="small">%s</span><br>'
                                '<a href="/dl?n=%s">Скачать на телефон</a></div>'
                                % (escape(n), fmt_size(os.path.getsize(p)),
                                   urllib.parse.quote(n)))
        if not rows:
            rows.append('<div class="small">Папка пуста. Файлы можно положить на сервер '
                        'в data/downloads, закачать с телефона формой ниже, найти во '
                        'вкладке «Книги» или поручить Веге.</div>')
        body = ('<h1>Загрузки</h1>' + "".join(rows) + '<hr>'
                '<h2>Закачать с телефона</h2>'
                '<form method="post" action="/downloads" enctype="multipart/form-data">'
                '<input type="file" name="f"> <input type="submit" value="Загрузить на сервер"></form>'
                '<div class="small">Если телефон не умеет выбирать файлы — закидывай по SSH/SFTP.</div>')
        self.send_html(200, page("Загрузки", "downloads", body, 0))

    def pg_dl(self, qs):
        n = safe_name(qs.get("n", [""])[0])
        p = os.path.join(DL_DIR, n)
        if not n or not os.path.isfile(p):
            self.send_html(404, page("404", "", 'Файл не найден. <a href="/downloads">Назад</a>', 0))
            return
        self.send_file(p, n)

    def po_upload(self):
        fields = self.form_fields()
        f = fields.get("f")
        if isinstance(f, dict) and f.get("filename"):
            name = safe_name(f["filename"]) or "file.bin"
            with open(os.path.join(DL_DIR, name), "wb") as fh:
                fh.write(f["data"])
        self.redirect("/downloads")

    # ------------------------- Книги --------------------------------------
    def pg_books(self, qs):
        q = (qs.get("q", [""])[0] or "").strip()
        body = ('<h1>Книги</h1>'
                '<form method="get" action="/books">'
                '<input type="text" name="q" value="%s"> '
                '<input type="submit" value="Искать"></form>'
                '<div class="small">Поиск по Flibusta: книги, ранобэ, фанфики. '
                'Нажми на формат — файл сохранится во вкладку «Загрузки».</div>'
                % escape(q))
        if q:
            body += "<hr><h2>Результаты</h2>"
            res = []
            try:
                res = books_search(q)
                if not res:
                    body += '<div class="msg">Ничего не найдено.</div>'
            except Exception as e:
                body += '<div class="msg">Ошибка поиска: %s</div>' % escape(str(e))
            for b in res[:15]:
                links = []
                for f in b["fmts"]:
                    links.append('[<a href="/books/get?b=%d&amp;fmt=%s&amp;t=%s&amp;a=%s&amp;h=%s">%s</a>]'
                                 % (b["id"], f, qparam(b["title"]), qparam(b["authors"]),
                                    qparam(b["host"]), f))
                links.append('[<a href="%s">читать</a>]' % escape(reader_url(
                    "https://%s/b/%d/read" % (b["host"], b["id"]), 0)))
                auth = ('<br><span class="small">%s</span>' % escape(b["authors"])) \
                       if b["authors"] else ""
                body += ('<div class="msg"><b>%s</b>%s<br>%s</div>'
                         % (escape(b["title"]), auth, " ".join(links)))
        self.send_html(200, page("Книги", "books", body, 0))

    def pg_books_get(self, qs):
        try:
            bid = int(qs.get("b", ["0"])[0])
        except ValueError:
            bid = 0
        fmt = (qs.get("fmt", ["fb2"])[0] or "fb2").strip().lower()
        if not re.fullmatch(r"[a-z0-9]{1,6}", fmt):
            fmt = "fb2"
        title = (qs.get("t", [""])[0] or "").strip()
        author = (qs.get("a", [""])[0] or "").strip()
        host = (qs.get("h", [""])[0] or "").strip()
        if not bid:
            self.redirect("/books")
            return
        fname = (title + (" - " + author if author else "")).strip()
        try:
            books_download(bid, fmt, fname, host)
            self.redirect("/downloads")
        except Exception as e:
            self.send_html(200, page("Книги", "books",
                '<div class="msg"><b>Не удалось скачать:</b> %s</div>'
                '<div><a href="/books?q=%s">← назад к результатам</a></div>'
                % (escape(str(e)), qparam(title)), 0))

    # ------------------------- Чат ----------------------------------------
    def pg_chat(self, qs):
        if qs.get("logout", ["0"])[0] == "1":
            self.redirect("/chat", cookies=["nick=; Path=/; Max-Age=0"])
            return
        nick = self.get_cookie("nick")
        rows = []
        for m in chat_load()[-40:]:
            rows.append('<div class="msg"><b>%s</b> <span class="small">%s</span><br>%s</div>'
                        % (escape(m["n"]),
                           time.strftime("%d.%m %H:%M", time.localtime(m["t"])),
                           nl2br(escape(m["x"]))))
        if not rows:
            rows.append('<div class="small">Сообщений пока нет.</div>')
        if nick:
            form = ('<form method="post" action="/chat"><input type="hidden" name="action" value="msg">'
                    '<input type="text" name="x" maxlength="500"> '
                    '<input type="submit" value="&gt;&gt;"></form>'
                    '<div class="small">Вы как <b>%s</b> · <a href="/chat?logout=1">сменить ник</a>'
                    ' · страница обновляется сама каждые 7 сек</div>' % escape(nick))
        else:
            form = ('<form method="post" action="/chat"><input type="hidden" name="action" value="nick">'
                    'Ник: <input type="text" name="n" maxlength="20"> '
                    '<input type="submit" value="Войти в чат"></form>')
        self.send_html(200, page("Чат", "chat", "<h1>Общий чат</h1>" + "".join(rows)
                                 + "<hr>" + form, refresh=7))

    def po_chat(self):
        f = self.form_fields()
        if f.get("action") == "nick":
            nick = (f.get("n") or "").strip()
            if not re.fullmatch(r"[\w\- ]{2,20}", nick, re.UNICODE):
                self.send_html(200, page("Чат", "chat",
                                         'Ник: 2–20 символов (буквы, цифры, пробел, дефис). '
                                         '<a href="/chat">Назад</a>', 0))
                return
            self.redirect("/chat", cookies=[cookie("nick", nick)])
        else:
            nick = self.get_cookie("nick")
            if not nick:
                self.redirect("/chat")
                return
            x = (f.get("x") or "").strip()
            if x:
                chat_append(nick, x[:500])
            self.redirect("/chat")

    # ------------------------- Архив --------------------------------------
    def pg_archive(self):
        rows = []
        if os.path.isdir(ARCHIVE_DIR):
            for n in sorted(os.listdir(ARCHIVE_DIR)):
                p = os.path.join(ARCHIVE_DIR, n)
                if os.path.isfile(p):
                    rows.append(
                        '<div class="msg"><b>%s</b> <span class="small">%s · %s</span><br>'
                        '<a href="/archive/view?n=%s">открыть</a> · '
                        '<a href="/archive/raw?n=%s">скачать</a> · '
                        '<form method="post" action="/archive/del">'
                        '<input type="hidden" name="n" value="%s">'
                        '<input type="submit" value="Удалить"></form></div>'
                        % (escape(n), fmt_size(os.path.getsize(p)),
                           time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(p))),
                           urllib.parse.quote(n), urllib.parse.quote(n), escape(n)))
        if not rows:
            rows.append('<div class="small">Архив пуст.</div>')
        body = ('<h1>Архив</h1>' + "".join(rows) + '<hr><h2>Новая запись</h2>'
                '<form method="post" action="/archive/save">Название: '
                '<input type="text" name="n" maxlength="60">Текст: '
                '<textarea name="x"></textarea>'
                '<input type="submit" value="Сохранить"></form>')
        self.send_html(200, page("Архив", "archive", body, 0))

    def pg_archive_view(self, qs):
        n = safe_name(qs.get("n", [""])[0])
        p = os.path.join(ARCHIVE_DIR, n)
        if not n or not os.path.isfile(p):
            self.send_html(404, page("404", "", 'Запись не найдена. <a href="/archive">Назад</a>', 0))
            return
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        body = ('<h2>%s</h2><form method="post" action="/archive/save">'
                '<input type="hidden" name="n" value="%s">'
                '<textarea name="x">%s</textarea>'
                '<input type="submit" value="Сохранить изменения"></form>'
                '<div class="small"><a href="/archive">← к архиву</a> · '
                '<a href="/archive/raw?n=%s">скачать</a></div>'
                % (escape(n), escape(n), escape(text), urllib.parse.quote(n)))
        self.send_html(200, page(n, "archive", body, 0))

    def pg_archive_raw(self, qs):
        n = safe_name(qs.get("n", [""])[0])
        p = os.path.join(ARCHIVE_DIR, n)
        if not n or not os.path.isfile(p):
            self.send_html(404, page("404", "", "Не найдено", 0))
            return
        self.send_file(p, n)

    def po_archive_save(self):
        f = self.form_fields()
        n = safe_name(f.get("n", ""))
        if not n:
            self.redirect("/archive")
            return
        if not n.lower().endswith(".txt"):
            n += ".txt"
        with open(os.path.join(ARCHIVE_DIR, n), "w", encoding="utf-8") as fh:
            fh.write((f.get("x") or "")[:200000])
        self.redirect("/archive")

    def po_archive_del(self):
        n = safe_name(self.form_fields().get("n", ""))
        if n:
            try:
                os.remove(os.path.join(ARCHIVE_DIR, n))
            except OSError:
                pass
        self.redirect("/archive")

    # ------------------------- Вега ---------------------------------------
    def vega_sid(self):
        sid = self.get_cookie("vsid")
        if sid:
            return sid, None
        return uuid.uuid4().hex, cookie("vsid", uuid.uuid4().hex)

    def pg_vega(self):
        sid, ck = self.vega_sid()
        parts = []
        if not LLM_URL:
            parts.append('<div class="msg"><b>Вега не подключена к модели.</b> Заполни LLM_URL, '
                         'LLM_MODEL (и LLM_KEY, если нужен) в начале server.py и перезапусти сервер.</div>')
        pend = vega_get_pending(sid)
        if pend:
            parts.append('<div><b>Вега хочет выполнить команду:</b></div><pre>%s</pre>' % escape(pend))
            parts.append('<form method="post" action="/vega/exec">'
                         '<input type="hidden" name="approve" value="1">'
                         '<input type="submit" value="Выполнить"></form>')
            parts.append('<form method="post" action="/vega/exec">'
                         '<input type="hidden" name="approve" value="0">'
                         '<input type="submit" value="Отклонить"></form><hr>')
        hist = vega_load(sid)[-30:]
        for m in hist:
            if m["role"] == "user":
                parts.append('<div class="msg"><b>Вы:</b><br>%s</div>' % nl2br(escape(m["content"])))
            elif m["role"] == "assistant":
                t = escape(m["content"]).replace("@@RUN ", "\n[команда] ")
                parts.append('<div class="msg"><b>Вега:</b><br>%s</div>' % nl2br(t))
            else:
                parts.append('<div class="msg"><b>[сервер]:</b><br><pre>%s</pre></div>'
                             % escape(m["content"]))
        if not hist:
            parts.append('<div class="small">Диалог пуст. Вега — ИИ-агент с доступом к серверу: '
                         'попроси её, например, узнать нагрузку или скачать файл в «Загрузки».</div>')
        form = ('<hr><form method="post" action="/vega/send">'
                '<input type="text" name="msg" maxlength="1000"> '
                '<input type="submit" value="Отправить"></form>'
                '<form method="post" action="/vega/clear">'
                '<input type="submit" value="Очистить диалог"></form>')
        self.send_html(200, page("Вега", "vega", "<h1>Вега</h1>" + "".join(parts) + form, 0),
                       cookies=[ck])

    def po_vega_send(self):
        sid, ck = self.vega_sid()
        msg = (self.form_fields().get("msg") or "").strip()
        if msg:
            hist = vega_load(sid)
            hist.append({"role": "user", "content": msg[:2000]})
            vega_save(sid, hist)
            vega_step(sid)
        self.redirect("/vega", cookies=[ck])

    def po_vega_exec(self):
        sid, ck = self.vega_sid()
        approve = self.form_fields().get("approve") == "1"
        pend = vega_pop_pending(sid)
        if pend:
            hist = vega_load(sid)
            if approve:
                hist.append({"role": "system",
                             "content": "Выполнена команда «%s»:\n%s" % (pend, run_cmd(pend))})
                vega_save(sid, hist)
                vega_step(sid, max_iters=3)   # Вега видит вывод и продолжает
            else:
                hist.append({"role": "system",
                             "content": "Пользователь отклонил команду «%s»." % pend})
                vega_save(sid, hist)
                vega_step(sid, max_iters=1)
        self.redirect("/vega", cookies=[ck])

    def po_vega_clear(self):
        sid, ck = self.vega_sid()
        vega_pop_pending(sid)
        vega_save(sid, [])
        self.redirect("/vega", cookies=[ck])

# ================================= Запуск ====================================
def main():
    ensure_dirs()
    if LLM_URL and LLM_STYLE == "openai" and not LLM_MODEL:
        m = vega_pick_model()
        if m:
            print("Вега: модель автоопределена: %s" % m, flush=True)
        else:
            print("ВНИМАНИЕ: не удалось автоопределить модель — задай LLM_MODEL вручную.",
                  flush=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Мини-портал запущен: http://0.0.0.0:%d" % PORT, flush=True)
    print("Вкладки: Поиск | Загрузки | Книги | Чат | Архив | Вега", flush=True)
    print("Книги: зеркала Flibusta: %s" % ", ".join(FLIBUSTA_HOSTS), flush=True)
    if not LLM_URL:
        print("ВНИМАНИЕ: Вега не настроена (LLM_URL пуст) — см. начало файла.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
