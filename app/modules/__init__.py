# -*- coding: utf-8 -*-
"""Реестр модулей портала.

Чтобы добавить новую вкладку:
1. Создай файл app/modules/<ключ>.py по образцу существующих:
   в нём объявляются ROUTES = [Route("GET", "/<ключ>", ...), ...]
2. Импортируй модуль здесь и добавь в список MODULES.
3. Добавь ("<ключ>", "Название") в TABS в app/config.py — появится в навигации.

Маршруты собираются в app/__init__.py (build_router).
"""

from app.modules import search
from app.modules import reader
from app.modules import downloads
from app.modules import library
from app.modules import flibusta
from app.modules import chat
from app.modules import archive
from app.modules import vega

MODULES = [search, reader, downloads, library, flibusta, chat, archive, vega]
