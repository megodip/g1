# -*- coding: utf-8 -*-
"""Вкладка «Библиотека»: локальные книги чистым текстом, с поиском.

Книги попадают сюда из вкладки «Флибуста» (сохранение чистого текста)
или руками на сервере (файлы data/library/<id>.txt, см. services/library.py).

- GET  /library        — список книг + поиск по названию/автору
- GET  /library/read   — чтение книги постранично (?b=<id>&page=N)
- POST /library/del    — удалить книгу

Текст разбивается на страницы по абзацам (LIBRARY_PAGE_CHARS знаков),
чтобы страница влезала в экран и медленный канал кнопочного телефона.
"""

import urllib.parse
from html import escape

from app import config
from app.core.http import Response
from app.core.router import Route
from app.services import library
from app.ui import components, layout


def _q(s):
    return urllib.parse.quote(s or "", safe="")


# ============================== Пагинация =====================================
def paginate(text, size):
    """Режет текст на страницы по абзацам, не рвя предложения/абзацы.

    Возвращает список страниц (каждая — строка с \n между абзацами).
    """
    pages, cur, cur_len = [], [], 0
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        if cur and cur_len + len(para) > size:
            pages.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(para)
        cur_len += len(para) + 1
    if cur:
        pages.append("\n".join(cur))
    return pages or [""]


def _pager(base, page_no, total, book_id):
    """Строка «вперёд/назад» для читалки (b= — id книги)."""
    tail = "&amp;b=" + _q(book_id)
    left = ('<a href="%s?page=%d%s">← назад</a>' % (base, page_no - 1, tail)
            if page_no > 1 else "← назад")
    right = ('<a href="%s?page=%d%s">вперёд →</a>' % (base, page_no + 1, tail)
             if page_no < total else "вперёд →")
    return ('<div class="small">%s · стр. %d/%d · <a href="%s?page=1%s">в начало</a>'
            ' · <a href="/library">к списку</a></div>'
            % (left, page_no, total, base, tail))


# ============================== Страницы ======================================
def pg_library(req):
    q = req.q("q").strip()
    cards = library.search(q)
    body = ('<h1>Библиотека</h1>'
            '<form method="get" action="/library">'
            '<input type="text" name="q" value="%s"> '
            '<input type="submit" value="Найти"></form>'
            '<div class="small">Книги в памяти сервера. Пополняется из вкладки '
            '<a href="/flibusta">Флибуста</a> — там кнопка «в библиотеку» '
            'сохраняет чистый текст.</div>' % escape(q))
    if not cards:
        body += '<div class="msg">Библиотека пуста%s.</div>' % (
            "" if not q else " — по запросу ничего нет")
    else:
        body += "<hr><h2>%s</h2>" % (
            "Найдено: %d" % len(cards) if q else "Все книги")
        for c in cards[:50]:
            author = ('<br><span class="small">%s</span>' % escape(c["author"])) \
                if c.get("author") else ""
            body += components.msg_box(
                "<b>%s</b>%s<br><span class=\"small\">%s знаков</span><br>"
                '<a href="/library/read?b=%s">читать</a> · '
                '<form method="post" action="/library/del">'
                '<input type="hidden" name="b" value="%s">'
                '<input type="submit" value="Удалить"></form>'
                % (escape(c["title"]), author, c.get("chars", 0),
                   _q(c["id"]), escape(c["id"])))
    return layout.page("Библиотека", "library", body, cache="no-store")


def pg_read(req):
    """/library/read?b=<id>&page=N — страница книги."""
    card = library.get(req.q("b"))
    if not card:
        return layout.page("404", "library",
                           'Книга не найдена. <a href="/library">В библиотеку</a>',
                           status=404)

    pages = paginate(library.load_text(card["id"]), config.LIBRARY_PAGE_CHARS)
    try:
        page_no = int(req.q("page", "1"))
    except ValueError:
        page_no = 1
    page_no = max(1, min(page_no, len(pages)))

    head = ("<h2>%s</h2><div class=\"small\">%s</div><hr>" % (
        escape(card["title"]),
        escape(card.get("author", "")) or "&nbsp;"))
    body = (_pager("/library/read", page_no, len(pages), card["id"])
            + head
            + components.nl2br(pages[page_no - 1])
            + "<hr>" + _pager("/library/read", page_no, len(pages), card["id"]))
    return layout.page(card["title"][:60], "library", body, cache="no-store")


# ============================== POST-обработчики ==============================
def po_delete(req):
    library.delete(req.form.get("b", ""))
    return Response.redirect("/library")


ROUTES = [
    Route("GET", "/library", pg_library),
    Route("GET", "/library/read", pg_read),
    Route("POST", "/library/del", po_delete),
]
