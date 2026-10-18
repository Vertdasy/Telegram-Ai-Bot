"""Text helpers: Markdown to Telegram HTML conversion, message splitting, timestamps."""

import html
import re
from datetime import datetime, timedelta, timezone

from aibot.config import CHUNK, TZ_HOURS


# Photo-question marker in history: "[Photo]" or "[Photo x3]" (the legacy "[Фото]" variant is also recognised)
PHOTO_RE = re.compile(r"^\[(?:Photo|Фото)(?: x(\d+))?\]\s*")


def photo_tag(n: int) -> str:
    return "[Photo]" if n <= 1 else f"[Photo x{n}]"


def md_to_html(text: str) -> str:
    """Converts the AI's Markdown to HTML that Telegram understands."""
    if text.count("```") % 2:
        text += "\n```"  # unclosed code block (e.g. during streaming)

    stash: list[str] = []

    def keep(s: str) -> str:
        stash.append(s)
        return f"\x00{len(stash) - 1}\x00"

    def code_block(m):
        lang = m.group(1)
        code = m.group(2).strip("\n")
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return keep(f"<pre><code{cls}>{html.escape(code, quote=False)}</code></pre>")

    text = re.sub(r"```([\w+#.-]*)[ \t]*\n?(.*?)```", code_block, text, flags=re.S)
    text = re.sub(r"`([^`\n]+)`", lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)

    text = html.escape(text, quote=False)

    text = re.sub(r"^[ ]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"^([ \t]*)[-*+][ \t]+", r"\1• ", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.S)
    # in "Telegram Markdown" (requested by the system prompt) single *asterisks* mean BOLD and italics use _underscores_
    text = re.sub(r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)\"]+)\)", r'<a href="\2">\1</a>', text)

    text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text.strip()


def split_markdown(text: str, limit: int = CHUNK) -> list[str]:
    """Splits a long reply into chunks without breaking code blocks."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunk, text = text[:cut], text[cut:].lstrip("\n")
        if chunk.count("```") % 2:
            chunk += "\n```"
            text = "```\n" + text
        chunks.append(chunk)
    chunks.append(text)
    return chunks


def fmt_ts(ts, fmt: str = "%d.%m %H:%M") -> str:
    if not ts:
        return "—"
    tz = timezone(timedelta(hours=TZ_HOURS))
    return datetime.fromtimestamp(ts, tz=tz).strftime(fmt)
