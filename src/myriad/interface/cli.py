"""Interactive terminal chat with Atlas."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from myriad import __version__
from myriad.agents.definition import DefinitionError, load_agents
from myriad.config import (
    ConfigError,
    ProviderName,
    build_coding_service,
    build_provider,
    load_context,
    load_model_config,
)
from myriad.executors.workspace import WorkspaceError
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
  /changes          offene Code-Änderungen von Forge
  /review <id>      Änderung ansehen (Zusammenfassung, Tests, Diff)
  /apply <id>       Änderung ins Projekt übernehmen (nur nach deiner Prüfung)
  /discard <id>     Änderung verwerfen
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
    FailureReason.EXECUTOR_ERROR: "Coding-Agent fehlgeschlagen",
    FailureReason.TESTS_FAILED: "Tests fehlgeschlagen",
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
    data_dir = data_dir or default_data_dir()
    agents = load_agents(agents_dir)
    config_path = default_config_path()
    config = load_model_config(config_path)
    llm = build_provider(provider or config.default_provider, config, agents)
    context = load_context(config_path.parent / "context.md")
    skills = load_skills(agents_dir.parent / "skills")
    coding, forge_status = build_coding_service(config.forge, agents_dir.parent, data_dir)
    if coding is None:  # executor agents only exist when their executor works
        agents = {aid: agent for aid, agent in agents.items() if agent.executor is None}
    memory = SQLiteMemoryStore(data_dir / "myriad.db")
    engine = TaskEngine(
        agents, llm, root_agent="atlas", memory=memory, context=context, skills=skills,
        executors={"coding": coding} if coding else None,
    )
    session = ChatSession(engine)
    session.info["Forge"] = forge_status
    return session


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
        + "".join(f"{key}: {value}\n" for key, value in session.info.items())
        + ""
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


def _coding(session: ChatSession):
    return session.engine.executors.get("coding")


def format_changes(session: ChatSession) -> str:
    coding = _coding(session)
    if coding is None:
        return "Forge ist nicht aktiv."
    pending = coding.pending()
    if not pending:
        return "Keine offenen Änderungen."
    lines = ["Offene Änderungen (/review <id>, /apply <id>, /discard <id>):"]
    for artifact in pending:
        tests = {True: "Tests grün", False: "Tests ROT", None: "keine Tests"}[artifact.tests_passed]
        files = artifact.diff_stat.splitlines()[-1].strip() if artifact.diff_stat else ""
        lines.append(f"  {artifact.id} · {tests} · {files} · {_short(artifact.title, 70)}")
    return "\n".join(lines)


def format_review(session: ChatSession, artifact_id: str, max_lines: int = 120) -> str:
    coding = _coding(session)
    artifact = coding.artifacts.get(artifact_id) if coding else None
    if artifact is None:
        return f"Keine Änderung mit der ID {artifact_id or '(fehlt)'}."
    tests = {True: "grün", False: "ROT", None: "nicht ausgeführt"}[artifact.tests_passed]
    diff = artifact.diff.splitlines()
    shown = "\n".join(diff[:max_lines])
    more = f"\n… {len(diff) - max_lines} weitere Zeilen (Worktree: {artifact.worktree})" if len(diff) > max_lines else ""
    return (
        f"Änderung {artifact.id} · Status {artifact.status} · Tests {tests}\n"
        f"Aufgabe: {artifact.title}\n\nZusammenfassung von Claude Code:\n{artifact.summary}\n\n"
        f"Dateien:\n{artifact.diff_stat}\n\nTest-Ausgabe (Ende):\n{artifact.test_output or '-'}\n\n"
        f"Diff:\n{shown}{more}"
    )


def _decide(session: ChatSession, command: str, args: str) -> str:
    coding = _coding(session)
    if coding is None:
        return "Forge ist nicht aktiv."
    parts = args.split()
    if not parts:
        return f"Bitte die ID angeben: {command} <id>"
    try:
        if command == "/apply":
            return coding.apply(parts[0], force="force" in parts[1:])
        return coding.discard(parts[0])
    except WorkspaceError as exc:
        return f"Nicht möglich: {exc}"


def _command(session: ChatSession, command: str, args: str = "") -> str:
    if command == "/help":
        return HELP
    if command == "/changes":
        return format_changes(session)
    if command == "/review":
        return format_review(session, args.strip())
    if command in ("/apply", "/discard"):
        return _decide(session, command, args)
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
            name, _, args = line.partition(" ")
            command = name.lower()
            if command in ("/exit", "/quit"):
                out("Sitzung beendet.")
                return 0
            if command == "/reset":
                session.abandon_pending()
                info = session.info
                session = ChatSession(session.engine)
                session.info.update(info)
                out(f"Neue Sitzung {session.id} gestartet.")
                continue
            out(_command(session, command, args))
            continue
        out(format_reply(session.send(line)))
