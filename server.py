#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""g1 — мини-портал для кнопочных телефонов (браузер без JavaScript).

Запуск:      python3 server.py            ->  http://IP_СЕРВЕРА:8000
Настройки:   app/config.py  (или переменные окружения G1_*)
Зависимости: только Python 3.7+

Структура и правила разработки — в README.md и AGENTS.md.
"""

import sys

from app import build_router
from app.core.server import serve


if __name__ == "__main__":
    if sys.version_info < (3, 7):
        sys.exit("Нужен Python 3.7+")
    serve(build_router())
