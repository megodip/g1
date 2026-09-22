# -*- coding: utf-8 -*-
"""Вкладка «Вега»: ИИ-агент с доступом к shell сервера.

Диалог хранится в data/vega_<sid>.json, sid — в куке И в URL (?sid=...):
часть старых браузеров не шлёт куки, поэтому параметр дублируется в каждой
ссылке (тот же приём, что и с ником в чате).

Протокол агента (см. services/llm.py):
- модель предлагает shell-команду меткой @@RUN в конце ответа;
- при VEGA_CONFIRM команда ждёт подтверждения пользователя (data/*.pending);
- после выполнения модель получает вывод и продолжает работу.
"""

import json
import os
import re
import uuid
from html import escape

from app import config
from app.core.http import Response, make_cookie
from app.core.router import Route
from app.services import llm, storage
from app.ui import components, layout


# ============================== История диалога ==============================
def _sid_safe(sid):
    return re.sub(r"\W", "", sid)


def hist_path(sid):
    return os.path.join(config.DATA_DIR, "vega_%s.json" % _sid_safe(sid))


def hist_load(sid):
    try:
        with open(hist_path(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def hist_save(sid, history):
    with storage.LOCK, open(hist_path(sid), "w", encoding="utf-8") as f:
        json.dump(history[-60:], f, ensure_ascii=False)


# ============================== Ожидающие команды ============================
def pending_path(sid):
    return os.path.join(config.DATA_DIR, "vega_%s.pending" % _sid_safe(sid))


def pending_get(sid):
    try:
        with open(pending_path(sid), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def pending_save(sid, cmd):
    with open(pending_path(sid), "w", encoding="utf-8") as f:
        f.write(cmd)


def pending_pop(sid):
    cmd = pending_get(sid)
    try:
        os.remove(pending_path(sid))
    except Exception:
        pass
    return cmd


# ============================== Шаг диалога ==================================
def vega_step(sid, max_iters=4):
    """Диалог с моделью: пока Вега просит команды — выполняем (или ставим
    на подтверждение) и кормим её выводом, максимум max_iters шагов."""
    history = hist_load(sid)
    for _ in range(max_iters):
        try:
            reply = llm.chat(history)
        except Exception as e:
            history.append({"role": "system",
                            "content": "Ошибка связи с моделью: %s" % e})
            break
        history.append({"role": "assistant", "content": reply})
        m = re.search(r"@@RUN[ \t]+(.+)", reply)
        if not m:
            break
        cmd = m.group(1).strip()[:500]
        if config.VEGA_CONFIRM:
            pending_save(sid, cmd)   # ждём подтверждения на /vega
            break
        history.append({"role": "system",
                        "content": "Вывод команды «%s»:\n%s" % (cmd, llm.run_cmd(cmd))})
    hist_save(sid, history)


# ============================== Сессия =======================================
def get_sid(req):
    """sid из URL (?sid=) -> куки -> новый. Возвращает (sid, cookies_to_set)."""
    sid = req.q("sid")
    if sid and re.fullmatch(r"[0-9a-f]{8,64}", sid):
        return sid, ()
    sid = req.cookie("vsid")
    if sid and re.fullmatch(r"[0-9a-f]{8,64}", sid):
        return sid, ()
    new = uuid.uuid4().hex
    return new, (make_cookie("vsid", new),)


# ============================== Страницы =====================================
def pg_vega(req):
    sid, cookies = get_sid(req)
    qs = "?sid=" + sid
    parts = []

    if not config.LLM_URL:
        parts.append('<div class="err"><b>Вега не подключена к модели.</b> '
                     'Заполни LLM_URL, LLM_MODEL (и LLM_KEY, если нужен) '
                     'в app/config.py и перезапусти сервер.</div>')

    cmd = pending_get(sid)
    if cmd:
        parts.append('<div><b>Вега хочет выполнить команду:</b></div>'
                     '<pre>%s</pre>' % escape(cmd))
        parts.append('<form method="post" action="/vega/exec%s">'
                     '<input type="hidden" name="approve" value="1">'
                     '<input type="submit" value="Выполнить"></form>' % qs)
        parts.append('<form method="post" action="/vega/exec%s">'
                     '<input type="hidden" name="approve" value="0">'
                     '<input type="submit" value="Отклонить"></form><hr>' % qs)

    history = hist_load(sid)[-30:]
    for m in history:
        if m["role"] == "user":
            parts.append('<div class="msg"><b>Вы:</b><br>%s</div>'
                         % components.nl2br(m["content"]))
        elif m["role"] == "assistant":
            text = escape(m["content"]).replace("@@RUN ", "\n[команда] ")
            parts.append('<div class="msg"><b>Вега:</b><br>%s</div>'
                         % components.nl2br(text))
        else:
            parts.append('<div class="msg"><b>[сервер]:</b><br><pre>%s</pre></div>'
                         % escape(m["content"]))

    if not history:
        parts.append('<div class="small">Диалог пуст. Вега — ИИ-агент с доступом '
                     'к серверу: попроси её, например, узнать нагрузку или скачать '
                     'файл в «Загрузки».</div>')

    form = ('<hr><form method="post" action="/vega/send%s">'
            '<input type="text" name="msg" maxlength="%d"> '
            '<input type="submit" value="Отправить"></form>'
            '<form method="post" action="/vega/clear%s">'
            '<input type="submit" value="Очистить диалог"></form>'
            % (qs, config.VEGA_MAX_LEN, qs))
    return layout.page("Вега", "vega", "<h1>Вега</h1>" + "".join(parts) + form,
                       cache="no-store", cookies=cookies)


def po_send(req):
    sid, cookies = get_sid(req)
    msg = (req.form.get("msg") or "").strip()
    if msg:
        history = hist_load(sid)
        history.append({"role": "user", "content": msg[:config.VEGA_MAX_LEN]})
        hist_save(sid, history)
        vega_step(sid)   # синхронный вызов модели — страница ответит, когда Вега закончит
    return Response.redirect("/vega?sid=" + sid, cookies=cookies)


def po_exec(req):
    sid, cookies = get_sid(req)
    approve = req.form.get("approve") == "1"
    cmd = pending_pop(sid)
    if cmd:
        history = hist_load(sid)
        if approve:
            history.append({"role": "system",
                            "content": "Выполнена команда «%s»:\n%s"
                                       % (cmd, llm.run_cmd(cmd))})
            hist_save(sid, history)
            vega_step(sid, max_iters=3)   # Вега видит вывод и продолжает
        else:
            history.append({"role": "system",
                            "content": "Пользователь отклонил команду «%s»." % cmd})
            hist_save(sid, history)
            vega_step(sid, max_iters=1)
    return Response.redirect("/vega?sid=" + sid, cookies=cookies)


def po_clear(req):
    sid, cookies = get_sid(req)
    pending_pop(sid)
    hist_save(sid, [])
    return Response.redirect("/vega?sid=" + sid, cookies=cookies)


ROUTES = [
    Route("GET", "/vega", pg_vega),
    Route("POST", "/vega/send", po_send),
    Route("POST", "/vega/exec", po_exec),
    Route("POST", "/vega/clear", po_clear),
]
