"""Referral links and traffic-source tracking."""

import logging
import time

from aibot.config import REF_BONUS, REF_MAX, SRC_RE
from aibot.database.settings import get_setting, scalar
from aibot.database.users import get_lang
from aibot.i18n import t
from aibot.state import bot, db


_me = {"username": ""}


async def bot_username() -> str:
    if not _me["username"]:
        try:
            _me["username"] = (await bot.get_me()).username or ""
        except Exception:
            logging.warning("Could not get the bot username")
    return _me["username"]


async def get_ref_cfg() -> tuple[int, int]:
    """(daily bonus per invitation, max number of friends that earn a bonus); changeable in the admin panel."""
    try:
        bonus = max(0, int(await get_setting("ref_bonus", str(REF_BONUS))))
    except ValueError:
        bonus = REF_BONUS
    try:
        mx = max(0, int(await get_setting("ref_max", str(REF_MAX))))
    except ValueError:
        mx = REF_MAX
    return bonus, mx


async def register_start(uid: int, payload: str) -> None:
    """Records where a NEW user came from: an invitation (ref_<id>) or a source tag."""
    cur = await db.execute("SELECT src, joined FROM users WHERE id=?", (uid,))
    row = await cur.fetchone()
    if not row or row["src"] is not None:
        return
    if row["joined"] and row["joined"] < int(time.time()) - 600:
        # has been in the bot for a while but has no recorded source; not counted as new
        await db.execute("UPDATE users SET src='old' WHERE id=?", (uid,))
        await db.commit()
        return
    payload = (payload or "").strip()
    src, ref = "direct", None
    if payload.startswith("ref_") and payload[4:].isdigit():
        src = "ref"
        r = int(payload[4:])
        if r != uid and await scalar("SELECT COUNT(*) FROM users WHERE id=?", (r,)):
            ref = r
    elif SRC_RE.match(payload):
        src = payload.lower()
    await db.execute("UPDATE users SET src=?, ref_by=? WHERE id=?", (src, ref, uid))
    await db.commit()


async def grant_ref_reward(uid: int):
    """After the invitee's FIRST successful reply, both get a bonus to the daily limit."""
    cur = await db.execute("SELECT ref_by, ref_paid FROM users WHERE id=?", (uid,))
    row = await cur.fetchone()
    if not row or not row["ref_by"] or row["ref_paid"]:
        return
    ref = row["ref_by"]
    bonus, mx = await get_ref_cfg()
    await db.execute("UPDATE users SET ref_paid=1, bonus=COALESCE(bonus,0)+? WHERE id=?", (bonus, uid))
    paid = await scalar("SELECT COUNT(*) FROM users WHERE ref_by=? AND ref_paid=1", (ref,))
    rewarded = paid <= mx
    if rewarded:
        await db.execute("UPDATE users SET bonus=COALESCE(bonus,0)+? WHERE id=?", (bonus, ref))
    await db.commit()
    if bonus <= 0:
        return
    try:
        await bot.send_message(uid, t(await get_lang(uid), "ref_welcome", b=bonus))
        if rewarded:
            await bot.send_message(ref, t(await get_lang(ref), "ref_notify", b=bonus))
    except Exception:
        pass  # the user may have blocked the bot; the bonus is credited anyway
