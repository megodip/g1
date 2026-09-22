# -*- coding: utf-8 -*-
"""Вкладка «Чат» — обмен сообщениями без JavaScript.

ГЛАВНОЕ: как устроено обновление переписки (не ломая ввод текста).

Старый вариант перезагружал всю страницу <meta refresh> каждые 7 секунд —
из-за этого сбрасывался ввод. Здесь механика из wapchat:

  /chat          — «рамка» с полем ввода. НИКОГДА не перезагружается сама.
  /chat/frame    — страница сообщений, живёт внутри <iframe src="/chat/frame">.
                   У НЕЁ одной стоит <meta http-equiv="refresh content=N"> —
                   значит, каждые N секунд перезагружается ТОЛЬКО область
                   сообщений, а поле ввода не трогается.
  /chat/send     — POST-приёмник сообщения. Форма на /chat имеет
                   target="chat_frame", поэтому ответ уходит в iframe:
                   основная страница не перезагружается и текст не сбрасывается.

Надёжность на старых телефонах:
- Ник передаётся и в query (?nick=...), и в куке. Часть старых браузеров не
  шлёт куки для iframe — тогда работает параметр в URL (как token в wapchat).
- Для браузеров БЕЗ поддержки iframe есть /chat/simple — полная страница с
  ручным обновлением (без meta refresh, чтобы не ломать ввод).
- Ссылки «Обновить» с target="chat_frame" перезагружают только iframe.
"""

import hashlib
import re
import time
import urllib.parse
from html import escape

from app import config
from app.core.http import Response, make_cookie, del_cookie
from app.core.router import Route
from app.services import storage
from app.ui import components, layout

NICK_RE = re.compile(config.NICK_RE_TEXT, re.UNICODE)


# ============================== Служебное ====================================
def valid_nick(nick):
    return bool(nick) and bool(NICK_RE.fullmatch(nick))


def get_nick(req):
    """Ник из query (?nick=) или куки. Пустая строка, если ник не определён."""
    nick = req.q("nick") or req.cookie("nick")
    return nick if valid_nick(nick) else ""


def nick_color(nick):
    """Стабильный ТЁМНЫЙ цвет ника на белом фоне (как в wapchat)."""
    digest = hashlib.md5(nick.encode("utf-8")).digest()
    r, g, b = digest[0] % 150, digest[1] % 150, digest[2] % 150
    if r + g + b < 100:  # не даём цвету стать слишком тёмным
        r, g, b = r + 50, g + 50, b + 50
    return "#%02X%02X%02X" % (r, g, b)


def fmt_time(ts):
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


def messages_page(all_messages, page):
    """Нарезает историю на страницы. Страница 1 — самые свежие.

    Возвращает (сообщения_страницы_в_хронологии, current, total_pages).
    Порядок отображения (новые сверху) обеспечивает render_messages.
    """
    size = config.CHAT_PAGE_SIZE
    total_pages = max(1, (len(all_messages) + size - 1) // size)
    page = max(1, min(page, total_pages))
    end = len(all_messages) - (page - 1) * size
    start = max(0, end - size)
    return all_messages[start:end], page, total_pages


def render_messages(messages):
    """HTML-блок сообщений. НОВЫЕ СВЕРХУ: список приходит в хронологии,
    при выводе переворачиваем, чтобы последнее сообщение было первым."""
    if not messages:
        return '<div class="small">Сообщений пока нет.</div>'
    return "".join(
        components.chat_row(m.get("n", "?"), m.get("x", ""), fmt_time(m.get("t", 0)),
                            nick_color(m.get("n", "?")))
        for m in reversed(messages))


# ============================== Страницы =====================================
def pg_chat(req):
    """/chat — основная страница (ввод + iframe с сообщениями)."""
    if req.q("logout") == "1":
        return Response.redirect("/chat", cookies=(del_cookie("nick"),))

    nick = get_nick(req)

    # --- нет ника -> форма входа ------------------------------------------
    if not nick:
        body = ('<h1>Чат</h1>'
                '<form method="post" action="/chat">'
                '<input type="hidden" name="action" value="nick">'
                'Ник: <input type="text" name="n" maxlength="20"> '
                '<input type="submit" value="Войти в чат"></form>'
                '<div class="small">Ник: 2–20 символов (буквы, цифры, пробел, дефис).</div>')
        return layout.page("Чат", "chat", body, cache="no-store")

    nq = urllib.parse.quote(nick)

    # --- основная страница: iframe + форма с target -------------------------
    body = (
        '<h1>Общий чат</h1>'
        '<iframe src="/chat/frame?nick=%(nq)s" name="chat_frame" '
        'width="100%%" height="200">'
        'Ваш браузер не показывает фреймы. '
        '<a href="/chat/simple?nick=%(nq)s">Открыть чат целиком</a>'
        '</iframe>'
        '<form method="post" action="/chat/send?nick=%(nq)s" target="chat_frame">'
        '<input type="text" name="x" maxlength="%(maxlen)d"> '
        '<input type="submit" value="&gt;&gt;"></form>'
        '<div class="small">'
        '<a href="/chat/frame?nick=%(nq)s" target="chat_frame">Обновить</a> · '
        '<a href="/chat/simple?nick=%(nq)s">без фреймов</a> · '
        '<a href="/chat?logout=1">сменить ник</a><br>'
        'Вы как <b>%(nick)s</b> · область сообщений обновляется сама каждые '
        '%(refresh)d сек, поле ввода не сбрасывается.</div>'
        % {"nq": nq, "nick": escape(nick), "maxlen": config.CHAT_MAX_LEN,
           "refresh": config.CHAT_REFRESH_SEC})
    return layout.page("Чат", "chat", body, cache="no-store")


def pg_frame(req):
    """/chat/frame — содержимое iframe: только сообщения + пагинация."""
    nick = get_nick(req)
    try:
        page_no = int(req.q("page", "1"))
    except ValueError:
        page_no = 1

    msgs, page_no, total_pages = messages_page(storage.chat_load(), page_no)
    nq = urllib.parse.quote(nick)
    extra = "&nick=" + nq if nick else ""

    body = (
        components.pager("/chat/frame", page_no, total_pages, extra)
        + render_messages(msgs)
        + components.pager("/chat/frame", page_no, total_pages, extra)
    )
    # meta refresh перезагружает ТОЛЬКО эту страницу (внутри iframe),
    # сохраняя текущую страницу пагинации.
    return layout.raw_page(
        "Сообщения", body,
        refresh=config.CHAT_REFRESH_SEC, cache="no-store")


def pg_simple(req):
    """/chat/simple — версия без iframe для совсем старых браузеров.

    Без автообновления: meta refresh здесь ломал бы ввод. Обновление —
    вручную по ссылке (или после отправки сообщения).
    """
    nick = get_nick(req)
    # последние N сообщений; render_messages сам поставит новые сверху
    msgs = storage.chat_load()[-config.CHAT_SIMPLE_COUNT:]

    # Ссылки-хвосты с ником (или без него, если ник ещё не введён).
    suffix = "?nick=" + urllib.parse.quote(nick) if nick else ""
    body = ('<h1>Общий чат</h1>'
            '<div class="small"><a href="/chat%s">версия с автообновлением</a> · '
            '<a href="/chat/simple%s">обновить</a></div><hr>'
            % (suffix, suffix))
    body += render_messages(msgs)

    if nick:
        nq = urllib.parse.quote(nick)
        body += ('<hr><form method="post" action="/chat/send?nick=%s">'
                 '<input type="hidden" name="back" value="simple">'
                 '<input type="text" name="x" maxlength="%d"> '
                 '<input type="submit" value="&gt;&gt;"></form>'
                 '<div class="small">Вы как <b>%s</b> · автообновления нет — '
                 'жмите «обновить».</div>'
                 % (nq, config.CHAT_MAX_LEN, escape(nick)))
    else:
        body += ('<hr><div class="small"><a href="/chat">Сначала введите ник</a>.</div>')
    return layout.page("Чат", "chat", body, cache="no-store")


# ============================== POST-обработчики =============================
def po_chat(req):
    """/chat POST — вход по нику."""
    if req.form.get("action") == "nick":
        nick = (req.form.get("n") or "").strip()
        if not valid_nick(nick):
            return layout.page(
                "Чат", "chat",
                'Ник: 2–20 символов (буквы, цифры, пробел, дефис). '
                '<a href="/chat">Назад</a>')
        # Ник и в куке, и в URL — работает на телефонах без куков.
        return Response.redirect("/chat?nick=" + urllib.parse.quote(nick),
                                 cookies=(make_cookie("nick", nick),))
    return Response.redirect("/chat")


def po_send(req):
    """/chat/send POST — сохранить сообщение и вернуться в область переписки.

    Ответ уходит в iframe (form target="chat_frame"), поэтому основная
    страница с полем ввода НЕ перезагружается.
    """
    nick = get_nick(req)
    if not nick:
        return Response.redirect("/chat")

    text = (req.form.get("x") or "").strip()
    if text:
        storage.chat_append(nick, text[:config.CHAT_MAX_LEN])

    if req.form.get("back") == "simple":
        return Response.redirect("/chat/simple?nick=" + urllib.parse.quote(nick))
    # Страница 1 = самые свежие; внутри страницы новые сверху,
    # поэтому только что отправленное видно первым.
    return Response.redirect("/chat/frame?page=1&nick=" + urllib.parse.quote(nick))


ROUTES = [
    Route("GET", "/chat", pg_chat),
    Route("POST", "/chat", po_chat),
    Route("GET", "/chat/frame", pg_frame),
    Route("GET", "/chat/simple", pg_simple),
    Route("POST", "/chat/send", po_send),
]
