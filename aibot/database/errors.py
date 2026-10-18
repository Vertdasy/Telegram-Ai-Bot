"""Error log table."""

import time

from aibot.state import db


async def log_error(user_id: int, text: str):
    await db.execute(
        "INSERT INTO errors(ts, user_id, text) VALUES(?,?,?)",
        (int(time.time()), user_id, text[:500]),
    )
    await db.execute(
        "DELETE FROM errors WHERE id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT 200)"
    )
    await db.commit()
