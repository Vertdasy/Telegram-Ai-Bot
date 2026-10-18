"""Text extraction from user files: plain text/code, docx, pdf, xlsx, pptx."""

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from aibot.config import MAX_DOC_CHARS


# --- files ---
TEXT_EXTS = {
    "txt", "md", "markdown", "rst", "log", "csv", "tsv", "json", "jsonl", "xml", "yaml", "yml", "toml", "ini",
    "cfg", "conf", "env", "properties", "html", "htm", "css", "scss", "js", "jsx", "ts", "tsx", "py", "pyw",
    "ipynb", "java", "kt", "kts", "scala", "c", "h", "cpp", "hpp", "cc", "cs", "go", "rs", "php", "rb", "swift",
    "dart", "lua", "pl", "r", "sql", "sh", "bash", "zsh", "bat", "ps1", "gradle", "tex", "srt", "vtt", "diff",
    "patch", "gitignore", "dockerfile", "makefile", "vue", "svelte", "m", "asm", "vb", "tf",
}


IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class FileProblem(Exception):
    """The file cannot be read; key is the user-facing text key."""

    def __init__(self, key: str, **kw):
        super().__init__(key)
        self.key = key
        self.kw = kw


def _file_ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise FileProblem("doc_unsupported")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("latin-1")
    head = text[:5000]
    bad = sum(1 for ch in head if ord(ch) < 32 and ch not in "\n\r\t")
    if head and bad / len(head) > 0.05:
        raise FileProblem("doc_unsupported")
    return text


def _open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FileProblem("doc_bad")
    if sum(i.file_size for i in zf.infolist()) > 150 * 1024 * 1024:
        raise FileProblem("doc_bad")   # guard against archive bombs
    return zf


def _xml_root(zf: zipfile.ZipFile, name: str):
    raw = zf.read(name)
    if b"<!ENTITY" in raw:
        raise FileProblem("doc_bad")
    return ET.fromstring(raw)


def _docx_text(data: bytes) -> str:
    zf = _open_zip(data)
    if "word/document.xml" not in zf.namelist():
        raise FileProblem("doc_bad")
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = _xml_root(zf, "word/document.xml")
    paras = []
    for p in root.iter(w + "p"):
        parts = []
        for el in p.iter():
            if el.tag == w + "t" and el.text:
                parts.append(el.text)
            elif el.tag == w + "tab":
                parts.append("\t")
            elif el.tag == w + "br":
                parts.append("\n")
        paras.append("".join(parts))
    return "\n".join(paras)


def _pptx_text(data: bytes) -> str:
    zf = _open_zip(data)
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    slides = sorted(
        (n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: int(re.findall(r"\d+", n)[-1]),
    )
    if not slides:
        raise FileProblem("doc_bad")
    out = []
    for n, name in enumerate(slides, 1):
        root = _xml_root(zf, name)
        lines = []
        for p in root.iter(a + "p"):
            line = "".join(t.text or "" for t in p.iter(a + "t")).strip()
            if line:
                lines.append(line)
        out.append(f"[Slide {n}]\n" + "\n".join(lines))
    return "\n\n".join(out)


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else "A"):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx_text(data: bytes) -> str:
    zf = _open_zip(data)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    names = zf.namelist()
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in _xml_root(zf, "xl/sharedStrings.xml").iter(ns + "si"):
            shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
    sheets = sorted(
        (n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
        key=lambda n: int(re.findall(r"\d+", n)[-1]),
    )
    if not sheets:
        raise FileProblem("doc_bad")
    out = []
    for n, name in enumerate(sheets[:5], 1):
        root = _xml_root(zf, name)
        rows = []
        for row in root.iter(ns + "row"):
            cells: dict[int, str] = {}
            for c in row.iter(ns + "c"):
                kind = c.get("t")
                v = c.find(ns + "v")
                if kind == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                    val = shared[int(v.text)]
                elif kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(ns + "t"))
                elif v is not None and v.text is not None:
                    val = v.text
                else:
                    continue
                cells[_col_index(c.get("r", ""))] = val.replace("\t", " ").replace("\n", " ")
            if cells:
                rows.append("\t".join(cells.get(i, "") for i in range(max(cells) + 1)))
        if rows:
            out.append(f"[Sheet {n}]\n" + "\n".join(rows))
    return "\n\n".join(out)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise FileProblem("pdf_nolib")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise FileProblem("pdf_locked")
        out, total = [], 0
        for page in reader.pages[:150]:
            txt = page.extract_text() or ""
            out.append(txt)
            total += len(txt)
            if total > MAX_DOC_CHARS * 2:
                break
    except FileProblem:
        raise
    except Exception:
        raise FileProblem("doc_bad")
    text = "\n\n".join(out)
    if not text.strip():
        raise FileProblem("doc_empty_scan")
    return text


def extract_text(name: str, mime: str | None, data: bytes) -> str:
    """Extracts text from a file (txt/code/docx/pdf/xlsx/pptx). Raises FileProblem on failure."""
    ext = _file_ext(name)
    mime = (mime or "").lower()
    if ext == "pdf" or mime == "application/pdf":
        text = _pdf_text(data)
    elif ext == "docx":
        text = _docx_text(data)
    elif ext in ("xlsx", "xlsm"):
        text = _xlsx_text(data)
    elif ext == "pptx":
        text = _pptx_text(data)
    elif ext in ("doc", "xls", "ppt", "zip", "rar", "7z", "exe", "apk", "mp4", "mov", "avi", "mkv"):
        raise FileProblem("doc_unsupported")
    else:
        text = _decode_text(data)   # text and code; binary files are rejected
    text = _clean_text(text)
    if not text:
        raise FileProblem("doc_empty")
    return text
