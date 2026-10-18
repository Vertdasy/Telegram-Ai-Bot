"""Shared runtime objects: bot, dispatcher, AI client, routers and in-memory state."""

import asyncio

import openai
from aiogram import Bot, Dispatcher, Router
from openai import AsyncOpenAI

from aibot.config import AI_API_KEY, AI_BASE_URL, AI_TIMEOUT, TELEGRAM_TOKEN


def make_timeout():
    """Timeout for AI requests.

    ``read`` is the maximum pause between chunks of the answer: with a short value a slow "thinking"
    model used to fail on a hard question before writing anything. The HTTP library is not imported
    directly: the class is taken from openai itself.
    """
    timeout_cls = getattr(openai, "Timeout", None)
    if timeout_cls is not None:
        try:
            return timeout_cls(connect=15.0, read=AI_TIMEOUT, write=30.0, pool=30.0)
        except Exception:
            pass
    return AI_TIMEOUT  # a plain number of seconds works everywhere


bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncOpenAI(
    api_key=AI_API_KEY,
    base_url=AI_BASE_URL,
    timeout=make_timeout(),
    max_retries=1,
)

admin_router = Router()
user_router = Router()

user_locks: dict[int, asyncio.Lock] = {}
waiting: dict[int, int] = {}                   # user_id -> how many requests wait for their turn
active_gens: dict = {}                         # user_id -> Gen of the current generation (for the Stop button)
last_kb_msg: dict[int, tuple[int, int]] = {}   # user_id -> (chat_id, message_id) holding Regenerate/Continue buttons
last_images: dict[int, list] = {}              # user_id -> data URLs of the last question's photos (for Regenerate)
albums: dict[str, dict] = {}                   # media_group_id -> photo album being collected
doc_albums: dict[str, dict] = {}               # media_group_id -> batch of files being collected
bg_tasks: set = set()
reasoning_bad: set[str] = set()                # models that rejected the reasoning_effort parameter
STATE = {"maintenance": False, "speedtest": False}


class Database:
    """Thin proxy around the aiosqlite connection.

    The connection is opened in ``init_db()`` (after import time), but every module can already do
    ``from aibot.state import db`` and call ``await db.execute(...)``.
    """

    conn = None

    def bind(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        if self.conn is None:
            raise RuntimeError("The database is not initialised yet: call init_db() first")
        return getattr(self.conn, name)


db = Database()
