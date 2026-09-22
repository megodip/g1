# -*- coding: utf-8 -*-
"""Сборка приложения: роутер из всех модулей.

Единственное место, знающее про все модули сразу — app/modules/MODULES.
"""

from app.core.router import Router
from app.modules import MODULES


def build_router():
    """Собирает маршруты всех модулей в один роутер."""
    router = Router()
    for module in MODULES:
        router.add_all(module.ROUTES)
    return router
