"""Memory tools. Arguments are validated strictly; origin, agent and retention are set by
code, and tool-written entries are never marked reusable."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from myriad.memory.access import MemoryAccess, MemoryPermissionError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchMemoryArgs(_Args):
    query: Annotated[str, Field(min_length=1, max_length=200)]


class WriteMemoryArgs(_Args):
    kind: Literal["fact", "decision", "episode", "preference"]
    content: Annotated[str, Field(min_length=1, max_length=2000)]


TOOL_HINTS: dict[str, str] = {
    "search_memory": '{"query": "<text>"} - search your readable memory (max 3 hits)',
    "write_memory": '{"kind": "fact|decision|episode|preference", "content": "<text>"} '
                    "- store a note in your private memory (only kinds your store rules allow)",
}
# Native tool-calling specs (OpenAI/Ollama function format) for the same tools.
TOOL_SPECS: dict[str, dict[str, Any]] = {
    "search_memory": {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search your readable memory for earlier notes and results (max 3 hits).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Words to look for"}},
                "required": ["query"],
            },
        },
    },
    "write_memory": {
        "type": "function",
        "function": {
            "name": "write_memory",
            "description": "Store a short note in your private memory (only kinds your store rules allow).",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["fact", "decision", "episode", "preference"]},
                    "content": {"type": "string", "description": "The note"},
                },
                "required": ["kind", "content"],
            },
        },
    },
}
MAX_HITS = 3
MAX_HIT_CHARS = 200


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_HIT_CHARS else text[: MAX_HIT_CHARS - 3] + "..."


def run_memory_tool(
    name: str, arguments: dict[str, Any], access: MemoryAccess, task_id: str
) -> tuple[str, bool]:
    """Execute a memory tool. Returns (feedback, ok)."""
    try:
        if name == "search_memory":
            args = SearchMemoryArgs.model_validate(arguments)
            hits = access.search(args.query, limit=MAX_HITS)
            if not hits:
                return "search_memory: no hits.", True
            listed = "; ".join(f"[{h.kind}] {_clip(h.content)}" for h in hits)
            return f"search_memory: {len(hits)} hits: {listed}", True
        if name == "write_memory":
            args = WriteMemoryArgs.model_validate(arguments)
            record = access.write(kind=args.kind, content=args.content, source_task_id=task_id)
            return f"write_memory: stored ({record.id}, {record.kind}).", True
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(p) for p in first["loc"]) or "arguments"
        return f"Tool '{name}' rejected: invalid arguments ({location}: {first['msg']}).", False
    except MemoryPermissionError as exc:
        return f"Tool '{name}' rejected: {exc}.", False
    return f"Tool '{name}' failed: unknown tool.", False
