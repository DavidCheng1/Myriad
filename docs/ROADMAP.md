# Roadmap

Kleine Phasen, die sich einzeln umsetzen lassen. Pro Sitzung genau eine Phase (siehe [CLAUDE.md](../CLAUDE.md)).
Status: ✅ erledigt · ⏳ als Nächstes · ⬜ offen

## MVP-Szenario (Abnahmekriterium)
Der Nutzer chattet über die CLI ausschließlich mit Atlas. Atlas versteht das Anliegen, beantwortet Einfaches selbst und zieht Sage nur bei Planungsbedarf hinzu.

| Fall | Beispiel | Erwartetes Verhalten | Max. LLM-Aufrufe |
|---|---|---|---|
| A · Einfache Frage | „Was kannst du für mich tun?“ | Atlas antwortet direkt | 1 |
| B · Fachaufgabe | „Plane ein 4-Wochen-Lernprojekt für Python.“ | Atlas delegiert mit Briefing an Sage, prüft das Ergebnis und antwortet selbst | 3 |
| C · Unklares Anliegen | „Mach mal einen Plan.“ | Atlas stellt eine Rückfrage, statt zu delegieren | 1 |
| D · Fehlschlag | Sage scheitert oder erreicht eine Grenze | Atlas meldet das transparent, kein Schein-Erfolg | 3 |

**Erfolgskriterien**
- Nur Atlas spricht mit dem Nutzer. Sage erhält nur das Briefing, nicht den Gesprächsverlauf (per Test geprüft).
- Fall B liefert einen Plan mit mindestens 3 Schritten.
- Budget je Task-Baum ≤ 15.000 Tokens. Der Verbrauch wird nach jeder Antwort angezeigt.
- Delegation und Entscheidungen sind im Protokoll nachvollziehbar.
- Alle Fälle laufen deterministisch mit dem Mock, in Phase 9 zusätzlich mit einem lokalen Modell.

## Übersicht

| # | Phase | Status |
|---|---|---|
| 1 | Architektur & Projektgrundlage | ✅ |
| 2 | Projektskelett | ✅ |
| 3 | Agent-Definitionen laden & validieren | ✅ |
| 4 | Kernverträge & Mock | ✅ |
| 5 | Atlas-Dialog | ✅ |
| 6 | Delegation an Sage | ✅ |
| 7 | SQLite-Memory | ✅ |
| 8 | Context Manager | ✅ |
| 9 | MVP-Abnahme mit lokalem Modell | ✅ |

> **Einmalige Ausnahme zu CLAUDE.md (genehmigt am 15.11.2026):** Die Phasen 2–5 wurden in einer Sitzung umgesetzt, damit Atlas eigenständig startbar ist. Jede Phase wurde einzeln gegen ihr Done-Kriterium geprüft und erst danach abgeschlossen. Ab Phase 6 gilt wieder eine Phase pro Sitzung.

## Phasen

**1 · Architektur & Projektgrundlage**
Ergebnis: `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `agents/atlas.yaml`, überarbeitet nach dem Codex-Review.

**2 · Projektskelett**
Ergebnis: `pyproject.toml`, `src/myriad/`, `tests/`, `.gitignore` (inkl. `data/`).
Done: `pytest` läuft grün mit einem Smoke-Test.

**3 · Agent-Definitionen laden & validieren**
Ergebnis: Schema und Loader. Entscheidung über die Validierungs-Library (z. B. Pydantic).
Done: `atlas.yaml` lädt fehlerfrei. Fehlerhafte YAMLs liefern verständliche Fehler.

**4 · Kernverträge & Mock**
Ergebnis: Modelle `Task`, `Decision`, `Message`, `TokenUsage`. Ein `LLMProvider`-Protocol und ein geskripteter `MockProvider`, der strukturierte `Decision`s samt Token-Zahlen liefert. Entscheidung über das Format: natives Tool-Calling oder JSON.
Done: Mock-Skripte für die Fälle A–D existieren. Ungültige `Decision`s werden erkannt.

**5 · Atlas-Dialog**
Ergebnis: Chat-CLI, `AgentRuntime` mit Dispatcher für `answer` und `ask_user`, eine Task Engine mit Schritt- und Budgetgrenze, Token-Tracking in-memory, Verbrauch je Antwort sichtbar.
Done: Fälle A und C bestehen. Der Fehlerpfad (max. 2 Korrekturversuche) ist getestet.
Stand: `delegate` und `tool_call` werden validiert und gegen die Rechte geprüft, aber noch nicht ausgeführt. Der Test mit fehlendem Sage gilt als Fehlerpfad. Der erfolgreiche Fall B bleibt Phase 6.

**6 · Delegation an Sage**
Ergebnis: `sage.yaml`. `delegate` läuft über die Task Engine (synchron, Tiefe 1). Briefing-Format und Rechteprüfung. Protokollmodell `Message` für die Kommunikation zwischen Agenten (in Phase 4 noch nicht nötig).
Done: Fälle B und D bestehen. Sage erhält nachweislich nur das Briefing. Delegation an nicht freigegebene Agenten wird abgewiesen.
Stand: Sage führt Kind-Tasks synchron aus, mit dem `output_limit` des Briefings als Ausgabegrenze. Der Prompt nennt nur geladene Agenten und vorhandene Tools. Delegationen und Tool-Aufrufe werden protokolliert und sind mit `/log` abrufbar.

**7 · SQLite-Memory**
Ergebnis: `MemoryStore`-Protocol und SQLite-Adapter (stdlib `sqlite3`) mit Namespace-Rechten, Herkunft, `supersedes` und Ablauf. Dazu eine minimale Tool Registry mit `search_memory` und `write_memory`.
Done: Eine Wiederholung von Fall B wird aus dem Gedächtnis beantwortet, mit höchstens 1 LLM-Aufruf und ohne neue Delegation.
Voraussetzungen:
- Das Ein-Aufruf-Ziel braucht einen begrenzten Memory-Abruf durch Code **vor** dem Modellaufruf: wenige Treffer, festes Token-Teilbudget.
- Ein gespeichertes Ergebnis darf nur wiederverwendet werden, wenn es aktuell ist (nicht abgelaufen oder ersetzt), erfolgreich war (`outcome: done`) und zur konkreten Aufgabe passt.

Stand: Seit dem 15.11.2026 arbeitet Codex bis vor Phase 9 als Judge: Er gibt Pläne frei und nimmt Ergebnisse ab, im Auftrag des Nutzers. Phase 7 wurde von Codex abgenommen. Eine wiederholte Fachanfrage wird nach einem Neustart mit 1 Aufruf aus dem Gedächtnis beantwortet. Nicht wiederverwendet werden Fehlschläge, Aufgaben mit Rückfragen, abweichende Anfragen und Tool-Einträge.

**8 · Context Manager**
Ergebnis: Kürzung nach fester Priorität, festes Memory-Teilbudget, Kürzung des Verlaufs ohne LLM-Zusammenfassung.
Done: Ein langer Dialog bleibt nachweislich im Budget. Systemanweisungen und aktuelle Aufgabe bleiben erhalten.
Stand: von Codex abgenommen. Der Kontext wird nach Tokens begrenzt: Pflichtkontext, dann ein optionaler Memory-Block, dann höchstens 1.200 Tokens Verlauf. Eine große frühere Eingabe blockiert keine Folgefragen mehr.

**9 · MVP-Abnahme mit lokalem Modell**
Ergebnis: Adapter für eine OpenAI-kompatible API (z. B. Ollama; LiteLLM prüfen). Tier-Mapping inkl. Kontextfenster per Konfiguration. Installation nur nach Freigabe.
Done: Die Fälle A–D bestehen mit dem lokalen Modell, Aufrufzahlen und Budget eingehalten. Der Mock bleibt Default.
Stand (15.11.2026):
- Ollama 0.40.16 läuft unter `D:\Ollama`, das Modell `qwen3:8b` liegt in `D:\Ollama\models`.
- Der Adapter ist umgesetzt, inklusive `ProviderError` (offener Punkt aus dem Codex-Review). Start mit `python -m myriad --provider ollama`.
- Abnahmelauf (`MYRIAD_LIVE=1 pytest tests/test_live_ollama.py`): 5 von 5 bestanden. Fall A mit 1 Aufruf, Fall B Atlas → Sage → Atlas mit 3 Aufrufen und Plan, Fall C mit Rückfrage, Fall D mit offener Fehlermeldung, Wiederholung von B aus dem Gedächtnis mit 1 Aufruf (~1.240 statt ~3.000 Tokens).
- *Bekannte Schwankung:* Fall B gelang nach einer zusätzlichen Regel in `atlas.yaml` in 10 von 12 Wiederholungen. Bei den übrigen fragte Atlas nach einem brauchbaren Plan noch nach Vorlieben, statt ihn direkt zu liefern. Mögliche nächste Schritte: niedrigere Temperatur oder eine gezielte Prompt-Anpassung.
Zielhardware (geprüft am 15.11.2026): RTX 4060 Ti mit 8 GB VRAM und 32 GB RAM. Geeignet ist ein 7–8B-Modell in 4-Bit-Quantisierung. Die Modelle kommen auf Laufwerk D (`OLLAMA_MODELS`), weil auf C nur etwa 41 GB frei sind. Kostenpflichtige Cloud-APIs sind ausdrücklich nicht gewünscht.

## Qualitätsrunde Atlas & Sage (nach Phase 9) ✅
Anlass: Atlas wirkte mit `qwen3:8b` weniger intelligent als nötig. Ein Vergleich zeigte, dass das bloße Modell ohne MYRIAD teils besser antwortete. Die Ursache lag also zu einem großen Teil im Aufbau, nicht nur in der Hardware.

Umgesetzt:
- native Tool-Aufrufe statt Antworten in JSON
- kompakter Prompt mit Organisationsprofil, Fähigkeitskarten und Beispielen
- Briefings mit der Anfrage im Wortlaut
- Ergebnisprüfung als Regel
- Denken für Sage
- Gedächtnis nur für Antworten mit Substanz

Messung mit `scripts/benchmark_atlas.py` (7 Fälle × 3 Läufe, echtes Modell):

| Fall | vorher | nachher |
|---|---|---|
| SQLite-Empfehlung | 1/3 | 3/3 |
| „Frag Sage nach weiteren Agenten“ (Missverständnis „Personal einstellen“) | 0/3 | 2/3 (1× sinnvolle Rückfrage) |
| Übrige Fälle | 3/3 | 3/3 |
| **Gesamt** | **16/21** | **20/21** |

Live-Abnahme: zweimal 5 von 5.

Preis:
- etwa 1.800 statt 1.000 Tokens pro Direktantwort (Profil, Beispiele, Tool-Beschreibungen, ausführlichere Antworten)
- Fall B etwa 30 statt 12 Sekunden, weil Sage nachdenkt

Weiterhin begrenzt durch das 8B-Modell: tiefe, projektspezifische Ratschläge (z. B. „wichtigster nächster Schritt“) bleiben eher allgemein. Für anspruchsvolle Arbeit ist Forge über Claude Code geplant.

## Meilenstein 10: Skills + Forge
Ausgangspunkt war eine Recherche zu offenen Tools (Hermes, Agent Skills, Goose, OpenHands, MCP, Promptfoo, Langfuse). Leitlinie: nichts übernehmen, was unsere Orchestrierung oder unser Gedächtnis dupliziert. Bausteine nur dort ergänzen, wo sie eigene Entwicklung ersparen.

| # | Phase | Status |
|---|---|---|
| 10a | Skill-Fundament (Agent-Skills-Format, Auswahl durch Code, erste Skills) | ✅ |
| 10b | Forge MVP: Claude Code headless (`claude -p`) in einem Git-Worktree, Artefakt-Ablage, Test-Gate, Freigabe durch den Nutzer | ✅ |
| 10c | Qualitätstor: Benchmark als Regressionstest mit Schwellen, Sicherheitstests gegen Prompt-Injection, Skill `test-and-fix`; Codex als zweiter Ausführender | ⏳ |

**10a · Ergebnis:**
- Loader nach Standard, Auswahl durch Code, Einbindung ins Ende des Systemprompts, Befehl `/skills`, 3 Skills
- Benchmark mit 2 neuen Fällen: **19/27 ohne → 26/27 mit Skills**
  - Plan mit Risiken: 0/3 → 3/3
  - Entscheidung mit Trade-off: 0/3 → 3/3
  - „Agenten über Sage“: 1/3 → 3/3
- Live-Abnahme 5/5, 163 Unit-Tests grün
- Wichtigste Erkenntnis: Anleitungen für Tool-Aufrufe gehören in die Tool-Beschreibung, nicht in einen Skill (siehe ARCHITECTURE)

**10b · Ergebnis:**
- Forge als Executor-Agent ohne lokalen Modellschritt
- Claude Code headless aus der VS-Code-Erweiterung, Git-Worktree pro Aufgabe, Test-Gate, Artefakt-Ablage
- Befehle `/changes`, `/review`, `/apply`, `/discard`
- Übungs-Repo über `scripts/create_practice_repo.py`
- Erster echter Lauf: Atlas → Forge → Claude Code hat `average_word_length` mit Tests ergänzt
  - etwa 65 s gesamt, 2 lokale Aufrufe, 4/4 Tests grün, Artefakt zur Freigabe
  - das Übungs-Projekt blieb bis zur Freigabe unberührt
- Benchmark weiterhin 26/27 (kein Routing-Fehler zu Forge), Live-Abnahme 5/5, 182 Unit-Tests grün

**Später, nach Bedarf:**
- MCP-Client (selektiv, höchstens etwa 5 Tools pro Agent)
- FTS5-Suche, Nutzerprofil, gespeicherte Sitzungen
- Streaming
- Skill-Vorschläge durch Atlas, nur nach Freigabe

## Danach (nach nachgewiesenem Bedarf, Reihenfolge offen)
- **Weitere Fachagenten** (Nova, Orion, Forge, Muse, Aegis, Nexus): einzeln, jeweils mit konkretem Anwendungsfall.
- **Resource Manager ausbauen:** Kostenschätzung, Berichte, Budgets je Zeitraum.
- **Ausführung erweitern:** LangGraph evaluieren, asynchrones Messaging, Parallelität. Erst wenn die sequenzielle Delegation nachweislich bremst.
- **Memory-Erweiterungen:** Mem0, PostgreSQL, Obsidian-Export.
- **n8n-Integration:** inkl. Freigabe-Mechanik für Aktionen mit externer Wirkung (Zuständigkeit: Nexus).
- **Cloud-Provider:** nur nach Freigabe.
