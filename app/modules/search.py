# -*- coding: utf-8 -*-
"""Вкладка «Поиск»: Google с фолбэком на DuckDuckGo (html-версии без JS).

Результаты ведут в прокси-читалку /reader (modules/reader.py), чтобы страницы
открывались прямо на телефоне. Здесь же маршрут "/" — редирект на поиск.
"""

import re
import urllib.parse
from html import escape
from html.parser import HTMLParser

from app.core.http import Response
from app.core.router import Route
from app.services import fetcher
from app.ui import layout
from app import config

FETCH_TIMEOUT = config.FETCH_TIMEOUT
MAX_PAGE_KB = config.MAX_PAGE_KB

# Фрагменты URL, которые не показываем в результатах (служебные ссылки поисковиков).
SKIP_FRAGMENTS = ("google.", "gstatic.", "googleusercontent", "accounts.",
                  "policies.", "support.google", "webcache.", "translate.",
                  "/search?", "/preferences", "duckduckgo.com", "yandex.",
                  "docs.google", "drive.google", "play.google", "books.google")


# ============================ Разбор ссылок ==================================
class AnchorParser(HTMLParser):
    """Собирает все <a href="...">текст</a> со страницы."""

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

    def handle_data(self, data):
        if self.href is not None:
            self.buf.append(data)


def clean_results(links):
    """Фильтрует и распутывает редиректы поисковиков, оставляет первые 15."""
    out, seen = [], set()
    for href, text in links:
        if not href:
            continue
        if href.startswith("/url?"):                    # редирект Google
            p = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = p.get("q", p.get("url", [href]))[0]
        if href.startswith("//"):
            href = "https:" + href
        if "uddg=" in href:                             # редирект DDG
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
    data, ctype = fetcher.fetch_url(url, FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
    parser = AnchorParser()
    parser.feed(fetcher.decode_html(data, ctype))
    parser.close()
    res = clean_results(parser.links)
    if not res:
        raise RuntimeError("Google не дал результатов")
    return res


def ddg_search(q):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    data, ctype = fetcher.fetch_url(url, FETCH_TIMEOUT, MAX_PAGE_KB * 1024)
    parser = AnchorParser()
    parser.feed(fetcher.decode_html(data, ctype))
    parser.close()
    return clean_results(parser.links)


def web_search(q):
    """Google -> фолбэк DuckDuckGo. Возвращает (список, название движка)."""
    try:
        r = google_search(q)
        if r:
            return r, "Google"
    except Exception:
        pass
    return ddg_search(q), "DuckDuckGo"


def reader_url(url, imgs):
    """Ссылка на страницу результата через прокси-читалку."""
    return "/reader?url=" + urllib.parse.quote(url, safe="") + \
        ("&imgs=1" if imgs else "&imgs=0")


# ============================== Страницы =====================================
def pg_home(req):
    """/ -> на главную вкладку."""
    return Response.redirect("/search")


def pg_search(req):
    q = req.q("q").strip()
    imgs = req.q("imgs", "1") == "1"
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
            results, engine = web_search(q)
        except Exception as e:
            results, engine = None, ""
            body += '<div class="err">Ошибка поиска: %s</div>' % escape(e)
        if results is not None:
            if not results:
                body += '<div class="msg">Ничего не найдено.</div>'
            else:
                body += "<ol>" + "".join(
                    '<li><a href="%s">%s</a><br><span class="small">%s</span></li>'
                    % (escape(reader_url(href, imgs)), escape(text[:120]),
                       escape(href[:100]))
                    for href, text in results) + "</ol>"
                body += ('<div class="small">Источник: %s. '
                         'Страницы открываются без JavaScript.</div>' % engine)
    return layout.page("Поиск", "search", body, cache="no-store")


ROUTES = [
    Route("GET", "/", pg_home),
    Route("GET", "/search", pg_search),
]
