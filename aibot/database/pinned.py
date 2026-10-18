"""Saved (pinned) messages and remembered answers for the Save button."""

import time

from aibot.config import ANSWERS_KEEP, PINNED_ITEM_CHARS, PINNED_MAX
from aibot.database.settings import scalar
from aibot.state import db


# --- saved messages ---
def _norm(content: str) -> str:
    return content.strip()[:PINNED_ITEM_CHARS]


async def count_pinned(user_id: int) -> int:
    return await scalar("SELECT COUNT(*) FROM pinned WHERE user_id=?", (user_id,))


async def list_pinned(user_id: int) -> list:
    cur = await db.execute("SELECT id, content FROM pinned WHERE user_id=? ORDER BY id", (user_id,))
    return list(await cur.fetchall())


async def is_pinned(user_id: int, content: str) -> bool:
    return bool(
        await scalar(
            "SELECT COUNT(*) FROM pinned WHERE user_id=? AND content=?", (user_id, _norm(content))
        )
    )


async def add_pinned(user_id: int, content: str) -> str:
    """Returns 'ok', 'dup' (already saved) or 'limit' (maximum reached)."""
    content = _norm(content)
    if await is_pinned(user_id, content):
        return "dup"
    if await count_pinned(user_id) >= PINNED_MAX:
        return "limit"
    await db.execute(
        "INSERT INTO pinned(user_id, content, ts) VALUES(?,?,?)", (user_id, content, int(time.time()))
    )
    await db.commit()
    return "ok"


async def remember_answer(user_id: int, message_id: int, content: str):
    """Caches the full reply text by message id so the Save button knows what to save."""
    await db.execute(
        "INSERT OR REPLACE INTO answers(user_id, message_id, content, ts) VALUES(?,?,?,?)",
        (user_id, message_id, content, int(time.time())),
    )
    await db.execute(
        "DELETE FROM answers WHERE user_id=? AND message_id NOT IN ("
        " SELECT message_id FROM answers WHERE user_id=? ORDER BY ts DESC, message_id DESC LIMIT ?)",
        (user_id, user_id, ANSWERS_KEEP),
    )
    await db.commit()


async def get_answer(user_id: int, message_id: int) -> str | None:
    cur = await db.execute(
        "SELECT content FROM answers WHERE user_id=? AND message_id=?", (user_id, message_id)
    )
    row = await cur.fetchone()
    return row["content"] if row else None
