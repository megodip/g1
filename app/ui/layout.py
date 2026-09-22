# -*- coding: utf-8 -*-
"""Каркас страницы: HTML-шаблон, стили и навигация.

Правила UI для кнопочных телефонов (НЕ НАРУШАТЬ):
- Никакого JavaScript. Обновление областей — только <meta http-equiv="refresh">
  внутри iframe (см. app/modules/chat.py).
- Один столбец, максимум простых блочных элементов. Без flexbox/grid/float.
- CSS в одном маленьком блоке; без CSS страница обязана читаться линейно.
- Все формы — обычные method/action, кнопки — <input type="submit">.
- У ссылок навигации проставлен accesskey (на Opera Mini: # + цифра).
- Кодировка UTF-8 объявляется и в заголовке ответа, и в <meta>.
"""

from html import escape

from app import config

# Компактная таблица стилей: чёрный текст на белом, высокий контраст,
# читаемо даже на монохромных и маленьких экранах.
CSS = """body{background:#fff;color:#000;margin:3px auto;max-width:480px;
font:14px/1.4 Arial,sans-serif}
.nav{border-bottom:1px solid #888;margin:0 0 5px;padding:0 0 4px}
.nav a,.nav b{margin-right:3px;white-space:nowrap}
a{color:#0046b0}
h1{font-size:16px;margin:3px 0 5px}
h2{font-size:14px;margin:8px 0 3px}
.small{color:#555;font-size:11px}
.msg{border:1px solid #bbb;background:#f7f7f7;margin:2px 0;padding:3px}
.err{color:#a00;border:1px solid #a00;padding:3px;margin:3px 0}
pre{white-space:pre-wrap;background:#f0f0f0;padding:4px;margin:3px 0}
input[type=text],input[type=file],textarea,select{width:97%;font:14px Arial;margin:2px 0}
input[type=submit]{font:14px Arial;margin:2px 0}
textarea{height:120px}
img{max-width:100%}
hr{border:0;border-top:1px solid #aaa;margin:6px 0}"""


def nav_html(active):
    """Строка навигации с номерами: 1.Поиск 2.Загрузки ..."""
    parts = []
    for num, (key, name) in enumerate(config.TABS, 1):
        if key == active:
            parts.append("<b>%d.%s</b>" % (num, escape(name)))
        else:
            parts.append('<a href="/%s" accesskey="%d">%d.%s</a>'
                         % (key, num, num, escape(name)))
    return '<div class="nav">%s</div>' % " ".join(parts)


def _shell(title, body, refresh=0):
    """Общий HTML-каркас (используется page и raw_page)."""
    meta_refresh = ('<meta http-equiv="refresh" content="%d">' % refresh) if refresh else ""
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset=\"utf-8\">%(meta)s\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<title>%(title)s</title>\n"
        "<style>%(css)s</style></head><body>\n%(body)s\n</body></html>"
    ) % {"meta": meta_refresh, "title": escape(title), "css": CSS, "body": body}


def page(title, active, body, refresh=0, cache=None, status=200, cookies=()):
    """Обычная страница портала: навигация + тело. Это ответ модуля."""
    from app.core.http import Response
    return Response.html(_shell(title, nav_html(active) + body, refresh),
                         status=status, cookies=cookies, cache=cache)


def raw_page(title, body, refresh=0, cache=None, cookies=()):
    """Страница без навигации — для содержимого внутри iframe (чат)."""
    from app.core.http import Response
    return Response.html(_shell(title, body, refresh), cookies=cookies, cache=cache)
