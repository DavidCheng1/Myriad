# CLAUDE.md – Entwicklungsregeln für Myriad

Myriad ist ein modulares Multi-Agenten-System, das wie eine digitale Organisation arbeitet.
Der Nutzer spricht nur mit Atlas (Main Orchestrator). Atlas zieht Fachagenten bei Bedarf hinzu.
Ziel und Aufbau: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Phasenplan und Status: [docs/ROADMAP.md](docs/ROADMAP.md).

## Arbeitsweise
- **Streng iterativ:** Pro Sitzung genau eine Phase aus der Roadmap. Ablauf: kurzer Plan → Freigabe des Nutzers → Umsetzung → stoppen.
- **Nur was die Phase verlangt.** Keine Vorarbeit für spätere Phasen, keine spekulativen Abstraktionen.
- **MVP zuerst:** Jede Phase bringt das MVP-Szenario aus der Roadmap voran. Interfaces folgen den Verträgen in `docs/ARCHITECTURE.md`.
- Nach jeder Phase den Status in `docs/ROADMAP.md` aktualisieren.
- Commits nur auf ausdrücklichen Wunsch.

## Ressourcen
- Keine Subagenten, Agent Teams oder parallelen Sitzungen.
- Keine API-Kosten: Standard ist der Mock-LLM-Provider. Echte Modelle nur nach Freigabe.
- **Tokeneffizienz ist ein Designziel.** Erreicht wird sie durch Arbeitsteilung und knappen Kontext, nicht durch schwächere Modelle (siehe `docs/ARCHITECTURE.md`).
- Packages nur nach Freigabe installieren. Neue Abhängigkeiten in der Phase begründen.
- Keine externen Dienste starten (Datenbankserver, n8n, Modellserver), solange die Phase es nicht verlangt.
- Nur gezielt recherchieren, mit möglichst wenigen Tool-Aufrufen.

## Quellen der Wahrheit
- `docs/ARCHITECTURE.md`: Komponenten, Schnittstellen, Datenmodelle
- `docs/ROADMAP.md`: Phasen, Done-Kriterien, Status
- `agents/*.yaml`: Agent-Definitionen (`agents/atlas.yaml` ist die Vorlage für das Schema)
- `skills/*/SKILL.md`: Skills im Agent-Skills-Format. Änderungen immer mit `scripts/benchmark_atlas.py` messen, mit und ohne `--no-skills`.

Wenn Code und diese Dokumente voneinander abweichen, die Abweichung benennen und nicht stillschweigend auflösen.

## Konventionen
- Doku auf Deutsch. Code, Bezeichner, YAML-Keys und Commit-Messages auf Englisch.
- Python ≥ 3.11, durchgehend Type Hints.
- Schnittstellen als `typing.Protocol`, Implementierungen austauschbar dahinter.
- Kleine Module mit einer klaren Verantwortung. Kein globaler Zustand.
- Tests mit `pytest`. Jede Phase bringt Tests für ihr Ergebnis mit. Normale Tests laufen ohne Modell. Live-Tests gegen Ollama laufen nur mit `MYRIAD_LIVE=1`.
- Frameworks (z. B. LangGraph, Mem0) erst festlegen, wenn eine Phase sie braucht. Etablierte Lösungen bevorzugen und nichts selbst nachbauen.
- Doku kurz, präzise und modular halten.
