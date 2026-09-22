# -*- coding: utf-8 -*-
"""Клиент LLM для Веги + запуск shell-команд.

Вега — ИИ-агент: модель общается с сервером, предлагая shell-команды
меткой @@RUN в конце ответа. Логика диалога — в modules/vega.py,
здесь только транспорт (OpenAI-совместимый / Ollama) и subprocess.
"""

import json
import subprocess
import threading
import urllib.request

from app import config

LOCK = threading.Lock()

VEGA_SYSTEM = (
    "Ты — Вега, ИИ-агент, работающий на Linux-сервере пользователя. "
    "Ты можешь выполнять команды оболочки. Чтобы выполнить команду, добавь "
    "В КОНЕЦ ответа отдельной строкой метку вида:\n@@RUN команда\n"
    "После выполнения ты получишь вывод команды и сможешь продолжить работу. "
    "Если команда не нужна — просто отвечай текстом. Отвечай кратко, по-русски."
)

_MODEL_CACHE = {"name": config.LLM_MODEL or None}


def pick_model():
    """Если модель не задана (стиль openai) — берём первую из {LLM_URL}/models."""
    if _MODEL_CACHE["name"]:
        return _MODEL_CACHE["name"]
    if config.LLM_STYLE != "openai" or not config.LLM_URL:
        return None
    url = config.LLM_URL.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    if config.LLM_KEY:
        req.add_header("Authorization", "Bearer " + config.LLM_KEY)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        items = data.get("data") or data.get("models") or []
        ids = [m.get("id") or m.get("name") for m in items if isinstance(m, dict)]
        ids = [i for i in ids if i]
        if ids:
            _MODEL_CACHE["name"] = ids[0]
    except Exception:
        pass
    return _MODEL_CACHE["name"]


def chat(history):
    """Отправляет историю диалога модели, возвращает текст ответа.

    history — список {"role": "user"|"assistant"|"system", "content": str}
    """
    if not config.LLM_URL:
        raise RuntimeError("LLM не настроен: заполни LLM_URL / LLM_MODEL / LLM_KEY "
                           "в app/config.py и перезапусти сервер")
    msgs = [{"role": "system", "content": VEGA_SYSTEM}] + history[-20:]

    if config.LLM_STYLE == "ollama":
        if not config.LLM_MODEL:
            raise RuntimeError("Для Ollama обязательно укажи LLM_MODEL в app/config.py")
        url = config.LLM_URL
        payload = {"model": config.LLM_MODEL, "messages": msgs, "stream": False}
    else:
        url = config.LLM_URL if "/chat/completions" in config.LLM_URL \
            else config.LLM_URL.rstrip("/") + "/chat/completions"
        payload = {"messages": msgs, "temperature": 0.4}
        model = pick_model()
        if model:
            payload["model"] = model

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    if config.LLM_KEY:
        req.add_header("Authorization", "Bearer " + config.LLM_KEY)
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))

    if config.LLM_STYLE == "ollama":
        return resp["message"]["content"]
    return resp["choices"][0]["message"]["content"]


def run_cmd(cmd):
    """Выполняет shell-команду (из data/), возвращает вывод до 4000 символов."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           errors="replace", timeout=config.CMD_TIMEOUT,
                           cwd=config.DATA_DIR)
        out = r.stdout or ""
        if r.stderr:
            out += "\n[stderr]\n" + r.stderr
    except subprocess.TimeoutExpired:
        out = "Команда превысила таймаут %d сек" % config.CMD_TIMEOUT
    except Exception as e:
        out = "Ошибка запуска: %s" % e
    out = out.strip() or "(пустой вывод)"
    return out[:4000]
