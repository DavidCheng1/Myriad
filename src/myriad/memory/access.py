"""Memory access bound to one agent. Rights, origin and retention come from the agent
definition and the engine - never from model output."""

from __future__ import annotations

from datetime import timedelta

from myriad.agents.definition import AgentDefinition
from myriad.memory.store import Clock, MemoryKind, MemoryRecord, MemoryStore, _utc_now


class MemoryPermissionError(Exception):
    """A memory operation is not allowed by the agent definition."""


class MemoryAccess:
    def __init__(self, agent: AgentDefinition, store: MemoryStore, clock: Clock = _utc_now) -> None:
        self.agent = agent
        self.store = store
        self._now = clock

    @property
    def private_namespace(self) -> str:
        return self.agent.memory.private_namespace

    def readable(self) -> list[str]:
        return [self.private_namespace, *(ns.name for ns in self.agent.memory.shared_namespaces)]

    def can_write(self, namespace: str) -> bool:
        return namespace == self.private_namespace or any(
            ns.name == namespace and ns.access == "read_write"
            for ns in self.agent.memory.shared_namespaces
        )

    def retention_for(self, kind: str) -> str | None:
        return next((rule.retention for rule in self.agent.memory.store if rule.kind == kind), None)

    def write(
        self,
        *,
        kind: MemoryKind,
        content: str,
        source_task_id: str,
        namespace: str | None = None,
        subject: str = "",
        reusable: bool = False,
    ) -> MemoryRecord:
        namespace = namespace or self.private_namespace
        if not self.can_write(namespace):
            raise MemoryPermissionError(f"{self.agent.id} may not write to '{namespace}'")
        retention = self.retention_for(kind)
        if retention is None:
            raise MemoryPermissionError(f"{self.agent.id} has no store rule for kind '{kind}'")
        now = self._now()
        expires = None if retention == "permanent" else now + timedelta(days=int(retention[:-1]))
        return self.store.write(MemoryRecord(
            namespace=namespace, agent_id=self.agent.id, source_task_id=source_task_id,
            kind=kind, content=content, subject=subject, reusable=reusable,
            expires_at=expires, created_at=now,
        ))

    def search(self, query: str, namespace: str | None = None, limit: int = 3) -> list[MemoryRecord]:
        if namespace is not None and namespace not in self.readable():
            raise MemoryPermissionError(f"{self.agent.id} may not read '{namespace}'")
        namespaces = [namespace] if namespace else self.readable()
        hits = [hit for ns in namespaces for hit in self.store.search(ns, query, limit)]
        return sorted(hits, key=lambda r: r.created_at, reverse=True)[:limit]

    def find_reusable(self, subject: str) -> MemoryRecord | None:
        return self.store.find_reusable(self.private_namespace, subject)
