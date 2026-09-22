# -*- coding: utf-8 -*-
"""Переиспользуемые куски HTML: блоки сообщений, пагинация, оформление.

Все функции возвращают ГОТОВЫЙ к вставке HTML (внутренности уже экранированы
самими функциями — параметры принимаются «сырыми»).
"""

from html import escape


def nl2br(text):
    """Переносы строк -> <br> (после escape)."""
    return escape(text).replace("\n", "<br>")


def msg_box(body_html):
    """Серый блок с рамкой — универсальный контейнер (сообщение, заметка, файл)."""
    return '<div class="msg">%s</div>' % body_html


def error_box(text):
    """Красный блок ошибки. text — сырой текст (экранируется)."""
    return '<div class="err">%s</div>' % escape(text)


def small_note(text):
    """Мелкая серая подсказка. text — сырой текст (экранируется)."""
    return '<div class="small">%s</div>' % escape(text)


def chat_row(nick, text, time_str, nick_color="#000"):
    """Одно сообщение чата: ник (свой цвет) + время + текст с переносами."""
    return (
        '<div class="msg"><b style="color:%s">%s</b>'
        ' <span class="small">%s</span><br>%s</div>'
    ) % (nick_color, escape(nick), escape(time_str), nl2br(text))


def pager(base_url, current, total, extra_qs=""):
    """Строка пагинации: [← новее] стр. 1/7 [старее →].

    base_url  — например "/chat/frame"
    extra_qs  — уже ЗАКОДИРОВАНный хвост вида "&nick=..." (или "")
    """
    sep = "&amp;" if "?" in base_url else "?"
    left = ('<a href="%s%s%s=%d">%s</a>'
            % (base_url, sep, extra_qs, current - 1, "← новее")
            if current > 1 else "← новее")
    right = ('<a href="%s%s%s=%d">%s</a>'
             % (base_url, sep, extra_qs, current + 1, "старее →")
             if current < total else "старее →")
    return ('<div class="small">%s · стр. %d/%d · %s · '
            '<a href="%s%s%s=%d">обновить</a></div>'
            % (left, current, total, right,
               base_url, sep, extra_qs, current))
