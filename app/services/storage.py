# -*- coding: utf-8 -*-
"""Хранилище: папки данных, безопасные имена файлов, JSONL-история чата.

Всё состояние портала лежит в папке data/ обычными файлами:
  data/downloads/   — файлы для скачивания
  data/archive/     — текстовые записи архива
  data/chat.jsonl   — чат, одно сообщение = одна JSON-строка

Такую схему легко читать и править без инструментов; замена на БД
потребует переписать только этот файл.
"""

import json
import os
import re
import threading
import time

from app import config

LOCK = threading.Lock()


# ============================== ПАПКИ ========================================
def ensure_dirs():
    for d in (config.DATA_DIR, config.DL_DIR, config.ARCHIVE_DIR,
              config.LIB_DIR):
        os.makedirs(d, exist_ok=True)


# ============================== ФАЙЛЫ ========================================
def safe_name(name):
    """Имя файла, безопасное для файловой системы (без путей и мусора)."""
    name = os.path.basename(str(name).replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ]", "_", name, flags=re.UNICODE)
    return name.strip(". ")[:80]


def fmt_size(n):
    """1234567 -> '1.2 МБ' (для списка файлов)."""
    x = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if x < 1024:
            return ("%d %s" if unit == "Б" else "%.1f %s") % (x, unit)
        x /= 1024
    return "%.1f ТБ" % x


def list_dir(path):
    """Имена обычных файлов в папке, отсортированные по алфавиту."""
    if not os.path.isdir(path):
        return []
    return sorted(n for n in os.listdir(path)
                  if os.path.isfile(os.path.join(path, n)))


# ============================== ЧАТ (JSONL) ==================================
def chat_load():
    """Вся история чата: [{"n": ник, "x": текст, "t": timestamp}, ...]."""
    try:
        with LOCK, open(config.CHAT_FILE, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def chat_append(nick, text):
    """Добавляет сообщение и подрезает историю, если она разрослась."""
    with LOCK:
        with open(config.CHAT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": nick, "x": text, "t": time.time()},
                               ensure_ascii=False) + "\n")
        try:
            with open(config.CHAT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > config.CHAT_MAX_MESSAGES:
                with open(config.CHAT_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-config.CHAT_KEEP:])
        except Exception:
            pass
