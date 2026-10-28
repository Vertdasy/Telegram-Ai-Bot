"""Configuration: environment variables (optionally read from a .env file) and constants."""

import os
import re


def load_env_file(path: str = ".env"):
    """Loads settings from a .env file next to main.py (handy on hosts without an env-var panel).
    Variables set in the hosting panel take precedence."""
    try:
        full = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
        with open(full, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and val and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


load_env_file()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")      # bot token from @BotFather


AI_API_KEY = os.getenv("AI_API_KEY")              # AI API key


if not TELEGRAM_TOKEN or not AI_API_KEY:
    raise SystemExit(
        "TELEGRAM_TOKEN and/or AI_API_KEY are not set. Put them into the environment variables of your "
        "hosting panel or into a .env file next to main.py (see .env.example).\n"
        "Не заданы TELEGRAM_TOKEN и/или AI_API_KEY: добавь их в переменные окружения хостинга "
        "или в файл .env рядом с main.py (см. .env.example)."
    )

AI_BASE_URL = os.getenv("AI_BASE_URL", "https://ai.starimg.ru/v1")


MODEL = os.getenv("ID_MODEL", "deepseek-v4.1-flash")


# Model for photo processing (must accept images)
VISION_MODEL = os.getenv("VISION_MODEL", "deepseek-v4.1-flash")


# --- speed and reliability ---
# Model reasoning level for the "Fast" / "Thoughtful" styles:
# default (don't pass the parameter), low, medium, high, xhigh. Changeable in the admin panel: ⚡ Speed.
# The provider benchmark timed out on "low" but returned a result for "default".
EFFORT_FAST = os.getenv("EFFORT_FAST", "default")


EFFORT_THINK = os.getenv("EFFORT_THINK", "high")


# Fallback model: used if the primary one didn't respond (timeout/error). "off" disables it.
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "deepseek-v4-flash")


AI_TIMEOUT = env_float("AI_TIMEOUT", 150.0)         # max pause without data from the model, sec


TOTAL_TIMEOUT = env_float("TOTAL_TIMEOUT", 300.0)   # max total time of a single request, sec


MAX_TOKENS = env_int("MAX_TOKENS", 0)               # 0 = no limit on reply length


# --- conversation memory ---
# The bot stores both user questions and AI replies (each is a separate "message"),
# so 30 messages = 15 question-answer pairs. Changeable in the admin panel: 🧮 Memory.
MEMORY_DEFAULT = env_int("MEMORY_LEN", 30)


MEMORY_MIN, MEMORY_MAX = 2, 200


# History size limit per request (characters). If the dialogue is longer, the oldest
# messages are not sent to the model (faster replies). Changeable in the admin panel.
HISTORY_CHARS = env_int("HISTORY_CHARS", 40000)


# --- saved messages ("📌 Save") ---
PINNED_MAX = env_int("PINNED_MAX", 10)              # how many messages can be saved


PINNED_ITEM_CHARS = 4000                            # max length of a single saved message


PINNED_TOTAL_CHARS = 8000                           # how many characters of saved messages are sent to the model


ANSWERS_KEEP = 40                                   # how many recent replies to remember for the Save button


# --- photos and queue ---
MAX_IMAGES = env_int("MAX_IMAGES", 6)               # max photos in one message/album


ALBUM_WAIT = env_float("ALBUM_WAIT", 1.2)           # how long to wait for the rest of an album's photos, sec


MAX_QUEUE = env_int("MAX_QUEUE", 2)                 # how many requests from one user may wait in the queue


# --- friend invitations ---
REF_BONUS = env_int("REF_BONUS", 20)      # extra daily requests each side gets (invitee and inviter)


REF_MAX = env_int("REF_MAX", 25)          # number of friends for which the inviter gets a bonus


SRC_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")   # source tags in the link: t.me/bot?start=tag


# --- web search for the AI ---
# The AI decides when to search (if it doesn't know or isn't sure). The user can disable search in settings,
# the admin in the admin panel (🔎 Web search). Without keys it uses DuckDuckGo/Wikipedia; for reliability
# get a free Tavily (TAVILY_API_KEY) or Brave (BRAVE_API_KEY) key.
WEB_ENABLED = env_int("WEB_SEARCH", 1)             # 0 = search disabled by default


TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")


WEB_MAX_ROUNDS = env_int("WEB_MAX_ROUNDS", 2)      # how many times the AI may search per reply


WEB_RESULTS = 5                                    # how many results to give the model


WEB_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


# --- files ---
MAX_FILE_MB = env_int("MAX_FILE_MB", 10)           # max document file size


MAX_DOC_CHARS = env_int("MAX_DOC_CHARS", 30000)    # how many characters of a file are sent to the model


HISTORY_DOC_CHARS = env_int("HISTORY_DOC_CHARS", 12000)  # how many characters of a file remain in conversation memory


MAX_DOCS = env_int("MAX_DOCS", 5)                  # max files in one message (album)


# Admin IDs, comma-separated, e.g. ADMIN_IDS=123456789 (the /myid command shows your own ID)
ADMIN_IDS = {
    int(x)
    for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")).replace(" ", "").split(",")
    if x.isdigit()
}


AVAILABLE_MODELS = [
    m.strip()
    for m in os.getenv("MODELS", "deepseek-v4.1-flash,deepseek-v4-flash,deepseek-v4-pro").split(",")
    if m.strip()
]


# Per-user request limits (0 = unlimited). The admin can change them in the panel.
RATE_PER_MIN = env_int("RATE_PER_MIN", 5)


RATE_PER_DAY = env_int("RATE_PER_DAY", 100)


# Database. On Amvera the persistent storage is mounted at /data
DB_PATH = os.getenv("DB_PATH") or ("/data/bot.db" if os.path.isdir("/data") else "bot.db")


TZ_HOURS = env_int("TZ_HOURS", 4)                   # time zone for timestamps in the admin panel


EDIT_INTERVAL = env_float("EDIT_INTERVAL", 1.0)     # how often to update the message while streaming (sec)


CHUNK = 3500                                        # text chunk size for one Telegram message


PROMPT_MAX = 4000                                   # max length of the system prompt set by the admin


CUSTOM_PROMPT_MAX = 1000                            # max length of the user's custom instructions


EFFORTS = ("default", "low", "medium", "high", "xhigh")


TEST_MESSAGES = [
    {"role": "system", "content": "Answer as briefly as possible."},
    {"role": "user", "content": "Answer in one word: ok"},
]


MENU_VERSION = 3   # bottom menu version: when it changes, every user gets the updated keyboard once
