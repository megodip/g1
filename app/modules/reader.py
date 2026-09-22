# -*- coding: utf-8 -*-
"""Прокси-читалка (/reader): отдаёт любую веб-страницу в упрощённом HTML.

Зачем: на телефоне нет JS и часто нет быстрого интернета, поэтому сервер
скачивает страницу сам, вырезает скрипты/меню и отдаёт чистый текст с
перекодированными ссылками (все ссылки снова идут через /reader).
Картинки по желанию прогоняются через /img (прокси).
"""

import re
import urllib.parse
from html import escape
from html.parser import HTMLParser

from app.core.http import Response
from app.core.router import Route
from app.services import fetcher
from app.ui import components, layout
from app import config


# ============================== Извлечение текста =============================
class Extractor(HTMLParser):
    """Вырезает из HTML читаемый текст, сохраняя ссылки и (опционально) картинки."""

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

    def abs_url(self, href):
        href = (href or "").strip()
        if href.startswith("data:"):
            return ""
        return urllib.parse.urljoin(self.base, href)

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
                url = self.abs_url(src)
                if url.startswith("http"):
                    if self.imgs:
                        self.out.append(' <img src="%s" alt="%s"> '
                                        % (escape(img_proxy_url(url)), escape(alt)))
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
        text = data.strip()
        if not text:
            return
        if self.href and self.href.startswith("http"):
            self.out.append('<a href="%s">%s</a> '
                            % (escape(reader_url(self.href, self.imgs)), escape(text)))
        else:
            self.out.append(escape(text) + " ")


# ============================== Построение ссылок =============================
def reader_url(url, imgs):
    return "/reader?url=" + urllib.parse.quote(url, safe="") + \
        ("&imgs=1" if imgs else "&imgs=0")


def img_proxy_url(url):
    return "/img?url=" + urllib.parse.quote(url, safe="")


# ============================== Страницы =====================================
def pg_reader(req):
    """/reader?url=... — страница чужого сайта в упрощённом виде."""
    url = req.q("url")
    imgs = req.q("imgs", "1") == "1"

    if not url.startswith(("http://", "https://")):
        return layout.page(
            "Читалка", "search",
            'Нужен адрес http(s)://... <a href="/search">Назад</a>', cache="no-store")

    try:
        data, ctype = fetcher.fetch_url(url, config.FETCH_TIMEOUT,
                                        config.MAX_PAGE_KB * 1024)
    except Exception as e:
        return layout.page(
            "Читалка", "search",
            "Не удалось загрузить страницу: %s<br><a href=\"/search\">Назад к поиску</a>"
            % escape(e), cache="no-store")

    raw = fetcher.decode_html(data, ctype)
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else url

    ex = Extractor(url, imgs)
    ex.feed(raw)
    ex.close()
    content = "".join(ex.out).strip() or "(не удалось извлечь текст)"

    toggle = '<a href="%s">%s</a>' % (escape(reader_url(url, not imgs)),
                                      "только текст" if imgs else "с картинками")
    top = ('<div class="small">Источник: %s<br>Режим: %s · '
           '<a href="/search">← к поиску</a></div><hr>'
           % (escape(url[:100]), toggle))
    return layout.page(title, "search", top + components.nl2br(content),
                       cache="no-store")


def pg_img(req):
    """/img?url=... — прокси картинок: телефон качает их с нашего сервера."""
    url = req.q("url")
    if url.startswith(("http://", "https://")):
        try:
            data, ctype = fetcher.fetch_url(url, 15, 3 * 1024 * 1024)
            if not ctype.startswith("image/"):
                ctype = sniff_image(data)
            if ctype:
                return Response.binary(data, ctype, cache="public, max-age=3600")
        except Exception:
            pass
    return Response.binary(b"not found", "text/plain", status=404)


def sniff_image(data):
    """Определяет тип картинки по магическим байтам."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


ROUTES = [
    Route("GET", "/reader", pg_reader),
    Route("GET", "/img", pg_img),
]
