# -*- coding: utf-8 -*-
"""Вкладка «Флибуста»: поиск книг и сохранение ИХ ЧИСТОГО ТЕКСТА в библиотеку.

Форматов (epub/pdf/fb2-файлы) тут больше нет — кнопочный телефон их всё равно
не читает. Вместо «скачать/читать» у книги одна кнопка — «в библиотеку»:

  GET /flibusta        — форма поиска + результаты
  GET /flibusta/save   — скачать fb2 на сервере, вытащить чистый текст
                         и положить книгу в локальную библиотеку

Как добывается чистый текст (по порядку попыток):
1. файл книги (/b/<id>/fb2) — fb2-XML или EPUB (Flibusta под этой ссылкой
   отдаёт и то и другое): автор/название/абзацы берутся из структуры,
   сайт-мусор (меню, алфавит, ссылки) сюда попасть не может;
2. txt (/b/<id>/txt) — если зеркало отдаёт plain text, берём как есть.

Поиск: сначала OPDS-каталог (стабильный XML), фолбэк — парсинг HTML-поиска.
"""

import io
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from html import escape

from app import config
from app.core.http import Response
from app.core.router import Route
from app.services import fetcher, library
from app.ui import components, layout

ATOM = "http://www.w3.org/2005/Atom"

# HTML-сущности в XHTML внутри epub: ElementTree их не знает (нет DTD),
# поэтому заменяем на символы ДО разбора. Неизвестные — на пробел.
ENTITIES = {"nbsp": "\u00a0", "mdash": "\u2014", "ndash": "\u2013",
            "laquo": "«", "raquo": "»", "ldquo": "“", "rdquo": "”",
            "lsquo": "‘", "rsquo": "’", "hellip": "…", "middot": "·",
            "bull": "•", "amp": "&", "lt": "<", "gt": ">", "quot": '"',
            "apos": "'", "copy": "©", "reg": "®", "trade": "™", "shy": ""}


def _clean_entities(xml_text):
    """&nbsp;-подобные сущности -> символы/пробел (иначе ET не распарсит)."""
    def repl(m):
        return ENTITIES.get(m.group(1).lower(), " ")
    return re.sub(r"&([a-zA-Z][a-zA-Z0-9]{1,7});", repl, xml_text)


# Блочные/пропускаемые теги XHTML при извлечении текста из epub
_BLOCK_TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
               "div", "li", "v", "cite", "epigraph", "poem", "stanza",
               "title", "subtitle")
_SKIP_TAGS = ("script", "style", "head", "title", "link", "meta")


def _html_paragraphs(doc):
    """Абзацы из XHTML-документа epub.

    Реальный epub с Flibusta часто вообще без <p>: текст лежит прямо в
    <body>, строки разделены <br/>. Поэтому обходим дерево по порядку:
    блочный тег и <br> завершают абзац, текст собираем из text/tail узлов.
    """
    def walk(node, acc):
        if node.text and node.text.strip():
            acc.append(node.text.strip())
        for child in node:
            tag = fb2_local(child.tag)
            if tag in _SKIP_TAGS:
                continue
            if tag == "br":
                acc.append("\n")
            elif tag in _BLOCK_TAGS:
                acc.append("\n")
                t = " ".join("".join(child.itertext()).split())
                if t:
                    acc.append(t)
                acc.append("\n")
            else:  # inline (span/em/i/b/a/...) — уходим внутрь
                walk(child, acc)
            if child.tail and child.tail.strip():
                acc.append(child.tail.strip())
        return acc

    paras = []
    for el in doc.iter():
        if fb2_local(el.tag) == "body":
            buf = []
            for piece in walk(el, []):
                if piece == "\n":
                    t = " ".join("".join(buf).split())
                    if t:
                        paras.append(t)
                    buf = []
                else:
                    buf.append(piece)
            t = " ".join("".join(buf).split())
            if t:
                paras.append(t)
            break
    return paras


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
        book_id = 0
        for link in entry.findall("{%s}link" % ATOM):
            m = re.search(r"/b/(\d+)/\w+", link.get("href") or "")
            if m:
                book_id = int(m.group(1))
                break
        if book_id:
            res.append({"title": title, "authors": ", ".join(authors),
                        "id": book_id, "host": host})
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
        res.append({"title": text, "authors": "", "id": book_id, "host": host})
    return res


def flibusta_search(q):
    try:
        res = fb_search_opds(q)
        if res:
            return res
    except Exception:
        pass
    return fb_search_html(q)


# ============================== Извлечение текста =============================
FB2 = "http://www.gribuser.ru/xml/fictionbook/2.0"


def fb2_local(tag):
    """Локальное имя тега без namespace: '{ns}p' -> 'p'."""
    return tag.rsplit("}", 1)[-1].lower()


def fb2_extract(data):
    """fb2 (bytes, голый XML) -> (title, author, text).

    Абзацы берём только из <body> (все <p>/<v>/<subtitle>), служебные
    <body name="notes"> пропускаем — так в текст не попадает ничего,
    кроме самой книги.
    """
    root = ET.fromstring(data)

    title, author = "", ""
    ti = root.find("./{http://www.gribuser.ru/xml/fictionbook/2.0}description/"
                   "{http://www.gribuser.ru/xml/fictionbook/2.0}title-info")
    if ti is not None:
        title = (ti.findtext("{http://www.gribuser.ru/xml/fictionbook/2.0}"
                             "book-title") or "").strip()
        names = []
        for au in ti.findall("{http://www.gribuser.ru/xml/fictionbook/2.0}author"):
            parts = [(au.findtext("{http://www.gribuser.ru/xml/fictionbook/2.0}"
                                  + x) or "").strip()
                     for x in ("first-name", "middle-name", "last-name")]
            nm = " ".join(p for p in parts if p)
            if nm:
                names.append(nm)
        author = ", ".join(names)

    paras = []
    for body in root.findall("{http://www.gribuser.ru/xml/fictionbook/2.0}body"):
        if (body.get("name") or "").lower() in ("notes", "comments"):
            continue
        for el in body.iter():
            if fb2_local(el.tag) in ("p", "v", "subtitle"):
                t = " ".join("".join(el.itertext()).split())
                if t:
                    paras.append(t)
    text = "\n".join(paras).strip()
    if len(text) < 200:
        raise RuntimeError("в fb2 не нашлось текста книги")
    return title, author, text


def epub_extract(data):
    """EPUB (bytes, zip с XHTML) -> (title, author, text).

    Flibusta под ссылкой /b/<id>/fb2 часто отдаёт именно epub. Порядок глав
    берём из OPF-spine (manifest id -> href); текст — только абзацы и
    заголовки из XHTML-файлов, вся вёрстка выбрасывается.
    """
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()

    # --- OPF: через container.xml или первый *.opf --------------------------
    opf_path = ""
    try:
        c = ET.fromstring(zf.read("META-INF/container.xml"))
        for el in c.iter():
            if fb2_local(el.tag) == "rootfile" and el.get("full-path"):
                opf_path = el.get("full-path")
                break
    except Exception:
        pass
    if not opf_path:
        opf_path = next((n for n in names if n.lower().endswith(".opf")), "")
    if not opf_path:
        raise RuntimeError("в epub нет OPF-манифеста")

    opf = ET.fromstring(zf.read(opf_path))
    title, author = "", ""
    id2href, spine = {}, []
    for el in opf.iter():
        ln = fb2_local(el.tag)
        if ln == "title" and not title:
            title = (el.text or "").strip()
        elif ln == "creator" and not author:
            author = (el.text or "").strip()
        elif ln == "item" and el.get("id"):
            id2href[el.get("id")] = el.get("href") or ""
        elif ln == "itemref" and el.get("idref"):
            spine.append(el.get("idref"))

    base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    docs = []
    for idref in spine:
        href = urllib.parse.unquote(id2href.get(idref, "")).split("#")[0]
        if href.lower().endswith((".xhtml", ".html", ".htm")):
            docs.append(base + href)
    if not docs:
        docs = sorted(n for n in names
                      if n.lower().endswith((".xhtml", ".html", ".htm")))

    paras = []
    for name in docs:
        try:
            raw = zf.read(name).decode("utf-8", "replace")
            doc = ET.fromstring(_clean_entities(raw).encode("utf-8"))
        except Exception:
            continue
        paras.extend(_html_paragraphs(doc))
    text = "\n".join(paras).strip()
    if len(text) < 200:
        raise RuntimeError("в epub не нашлось текста книги")
    return title, author, text


def extract_book(data):
    """fb2 или epub (bytes) -> (title, author, text).

    PK-магия — это zip: внутри может лежать fb2, тогда парсим fb2,
    иначе считаем epub. Без PK — голый fb2-XML.
    """
    if data[:2] == b"PK":
        zf = zipfile.ZipFile(io.BytesIO(data))
        fb2s = sorted(n for n in zf.namelist() if n.lower().endswith(".fb2"))
        if fb2s:
            return fb2_extract(zf.read(fb2s[0]))
        return epub_extract(data)
    return fb2_extract(data)


def download_clean_text(book_id, host=""):
    """Скачивает книгу и возвращает (title, author, text, source_id).

    Порядок: fb2/epub (структурированный файл) -> txt. HTML-мусор сайта
    в результат попасть не может в принципе.
    """
    hosts = ([host] if host in config.FLIBUSTA_HOSTS else []) + \
            [h for h in config.FLIBUSTA_HOSTS if h != host]

    # --- попытка 1: fb2/epub -------------------------------------------------
    last_err = None
    for h in hosts:
        try:
            _, data, ctype = fb_get("/b/%d/fb2" % book_id, config.FB2_TIMEOUT,
                                    config.FB2_MAX_MB * 1024 * 1024, hosts=[h])
        except Exception as e:
            last_err = e
            continue
        if "text/html" in (ctype or "").lower():
            # зеркало отдало страницу (капча/ошибка) вместо файла
            last_err = RuntimeError("зеркало %s вернуло страницу вместо fb2 "
                                    "(капча/ошибка)" % h)
            continue
        try:
            title, author, text = extract_book(data)
            return title, author, text, "flibusta:%d" % book_id
        except Exception as e:
            last_err = e

    # --- попытка 2: txt --------------------------------------------------------
    for h in hosts:
        try:
            _, data, ctype = fb_get("/b/%d/txt" % book_id, config.FB2_TIMEOUT,
                                    config.FB2_MAX_MB * 1024 * 1024, hosts=[h])
        except Exception as e:
            last_err = e
            continue
        if "text/html" in (ctype or "").lower():
            continue
        text = fetcher.decode_html(data, ctype)
        # бинарник (например, epub под видом txt) выдаёт себя за нулевыми
        # байтами или кучей символов замены — такой «текст» не принимаем
        if "\x00" in text or text.count("\ufffd") * 100 > len(text):
            continue
        text = text.strip()
        if len(text) >= 200 and "<html" not in text[:300].lower():
            return "", "", text, "flibusta:%d" % book_id

    raise last_err or RuntimeError(
        "не удалось получить чистый текст книги (fb2 и txt недоступны)")


# ============================== Страницы =====================================
def pg_flibusta(req):
    q = req.q("q").strip()
    body = ('<h1>Флибуста</h1>'
            '<form method="get" action="/flibusta">'
            '<input type="text" name="q" value="%s"> '
            '<input type="submit" value="Искать"></form>'
            '<div class="small">Поиск по Flibusta. Кнопка «в библиотеку» '
            'сохраняет чистый текст книги в <a href="/library">Библиотеку</a> '
            '— читать можно прямо с телефона.</div>' % escape(q))
    if q:
        body += "<hr><h2>Результаты</h2>"
        results = []
        try:
            results = flibusta_search(q)
            if not results:
                body += '<div class="msg">Ничего не найдено.</div>'
        except Exception as e:
            body += components.error_box("Ошибка поиска: %s" % e)
        for b in results[:15]:
            saved = library.has_source("flibusta:%d" % b["id"])
            action = ('<span class="small">уже в библиотеке — '
                      '<a href="/library/read?b=%s">читать</a></span>' % _q(saved)
                      if saved else
                      '[<a href="/flibusta/save?b=%d&amp;t=%s&amp;a=%s&amp;h=%s">'
                      'в библиотеку</a>]'
                      % (b["id"], qparam(b["title"]), qparam(b["authors"]),
                         qparam(b["host"])))
            auth = ('<br><span class="small">%s</span>' % escape(b["authors"])) \
                if b["authors"] else ""
            body += components.msg_box("<b>%s</b>%s<br>%s"
                                       % (escape(b["title"]), auth, action))
    return layout.page("Флибуста", "flibusta", body, cache="no-store")


def _q(s):
    return urllib.parse.quote(s or "", safe="")


def pg_save(req):
    """/flibusta/save — книга уходит в библиотеку чистым текстом."""
    try:
        book_id = int(req.q("b", "0"))
    except ValueError:
        book_id = 0
    title = req.q("t").strip()
    author = req.q("a").strip()
    host = req.q("h").strip()
    if not book_id:
        return Response.redirect("/flibusta")

    source = "flibusta:%d" % book_id
    existing = library.has_source(source)
    if existing:
        return Response.redirect("/library/read?b=" + _q(existing))

    body = ""
    try:
        fb_title, fb_author, text, _src = download_clean_text(book_id, host)
        # название/автор из OPDS надёжнее, fb2 — запасной вариант
        final_title = title or fb_title or ("Книга %d" % book_id)
        final_author = author or fb_author
        saved_id = library.add(final_title, final_author, text, source)
        return Response.redirect("/library/read?b=" + _q(saved_id))
    except Exception as e:
        body = (components.error_box("Не удалось сохранить книгу: %s" % e) +
                '<div><a href="/flibusta?q=%s">← назад к результатам</a></div>'
                % qparam(title))
    return layout.page("Флибуста", "flibusta", body, cache="no-store")


ROUTES = [
    Route("GET", "/flibusta", pg_flibusta),
    Route("GET", "/flibusta/save", pg_save),
]
