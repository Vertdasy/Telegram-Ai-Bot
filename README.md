# Telegram AI Bot

[Русская версия](README.ru.md) · Live demo: [@aibottext_bot](https://t.me/aibottext_bot)

A feature-rich Telegram chatbot powered by any **OpenAI-compatible API** (DeepSeek, OpenAI, OpenRouter, …).
Built with Python, **aiogram 3** and **SQLite**. Fully localized (🇷🇺 / 🇬🇧) — including the admin panel — and
configured entirely from inside Telegram.

## Features

**For users**
- 💬 Streaming answers with Markdown → Telegram HTML rendering and automatic message splitting
- 🖼 Photos (up to 6, albums supported) and 📎 documents: txt, code, docx, pdf, xlsx, pptx
- 🔎 Web search tool (Tavily / Brave / keyless fallback) the model calls when it needs fresh data
- 🎭 Modes: Default, Translator, Programmer, Editor — plus personal custom instructions (`/myprompt`)
- 🧠 Conversation memory, 📌 saved messages, 🔁 regenerate / continue buttons, ⏹ Stop button, request queue
- 📤 `/export` the chat as a file, 🎁 referral bonuses, 🌐 language picker

**For admins** (`/admin`, localized)
- 📊 Statistics, user list and user cards, ban / unban, broadcast, error log, DB backup
- 🧠 Model switch, fallback model, speed ("fast" / "thoughtful") levels, speed test
- 📝 Per-mode prompt editor, 🧮 memory size, ⏱ rate limits, 🔎 web search, 🎁 referral settings
- 🔗 Traffic-source tracking via `https://t.me/<bot>?start=<tag>` links, 🔧 maintenance mode

## Project structure

```
main.py                    # entry point
aibot/
├── app.py                 # wiring: middlewares, routers, polling
├── config.py              # environment variables / .env loader
├── i18n.py                # JSON locale loader, t(lang, key, **kwargs)
├── state.py               # bot, dispatcher, routers, shared runtime state
├── middlewares.py         # user registration, bans, maintenance mode
├── database/              # SQLite access: users, history, pinned, settings, migrations
├── services/              # AI streaming, prompts, limits, web search, referrals
├── handlers/
│   ├── user/              # start, modes, settings, memory, generation, photos, documents, text
│   └── admin/             # panel, views, input (FSM), broadcast
├── keyboards/             # user.py, admin.py
├── utils/                 # text/markdown, Telegram helpers, file extraction, tasks
└── locales/
    ├── ru/                # user.json, admin.json, modes.json
    └── en/
tests/                     # dependency-free tests (locale parity)
```

## Quick start

Requires Python 3.10+.

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Get your Telegram ID from [@Getmyid_bot](https://t.me/Getmyid_bot) — this makes you the admin.
3. Get an API key from any OpenAI-compatible provider (and note its base URL and model name).
4. Configure:
   ```bash
   cp .env.example .env     # then fill in TELEGRAM_TOKEN, AI_API_KEY, ADMIN_IDS
   ```
5. Run:
   ```bash
   pip install -r requirements.txt
   python main.py
   ```
6. Open your bot in Telegram, send `/admin` and tune everything from there.

### Hosting

Works on any host that runs Python (Amvera, Railway, Render, a VPS, …). Upload the whole project, set the
start command to `python main.py`, and either upload `.env` or define the same names as environment
variables in the host panel (panel variables take priority). Make sure the database path is on persistent
storage: by default `/data/bot.db` is used if `/data` exists, otherwise `./bot.db` (override with `DB_PATH`).

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | — | **Required.** Bot token |
| `AI_API_KEY` | — | **Required.** Provider API key |
| `ADMIN_IDS` | — | Admin Telegram IDs, comma separated |
| `AI_BASE_URL` | `https://ai.starimg.ru/v1` | OpenAI-compatible endpoint |
| `ID_MODEL` / `VISION_MODEL` / `FALLBACK_MODEL` | DeepSeek models | Main, photo and fallback models |
| `MODELS` | 3 DeepSeek models | Models selectable in the admin panel |
| `TAVILY_API_KEY` / `BRAVE_API_KEY` | — | Optional web search backends |
| `WEB_SEARCH` | `1` | Web search on by default (0 = off) |
| `MEMORY_LEN` | `30` | Messages kept per user |
| `RATE_PER_MIN` / `RATE_PER_DAY` | `5` / `100` | Default limits |
| `MAX_FILE_MB`, `MAX_DOC_CHARS`, `MAX_DOCS` | `10`, `30000`, `5` | Document limits |
| `REF_BONUS` / `REF_MAX` | `20` / `25` | Referral bonus and max invited friends |
| `TZ_HOURS` | `4` | UTC offset used for admin timestamps |
| `DB_PATH` | see above | SQLite file location |

More tuning variables (timeouts, queue size, stream edit interval, …) are documented in `aibot/config.py`.

## Localization

All texts live in JSON files under `aibot/locales/<lang>/`:

- `user.json` — user-facing texts and bot commands (`cmd_*` keys)
- `admin.json` — admin panel texts (`adm_*` keys)
- `modes.json` — default prompt and the modes (title, description, prompt)

A value is a string or a list of lines. Placeholders use `{name}`.

**Adding a language:** copy `locales/en` to `locales/<code>`, translate the values, and set `lang_name`
(e.g. `"🇩🇪 Deutsch"`) in `user.json`. The language shows up in the picker automatically. Run
`python -m unittest discover tests` to verify all languages have identical keys and placeholders.
The admin panel uses the admin's own language; the 🌐 button in the panel switches it.

## Tests

```bash
python -m unittest discover tests
```

## License

[MIT](LICENSE)
