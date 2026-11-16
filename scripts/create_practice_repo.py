"""Create the practice repository Forge works on first: data/projects/practice (git-ignored).

Usage: .venv\\Scripts\\python.exe scripts\\create_practice_repo.py
Idempotent: an existing repository is left untouched. Tests use only the standard library
(unittest), so Claude Code can run them with the system Python.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "data" / "projects" / "practice"

FILES = {
    "README.md": "# Practice project\n\nA small text-statistics module that MYRIAD's Forge practises on.\n"
                 "Run the tests with `python -m unittest discover -s tests -q`.\n",
    "textstats.py": '''"""Small text statistics - a practice project for MYRIAD's Forge."""


def word_count(text: str) -> int:
    """Number of whitespace-separated words."""
    return len(text.split())


def char_count(text: str, include_spaces: bool = True) -> int:
    """Number of characters, optionally without spaces."""
    return len(text) if include_spaces else len(text.replace(" ", ""))
''',
    "tests/__init__.py": "",
    "tests/test_textstats.py": '''import unittest

from textstats import char_count, word_count


class TextStatsTest(unittest.TestCase):
    def test_word_count(self) -> None:
        self.assertEqual(word_count("one two  three"), 3)
        self.assertEqual(word_count(""), 0)

    def test_char_count(self) -> None:
        self.assertEqual(char_count("a b"), 3)
        self.assertEqual(char_count("a b", include_spaces=False), 2)


if __name__ == "__main__":
    unittest.main()
''',
    ".gitignore": "__pycache__/\n",
}


def git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True)


def main() -> int:
    if (REPO / ".git").exists():
        print(f"Practice repository already exists: {REPO}")
        return 0
    REPO.mkdir(parents=True, exist_ok=True)
    for name, content in FILES.items():
        path = REPO / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=MYRIAD", "-c", "user.email=myriad@localhost", "commit", "-q", "-m", "Initial practice project")
    print(f"Created practice repository: {REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
