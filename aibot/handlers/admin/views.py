"""Texts (screens) of the admin panel. Every function takes the admin's language first."""

import asyncio
import html
import time

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from aibot.config import (
    ADMIN_IDS, AI_TIMEOUT, BRAVE_API_KEY, EFFORT_FAST, EFFORT_THINK, FALLBACK_MODEL, MEMORY_MAX, MEMORY_MIN,
    MODEL, TAVILY_API_KEY, TEST_MESSAGES, TOTAL_TIMEOUT,
)
from aibot.database.history import count_history, get_history_chars, get_memory_len
from aibot.database.pinned import count_pinned
from aibot.database.settings import get_setting, scalar
from aibot.i18n import LANGS, MODES, t
from aibot.keyboards.admin import effort_label, speed_kb, user_card_kb
from aibot.services.ai import Gen, generate
from aibot.services.prompts import default_prompt, prompt_key
from aibot.services.referral import bot_username, get_ref_cfg
from aibot.services.websearch import web_backends, web_enabled, web_last
from aibot.state import db, reasoning_bad, STATE
from aibot.utils.text import fmt_ts


def fmt_user(r) -> str:
    name = html.escape(r["first_name"] or "—")
    uname = f" @{html.escape(r['username'])}" if r["username"] else ""
    return f"{name}{uname}"


def lang_label(code: str) -> str:
    """'🇷🇺 RU' for the language code 'ru'."""
    return f"{LANGS[code].split()[0]} {code.upper()}"


async def stats_text(lang: str) -> str:
    day = int(time.time()) - 86400
    top_rows = await (await db.execute(
        "SELECT id, first_name, username, msgs FROM users ORDER BY msgs DESC LIMIT 5"
    )).fetchall()
    top = "\n".join(
        f"{i}. {fmt_user(r)} — {r['msgs']}" for i, r in enumerate(top_rows, 1) if r["msgs"]
    ) or "—"
    return t(
        lang, "adm_stats",
        total=await scalar("SELECT COUNT(*) FROM users"),
        banned=await scalar("SELECT COUNT(*) FROM users WHERE banned=1"),
        en_users=await scalar("SELECT COUNT(*) FROM users WHERE lang='en'"),
        active=await scalar("SELECT COUNT(*) FROM users WHERE last_seen>?", (day,)),
        new=await scalar("SELECT COUNT(*) FROM users WHERE joined>?", (day,)),
        req_day=await scalar("SELECT COUNT(*) FROM requests WHERE ts>?", (day,)),
        req_all=await scalar("SELECT COUNT(*) FROM requests"),
        err_day=await scalar("SELECT COUNT(*) FROM errors WHERE ts>?", (day,)),
        model=html.escape(await get_setting("model", MODEL)),
        memory=await get_memory_len(),
        top=top,
    )


async def users_text(lang: str) -> str:
    cur = await db.execute(
        "SELECT id, first_name, username, msgs, banned FROM users ORDER BY last_seen DESC LIMIT 15"
    )
    lines = [
        f"{'🚫' if r['banned'] else '👤'} <code>{r['id']}</code> {fmt_user(r)} — {r['msgs']}"
        for r in await cur.fetchall()
    ]
    return t(lang, "adm_users", items="\n".join(lines) or "—")


async def errors_text(lang: str) -> str:
    cur = await db.execute("SELECT ts, user_id, text FROM errors ORDER BY id DESC LIMIT 8")
    lines = [
        f"<code>{fmt_ts(r['ts'])}</code> ({r['user_id']}): {html.escape(r['text'][:200])}"
        for r in await cur.fetchall()
    ]
    return t(lang, "adm_errors", items="\n\n".join(lines) or t(lang, "adm_no_errors"))


async def prompt_menu_text(lang: str) -> str:
    lines = []
    for key, m in MODES.items():
        marks = [
            code.upper() for code in LANGS
            if await get_setting(prompt_key(key, code), "") not in ("", default_prompt(key, code))
        ]
        if marks:
            lines.append(t(lang, "adm_prompt_item_custom", title=m["title"][lang], langs=", ".join(marks)))
        else:
            lines.append(t(lang, "adm_prompt_item_default", title=m["title"][lang]))
    return t(lang, "adm_prompt_menu", items="\n".join(lines))


async def prompt_mode_text(lang: str, mode: str) -> str:
    parts = [t(lang, "adm_prompt_mode_head", title=MODES[mode]["title"][lang])]
    for code in LANGS:
        cur = await get_setting(prompt_key(mode, code), default_prompt(mode, code))
        custom = cur != default_prompt(mode, code)
        parts.append(t(
            lang, "adm_prompt_lang_custom" if custom else "adm_prompt_lang_default",
            flag=lang_label(code), text=html.escape(cur[:1400]),
        ))
    parts.append(t(lang, "adm_prompt_note_default" if mode == "default" else "adm_prompt_note_mode"))
    return "\n".join(parts)


async def memory_text(lang: str) -> str:
    n = await get_memory_len()
    return t(
        lang, "adm_memory",
        n=n, pairs=n // 2, chars=await get_history_chars(),
        total=await scalar("SELECT COUNT(*) FROM history"),
        users=await scalar("SELECT COUNT(DISTINCT user_id) FROM history"),
        pins=await scalar("SELECT COUNT(*) FROM pinned"),
        min=MEMORY_MIN, max=MEMORY_MAX,
    )


async def ref_text(lang: str) -> str:
    bonus, mx = await get_ref_cfg()
    top_rows = await (await db.execute(
        "SELECT ref_by, COUNT(*) AS n FROM users WHERE ref_paid=1 GROUP BY ref_by ORDER BY n DESC LIMIT 5"
    )).fetchall()
    top = "\n".join(f"{i}. <code>{r['ref_by']}</code> — {r['n']}" for i, r in enumerate(top_rows, 1)) or "—"
    state = t(lang, "adm_ref_state_on", bonus=bonus) if bonus > 0 else t(lang, "adm_ref_state_off")
    return t(
        lang, "adm_ref", state=state, mx=mx, top=top,
        refs=await scalar("SELECT COUNT(*) FROM users WHERE ref_by IS NOT NULL"),
        paid=await scalar("SELECT COUNT(*) FROM users WHERE ref_paid=1"),
    )


async def web_text(lang: str) -> str:
    day = int(time.time()) - 86400
    cur = await db.execute("SELECT query FROM web_log ORDER BY ts DESC LIMIT 5")
    last = "\n".join(f"• {html.escape(r['query'])}" for r in await cur.fetchall()) or "—"
    reason = (
        t(lang, "adm_web_reason", why=html.escape(web_last["why"]), ts=fmt_ts(web_last["ts"]))
        if web_last["why"] else ""
    )
    tip = "" if (TAVILY_API_KEY or BRAVE_API_KEY) else t(lang, "adm_web_tip")
    return t(
        lang, "adm_web",
        state=t(lang, "adm_web_on" if await web_enabled() else "adm_web_off"),
        names=" → ".join(n for n, _ in web_backends()),
        ok=await scalar("SELECT COUNT(*) FROM web_log WHERE ok=1 AND ts>?", (day,)),
        bad=await scalar("SELECT COUNT(*) FROM web_log WHERE ok=0 AND ts>?", (day,)),
        users=await scalar("SELECT COUNT(DISTINCT user_id) FROM web_log WHERE ts>?", (day,)),
        reason=reason, tip=tip, last=last,
    )


async def sources_text(lang: str) -> str:
    week = int(time.time()) - 7 * 86400
    cur = await db.execute(
        "SELECT COALESCE(src, 'direct') AS s, COUNT(*) AS n, "
        "SUM(CASE WHEN msgs > 0 THEN 1 ELSE 0 END) AS act, "
        "SUM(CASE WHEN joined > ? THEN 1 ELSE 0 END) AS d7 "
        "FROM users GROUP BY s ORDER BY n DESC LIMIT 15",
        (week,),
    )
    known = {"old": "adm_src_old", "direct": "adm_src_direct", "ref": "adm_src_ref"}
    lines = "\n".join(
        t(
            lang, "adm_src_line",
            name=html.escape(t(lang, known[r["s"]]) if r["s"] in known else r["s"]),
            n=r["n"], act=r["act"] or 0, d7=r["d7"] or 0,
        )
        for r in await cur.fetchall()
    ) or "—"
    return t(
        lang, "adm_sources", lines=lines,
        refs=await scalar("SELECT COUNT(*) FROM users WHERE ref_by IS NOT NULL"),
        refs_paid=await scalar("SELECT COUNT(*) FROM users WHERE ref_paid=1"),
        bot=await bot_username() or t(lang, "adm_src_fallback_bot"),
    )


async def speed_text(lang: str) -> str:
    fast = await get_setting("effort_fast", EFFORT_FAST)
    deep = await get_setting("effort_think", EFFORT_THINK)
    fb = await get_setting("fallback_model", FALLBACK_MODEL)
    return t(
        lang, "adm_speed",
        model=html.escape(await get_setting("model", MODEL)),
        fast=effort_label(lang, fast), deep=effort_label(lang, deep),
        fb=html.escape(fb) if fb not in ("", "off") else t(lang, "adm_speed_fb_off"),
        timeout=f"{AI_TIMEOUT:.0f}", total=f"{TOTAL_TIMEOUT:.0f}",
        bad=html.escape(", ".join(sorted(reasoning_bad)) or "—"),
    )


async def run_speedtest(lang: str, msg: types.Message):
    """Measures time to first token for several reasoning levels and for the fallback model."""
    if STATE["speedtest"]:
        return
    STATE["speedtest"] = True
    try:
        model = await get_setting("model", MODEL)
        fb = await get_setting("fallback_model", FALLBACK_MODEL)
        cases = [(model, e) for e in ("default", "low", "medium", "high")]
        if fb not in ("", "off") and fb != model:
            cases.append((fb, "default"))

        head = t(lang, "adm_st_head")
        lines: list[str] = []

        async def render(tail: str, kb=None):
            try:
                await msg.edit_text(head + "\n".join(lines) + tail, parse_mode="HTML", reply_markup=kb)
            except TelegramBadRequest:
                pass

        await render(t(lang, "adm_st_running"))
        for m, eff in cases:
            g = Gen()
            label = f"<code>{html.escape(m)}</code> · {effort_label(lang, eff)}"
            try:
                await asyncio.wait_for(generate(m, TEST_MESSAGES, eff, g, remember=False), 60)
                total = time.time() - g.started
                if g.first_token_at is None:
                    lines.append(t(lang, "adm_st_empty", label=label, total=f"{total:.1f}"))
                else:
                    extra = ""
                    if g.thinking:
                        extra += t(lang, "adm_st_thinking")
                    if g.dropped_effort:
                        extra += t(lang, "adm_st_dropped")
                    lines.append(t(
                        lang, "adm_st_ok", label=label, ttft=f"{g.first_token_at - g.started:.1f}",
                        total=f"{total:.1f}", extra=extra,
                    ))
            except Exception as e:
                lines.append(t(
                    lang, "adm_st_fail", label=label,
                    err=f"{html.escape(type(e).__name__)}: {html.escape(str(e)[:120])}",
                ))
            await render(t(lang, "adm_st_running"))
        await render(t(lang, "adm_st_done"), speed_kb(lang))
    finally:
        STATE["speedtest"] = False


async def user_card(lang: str, uid: int):
    """Returns (text, keyboard) of the user card, or (None, None) if the user is unknown."""
    r = await (await db.execute("SELECT * FROM users WHERE id=?", (uid,))).fetchone()
    if not r:
        return None, None
    custom = r["custom_prompt"]
    on_off = lambda v: t(lang, "adm_state_on" if v else "adm_state_off")  # noqa: E731
    text = t(
        lang, "adm_card",
        name=fmt_user(r), uid=uid,
        status=t(lang, "adm_card_banned" if r["banned"] else "adm_card_active"),
        joined=fmt_ts(r["joined"], "%d.%m.%Y %H:%M"),
        seen=fmt_ts(r["last_seen"], "%d.%m.%Y %H:%M"),
        msgs=r["msgs"],
        req24=await scalar("SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (uid, int(time.time()) - 86400)),
        mode=html.escape(str(r["mode"])), ulang=html.escape(str(r["lang"])),
        style=t(lang, "adm_style_deep" if r["think"] else "adm_style_fast"),
        buttons=on_off(r["buttons"]), save=on_off(r["save_btn"]),
        used=await count_history(uid), pins=await count_pinned(uid),
        custom=html.escape(custom[:200]) if custom else "—",
    )
    return text, user_card_kb(lang, uid, bool(r["banned"]), uid in ADMIN_IDS)
