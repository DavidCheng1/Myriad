# Architektur

Myriad ist ein Multi-Agenten-System, das wie eine digitale Organisation arbeitet. Der Nutzer spricht ausschließlich mit **Atlas**, dem Main Orchestrator. Einfache Anliegen beantwortet Atlas selbst. Fachagenten zieht Atlas nur hinzu, wenn sie einen echten Mehrwert bringen.

## Prinzipien
1. **Ein Ansprechpartner.** Nutzer ⇄ Atlas. Fachagenten arbeiten im Hintergrund und antworten nur Atlas (Stern-Topologie).
2. **Agenten sind Daten.** Jeder Agent ist eine YAML-Definition. Eine generische Runtime führt alle Agenten aus.
3. **Schnittstellen mit Verträgen.** Interfaces als `typing.Protocol`. Austauschbar ist eine Implementierung erst, wenn sie die Verträge unten erfüllt (Ausführung, Lebenszyklus, Budget).
4. **Die Runtime entscheidet über Rechte, nicht das Modell.**
5. **Tokeneffizienz durch Organisation**, nicht durch schwächere Modelle.
6. **Frameworks spät.** Externe Frameworks kommen hinter bestehende Interfaces, sobald eine Phase sie braucht.

## Interaktionsmodell

```
                 Nutzer
                   │  Dialog (nur mit Atlas)
                   ▼
               ┌───────┐
               │ Atlas │  versteht, entscheidet, antwortet
               └───┬───┘
       Briefing ┌──┴──────┬──────────┐   Ergebnis (kompakt)
                ▼         ▼          ▼
              Sage      Nova   …   Aegis      Fachagenten
```

**Entscheidungsleiter.** Atlas wendet sie auf jedes Anliegen an:
1. Ist das Anliegen unklar? Dann eine gezielte Rückfrage. Das ist günstiger als falsche Arbeit.
2. Ist die Antwort einfach oder schon im Gedächtnis? Dann antwortet Atlas selbst.
3. Braucht es Fachwissen? Dann an **genau einen** passenden Fachagenten delegieren.
4. Weitere Agenten nur, wenn nötig, und nacheinander.

Die Antwort an den Nutzer formuliert Atlas immer selbst.

## Tokeneffizienz durch Organisation
Gespart wird durch kluge Arbeitsteilung. Das Modell richtet sich nach den Anforderungen der Aufgabe.
- **Need-to-know:** Fachagenten erhalten ein Briefing (Ziel, nötiger Kontext, erwartetes Ergebnis, Ausgabelimit), nie den Gesprächsverlauf.
- **Delegation nur mit Mehrwert:** Eine Direktantwort braucht 1 LLM-Aufruf, eine Delegation etwa 3 (Atlas, Fachagent, Konsolidierung).
- **Kompakte Rückgaben:** Fachagenten liefern knappe, strukturierte Ergebnisse.
- **Erinnern statt neu erarbeiten:** Ergebnisse und Entscheidungen werden gespeichert. Eine wiederholte Frage löst keine neue Delegation aus.
- **Code vor LLM:** Was sich deterministisch entscheiden lässt, erledigt Code: Validierung, Rechte, Status, Formatierung.
- **Nur Verfügbares im Prompt:** Der Prompt nennt nur geladene Delegationsziele und vorhandene Tools. Das verhindert Aufrufe, die sicher scheitern. Die Rechte prüft weiterhin der Code.
- **Stabile Prompt-Präfixe:** Der Systemprompt aus der Definition ist statisch und steht vorne. So greift Provider-Caching, wo es verfügbar ist.
- **Knapper Verlauf:** Der Gesprächsverlauf wird gekürzt. LLM-Zusammenfassungen kosten selbst Tokens und kommen nicht im MVP.
- **Transparenz:** Der Tokenverbrauch ist nach jeder Antwort sichtbar.

## Datenfluss

```
 Nutzer ⇄ Chat-Schnittstelle (CLI)
                 │
                 ▼
┌──────────────────────────────────────────┐
│ Task Engine   Status, Eltern/Kind,       │
│               Grenzen, Protokoll         │
└────────────────────┬─────────────────────┘
                     ▼
┌──────────────────────────────────────────┐
│ Agent Runtime (Atlas oder Fachagent)     │
│   Context Manager → Prompt               │
│   LLM Gateway     → Decision             │
│   Dispatcher      → answer | ask_user |  │
│                     delegate | tool_call │
└───────┬──────────────────────┬───────────┘
        ▼                      ▼
  Memory Layer            Tool Registry

  Token Tracker ──► Resource Manager   (Prüfung vor jedem LLM-Aufruf)
```

## Komponenten

| Komponente | Verantwortung | Kernschnittstelle (Skizze) |
|---|---|---|
| **Chat-Schnittstelle** | Dialog zwischen Nutzer und Atlas, zuerst als CLI. Zeigt den Tokenverbrauch je Antwort an. | `ChatSession.send(text) -> Reply` |
| **Agent Definition** | YAML mit `soul`, `job`, `behavior`, `skills`, `memory`, `llm`, `limits`. Wird beim Laden gegen ein Schema validiert. | `load_agent(path) -> AgentDefinition` |
| **Agent Runtime** | Ein Schritt ist genau ein Modellaufruf: Kontext → Vorabprüfung durch die Engine → LLM → Validierung. Der Provider wird übergeben. | `AgentRuntime.step(task, history, preflight) -> StepResult` |
| **Task Engine** | Verwaltet verbindlich Task-Status, Eltern-Kind-Beziehungen und Grenzen. Führt Delegation synchron aus und protokolliert Nachrichten. Atlas entscheidet fachlich, wer was tut. Die Task Engine setzt das um und begrenzt es. Später ggf. LangGraph. | `TaskEngine.run(task) -> TaskResult` |
| **LLM Gateway** | Provider-unabhängig. Liefert eine `Decision` und den Tokenverbrauch. Abstrakte Tiers (`fast`, `reasoning`) werden in `config/models.yaml` auf konkrete Modelle gemappt, inkl. Kontextfenster. Adapter: `MockProvider` (Standard) und `OllamaProvider` (lokal, Phase 9). | `LLMProvider.complete(messages, max_output_tokens, temperature) -> Completion` |
| **Memory Layer** | Privates und gemeinsames Gedächtnis über Namespaces. | `MemoryStore.write(record)`, `.search(namespace, query, limit)` |
| **Context Manager** | Baut den Prompt innerhalb des Budgets und kürzt nach fester Priorität. | `ContextManager.build(system_prompt, task, history, input_limit) -> BuiltContext` |
| **Token Tracker** | Erfasst `TokenUsage` je Aufruf, Agent, Task und Task-Baum. | `TokenTracker.record(usage)`, `.total(filter)` |
| **Resource Manager** | Prüft Budget, Schritte und Delegationstiefe vor jedem Aufruf. | `ResourceManager.check(task, request) -> Allowed \| Denied` |
| **Tool Registry** | Gibt jedem Agenten nur die Tools frei, die unter `skills.tools` stehen. | `ToolRegistry.get(agent, name) -> Tool` |
| **Integrationen** | Externe Anbindung, später n8n. | offen |

## Ausführungsvertrag
Jeder LLM-Aufruf eines Agenten endet in genau einer `Decision`:

| Typ | Inhalt | Verarbeitet von |
|---|---|---|
| `answer` | Ergebnis mit `outcome`: `done`, `needs_input` oder `failed`. Atlas antwortet dem Nutzer, ein Fachagent antwortet Atlas. | Task Engine |
| `ask_user` | Rückfrage an den Nutzer, **nur Atlas**. Fachagenten melden fehlende Infos als `answer` mit `needs_input`. | Chat-Schnittstelle |
| `delegate` | Ziel-Agent und Briefing | Task Engine (erzeugt Kind-Task) |
| `tool_call` | Toolname und Argumente | Tool Registry |

- Die Runtime validiert jede `Decision` (Typ, Pflichtfelder, Rechte), bevor sie wirkt. Ist sie ungültig, bekommt das Modell eine Fehlermeldung. Nach höchstens 2 Korrekturversuchen wird der Task `failed`.
- `delegate` geht nur an geladene Agenten aus `job.delegates_to`. Delegation und Rückfrage sind Entscheidungstypen, keine Tools.
- **Format (Entscheidung Phase 4):** genau ein JSON-Objekt pro Antwort. Validiert wird es als Pydantic-Union, die am Feld `type` unterscheidet, mit festen Pflichtfeldern. Unbekannte Felder werden abgewiesen. Mock und echte Provider erfüllen denselben Vertrag.
- **Entscheidungsmodus (Qualitätsrunde):** Ein Provider arbeitet entweder im Modus `json` (das Modell schreibt Decision-JSON; Mock) oder im Modus `tools` (Ollama-Standard).
  - Im Modus `tools` antwortet das Modell in freiem Text. Handlungen sind native Tool-Aufrufe: `delegate`, `ask_user` bzw. `need_more_info` bei Kind-Tasks, `report_failure`, Memory-Tools.
  - `llm/actions.py` übersetzt Tool-Aufrufe in die unveränderte Decision. Frühere Delegationen und Tool-Aufrufe gehen als native Tool-Aufrufe zurück an das Modell.
  - Grund: Kleine Modelle antworten in freiem Text deutlich besser als in einem JSON-Textfeld. Im Benchmark stieg die Trefferquote von 16 auf 20 von 21.
- Jeder Modellaufruf zählt als Schritt, auch ein Korrekturversuch. Der Verbrauch wird auch bei ungültigen Ausgaben gebucht.
- **Provider-Fehler** (nicht erreichbar, Zeitüberschreitung, Modell fehlt, HTTP-Fehler) werden zu `ProviderError` und führen zu einem kontrollierten Abbruch mit Grund `provider_error`. Eine am Ausgabelimit abgeschnittene Antwort läuft in den Korrekturpfad.
- **Sprache:** Alles, was das Modell liest, ist englisch: Definitionen, Systemprompt und Rückmeldungen der Engine. Doku und CLI-Beschriftungen bleiben deutsch.

### Ollama-Adapter (Phase 9)
- Nutzt Ollamas eigenen lokalen Endpunkt `/api/chat` über die Standardbibliothek, ohne Zusatzpaket und ohne Kosten.
- `format` erhält das JSON-Schema der Decision-Union (`oneOf` wird zu `anyOf`, ohne `discriminator`). Ollama erzwingt damit gültiges JSON, die eigene Validierung bleibt als zweite Prüfung.
- `num_ctx` = Kontextfenster aus der Konfiguration, `num_predict` = reservierte Ausgabe, `think: false`.
- Der Tokenverbrauch kommt echt von Ollama (`prompt_eval_count`, `eval_count`). Fehlen die Werte, wird geschätzt.

## Task-Lebenszyklus
- **Status:** `open → running → done | failed | needs_input`. Nach der Antwort des Nutzers geht `needs_input → running`.
- Ein Nutzeranliegen ist ein Root-Task. Jede Delegation erzeugt einen Kind-Task. Im MVP läuft das **synchron und nacheinander**, mit genau einem offenen Auftrag je Kind, und nur Atlas delegiert (Tiefe 1).
- Endet ein Kind mit `failed` oder einer überschrittenen Grenze, erhält Atlas das Ergebnis mit Status. Atlas wählt dann einen anderen Weg, stellt eine Rückfrage oder meldet den Fehlschlag transparent. Ein fehlgeschlagenes Kind wird nie als Erfolg ausgegeben.
- Die Task Engine bricht bei überschrittener Grenze (Schritte, Budget, Tiefe) ab. Der Task wird `failed`, mit Angabe des Grunds.
- `ask_user` setzt denselben Task auf `needs_input`. Die nächste Nachricht setzt ihn mit gleicher ID, gleichem Budget und gleichem Schrittzähler fort. `/new` verwirft ihn. Ohne offenen Task bekommt jede Nachricht einen neuen Root-Task.
- **Erfolgsregel:** Ist die letzte Aktion eines Tasks fehlgeschlagen, kann er nicht `done` werden, auch wenn das Modell Erfolg meldet. Gelingt danach ein neuer Versuch, ist Erfolg wieder möglich.
  - *Bewusst akzeptierte Einschränkung:* Der Code prüft nicht, ob der erfolgreiche Folgeversuch denselben Teilauftrag betrifft, denn ein verbesserter Versuch hat absichtlich ein anderes Briefing. Neu bewerten, sobald mehrere Fachagenten in einem Anliegen arbeiten.
  - Jede fehlgeschlagene Art bleibt einzeln offen, bis ein Erfolg derselben Art folgt: eine Delegation durch eine Delegation, ein Tool durch dasselbe Tool. Ein erfolgreicher Tool-Aufruf hebt eine fehlgeschlagene Delegation nicht auf.
- **Kind-Tasks** erhalten nur das Briefing (`[BRIEF from …]`), keinen Verlauf. Das `output_limit` des Briefings ist ihre Ausgabegrenze. Ihr Ergebnis geht als `[ENGINE DATA]` an den Eltern-Task.
  - Das Briefing enthält zusätzlich die **Anfrage des Nutzers im Wortlaut** (Ziel und letzte Nutzernachricht). So hängt der Fachagent nicht allein von Atlas' Umschreibung ab.
- **Organisationsprofil** (`config/context.md`): ein kurzer, fester Kontext für alle Agenten im Systemprompt. Er erklärt, was MYRIAD ist, das Team, die Rahmenbedingungen und Begriffe, z. B. „Agenten“ = KI-Agenten, nie Menschen. Dazu kommen Fähigkeitskarten der geladenen Fachagenten und Beispiele aus `behavior.examples`.
- **Denken pro Agent:** `llm.thinking` und `llm.thinking_budget`, aktuell nur bei Sage. Die Reservierung umfasst Antwort plus Denkbudget. Die Ausgabegrenze gilt nur für die sichtbare Antwort.
- **Protokoll:** Delegationen und Tool-Aufrufe werden als `Message` (`request`/`response`) mit Absender und Empfänger festgehalten.

## Budgets & Kontext
- **Ein Budget je Task-Baum:** `limits.token_budget_task_tree` des Root-Agenten. Kind-Tasks, Korrekturversuche und Zusammenfassungen werden alle darauf gebucht.
- **Vor jedem Aufruf** gilt: Eingabetokens + `max_output_tokens` ≤ min(Kontextfenster des Modells, Restbudget). Sonst wird gekürzt oder abgebrochen.
- **Kürzungspriorität:** Systemanweisungen und aktuelle Aufgabe bleiben immer erhalten. Danach kommen Briefing und Memory (festes Teilbudget), zuletzt der Verlauf, aus dem die ältesten Teile zuerst wegfallen.
- **Stand MVP (Phase 8):**
  - `input_limit` = min(Kontextfenster, Restbudget des Task-Baums) − reservierte Ausgabe. Bei Kind-Tasks ist die reservierte Ausgabe min(YAML-Limit, Briefing-Limit).
  - **Pflichtkontext**, nie gekürzt: Systemprompt, `[TASK GOAL]`, aktuelle Nutzereingabe und alle Nachrichten danach. Passt er nicht, folgt ein kontrollierter Abbruch (`input_too_large` oder `budget_exhausted`).
  - **Optionaler Memory-Block:** Er kommt vor dem Verlauf. Passt er nicht vollständig, wird er übersprungen und als `[info]` protokolliert.
  - **Verlauf:** zuerst ältere Nachrichten des Tasks, dann frühere Sitzungsnachrichten. Er wird vom neuesten zum ältesten als zusammenhängender Block aufgefüllt, höchstens 1.200 Tokens, ohne Kürzen und ohne LLM-Zusammenfassung. Eine große frühere Eingabe fällt dadurch einfach heraus und blockiert nichts.
  - Tokens werden beim Mock geschätzt (Zeichen ÷ 4). Die Vorabprüfung bleibt als letzte Sicherung bestehen.

## Sicherheitsgrenzen
- Identität und Projektkontext setzt die Runtime. Angaben des Modells dazu werden ignoriert.
- Rechte werden bei jeder Operation geprüft: Tool, Memory, Delegation.
- **Namespaces:** `agent:<id>` ist nur für diesen Agenten zugänglich. Ein `shared:<name>` nur, wenn es exakt in der Definition steht, ohne Wildcards. Projekt-Namespaces kommen später über den Task-Kontext.
- Memory-Inhalte und Agentennachrichten stehen als gekennzeichnete Daten im Prompt, getrennt von Systemanweisungen. Sie können keine Rechte ändern.
- **Herkunft** ist ein Feld (`ChatMessage.origin`), das nur Engine und Session setzen. Präfixe wie `[ENGINE DATA]` kennzeichnen Daten nur für das Modell. Tippt ein Nutzer ein solches Präfix, bleibt es Nutzertext.
- Aktionen mit externer Wirkung brauchen eine technisch geprüfte Freigabe des Nutzers für genau diese Aktion. Die Mechanik kommt mit dem ersten solchen Tool. Im MVP gibt es keines.

## Memory-Modell
- **Arten (`kind`):** `fact`, `decision`, `episode`, `preference`
- **Herkunft:** Jeder Eintrag trägt `agent_id` und `source_task_id`.
- **Ersetzung:** Neue Entscheidungen oder Präferenzen verweisen per `supersedes` auf die alte. Die Suche liefert nur aktuelle Einträge.
- **Ablauf:** `expires_at` ergibt sich aus `retention`. Abgelaufene Einträge werden bei der Suche ignoriert.
- **Zugriff:** `MemoryAccess` ist an einen Agenten gebunden. Rechte, Herkunft (`agent_id`, `source_task_id`) und Retention kommen aus dem YAML und der Engine, nie aus Modellausgaben. Eine Ersetzung erfolgt nur bei gleichem Namespace, gleicher Art und gleichem Subject, und zwar in einer Transaktion.
- **Wiederverwendung (Stand Phase 7):**
  - `subject` ist die normalisierte Anfrage: Kleinschreibung, zusammengefasster Leerraum, kein `.!?` am Ende. Alle anderen Zeichen bleiben, sodass „C++“ und „C#“ verschieden sind. Bewusst ein wörtlicher Abgleich ohne Vektorsuche.
  - `reusable` setzt nur die Engine: für Root-Tasks mit `done`, mindestens einer erfolgreichen Delegation und ohne Rückfrage an den Nutzer. Fehlschläge und Einträge aus Tools sind nie wiederverwendbar.
  - Vorab-Abruf durch Code vor dem ersten Modellaufruf: Nur ein aktueller, wiederverwendbarer Eintrag mit gleichem Subject kommt infrage. Er wird als `[MEMORY DATA]` mit `origin=engine` angehängt, wenn der vollständige Block in 800 Tokens passt (freie Pläne haben etwa 400 Tokens). Andernfalls läuft der normale Ablauf, abgeschnitten wird nie.
  - Gespeichert wird nur eine Antwort mit Substanz, also mindestens 200 Zeichen. Meta-Antworten wie „Sage ist bereit“ landen nicht im Gedächtnis.
- **Tools:** `search_memory` (wörtlich, höchstens 3 gekürzte Treffer) und `write_memory` (nur in den eigenen privaten Namespace, nur Arten mit `store`-Regel). Die Argumente werden streng validiert.
- **Erweiterung:** SQLite zuerst. Mem0, PostgreSQL und Obsidian kommen später als weitere `MemoryStore`-Adapter.

## Skill-System (Phase 10a)
- **Format:** [Agent Skills](https://agentskills.io/specification.md): `skills/<name>/SKILL.md` mit Frontmatter (`name`, `description`; optional `license`, `compatibility`, `metadata`, `allowed-tools`) und Markdown-Anleitung. Der Loader prüft die Regeln des Standards streng, z. B. dass der Name zum Ordner passt und `metadata` nur Strings enthält.
- **MYRIAD-Felder in `metadata`:**
  - `myriad-when`: `request` (Standard) oder `delegation-result`
  - `myriad-triggers`: kommagetrennte Wörter
- **Zuordnung:** `skills.library` in der Agent-Definition. Unbekannte Namen stoppen den Start.
- **Auswahl durch Code, nicht durch das Modell:**
  - Direkt nach einem Fachagenten-Ergebnis gilt nur ein `delegation-result`-Skill.
  - Sonst gewinnt der Skill mit den meisten passenden Wörtern in der Anfrage. Bei Atlas ist das Ziel plus letzte Nutzernachricht, bei Fachagenten das Briefing.
  - Höchstens ein Skill pro Aufruf.
- **Einbindung:** Der Skill wird ans **Ende des Systemprompts** angehängt. Er ist vertrauenswürdige Anleitung, und der feste Anfang des Prompts bleibt cachefähig.
  - Priorität: Pflichtkontext > Memory > Skill > Verlauf.
  - Höchstens 600 Tokens. Größere Skills werden übersprungen, nie gekürzt.
  - Protokolliert als `[info] skills -> <agent>: activated <name>`.
- **Gelernt (Benchmark):** Anleitungen dafür, *was in einen Tool-Aufruf gehört*, gehören in die **Feldbeschreibungen des Tools**, nicht in einen Skill. Ein Skill „brief-writing“ führte dazu, dass das 8B-Modell die Checkliste als Antwort schrieb, statt zu delegieren. Deshalb wurde er entfernt, und die Hinweise stehen jetzt im `delegate`-Tool.
- **Aktuelle Skills:**
  - `decision-record` (Atlas): Empfehlung, Gründe, Trade-off, nächster Schritt
  - `result-check` (Atlas, nach einem Ergebnis): Ergebnis prüfen und fast unverändert weitergeben
  - `project-planning` (Sage): Ziel, Schritte mit prüfbarem Ergebnis, Abhängigkeiten, Risiken, erster Schritt, innerhalb des Limits

## Forge und Executor-Agenten (Phase 10b)
- **Executor-Agent:** Eine Agent-Definition mit `executor: <name>` arbeitet nicht über das lokale Modell. Die Engine gibt das Briefing direkt an einen externen Executor. Das spart lokale Aufrufe und vermeidet 8B-Fehler beim bloßen Weiterreichen. Soul und Regeln gehen als Zusatz zum Systemprompt an den Executor.
- **Forge** (`executor: coding`) nutzt **Claude Code headless** über das Abo des Nutzers:
  - Aufruf: `claude -p` mit JSON-Ausgabe, Prompt über stdin, `--permission-mode acceptEdits`, `--allowedTools` aus der Konfiguration (alles andere wird abgelehnt), `--no-session-persistence`, `--strict-mcp-config`.
  - API-Schlüssel-Variablen werden aus der Umgebung entfernt, damit nur das Abo genutzt wird.
  - Programm: das neueste in der VS-Code-Erweiterung gebündelte `claude.exe`, sonst `claude` im PATH.
- **Ablauf:**
  1. Ein eigener **Git-Worktree** und Branch `forge/<task_id>`. Der Checkout des Nutzers bleibt unberührt.
  2. Claude Code arbeitet im Worktree.
  3. Die Änderungen werden auf dem Branch committet.
  4. **Test-Gate** mit dem konfigurierten Testbefehl.
  5. Ergebnis wird als **Artefakt** gespeichert: Zusammenfassung, Diff, Testausgabe.
  6. Atlas erhält nur eine kompakte Meldung mit Artefakt-ID.
- **Freigabe:** Nur der Nutzer übernimmt Änderungen, per `/apply <id>` als Fast-Forward-Merge. Bei roten Tests braucht es `force`. `/discard` verwirft. `/review` und `/changes` zeigen den Stand. Es gibt **keine automatische Übernahme**.
- **Fehlerpfade:** `executor_error` (Claude Code fehlt, Zeitlimit, Fehler) und `tests_failed`. Bei roten Tests bleibt das Artefakt zur Prüfung erhalten.
- **Budget:** Der Verbrauch von Claude Code läuft über das Abo und zählt nicht ins lokale Token-Budget des Task-Baums.
- **Konfiguration:** `config/models.yaml → forge` (Ziel-Repo, Werkzeuge, Testbefehl, Zeitlimit). Fehlt das Repo oder Claude Code, ist Forge aus und nicht im Team.

## Kern-Datenmodelle (Skizze)

```
Task         id, parent_id, root_id, goal, brief, created_by, assigned_to,
             status (open|running|needs_input|done|failed), result, created_at
Decision     type (answer|ask_user|delegate|tool_call), payload
Message      id, task_id, sender, recipient,
             type (request|response|info|escalation), content, created_at
MemoryRecord id, namespace, agent_id, source_task_id, kind, content, subject,
             reusable, supersedes, created_at, expires_at
TokenUsage   id, task_id, root_id, agent_id, provider, model,
             input_tokens, output_tokens, cost_estimate, created_at
```

## Agenten

| ID | Rolle | Kurzbeschreibung |
|---|---|---|
| `atlas` | Main Orchestrator | Einziger Ansprechpartner des Nutzers. Beantwortet Einfaches selbst, delegiert Fachliches. |
| `sage` | Planner | Zerlegt Ziele in Pläne und Schritte |
| `nova` | Researcher | Recherchiert und bewertet Informationen |
| `orion` | Architect | Entwirft technische Strukturen und Schnittstellen |
| `forge` | Developer | Implementiert und testet Code |
| `muse` | Creative | Erarbeitet Ideen, Texte und Konzepte |
| `aegis` | Reviewer | Prüft Qualität, Korrektheit und Sicherheit |
| `nexus` | Integrator | Bindet externe Systeme an |

Der MVP umfasst nur Atlas und Sage. Weitere Agenten kommen bei nachgewiesenem Bedarf. [agents/atlas.yaml](../agents/atlas.yaml) ist die Vorlage für das Schema.

## Geplante Verzeichnisstruktur

```
Myriad/
├─ agents/               Agent-Definitionen (YAML)
├─ docs/                 Architektur, Roadmap
├─ src/myriad/
│  ├─ models.py          Task, Decision, Message, MemoryRecord, TokenUsage
│  ├─ interface/         Chat-Schnittstelle (CLI)
│  ├─ agents/            Laden/Validieren der Definitionen, Runtime, Dispatcher
│  ├─ engine/            Task Engine
│  ├─ llm/               Gateway, Provider-Adapter
│  ├─ memory/            MemoryStore-Interface, Adapter
│  ├─ context/           Context Manager
│  ├─ resources/         Token Tracker, Resource Manager
│  └─ tools/             Tool Registry, Tools
├─ tests/
└─ data/                 lokale SQLite-DB (nicht versioniert)
```

## Erweiterungspunkte

| Bereich | Start (MVP) | Später (Kandidaten, Entscheidung in der jeweiligen Phase) |
|---|---|---|
| LLM | Mock (Standard), Ollama lokal mit `qwen3:8b` (Phase 9) | weitere lokale Modelle je Tier; kostenpflichtige Cloud-APIs sind ausdrücklich nicht gewünscht |
| Ausführung | synchrone Task Engine | LangGraph, asynchrones Messaging, Parallelität |
| Memory | SQLite | Mem0, PostgreSQL, Obsidian |
| Schema-Validierung | Pydantic (entschieden in Phase 3), YAML über `yaml.safe_load` | – |
| Integrationen | – | n8n |
