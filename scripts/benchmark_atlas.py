"""Quality benchmark for Atlas with a real local model.

Usage: .venv\\Scripts\\python.exe scripts\\benchmark_atlas.py [--runs 3] [--out results.json]
Needs a running Ollama with the model from config/models.yaml. Nothing is stored in memory.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from myriad.agents.definition import load_agents
from myriad.config import build_provider, load_context, load_model_config
from myriad.engine.task_engine import TaskEngine
from myriad.interface.session import ChatSession, Reply
from myriad.models import TaskStatus
from myriad.skills.loader import load_skills

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ("I'm building MYRIAD, a multi-agent system in Python. Atlas is the orchestrator, Sage the planner. "
           "Everything runs on a local 8B model via Ollama on an RTX 4060 Ti with 8 GB VRAM.")
HIRING = re.compile(r"recruit|hiring|candidate|job post|interview", re.IGNORECASE)


def children(session: ChatSession) -> list:
    return [t for t in session.engine.tasks.values() if not t.is_root]


def plan_steps(text: str) -> int:
    numbered = len(re.findall(r"(?:^|\s)\d+[.)]\s", text))
    bullets = len(re.findall(r"(?m)^\s*[-*•]\s", text))
    weeks = len(re.findall(r"\bweek\s*\d", text, re.IGNORECASE))
    return max(numbered, bullets, weeks)


def recommends_sqlite(text: str) -> bool:
    lower = text.lower()
    first_sqlite, first_pg = lower.find("sqlite"), lower.find("postgres")
    return first_sqlite != -1 and (first_pg == -1 or first_sqlite < first_pg)


@dataclass
class Case:
    name: str
    turns: list[str]
    check: Callable[[ChatSession, Reply], bool]
    expectation: str


CASES = [
    Case("A direct", ["What can you do for me?"],
         lambda s, r: r.task.status is TaskStatus.DONE and r.llm_calls == 1, "done, 1 call"),
    Case("expert SQLite",
         ["SQLite or PostgreSQL for the memory of a single-user local agent system? Give one recommendation with reasons."],
         lambda s, r: r.task.status is TaskStatus.DONE and recommends_sqlite(r.text), "recommends SQLite"),
    Case("reasoning hours",
         ["I have 3 hours a day, 5 days a week, for 4 weeks. How many hours is that in total, "
          "and how would you split them between theory and practice?"],
         lambda s, r: r.task.status is TaskStatus.DONE and "60" in r.text, "says 60 hours"),
    Case("B plan via Sage", ["Plan a 4-week learning project for Python."],
         lambda s, r: r.task.status is TaskStatus.DONE and [c.status for c in children(s)] == [TaskStatus.DONE]
         and r.llm_calls <= 3 and plan_steps(r.text) >= 3, "delegated, plan >= 3 steps, <= 3 calls"),
    Case("meta: agents via Sage", ["Ask Sage how I should proceed to add more specialist agents to MYRIAD."],
         lambda s, r: r.task.status is TaskStatus.DONE and not HIRING.search(r.text) and "agent" in r.text.lower(),
         "about AI agents, not hiring"),
    Case("C unclear", ["Make me a plan."],
         lambda s, r: r.task.status is TaskStatus.NEEDS_INPUT and r.llm_calls == 1, "asks back, 1 call"),
    Case("context follow-up", [CONTEXT, "What is the single most important next step for me, and why?"],
         lambda s, r: r.task.status is TaskStatus.DONE
         and any(w in r.text.lower() for w in ("myriad", "atlas", "sage", "forge")), "uses the project context"),
    # Phase 10a: does a skill change the shape of the answer?
    Case("plan names risks", ["Plan a 4-week learning project for Python."],
         lambda s, r: r.task.status is TaskStatus.DONE and "risk" in r.text.lower(), "plan includes risks"),
    Case("decision with trade-off",
         ["Should I use a monorepo or separate repositories for two small Python services? One recommendation, please."],
         lambda s, r: r.task.status is TaskStatus.DONE and ("monorepo" in r.text.lower() or "separate" in r.text.lower())
         and TRADE_OFF.search(r.text) is not None, "recommends and names the trade-off"),
]
TRADE_OFF = re.compile(r"trade-?off|downside|drawback|give up|when .* better|however", re.IGNORECASE)


def run(runs: int, out: Path | None, use_skills: bool = True) -> None:
    agents = load_agents(ROOT / "agents")
    config = load_model_config(ROOT / "config" / "models.yaml")
    context = load_context(ROOT / "config" / "context.md")
    skills = load_skills(ROOT / "skills") if use_skills else {}
    results = []
    for case in CASES:
        passed, seconds, tokens = 0, 0.0, 0
        for i in range(runs):
            engine = TaskEngine(agents, build_provider("ollama", config, agents), context=context,
                                skills=skills)
            session = ChatSession(engine)
            start = time.time()
            for turn in case.turns:
                reply = session.send(turn)
            seconds += time.time() - start
            tokens += reply.tree_tokens
            ok = case.check(session, reply)
            passed += ok
            results.append({"case": case.name, "run": i + 1, "ok": ok, "status": str(reply.task.status),
                            "calls": reply.llm_calls, "tokens": reply.tree_tokens, "answer": reply.text})
        print(f"{case.name:<24} {passed}/{runs}  avg {seconds / runs:5.1f}s  avg {tokens // runs:5d} tokens"
              f"  ({case.expectation})")
    total = sum(r["ok"] for r in results)
    print(f"{'TOTAL':<24} {total}/{len(results)}")
    if out:
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-skills", action="store_true", help="run without the skill library (baseline)")
    args = parser.parse_args()
    run(args.runs, args.out, use_skills=not args.no_skills)
