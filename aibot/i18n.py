"""Localization.

Everything the user sees lives in JSON files, one folder per language::

    aibot/locales/<lang>/user.json    texts for regular users
    aibot/locales/<lang>/admin.json   texts of the admin panel
    aibot/locales/<lang>/modes.json   mode names, descriptions and system prompts

A text may be a string or a list of lines (joined with "\\n"). To add a language, copy a folder,
translate the files and set ``lang_name`` in ``user.json``; the bot picks it up automatically.
"""

import json
import os


LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
DEFAULT_LANG = "ru"
TEXT_FILES = ("user.json", "admin.json")
COMMAND_ORDER = ("start", "help", "mode", "settings", "saved", "export", "invite", "clear", "myprompt")


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _join(value):
    return "\n".join(value) if isinstance(value, list) else value


def _discover() -> list[str]:
    codes = [
        d for d in os.listdir(LOCALES_DIR)
        if os.path.isfile(os.path.join(LOCALES_DIR, d, "user.json"))
    ]
    preferred = {"ru": 0, "en": 1}
    return sorted(codes, key=lambda c: (preferred.get(c, 9), c))


def _load_texts() -> dict[str, dict[str, str]]:
    texts: dict[str, dict[str, str]] = {}
    for code in _discover():
        merged: dict[str, str] = {}
        for name in TEXT_FILES:
            path = os.path.join(LOCALES_DIR, code, name)
            if os.path.isfile(path):
                merged.update({k: _join(v) for k, v in _read(path).items()})
        texts[code] = merged
    return texts


TEXTS = _load_texts()
LANGS = {code: TEXTS[code].get("lang_name", code) for code in TEXTS}


def _load_modes():
    """Builds DEFAULT_PROMPT and MODES (title/desc/prompt per language) from modes.json files."""
    raw = {code: _read(os.path.join(LOCALES_DIR, code, "modes.json")) for code in TEXTS}
    default_prompt = {code: _join(raw[code]["default_prompt"]) for code in raw}
    modes: dict[str, dict] = {}
    for key, info in raw[DEFAULT_LANG]["modes"].items():
        modes[key] = {
            "title": {c: raw[c]["modes"][key]["title"] for c in raw},
            "desc": {c: raw[c]["modes"][key]["desc"] for c in raw},
            # The default mode has no prompt of its own: it uses DEFAULT_PROMPT (editable by the admin)
            "prompt": None if info.get("prompt") is None
            else {c: _join(raw[c]["modes"][key]["prompt"]) for c in raw},
        }
    return default_prompt, modes


DEFAULT_PROMPT, MODES = _load_modes()

# Telegram command menu (the "/" button next to the input field)
COMMANDS = {
    code: [(c, TEXTS[code][f"cmd_{c}"]) for c in COMMAND_ORDER] for code in TEXTS
}


def t(lang: str, key: str, **kw) -> str:
    """Returns the text ``key`` in ``lang`` (falls back to the default language) formatted with ``kw``."""
    s = TEXTS.get(lang, TEXTS[DEFAULT_LANG]).get(key) or TEXTS[DEFAULT_LANG][key]
    return s.format(**kw) if kw else s


def all_langs(key: str) -> set:
    """All variants of a text in every language (used for button filters of handlers)."""
    return {TEXTS[code][key] for code in TEXTS}


def modes_list(lang: str, current: str) -> str:
    """List of modes with descriptions for the "Choose a mode" message."""
    lines = []
    for key, m in MODES.items():
        mark = "👉" if key == current else "•"
        lines.append(f"{mark} <b>{m['title'][lang]}</b> — {m['desc'][lang]}")
    return "\n".join(lines)


BTN_ASK = all_langs("btn_ask")
BTN_MODE = all_langs("btn_mode")
BTN_CLEAR = all_langs("btn_clear")
BTN_INFO = all_langs("btn_info")
BTN_SETTINGS = all_langs("btn_settings")
BTN_INVITE = all_langs("btn_invite")
BTN_HELP = all_langs("btn_help")
