# -*- coding: utf-8 -*-
"""Локальная библиотека: хранение книг чистым текстом.

Формат хранения (обычные файлы, правятся руками без инструментов):
  data/library/<id>.txt   — текст книги (чистый, без HTML/разметки)
  data/library.json       — карточки: id, title, author, source, chars, added

id — числовой: для книг из Flibusta это её номер на сайте (по нему работает
дедупликация «уже в библиотеке»), для ручных добавлений — текущий timestamp.

Такую схему легко читать и править; замена на БД потребует переписать
только этот файл (см. AGENTS.md).
"""

import json
import os
import threading
import time

from app import config

LOCK = threading.Lock()


# ============================== Карточки (index) ==============================
def _load_index():
    """Список карточек книг (или [], если файла ещё нет)."""
    try:
        with open(config.LIB_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_index(cards):
    with open(config.LIB_INDEX, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)


def _book_path(book_id):
    return os.path.join(config.LIB_DIR, "%s.txt" % book_id)


# ============================== Операции ======================================
def add(title, author, text, source=""):
    """Сохраняет книгу, возвращает id.

    Если книга с таким source уже есть (например, flibusta:522114) —
    новая запись НЕ создаётся, возвращается id существующей
    (текст при этом обновляется на свежескачанный).
    """
    title = (title or "").strip()[:120] or "Без названия"
    author = (author or "").strip()[:120]
    text = (text or "").replace("\x00", "").strip()
    if not text:
        raise RuntimeError("пустой текст книги — сохранять нечего")

    with LOCK:
        cards = _load_index()
        book_id = ""
        if source:
            for c in cards:
                if c.get("source") == source:
                    book_id = c["id"]
                    break
        if not book_id:
            book_id = str(int(time.time() * 100))
            cards.append({"id": book_id, "title": title, "author": author,
                          "source": source, "chars": len(text),
                          "added": time.time()})

        os.makedirs(config.LIB_DIR, exist_ok=True)
        with open(_book_path(book_id), "w", encoding="utf-8") as f:
            f.write(text)

        for c in cards:
            if c["id"] == book_id:
                c["title"], c["author"] = title, author
                c["chars"] = len(text)
        # библиотека не должна расти бесконечно: лишние самые старые удаляем
        cards.sort(key=lambda c: c.get("added", 0), reverse=True)
        while len(cards) > config.LIBRARY_MAX_BOOKS:
            old = cards.pop()
            try:
                os.remove(_book_path(old["id"]))
            except OSError:
                pass
        _save_index(cards)
    return book_id


def get(book_id):
    """Карточка книги по id или None."""
    book_id = str(book_id)
    for c in _load_index():
        if c["id"] == book_id:
            return c
    return None


def load_text(book_id):
    """Текст книги (или пустая строка)."""
    try:
        with open(_book_path(str(book_id)), "r", encoding="utf-8",
                  errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def list_all():
    """Все карточки, новые сверху."""
    cards = _load_index()
    cards.sort(key=lambda c: c.get("added", 0), reverse=True)
    return cards


def search(q):
    """Поиск по названию и автору (подстрока, без учёта регистра)."""
    q = (q or "").strip().lower()
    if not q:
        return list_all()
    return [c for c in list_all()
            if q in c.get("title", "").lower()
            or q in c.get("author", "").lower()]


def delete(book_id):
    """Удаляет книгу (карточку и текстовый файл)."""
    book_id = str(book_id)
    with LOCK:
        cards = [c for c in _load_index() if c["id"] != book_id]
        _save_index(cards)
    try:
        os.remove(_book_path(book_id))
    except OSError:
        pass


def has_source(source):
    """Есть ли уже книга с таким source (например, flibusta:522114)."""
    if not source:
        return None
    for c in _load_index():
        if c.get("source") == source:
            return c["id"]
    return None


def count():
    return len(_load_index())
