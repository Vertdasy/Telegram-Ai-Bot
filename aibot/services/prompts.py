"""System prompt assembly (mode prompt + user instructions + saved messages + search hint)."""

from datetime import datetime, timezone

from aibot.config import PINNED_TOTAL_CHARS
from aibot.database.pinned import list_pinned
from aibot.database.settings import get_setting
from aibot.database.users import get_custom_prompt
from aibot.i18n import DEFAULT_PROMPT, MODES, t


def prompt_key(mode: str, lang: str) -> str:
    """Key in the settings table where the admin stores their prompt for a mode and language."""
    if mode == "default":
        return "system_prompt" if lang == "ru" else "system_prompt_en"
    return f"prompt_{mode}_{lang}"


def default_prompt(mode: str, lang: str) -> str:
    if mode == "default":
        return DEFAULT_PROMPT[lang]
    return MODES[mode]["prompt"][lang]


async def get_system_prompt(mode: str, lang: str) -> str:
    return await get_setting(prompt_key(mode, lang), default_prompt(mode, lang))


async def build_system_prompt(user_id: int, mode: str, lang: str, web: bool = False) -> str:
    """Mode system prompt + the user's custom instructions + their saved messages."""
    prompt = await get_system_prompt(mode, lang)
    if mode == "default":
        custom = await get_custom_prompt(user_id)
        if custom:
            prompt += "\n\n" + t(lang, "user_instr") + "\n" + custom
    pins = await list_pinned(user_id)
    if pins:
        parts: list[str] = []
        total = 0
        for i, r in enumerate(pins, 1):
            text = r["content"]
            if total + len(text) > PINNED_TOTAL_CHARS:
                text = text[: max(0, PINNED_TOTAL_CHARS - total)]
                if not text:
                    break
            parts.append(f"[{i}] {text}")
            total += len(text)
        prompt += "\n\n" + t(lang, "pinned_hdr") + "\n" + "\n\n".join(parts)
    if web:
        prompt += "\n\n" + t(lang, "web_prompt", date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    return prompt
