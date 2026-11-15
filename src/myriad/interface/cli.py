"""Interactive terminal chat with Atlas."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from myriad import __version__
from myriad.agents.definition import DefinitionError, load_agents
from myriad.config import ConfigError, ProviderName, build_provider, load_context, load_model_config
from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession, Reply
from myriad.memory.store import SQLiteMemoryStore
from myriad.models import FailureReason, TaskStatus
from myriad.skills.loader import SkillError, load_skills

HELP = """Befehle:
  /help    diese Hilfe
  /tokens  Tokenverbrauch der Sitzung
  /prompt  System-Prompt von Atlas anzeigen (aus agents/atlas.yaml)
  /log     Protokoll des letzten Anliegens (Tasks, Delegationen, Tool-Aufrufe)
  /skills  verfügbare Skills und welcher Agent sie nutzt
  /new    offene Rückfrage verwerfen, nächste Nachricht ist ein neues Anliegen
  /reset   neue Sitzung (Verlauf und Zähler leeren)
  /exit    beenden"""

REASONS = {
    FailureReason.INPUT_TOO_LARGE: "Eingabe zu groß",
    FailureReason.BUDGET_EXHAUSTED: "Budget erschöpft",
    FailureReason.STEP_LIMIT: "Schrittlimit erreicht",
    FailureReason.INVALID_OUTPUT: "ungültige Modellausgabe",
    FailureReason.REPORTED_BY_AGENT: "von Atlas als gescheitert gemeldet",
    FailureReason.UNRESOLVED_ACTION: "fehlgeschlagene Aktion",
    FailureReason.PROVIDER_ERROR: "Modellanbieter nicht verfügbar",
}


def default_agents_dir() -> Path:
    env = os.environ.get("MYRIAD_AGENTS_DIR")
    if env:
        return Path(env)
    repo_agents = Path(__file__).resolve().parents[3] / "agents"
    return repo_agents if repo_agents.is_dir() else Path.cwd() / "agents"


def default_data_dir() -> Path:
    env = os.environ.get("MYRIAD_DATA_DIR")
    if env:
        return Path(env)
    return default_agents_dir().parent / "data"


def default_config_path() -> Path:
    env = os.environ.get("MYRIAD_CONFIG")
    if env:
        return Path(env)
    return default_agents_dir().parent / "config" / "models.yaml"


def build_session(
    agents_dir: Path | None = None,
    data_dir: Path | None = None,
    provider: ProviderName | None = None,
) -> ChatSession:
    agents_dir = agents_dir or default_agents_dir()
    agents = load_agents(agents_dir)
    config_path = default_config_path()
    config = load_model_config(config_path)
    llm = build_provider(provider or config.default_provider, config, agents)
    context = load_context(config_path.parent / "context.md")
    skills = load_skills(agents_dir.parent / "skills")
    memory = SQLiteMemoryStore((data_dir or default_data_dir()) / "myriad.db")
    engine = TaskEngine(agents, llm, root_agent="atlas", memory=memory, context=context, skills=skills)
    return ChatSession(engine)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m myriad", description="Chat mit Atlas.")
    parser.add_argument(
        "--provider",
        choices=["mock", "ollama"],
        help="Modellanbieter (Standard aus config/models.yaml, normalerweise mock)",
    )
    return parser.parse_args(list(argv))


def _n(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def banner(session: ChatSession) -> str:
    atlas, provider = session.engine.root, session.engine.provider
    model = (
        f"{provider.name} - SIMULIERT, keine echte KI"
        if provider.simulated
        else f"{provider.name}/{provider.model} (lokal, Kontextfenster {_n(provider.context_window)})"
    )
    return (
        f"MYRIAD {__version__} · {atlas.name} v{atlas.version} ({atlas.role})\n"
        f"Modell: {model}\n"
        f"Agenten: {', '.join(sorted(session.engine.agents))} · Skills: {len(session.engine.skills)}\n"
        f"Gedächtnis: {'an (SQLite, bleibt nach Neustart erhalten)' if session.engine.memory else 'aus'}\n"
        f"Sitzung {session.id} · /help zeigt alle Befehle, /exit beendet."
    )


def format_reply(reply: Reply) -> str:
    status = reply.task.status
    speaker = "Atlas (simuliert)" if reply.simulated else "Atlas"
    asks = " fragt" if status is TaskStatus.NEEDS_INPUT else ""
    lines = [f"{speaker}{asks}: {reply.text}"]
    lines += [f"  Hinweis: {notice}" for notice in reply.notices]
    if status is TaskStatus.FAILED and reply.task.failure_reason is not None:
        reason = reply.task.failure_reason
        lines.append(f"  [Anliegen nicht erfüllt: {REASONS.get(reason, reason)} ({reason})]")
    if status is TaskStatus.NEEDS_INPUT:
        lines.append("  (Antworte direkt oder beginne mit /new ein neues Anliegen.)")
    approx = "~" if reply.estimated else ""
    note = " · Schätzung (Zeichen/4)" if reply.estimated else ""
    lines.append(
        f"  [Tokens {approx}+{_n(reply.turn_tokens)} · Anliegen {_n(reply.tree_tokens)}/"
        f"{_n(reply.tree_budget)} · Sitzung {_n(reply.session_tokens)} · "
        f"Modellaufrufe {reply.llm_calls}{note}]"
    )
    return "\n".join(lines)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def format_protocol(session: ChatSession) -> str:
    if not session.root_ids:
        return "Noch kein Anliegen in dieser Sitzung."
    root_id = session.root_ids[-1]
    tasks, messages = session.engine.protocol(root_id)
    lines = [f"Protokoll des letzten Anliegens ({root_id}):"]
    for task in tasks:
        reason = f" ({task.failure_reason})" if task.failure_reason else ""
        lines.append(
            f"{'  ' * (task.depth + 1)}Task {task.id} · {task.assigned_to} · {task.status}{reason}"
            f" · {task.steps} Schritt{'' if task.steps == 1 else 'e'}, "
            f"{task.corrections} Korrektur{'' if task.corrections == 1 else 'en'}"
        )
    for message in messages:
        lines.append(
            f"  [{message.type}] {message.sender} -> {message.recipient}: {_short(message.content)}"
        )
    return "\n".join(lines)


def format_skills(session: ChatSession) -> str:
    engine = session.engine
    if not engine.skills:
        return "Keine Skills geladen."
    lines = ["Skills (werden nur geladen, wenn eine Anfrage passt):"]
    for name, skill in engine.skills.items():
        users = [aid for aid, agent in engine.agents.items() if name in agent.skills.library]
        when = "nach einem Fachagenten-Ergebnis" if skill.when == "delegation-result" else "bei passender Anfrage"
        lines.append(f"  {name} · {', '.join(users) or 'kein Agent'} · {when}")
        lines.append(f"    {_short(skill.description, 150)}")
    return "\n".join(lines)


def _command(session: ChatSession, command: str) -> str:
    if command == "/help":
        return HELP
    if command == "/log":
        return format_protocol(session)
    if command == "/skills":
        return format_skills(session)
    if command == "/tokens":
        estimated = any(r.estimated for r in session.engine.tracker.records)
        return (
            f"Sitzung {session.id}: {'~' if estimated else ''}{_n(session.total_tokens())} Tokens "
            f"in {len(session.root_ids)} Anliegen{' (Schätzung)' if estimated else ''}."
        )
    if command == "/prompt":
        return session.engine.runtimes[session.engine.root.id].system_prompt
    if command == "/new":
        if session.abandon_pending():
            return "Offene Rückfrage verworfen. Deine nächste Nachricht startet ein neues Anliegen."
        return "Es gibt keine offene Rückfrage."
    return "Unbekannter Befehl. /help zeigt alle Befehle."


def main(
    argv: Sequence[str] = (),
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    args = parse_args(argv)
    try:
        session = build_session(provider=args.provider)
    except (DefinitionError, ConfigError, SkillError, ValueError) as exc:
        out(f"Fehler beim Start von Atlas: {exc}")
        return 1
    out(banner(session))
    try:
        return _loop(session, input_fn, out)
    finally:
        if session.engine.memory is not None:
            session.engine.memory.close()


def _loop(session: ChatSession, input_fn: Callable[[str], str], out: Callable[[str], None]) -> int:
    while True:
        try:
            line = input_fn("\nDu: ").replace("﻿", "").strip()  # piped input may carry a BOM
        except (EOFError, KeyboardInterrupt):
            out("\nSitzung beendet.")
            return 0
        if not line:
            continue
        if line.startswith("/"):
            command = line.split()[0].lower()
            if command in ("/exit", "/quit"):
                out("Sitzung beendet.")
                return 0
            if command == "/reset":
                session.abandon_pending()
                session = ChatSession(session.engine)
                out(f"Neue Sitzung {session.id} gestartet.")
                continue
            out(_command(session, command))
            continue
        out(format_reply(session.send(line)))
