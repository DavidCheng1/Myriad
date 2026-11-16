"""Phase 5: terminal chat and `python -m myriad`."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from myriad.interface.cli import main


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Never touch the real data/myriad.db from tests."""
    monkeypatch.setenv("MYRIAD_DATA_DIR", str(tmp_path))
    return tmp_path


def test_cli_answers_repeated_request_from_memory(mvp_cases) -> None:
    inputs = iter([mvp_cases["B"], mvp_cases["B"], "/log", "/exit"])
    output: list[str] = []
    assert main(input_fn=lambda _prompt: next(inputs), out=output.append) == 0
    text = "\n".join(output)
    assert "Gedächtnis: an" in text
    assert "Aus dem Gedächtnis (bereits geprüft)" in text
    assert "Modellaufrufe 1" in text
    assert "[info] memory -> atlas: recalled" in text


def test_cli_memory_survives_restart(mvp_cases) -> None:
    first = iter([mvp_cases["B"], "/exit"])
    main(input_fn=lambda _prompt: next(first), out=lambda _line: None)
    second = iter([mvp_cases["B"], "/exit"])
    output: list[str] = []
    main(input_fn=lambda _prompt: next(second), out=output.append)
    assert "Aus dem Gedächtnis" in "\n".join(output)


def test_cli_flow(mvp_cases) -> None:
    inputs = iter([
        mvp_cases["A"], mvp_cases["C"], "/new", "/tokens", "/prompt", "/unbekannt",
        mvp_cases["B"], "/log", "Plane ein Projekt, das scheitern soll #fail", "/reset", "/exit",
    ])
    output: list[str] = []
    assert main(input_fn=lambda _prompt: next(inputs), out=output.append) == 0
    text = "\n".join(output)
    for expected in (
        "Modell: mock - SIMULIERT, keine echte KI",
        "Agenten: atlas, ",  # forge joins only where the practice repo and Claude Code exist
        "Atlas (simuliert): [SIMULIERT]",
        "Atlas (simuliert) fragt: [SIMULIERT]",
        "Offene Rückfrage verworfen",
        "## Character",
        "Unbekannter Befehl",
        "Ergebnis geprüft und zusammengefasst",
        "[request] atlas -> sage",
        "[response] sage -> atlas: done",
        "Hinweis: Delegation to 'sage' failed",
        "Anliegen nicht erfüllt",
        "Schätzung (Zeichen/4)",
        "Neue Sitzung",
        "Sitzung beendet.",
    ):
        assert expected in text, expected


def test_cli_strips_byte_order_mark_from_piped_input(mvp_cases) -> None:
    inputs = iter(["﻿" + mvp_cases["B"], "/log", "/exit"])
    output: list[str] = []
    main(input_fn=lambda _prompt: next(inputs), out=output.append)
    text = "\n".join(output)
    assert "﻿" not in text
    assert "1 Schritt, 0 Korrekturen" in text


def test_cli_reports_invalid_model_config(tmp_path, monkeypatch) -> None:
    broken = tmp_path / "models.yaml"
    broken.write_text("tiers: [", encoding="utf-8")
    monkeypatch.setenv("MYRIAD_CONFIG", str(broken))
    output: list[str] = []
    assert main(input_fn=lambda _prompt: "/exit", out=output.append) == 1
    assert "Fehler beim Start von Atlas" in output[0]


def test_cli_ends_on_eof() -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    assert main(input_fn=eof, out=lambda _line: None) == 0


def test_python_m_myriad_starts(mvp_cases, isolated_data_dir) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "myriad"],
        input=f"{mvp_cases['A']}\n/exit\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "MYRIAD_DATA_DIR": str(isolated_data_dir)},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Atlas (simuliert): [SIMULIERT]" in result.stdout
