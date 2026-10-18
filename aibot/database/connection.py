"""Database schema creation and light migrations."""

import logging

import aiosqlite

from aibot.config import DB_PATH
from aibot.database.migrations import upgrade_memory_len, upgrade_old_prompts
from aibot.database.settings import get_setting
from aibot.state import db, STATE


async def init_db():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    db.bind(conn)
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined INTEGER,
            last_seen INTEGER,
            msgs INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'default',
            lang TEXT DEFAULT 'ru',
            buttons INTEGER DEFAULT 0,
            think INTEGER DEFAULT 0,
            custom_prompt TEXT,
            save_btn INTEGER DEFAULT 0,
            ref_by INTEGER,
            ref_paid INTEGER DEFAULT 0,
            bonus INTEGER DEFAULT 0,
            src TEXT,
            menu_ver INTEGER DEFAULT 0,
            web INTEGER DEFAULT 1,
            lang_set INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id);
        CREATE TABLE IF NOT EXISTS pinned(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            content TEXT,
            ts INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_pinned_user ON pinned(user_id);
        CREATE TABLE IF NOT EXISTS answers(
            user_id INTEGER,
            message_id INTEGER,
            content TEXT,
            ts INTEGER,
            PRIMARY KEY (user_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS web_log(ts INTEGER, user_id INTEGER, query TEXT, ok INTEGER);
        CREATE INDEX IF NOT EXISTS idx_web_ts ON web_log(ts);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS errors(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER, user_id INTEGER, text TEXT
        );
        CREATE TABLE IF NOT EXISTS requests(ts INTEGER, user_id INTEGER);
        CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
        CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id, ts);
        """
    )
    # migration for databases created by earlier bot versions
    cur = await db.execute("PRAGMA table_info(users)")
    cols = {r["name"] for r in await cur.fetchall()}
    for name, ddl in (
        ("mode", "TEXT DEFAULT 'default'"),
        ("lang", "TEXT DEFAULT 'ru'"),
        ("buttons", "INTEGER DEFAULT 0"),
        ("think", "INTEGER DEFAULT 0"),
        ("custom_prompt", "TEXT"),
        ("save_btn", "INTEGER DEFAULT 0"),
        ("ref_by", "INTEGER"),
        ("ref_paid", "INTEGER DEFAULT 0"),
        ("bonus", "INTEGER DEFAULT 0"),
        ("src", "TEXT"),
        ("menu_ver", "INTEGER DEFAULT 0"),
        ("web", "INTEGER DEFAULT 1"),
        ("lang_set", "INTEGER DEFAULT 0"),
    ):
        if name not in cols:
            await db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
            if name == "lang_set":
                # existing users have already seen the language; don't show them the choice
                await db.execute("UPDATE users SET lang_set=1")
            if name == "src":
                # everyone who joined before source tracking existed forms a separate group
                await db.execute("UPDATE users SET src='old'")
    await db.commit()
    await upgrade_old_prompts()
    await upgrade_memory_len()
    STATE["maintenance"] = (await get_setting("maintenance", "0")) == "1"
    logging.info(f"Database: {DB_PATH}")
