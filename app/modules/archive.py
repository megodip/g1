# -*- coding: utf-8 -*-
"""Вкладка «Архив»: текстовые заметки в data/archive.

- GET  /archive       — список записей + форма создания
- GET  /archive/view  — просмотр/редактирование записи
- GET  /archive/raw   — скачать запись файлом
- POST /archive/save  — создать/обновить запись
- POST /archive/del   — удалить запись
"""

import os
import time
import urllib.parse
from html import escape

from app import config
from app.core.http import Response
from app.core.router import Route
from app.services import storage
from app.ui import components, layout


def archive_path(name):
    return os.path.join(config.ARCHIVE_DIR, name)


# ============================== Страницы =====================================
def pg_archive(req):
    rows = []
    for name in storage.list_dir(config.ARCHIVE_DIR):
        path = archive_path(name)
        rows.append(components.msg_box(
            "<b>%s</b> <span class=\"small\">%s · %s</span><br>"
            "<a href=\"/archive/view?n=%s\">открыть</a> · "
            "<a href=\"/archive/raw?n=%s\">скачать</a> · "
            "<form method=\"post\" action=\"/archive/del\">"
            "<input type=\"hidden\" name=\"n\" value=\"%s\">"
            "<input type=\"submit\" value=\"Удалить\"></form>"
            % (escape(name), storage.fmt_size(os.path.getsize(path)),
               time.strftime("%d.%m.%Y %H:%M", time.localtime(os.path.getmtime(path))),
               urllib.parse.quote(name), urllib.parse.quote(name), escape(name))))
    if not rows:
        rows.append('<div class="small">Архив пуст.</div>')
    body = ('<h1>Архив</h1>' + "".join(rows) + '<hr><h2>Новая запись</h2>'
            '<form method="post" action="/archive/save">Название: '
            '<input type="text" name="n" maxlength="60">Текст: '
            '<textarea name="x"></textarea>'
            '<input type="submit" value="Сохранить"></form>')
    return layout.page("Архив", "archive", body)


def pg_view(req):
    name = storage.safe_name(req.q("n"))
    path = archive_path(name)
    if not name or not os.path.isfile(path):
        return layout.page("404", "",
                           'Запись не найдена. <a href="/archive">Назад</a>',
                           status=404)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    body = ('<h2>%s</h2><form method="post" action="/archive/save">'
            '<input type="hidden" name="n" value="%s">'
            '<textarea name="x">%s</textarea>'
            '<input type="submit" value="Сохранить изменения"></form>'
            '<div class="small"><a href="/archive">← к архиву</a> · '
            '<a href="/archive/raw?n=%s">скачать</a></div>'
            % (escape(name), escape(name), escape(text), urllib.parse.quote(name)))
    return layout.page(name, "archive", body)


def pg_raw(req):
    """/archive/raw?n=... — скачать запись файлом."""
    name = storage.safe_name(req.q("n"))
    path = archive_path(name)
    if not name or not os.path.isfile(path):
        return layout.page("404", "", "Не найдено", status=404)
    return Response.file(path, name)


# ============================== POST-обработчики =============================
def po_save(req):
    name = storage.safe_name(req.form.get("n", ""))
    if not name:
        return Response.redirect("/archive")
    if not name.lower().endswith(".txt"):
        name += ".txt"
    with open(archive_path(name), "w", encoding="utf-8") as fh:
        fh.write((req.form.get("x") or "")[:200000])
    return Response.redirect("/archive")


def po_delete(req):
    name = storage.safe_name(req.form.get("n", ""))
    if name:
        try:
            os.remove(archive_path(name))
        except OSError:
            pass
    return Response.redirect("/archive")


ROUTES = [
    Route("GET", "/archive", pg_archive),
    Route("GET", "/archive/view", pg_view),
    Route("GET", "/archive/raw", pg_raw),
    Route("POST", "/archive/save", po_save),
    Route("POST", "/archive/del", po_delete),
]
