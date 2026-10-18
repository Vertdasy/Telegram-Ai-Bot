"""Per-user request rate limits and referral bonus."""

import time

from aibot.config import ADMIN_IDS, RATE_PER_DAY, RATE_PER_MIN
from aibot.database.settings import get_setting, scalar
from aibot.i18n import t


async def get_limits() -> tuple[int, int]:
    per_min = int(await get_setting("rate_min", str(RATE_PER_MIN)))
    per_day = int(await get_setting("rate_day", str(RATE_PER_DAY)))
    return per_min, per_day


async def get_bonus(user_id: int) -> int:
    return int(await scalar("SELECT COALESCE(bonus, 0) FROM users WHERE id=?", (user_id,)) or 0)


def fmt_limit(n: int, lang: str = "ru") -> str:
    return str(n) if n > 0 else t(lang, "unlimited")


def fmt_wait(sec: int, lang: str) -> str:
    sec = max(sec, 1)
    if sec < 60:
        return t(lang, "wait_sec", n=sec)
    if sec < 3600:
        return t(lang, "wait_min", n=sec // 60 + 1)
    return t(lang, "wait_hm", h=sec // 3600, m=sec % 3600 // 60)


async def check_limit(user_id: int, lang: str) -> str | None:
    """Returns the refusal text if the limit is exhausted, otherwise None. Admins are not limited."""
    if user_id in ADMIN_IDS:
        return None
    per_min, per_day = await get_limits()
    if per_day > 0:
        per_day += await get_bonus(user_id)
    now = int(time.time())
    if per_min > 0:
        cnt = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 60)
        )
        if cnt >= per_min:
            oldest = await scalar(
                "SELECT MIN(ts) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 60)
            )
            return t(lang, "limit_min", n=per_min, wait=fmt_wait(oldest + 60 - now, lang))
    if per_day > 0:
        cnt = await scalar(
            "SELECT COUNT(*) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 86400)
        )
        if cnt >= per_day:
            oldest = await scalar(
                "SELECT MIN(ts) FROM requests WHERE user_id=? AND ts>?", (user_id, now - 86400)
            )
            return t(lang, "limit_day", n=per_day, wait=fmt_wait(oldest + 86400 - now, lang))
    return None
