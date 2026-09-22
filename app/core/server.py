# -*- coding: utf-8 -*-
"""HTTP-сервер Vegas: диспетчеризация запросов и отправка ответов.

Единственная точка, где живёт BaseHTTPRequestHandler. Всё остальное
(модули, UI, сервисы) работает только с Request/Response из core/http.py.
"""

import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from app import config
from app.core.http import Request
from app.ui import layout


def _error_response(exc):
    return layout.page("Ошибка", "",
                       "<b>Ошибка:</b> %s<br><a href=\"/search\">На главную</a>"
                       % escape(str(exc)), status=500)


def _not_found_response():
    return layout.page("404", "",
                       "Страница не найдена. <a href=\"/search\">На главную</a>",
                       status=404)


class VegasHandler(BaseHTTPRequestHandler):
    """Принимает запрос, находит обработчик в роутере, отправляет Response."""

    protocol_version = "HTTP/1.1"
    router = None  # внедряется в serve()

    # --- входные точки -------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_HEAD(self):
        self._dispatch("HEAD")

    # --- основной конвейер -----------------------------------------------------
    def _dispatch(self, method):
        try:
            req = Request(self)
            handler = self.router.resolve(method, req.path)
            if handler is None:
                resp = _not_found_response()
            else:
                resp = handler(req)
        except BrokenPipeError:
            return
        except Exception as exc:  # любая ошибка модуля -> страница 500
            try:
                resp = _error_response(exc)
            except Exception:
                return
        try:
            self._send(resp, head_only=(method == "HEAD"))
        except BrokenPipeError:
            pass

    # --- отправка ответа ----------------------------------------------------------
    def _send(self, resp, head_only=False):
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.ctype)

        if resp.download_name:
            name = resp.download_name
            self.send_header(
                "Content-Disposition",
                'attachment; filename="%s"; filename*=UTF-8\'\'%s'
                % (name.encode("ascii", "replace").decode(), quote(name)))

        for h in resp.headers:
            self.send_header(*h.split(": ", 1))

        if resp.stream_path:
            size = os.path.getsize(resp.stream_path)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if head_only:
                return
            with open(resp.stream_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        else:
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(resp.body)

    def log_message(self, fmt, *args):
        # Короткий лог в stderr: "GET /chat"
        print("%s %s" % (self.command, self.path), flush=True)


# ============================== ЗАПУСК =======================================
def serve(router):
    """Собирает сервер и запускает бесконечный цикл обработки."""
    from app.services import storage, llm

    storage.ensure_dirs()
    VegasHandler.router = router

    # Автоопределение модели для Веги (если задано openai и модель пуста).
    if config.LLM_URL and config.LLM_STYLE == "openai" and not config.LLM_MODEL:
        name = llm.pick_model()
        print("Вега: модель автоопределена: %s" % name if name
              else "ВНИМАНИЕ: не удалось автоопределить модель — задай LLM_MODEL вручную.",
              flush=True)

    srv = ThreadingHTTPServer((config.HOST, config.PORT), VegasHandler)
    print("Портал Vegas запущен: http://%s:%d" % (config.HOST, config.PORT), flush=True)
    print("Вкладки: " + " | ".join(n for _, n in config.TABS), flush=True)
    if not config.LLM_URL:
        print("ВНИМАНИЕ: Вега не настроена (LLM_URL пуст) — см. app/config.py", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
