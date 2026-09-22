# -*- coding: utf-8 -*-
"""Вкладка «Загрузки»: файлы в data/downloads.

- GET  /downloads  — список файлов + форма закачки с телефона
- GET  /dl?n=...   — скачать файл на телефон
- POST /downloads  — закачать файл на сервер (multipart/form-data)
"""

import os
import urllib.parse
from html import escape

from app import config
from app.core.http import Response
from app.core.router import Route
from app.services import storage
from app.ui import components, layout


def pg_downloads(req):
    rows = []
    for name in storage.list_dir(config.DL_DIR):
        path = os.path.join(config.DL_DIR, name)
        rows.append(components.msg_box(
            "<b>%s</b> <span class=\"small\">%s</span><br>"
            "<a href=\"/dl?n=%s\">Скачать на телефон</a>"
            % (escape(name), storage.fmt_size(os.path.getsize(path)),
               urllib.parse.quote(name))))
    if not rows:
        rows.append('<div class="small">Папка пуста. Файлы можно положить на сервер '
                    'в data/downloads, закачать с телефона формой ниже, найти во '
                    'вкладке «Книги» или поручить Веге.</div>')
    body = ('<h1>Загрузки</h1>' + "".join(rows) + '<hr>'
            '<h2>Закачать с телефона</h2>'
            '<form method="post" action="/downloads" enctype="multipart/form-data">'
            '<input type="file" name="f"> <input type="submit" value="Загрузить на сервер"></form>'
            '<div class="small">Если телефон не умеет выбирать файлы — закидывай по SSH/SFTP.</div>')
    return layout.page("Загрузки", "downloads", body)


def pg_dl(req):
    """/dl?n=имя — отдаёт файл из data/downloads как attachment."""
    name = storage.safe_name(req.q("n"))
    path = os.path.join(config.DL_DIR, name)
    if not name or not os.path.isfile(path):
        return layout.page("404", "",
                           'Файл не найден. <a href="/downloads">Назад</a>', status=404)
    return Response.file(path, name)


def po_upload(req):
    f = req.form.get("f")
    if isinstance(f, dict) and f.get("filename"):
        name = storage.safe_name(f["filename"]) or "file.bin"
        with open(os.path.join(config.DL_DIR, name), "wb") as fh:
            fh.write(f["data"])
    return Response.redirect("/downloads")


ROUTES = [
    Route("GET", "/downloads", pg_downloads),
    Route("GET", "/dl", pg_dl),
    Route("POST", "/downloads", po_upload),
]
