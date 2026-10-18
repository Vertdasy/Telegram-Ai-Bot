"""Dialog memory: conversation history storage, trimming and limits."""

import logging

from aibot.config import HISTORY_CHARS, MEMORY_DEFAULT, MEMORY_MAX, MEMORY_MIN
from aibot.database.settings import get_setting, scalar, set_setting
from aibot.state import db, last_images


# --- conversation memory ---
async def get_memory_len() -> int:
    try:
        n = int(await get_setting("memory_len", str(MEMORY_DEFAULT)))
    except ValueError:
        n = MEMORY_DEFAULT
    return max(MEMORY_MIN, min(MEMORY_MAX, n))


async def get_history_chars() -> int:
    try:
        n = int(await get_setting("history_chars", str(HISTORY_CHARS)))
    except ValueError:
        n = HISTORY_CHARS
    return max(1000, n)


async def apply_memory_len(n: int):
    """Stores the new memory size and immediately trims other users' histories if it was reduced."""
    n = max(MEMORY_MIN, min(MEMORY_MAX, n))
    await set_setting("memory_len", str(n))
    try:
        await db.execute(
            "DELETE FROM history WHERE id IN ("
            " SELECT id FROM ("
            "  SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn FROM history"
            " ) WHERE rn > ?)",
            (n,),
        )
        await db.commit()
    except Exception:
        logging.exception("Could not trim history right away; it will happen on the next messages")


async def load_history(user_id: int, limit: int | None = None) -> list:
    if limit is None:
        limit = await get_memory_len()
    cur = await db.execute(
        "SELECT role, content FROM ("
        " SELECT id, role, content FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?"
        ") ORDER BY id",
        (user_id, limit),
    )
    return [{"role": r["role"], "content": r["content"]} for r in await cur.fetchall()]


def trim_by_chars(history: list, limit: int = HISTORY_CHARS) -> list:
    """Keeps the newest messages within the character limit; a shorter request means a faster reply."""
    out: list = []
    total = 0
    for m in reversed(history):
        total += len(m["content"])
        if total > limit and len(out) >= 2:
            break
        out.append(m)
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


async def count_history(user_id: int) -> int:
    return await scalar("SELECT COUNT(*) FROM history WHERE user_id=?", (user_id,))


async def pop_last_exchange(user_id: int):
    await db.execute(
        "DELETE FROM history WHERE id IN ("
        " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 2)",
        (user_id,),
    )
    await db.commit()


async def save_exchange(user_id: int, question: str, answer: str):
    """Stores both the user's question and the AI reply (two records) and deletes the excess old ones."""
    await db.executemany(
        "INSERT INTO history(user_id, role, content) VALUES(?,?,?)",
        [(user_id, "user", question), (user_id, "assistant", answer)],
    )
    await db.execute(
        "DELETE FROM history WHERE user_id=? AND id NOT IN ("
        " SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?)",
        (user_id, user_id, await get_memory_len()),
    )
    await db.execute("UPDATE users SET msgs = msgs + 1 WHERE id=?", (user_id,))
    await db.commit()


async def clear_history(user_id: int, *, pinned: bool = True, answers: bool = True):
    """Clears the conversation. pinned=False keeps saved messages, answers=False keeps the Save-button cache."""
    await db.execute("DELETE FROM history WHERE user_id=?", (user_id,))
    if answers:
        await db.execute("DELETE FROM answers WHERE user_id=?", (user_id,))
    if pinned:
        await db.execute("DELETE FROM pinned WHERE user_id=?", (user_id,))
    await db.commit()
    last_images.pop(user_id, None)
