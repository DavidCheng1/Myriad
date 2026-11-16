MYRIAD is a local multi-agent system written in Python that the user is building. It works like a small digital organization:
- Atlas is the user's single point of contact and leads the team. Specialists do focused work on self-contained briefs from Atlas and report back only to Atlas.
- "Agents" and "specialists" in MYRIAD always mean AI agents, never human employees. An agent is a YAML definition in agents/ (soul, job, behavior, skills, memory, llm, limits); new abilities come from tools written in Python.
- Current team: Atlas (lead), Sage (planner) and Forge (developer: Claude Code implements and tests code changes in an isolated copy of the project; every change waits for the user's /apply). Planned: Aegis (reviewer), Orion (architect), Muse (creative), Nova (researcher), Nexus (integrations, e.g. n8n).
- Everything runs locally: an 8B model (qwen3:8b) via Ollama on an RTX 4060 Ti with 8 GB VRAM, memory in SQLite. Paid cloud APIs are not used.
The user is a developer and speaks English and German.
