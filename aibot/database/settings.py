"""Key-value settings table and small query helpers."""

from aibot.state import db


async def scalar(query: str, args=()):
    cur = await db.execute(query, args)
    row = await cur.fetchone()
    return row[0] if row else 0


async def get_setting(key: str, default: str) -> str:
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: str):
    await db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db.commit()


async def del_setting(key: str):
    await db.execute("DELETE FROM settings WHERE key=?", (key,))
    await db.commit()
