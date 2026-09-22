# -*- coding: utf-8 -*-
"""Роутер: таблица маршрутов "метод + путь -> функция-обработчик".

Маршруты объявляются в модулях app/modules/*.py списком ROUTES,
собираются в app/__init__.py (build_router).

Пример из модуля:

    from app.core.http import Response
    from app.core.router import Route

    def pg_hello(req):
        return Response.html("<h1>Привет</h1>")

    ROUTES = [Route("GET", "/hello", pg_hello)]
"""


class Route:
    __slots__ = ("method", "path", "handler")

    def __init__(self, method, path, handler):
        self.method = method.upper()
        self.path = path.rstrip("/") or "/"
        self.handler = handler


class Router:
    def __init__(self):
        self._routes = {}

    def add_all(self, routes):
        for r in routes:
            self._routes[(r.method, r.path)] = r.handler

    def resolve(self, method, path):
        """Возвращает обработчик или None. HEAD обслуживается как GET."""
        key = ("GET" if method == "HEAD" else method, path)
        return self._routes.get(key)
