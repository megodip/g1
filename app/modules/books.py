# -*- coding: utf-8 -*-
"""Вкладка «Книги»: поиск и скачивание с Flibusta (зеркала в config).

- GET /books      — форма поиска + результаты (кнопки форматов = скачать)
- GET /books/get  — скачать книгу на сервер -> перенаправляет в «Загрузки»

Поиск: сначала OPDS-каталог (стабильный XML), фолбэк — парсинг HTML-поиска.
"""

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from html import escape

from app import config
from app.core.http import Response
from app.core.router import Route
from app.services import fetcher, storage
from app.ui import components, layout

ATOM = "http://www.w3.org/2005/Atom"


# ============================== Вспомогательное ==============================
def qparam(s):
    return urllib.parse.quote(s or "", safe="")


def fb_fetch(host, path, timeout, max_bytes):
    url = "https://%s%s" % (host, path)
    data, ctype = fetcher.fetch_url(url, timeout, max_bytes)
    if len(data) >= max_bytes:
        raise RuntimeError("файл больше лимита %d МБ" % (max_bytes // (1024 * 1024)))
    return data, ctype


def fb_get(path, timeout, max_bytes, hosts=None):
    """Пробует зеркала по очереди; возвращает (host, data, ctype)."""
    hosts = hosts or config.FLIBUSTA_HOSTS
    last = None
    for host in hosts:
        try:
            data, ctype = fb_fetch(host, path, timeout, max_bytes)
            return host, data, ctype
        except Exception as e:
            last = e
    raise RuntimeError("Зеркала Flibusta не ответили (%s)" % last)


def fmt_sort(formats):
    """Форматы сортируются по BOOK_FMT_ORDER (fb2, epub, ...)."""
    order = {f: i for i, f in enumerate(config.BOOK_FMT_ORDER)}
    return sorted(set(formats), key=lambda f: order.get(f, 99))


# ============================== Поиск ========================================
def fb_search_opds(q):
    """Поиск через OPDS-каталог Flibusta (стабильнее HTML)."""
    host, data, _ = fb_get("/opds/search?searchTerm=" + qparam(q),
                           config.FETCH_TIMEOUT, config.MAX_PAGE_KB * 1024)
    root = ET.fromstring(data)
    res = []
    for entry in root.findall("{%s}entry" % ATOM):
        title = (entry.findtext("{%s}title" % ATOM) or "").strip()
        if not title:
            continue
        authors = []
        for a in entry.findall("{%s}author" % ATOM):
            nm = (a.findtext("{%s}name" % ATOM) or "").strip()
            if nm:
                authors.append(nm)
        formats, book_id = [], 0
        for link in entry.findall("{%s}link" % ATOM):
            href = link.get("href") or ""
            rel = link.get("rel") or ""
            if rel and "acquisition" not in rel:
                continue
            m = re.search(r"/b/(\d+)/(\w+)", href)
            if m:
                book_id = int(m.group(1))
                formats.append(m.group(2).lower())
        if book_id and formats:
            res.append({"title": title, "authors": ", ".join(authors),
                        "id": book_id, "host": host, "fmts": fmt_sort(formats)})
    return res


def fb_search_html(q):
    """Фолбэк: парсим HTML-страницу поиска Flibusta."""
    host, data, ctype = fb_get("/booksearch?ask=" + qparam(q),
                               config.FETCH_TIMEOUT, config.MAX_PAGE_KB * 1024)
    from app.modules.search import AnchorParser
    parser = AnchorParser()
    parser.feed(fetcher.decode_html(data, ctype))
    parser.close()
    res, seen = [], set()
    for href, text in parser.links:
        m = re.match(r"^/b/(\d+)$", (href or "").strip())
        if not m or not text:
            continue
        book_id = int(m.group(1))
        if book_id in seen:
            continue
        seen.add(book_id)
        res.append({"title": text, "authors": "", "id": book_id, "host": host,
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


# ============================== Скачивание ===================================
def books_download(book_id, fmt, fname, host=""):
    """Скачивает книгу в data/downloads, возвращает имя файла."""
    hosts = ([host] if host in config.FLIBUSTA_HOSTS else []) + \
            [h for h in config.FLIBUSTA_HOSTS if h != host]
    path = "/b/%d/%s" % (book_id, fmt)
    last_err = None
    for h in hosts:
        try:
            _, data, ctype = fb_get(path, config.BOOK_DL_TIMEOUT,
                                    config.BOOK_MAX_MB * 1024 * 1024, hosts=[h])
        except Exception as e:
            last_err = e
            continue
        if "text/html" in (ctype or "").lower():
            # Зеркало вернуло страницу (капча/ошибка) вместо файла.
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                         fetcher.decode_html(data[:4000], ctype))).strip()
            last_err = RuntimeError("зеркало %s вернуло страницу вместо файла "
                                    "(капча/ошибка): %s" % (h, txt[:160]))
            continue
        name = storage.safe_name(fname) or ("book_%d" % book_id)
        fn = "%s.%s" % (name, fmt)
        with open(os.path.join(config.DL_DIR, fn), "wb") as f:
            f.write(data)
        return fn
    raise last_err or RuntimeError("не удалось скачать файл книги")


# ============================== Страницы =====================================
def pg_books(req):
    q = req.q("q").strip()
    body = ('<h1>Книги</h1>'
            '<form method="get" action="/books">'
            '<input type="text" name="q" value="%s"> '
            '<input type="submit" value="Искать"></form>'
            '<div class="small">Поиск по Flibusta: книги, ранобэ, фанфики. '
            'Нажми на формат — файл сохранится во вкладку «Загрузки».</div>'
            % escape(q))
    if q:
        body += "<hr><h2>Результаты</h2>"
        results = []
        try:
            results = books_search(q)
            if not results:
                body += '<div class="msg">Ничего не найдено.</div>'
        except Exception as e:
            body += components.error_box("Ошибка поиска: %s" % e)
        for b in results[:15]:
            links = []
            for f in b["fmts"]:
                links.append('[<a href="/books/get?b=%d&amp;fmt=%s&amp;t=%s&amp;a=%s&amp;h=%s">%s</a>]'
                             % (b["id"], f, qparam(b["title"]), qparam(b["authors"]),
                                qparam(b["host"]), f))
            from app.modules.reader import reader_url
            links.append('[<a href="%s">читать</a>]' % escape(reader_url(
                "https://%s/b/%d/read" % (b["host"], b["id"]), 0)))
            auth = ('<br><span class="small">%s</span>' % escape(b["authors"])) \
                if b["authors"] else ""
            body += components.msg_box("<b>%s</b>%s<br>%s"
                                       % (escape(b["title"]), auth, " ".join(links)))
    return layout.page("Книги", "books", body, cache="no-store")


def pg_books_get(req):
    """/books/get — скачивает книгу и уводит в «Загрузки»."""
    try:
        book_id = int(req.q("b", "0"))
    except ValueError:
        book_id = 0
    fmt = req.q("fmt", "fb2").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{1,6}", fmt):
        fmt = "fb2"
    title = req.q("t").strip()
    author = req.q("a").strip()
    host = req.q("h").strip()
    if not book_id:
        return Response.redirect("/books")
    fname = (title + (" - " + author if author else "")).strip()
    try:
        books_download(book_id, fmt, fname, host)
        return Response.redirect("/downloads")
    except Exception as e:
        return layout.page(
            "Книги", "books",
            components.msg_box("<b>Не удалось скачать:</b> %s" % escape(e)) +
            '<div><a href="/books?q=%s">← назад к результатам</a></div>'
            % qparam(title))


ROUTES = [
    Route("GET", "/books", pg_books),
    Route("GET", "/books/get", pg_books_get),
]
