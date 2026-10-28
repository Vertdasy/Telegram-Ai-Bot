"""Regression tests for text conversion, chunking, and document extraction."""

import io
import os
import unittest
import zipfile

# The application config requires credentials at import time; these placeholders
# keep utility tests independent from a real Telegram bot or AI provider.
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("AI_API_KEY", "test-api-key")

from aibot.config import MAX_FILE_MB
from aibot.utils.files import FileProblem, _clean_text, _decode_text, extract_text, is_file_too_large
from aibot.utils.text import md_to_html, photo_tag, split_markdown


class TextUtilityTests(unittest.TestCase):
    def test_markdown_html_is_escaped(self):
        rendered = md_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_markdown_bold_inline_code_and_link(self):
        rendered = md_to_html("**bold** `x < y` [site](https://example.com)")
        self.assertIn("<b>bold</b>", rendered)
        self.assertIn("<code>x &lt; y</code>", rendered)
        self.assertIn('<a href="https://example.com">site</a>', rendered)

    def test_unclosed_code_block_is_closed(self):
        rendered = md_to_html("```python\nprint('hello')")
        self.assertEqual(rendered.count("<pre><code"), 1)
        self.assertEqual(rendered.count("</code></pre>"), 1)

    def test_photo_tag_singular_and_plural(self):
        self.assertEqual(photo_tag(1), "[Photo]")
        self.assertEqual(photo_tag(3), "[Photo x3]")
        self.assertEqual(photo_tag(0), "[Photo]")

    def test_split_long_plain_text(self):
        chunks = split_markdown("x" * 25, limit=10)
        self.assertEqual(chunks, ["x" * 10, "x" * 10, "x" * 5])

    def test_split_preserves_balanced_code_fences(self):
        source = "```py\n" + "print('x')\n" * 8 + "```"
        chunks = split_markdown(source, limit=24)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.count("```") % 2, 0)


class FileUtilityTests(unittest.TestCase):
    def test_file_size_limit_boundary(self):
        limit = MAX_FILE_MB * 1024 * 1024
        self.assertFalse(is_file_too_large(None))
        self.assertFalse(is_file_too_large(0))
        self.assertFalse(is_file_too_large(limit))
        self.assertTrue(is_file_too_large(limit + 1))

    def test_clean_text_normalizes_newlines_and_nulls(self):
        self.assertEqual(_clean_text(" a\r\nb\x00\r\n\n\nc "), "a\nb\n\nc")

    def test_decode_utf8_with_bom(self):
        self.assertEqual(_decode_text(b"\xef\xbb\xbfhello"), "hello")

    def test_decode_cp1251_text(self):
        self.assertEqual(_decode_text("Привет".encode("cp1251")), "Привет")

    def test_extract_plain_text_file(self):
        self.assertEqual(extract_text("notes.txt", "text/plain", b"hello\r\nworld"), "hello\nworld")

    def test_empty_file_has_user_facing_problem(self):
        with self.assertRaises(FileProblem) as raised:
            extract_text("empty.txt", "text/plain", b"  \n ")
        self.assertEqual(raised.exception.key, "doc_empty")

    def test_extract_docx_paragraph(self):
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Hello document</w:t></w:r></w:p></w:body></w:document>'
        ).encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("word/document.xml", xml)
        self.assertEqual(extract_text("note.docx", None, archive.getvalue()), "Hello document")

    def test_docx_rejects_entity_declaration(self):
        xml = (
            '<!DOCTYPE w:document [<!ENTITY x "unsafe">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>'
        ).encode()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("word/document.xml", xml)
        with self.assertRaises(FileProblem) as raised:
            extract_text("note.docx", None, archive.getvalue())
        self.assertEqual(raised.exception.key, "doc_bad")

    def test_unsupported_binary_extension_has_user_facing_problem(self):
        with self.assertRaises(FileProblem) as raised:
            extract_text("program.exe", "application/octet-stream", b"MZ")
        self.assertEqual(raised.exception.key, "doc_unsupported")


if __name__ == "__main__":
    unittest.main()
