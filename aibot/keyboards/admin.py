"""Keyboards of the admin panel. Every builder takes the admin's language as the first argument."""

from aiogram.utils.keyboard import InlineKeyboardBuilder

from aibot.config import AVAILABLE_MODELS, EFFORTS
from aibot.i18n import LANGS, MODES, t
from aibot.state import STATE


MEMORY_PRESETS = (10, 20, 30, 50, 100)

MENU_BUTTONS = (
    ("stats", "adm:stats"),
    ("users", "adm:users"),
    ("broadcast", "adm:broadcast"),
    ("errors", "adm:errors"),
    ("ban", "adm:ban"),
    ("unban", "adm:unban"),
    ("model", "adm:model"),
    ("prompt", "adm:prompt"),
    ("memory", "adm:mem"),
    ("limits", "adm:limits"),
    ("sources", "adm:src"),
    ("web", "adm:web"),
    ("ref", "adm:ref"),
    ("speed", "adm:speed_menu"),
    ("user", "adm:userinfo"),
    ("backup", "adm:backup"),
    ("lang", "adm:lang"),
)


def effort_label(lang: str, effort: str) -> str:
    return t(lang, "adm_effort_default") if effort == "default" else effort


def _back(b: InlineKeyboardBuilder, lang: str, target: str = "adm:menu"):
    b.button(text=t(lang, "adm_back"), callback_data=target)


def admin_kb(lang: str):
    b = InlineKeyboardBuilder()
    for name, data in MENU_BUTTONS:
        b.button(text=t(lang, f"adm_btn_{name}"), callback_data=data)
    state = t(lang, "adm_state_on" if STATE["maintenance"] else "adm_state_off")
    b.button(text=t(lang, "adm_btn_maint", state=state), callback_data="adm:maint")
    b.adjust(2)
    return b.as_markup()


def model_kb(lang: str, current: str):
    b = InlineKeyboardBuilder()
    for m in AVAILABLE_MODELS:
        b.button(text=("✅ " if m == current else "") + m, callback_data=f"adm:m:{m}")
    b.button(text=t(lang, "adm_btn_model_custom"), callback_data="adm:model_custom")
    _back(b, lang)
    b.adjust(1)
    return b.as_markup()


def prompt_kb(lang: str):
    """Choice of the mode whose prompt is edited."""
    b = InlineKeyboardBuilder()
    for key, m in MODES.items():
        b.button(text=m["title"][lang], callback_data=f"adm:pm:{key}")
    _back(b, lang)
    b.adjust(2, 2, 1)
    return b.as_markup()


def prompt_mode_kb(lang: str, mode: str):
    b = InlineKeyboardBuilder()
    for code in LANGS:
        b.button(text=t(lang, "adm_btn_edit", code=code.upper()), callback_data=f"adm:pe:{mode}:{code}")
    b.button(text=t(lang, "adm_btn_reset_prompt"), callback_data=f"adm:pr:{mode}")
    b.button(text=t(lang, "adm_btn_modes_list"), callback_data="adm:prompt")
    b.adjust(len(LANGS), 1, 1)
    return b.as_markup()


def limits_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "adm_btn_limit_min"), callback_data="adm:limit_min")
    b.button(text=t(lang, "adm_btn_limit_day"), callback_data="adm:limit_day")
    _back(b, lang)
    b.adjust(2, 1)
    return b.as_markup()


def confirm_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "adm_btn_send"), callback_data="adm:bc_yes")
    b.button(text=t(lang, "adm_btn_cancel"), callback_data="adm:bc_no")
    b.adjust(2)
    return b.as_markup()


def memory_kb(lang: str, current: int):
    b = InlineKeyboardBuilder()
    for n in MEMORY_PRESETS:
        b.button(text=("✅ " if n == current else "") + str(n), callback_data=f"adm:memset:{n}")
    b.button(text=t(lang, "adm_btn_mem_custom"), callback_data="adm:mem_custom")
    b.button(text=t(lang, "adm_btn_hist_chars"), callback_data="adm:hist_chars")
    _back(b, lang)
    b.adjust(5, 2, 1)
    return b.as_markup()


def ref_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "adm_btn_ref_bonus"), callback_data="adm:ref_bonus")
    b.button(text=t(lang, "adm_btn_ref_max"), callback_data="adm:ref_max")
    _back(b, lang)
    b.adjust(2, 1)
    return b.as_markup()


def web_kb(lang: str, on: bool):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "adm_btn_web_off" if on else "adm_btn_web_on"), callback_data="adm:web_toggle")
    _back(b, lang)
    b.adjust(1)
    return b.as_markup()


def src_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "adm_btn_refresh"), callback_data="adm:src")
    _back(b, lang)
    b.adjust(2)
    return b.as_markup()


def speed_kb(lang: str):
    b = InlineKeyboardBuilder()
    b.button(text=t(lang, "adm_btn_eff_fast"), callback_data="adm:eff_pick:fast")
    b.button(text=t(lang, "adm_btn_eff_think"), callback_data="adm:eff_pick:think")
    b.button(text=t(lang, "adm_btn_fb"), callback_data="adm:fb_pick")
    b.button(text=t(lang, "adm_btn_speedtest"), callback_data="adm:speedtest")
    _back(b, lang)
    b.adjust(2, 1, 1, 1)
    return b.as_markup()


def effort_kb(lang: str, which: str, current: str):
    b = InlineKeyboardBuilder()
    for e in EFFORTS:
        label = effort_label(lang, e) + (t(lang, "adm_effort_not_passed") if e == "default" else "")
        b.button(text=("✅ " if e == current else "") + label, callback_data=f"adm:eff:{which}:{e}")
    _back(b, lang, "adm:speed_menu")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def fallback_kb(lang: str, current: str):
    b = InlineKeyboardBuilder()
    for m in AVAILABLE_MODELS:
        b.button(text=("✅ " if m == current else "") + m, callback_data=f"adm:fb:{m}")
    b.button(text=("✅ " if current == "off" else "") + t(lang, "adm_btn_fb_off"), callback_data="adm:fb:off")
    _back(b, lang, "adm:speed_menu")
    b.adjust(1)
    return b.as_markup()


def user_card_kb(lang: str, uid: int, banned: bool, is_admin: bool):
    b = InlineKeyboardBuilder()
    if banned:
        b.button(text=t(lang, "adm_btn_unban_user"), callback_data=f"adm:unban_id:{uid}")
    elif not is_admin:
        b.button(text=t(lang, "adm_btn_ban_user"), callback_data=f"adm:ban_id:{uid}")
    _back(b, lang)
    b.adjust(2)
    return b.as_markup()
