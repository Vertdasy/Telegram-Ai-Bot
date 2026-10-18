"""Dependency-free checks for the JSON locale files. Run: python -m unittest discover tests"""
import json
import re
import unittest
from pathlib import Path

LOC = Path(__file__).resolve().parent.parent / "aibot" / "locales"
LANGS = sorted(p.name for p in LOC.iterdir() if p.is_dir())
PH = re.compile(r"\{(\w+)\}")


def load(lang, name):
    return json.loads((LOC / lang / f"{name}.json").read_text(encoding="utf-8"))


class LocaleTests(unittest.TestCase):
    def test_languages_found(self):
        self.assertIn("ru", LANGS)
        self.assertIn("en", LANGS)

    def test_same_keys_in_every_language(self):
        for name in ("user", "admin"):
            base = set(load("en", name))
            for lang in LANGS:
                self.assertEqual(base, set(load(lang, name)), f"{lang}/{name}.json keys differ from en")

    def test_same_placeholders(self):
        for name in ("user", "admin"):
            base = load("en", name)
            for lang in LANGS:
                data = load(lang, name)
                for key, val in base.items():
                    a = sorted(PH.findall(json.dumps(val)))
                    b = sorted(PH.findall(json.dumps(data[key])))
                    self.assertEqual(a, b, f"{lang}/{name}.json: placeholders of '{key}' differ")

    def test_modes_have_same_keys(self):
        base = load("en", "modes")["modes"]
        for lang in LANGS:
            self.assertEqual(set(base), set(load(lang, "modes")["modes"]))


if __name__ == "__main__":
    unittest.main()
