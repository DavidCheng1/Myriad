# Unabhängiger Technical Review von MYRIAD

Stand 15. November 2026. Grundlage sind ausschließlich `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md` und `agents/atlas.yaml`.

## Gesamtbewertung

Die Grundstruktur ist technisch sinnvoll. Deklarative Agenten, eine gemeinsame Runtime, getrennte Provider- und Memory-Zugänge sowie SQLite und In-Process-Kommunikation passen zu einem kleinen MVP. Die Aufteilung lässt Verantwortlichkeiten erkennen. Wesentliche Verträge für die tatsächliche Ausführung bleiben jedoch offen. Austauschbarkeit über `Protocol` hängt zusätzlich von diesen Verhaltensverträgen ab.

Der geplante Umfang verzögert die Validierung des eigentlichen Multi-Agenten-Verhaltens. Vor der Implementierung sollten die folgenden fünf Punkte entschieden werden. Die nachweisbaren Probleme beziehen sich auf die vorliegenden Spezifikationen. Die jeweils beschriebenen Laufzeitfolgen sind mögliche Risiken.

## 1. Hoch – Der Vertrag zwischen Modellantwort und ausführbarer Aktion fehlt

**Dateibezug**

`docs/ARCHITECTURE.md`, Z. 39–48 und 59–66. `docs/ROADMAP.md`, Z. 41–44 und 56–69. `agents/atlas.yaml`, Z. 65–70.

**Nachweisbares Problem und Begründung**

Das Gateway liefert Text und Tokenverbrauch. Die Runtime soll daraus Tool-Aufrufe, Delegationen oder Antworten erzeugen. Das Format dieser Entscheidungen, die Struktur von `StepResult`, Tool-Argumente und Fehlerantworten sind undefiniert. Auch bleibt offen, wie `delegate_task` aus der Tool-Liste mit dem Orchestrator verbunden ist. Damit fehlt der zentrale Ausführungsvertrag bereits für den Single-Agent-Loop und anschließend für Delegation.

**Mögliches Risiko**

Runtime und Provider entwickeln unterschiedliche Interpretationen derselben Modellantwort. Ungültige Argumente, unbekannte Aktionen oder unvollständige Ausgaben führen zu unkontrollierten Wiederholungen oder falsch ausgeführten Aktionen. Ein deterministischer Text-Mock kann diese Lücke verdecken.

**Konkreter Verbesserungsvorschlag**

Vor dem Gateway eine kleine typisierte Entscheidungsstruktur für finale Antwort, Tool-Aufruf, Delegation und Rückfrage festlegen. Tool-Aufrufe brauchen einen Namen und schema-validierte Argumente. Die Runtime validiert und dispatcht Entscheidungen. Der Orchestrator übernimmt Task-Erzeugung und Statuswechsel. Fehlerhafte Modellausgaben erhalten einen definierten Fehlerpfad mit begrenzten Wiederholungen. Mock und echter Provider müssen denselben Vertrag erfüllen.

## 2. Hoch – Die Sicherheitsgrenzen sind deklarativ beschrieben, aber unvollständig spezifiziert

**Dateibezug**

`docs/ARCHITECTURE.md`, Z. 8, 44–48 und 51–64. `docs/ROADMAP.md`, Z. 51–54 und 66–69. `agents/atlas.yaml`, Z. 51–57 und 72–78.

**Nachweisbares Problem und Begründung**

Namespace- und Tool-Prüfungen sind ausdrücklich vorgesehen. Die Memory-Skizze erklärt jedoch weder eine vertrauenswürdige Aufruferidentität noch einen agentengebundenen Store-Zugriff. Atlas erhält `shared:project:*`, während Matching-Regeln und projektbezogene Einschränkungen fehlen. Die Eskalation bei irreversiblen oder extern wirksamen Aktionen steht ausschließlich als Verhaltensregel im YAML. Ein technischer Freigabezustand ist unbeschrieben. Ebenso fehlt eine Regel, wie Inhalte aus Memory und Agentennachrichten im Prompt als Daten behandelt werden.

**Mögliches Risiko**

Eine Implementierung vertraut vom Modell gelieferten Agenten- oder Namespace-Angaben. Die Wildcard erlaubt dann möglicherweise projektübergreifenden Zugriff. Gespeicherte oder weitergereichte Anweisungen können das Modell zu unzulässigen Aktionen bewegen. Tool-Freischaltung allein begrenzt dabei weder zulässige Argumente noch externe Auswirkungen.

**Konkreter Verbesserungsvorschlag**

Runtime-seitig eine unveränderliche Agentenidentität und einen Task-/Projektkontext an alle Zugriffe binden. Namespace-Matching eindeutig festlegen und Atlas für den MVP konkrete Projektfreigaben geben. Rechte bei jeder Operation einschließlich Delegation prüfen. Extern wirksame Aktionen benötigen vor ihrer Ausführung eine technisch geprüfte, an die konkrete Aktion gebundene Nutzerfreigabe. Memory und Nachrichten getrennt von Systemanweisungen einfügen. Nur die Runtime darf Rechte und Freigaben bestimmen.

## 3. Hoch – Messaging besitzt noch keinen definierten Task-Lebenszyklus

**Dateibezug**

`docs/ARCHITECTURE.md`, Z. 40–42 und 59–63. `docs/ROADMAP.md`, Z. 61–64. `agents/atlas.yaml`, Z. 31–34 und 47–56.

**Nachweisbares Problem und Begründung**

Nachrichten haben eine eigene ID und `task_id`. Eine Antwortzuordnung bei mehreren Requests desselben Tasks ist unbeschrieben. Für die Task-Status fehlen erlaubte Übergänge und Regeln zur Fortsetzung eines Eltern-Tasks nach Abschluss oder Fehlschlag eines Kindes. Atlas soll überwachen und neu zuweisen, während der Orchestrator ebenfalls Status und Delegationsketten verfolgt. Die verbindliche Zuständigkeit bleibt offen. Zeitlimits, Abbruch und der Umgang mit wiederholten oder verspäteten Antworten fehlen ebenfalls.

**Mögliches Risiko**

Ein Eltern-Task wartet unbegrenzt, konsolidiert ein veraltetes Ergebnis oder beendet sich trotz fehlgeschlagenem Kind erfolgreich. Wiederholte Zustellung beziehungsweise erneute Ausführung kann Aufgaben und Seiteneffekte duplizieren. Eine Delegationstiefe allein begrenzt keine wiederholten Nachrichten innerhalb derselben Ebene.

**Konkreter Verbesserungsvorschlag**

Für den MVP synchrone, sequenzielle Delegation mit einem offenen Request je Kind-Task festlegen. Alternativ Antworten explizit mit einer Request-ID korrelieren. Der Orchestrator verwaltet verbindlich Status, Eltern-Kind-Zuordnung, Deadline und Wiederholungszähler. Atlas schlägt fachliche Zuweisungen vor. Fehler- und Abbruchweitergabe sowie die Behandlung doppelter Ergebnisse definieren. Die Done-Kriterien um Kind-Fehlschlag, ausbleibende Antwort und doppelte Antwort ergänzen.

## 4. Hoch – Kontextgrenze und delegationsübergreifendes Tokenbudget sind nicht ausreichend definiert

**Dateibezug**

`docs/ARCHITECTURE.md`, Z. 43–47 und 51–66. `docs/ROADMAP.md`, Z. 71–79. `agents/atlas.yaml`, Z. 79–98.

**Nachweisbares Problem und Begründung**

Atlas hat 20.000 Tokens pro Task und maximal 1.024 Ausgabetokens. Eine eigenständige Eingabekontextgrenze und das Kontextfenster des zugeordneten Modells sind unbeschrieben. Die Roadmap verlangt ein konfiguriertes Kontextlimit, dessen Konfiguration im YAML fehlt. Unklar bleibt außerdem, ob Kind-Tasks dasselbe Gesamtbudget belasten und wie vor dem Aufruf erwartete Ausgabetokens berücksichtigt werden. Memory-Suche begrenzt die Trefferzahl, aber spezifiziert weder Treffergrößen noch ein Tokenbudget für Erinnerungen. Für dauerhaft gespeicherte Entscheidungen und Präferenzen fehlen Regeln zu Duplikaten und überholten Einträgen.

**Mögliches Risiko**

Wenige große Erinnerungen oder Delegationsergebnisse überschreiten das Modellfenster. Neue Kind-Tasks vervielfachen das verfügbare Budget, falls jeder sein eigenes Limit erhält. Ungefiltert gespeicherte Ergebnisse erhöhen langfristig Suchrauschen und Kontextverbrauch. Zusammenfassungen erzeugen zusätzliche LLM-Kosten.

**Konkreter Verbesserungsvorschlag**

Modellfenster, maximale Ausgabe und Gesamtbudget des Root-Tasks getrennt konfigurieren. Vor jedem Aufruf Eingabetokens plus reservierte Ausgabe gegen Modellfenster und Restbudget prüfen. Alle Kind-Tasks, Wiederholungen und LLM-Zusammenfassungen auf dasselbe Root-Budget buchen. Bei Parallelität Reservierungen atomar vornehmen. Für den MVP begrenzte SQLite-Suche, ein festes Memory-Tokenbudget und kurze Ergebniszusammenfassungen vorsehen. Sicherheitsanweisungen und aktuelle Aufgabe beim Kürzen erhalten. Memory-Einträge mit Herkunft, Aktualisierungs-/Ersetzungsregel und tatsächlich angewandtem Ablauf versehen.

## 5. Mittel – Der MVP wird zu spät als zusammenhängender Ablauf validiert

**Dateibezug**

`CLAUDE.md`, Z. 7–8 und 29–32. `docs/ROADMAP.md`, Z. 36–96. `agents/atlas.yaml`, Z. 31–40 und 47–50.

**Nachweisbares Problem und Begründung**

Die Roadmap trennt Loader, Gateway, Tracking, Memory, Loop, Delegation, Tools, Kontext und Ressourcen in aufeinanderfolgende Phasen. Delegation kommt vor der Registry ihrer deklarierten Tools. Kontext- und Ressourcenbegrenzung folgen auf die ersten Agenten-Loops. Erst Phase 12 prüft ein echtes Modell. Ein fachliches MVP-Abnahmeszenario mit messbarem Ergebnis fehlt. Atlas ist bereits auf Sage und Spezialisten ausgerichtet, die Single-Agent-Phase prüft dagegen nur eine Mock-Antwort. Ob Delegation für einen konkreten Anwendungsfall ihren zusätzlichen Kontext- und Koordinationsaufwand rechtfertigt, wird damit spät sichtbar.

**Mögliches Risiko**

Mehrere separat getestete Komponenten passen beim ersten realen Ablauf semantisch schlecht zusammen. Die zusätzlichen sechs Rollen und eine spätere Orchestrator-Migration binden Aufwand, bevor der Nutzen des kleinsten Systems belegt ist. Die Architektur kann dadurch für das MVP unnötig komplex werden.

**Konkreter Verbesserungsvorschlag**

Vor dem Projektskelett einen konkreten Nutzerauftrag mit Erfolgskriterium und maximalem Tokenverbrauch auswählen. Die frühen Phasen auf einen vollständigen Ablauf mit Atlas und einem Spezialisten ausrichten, einschließlich minimalem Action-Dispatcher, Rechteprüfung und festen Kontext-/Schrittgrenzen. Den deterministischen Mock als Standard behalten und eine frühe, ausdrücklich freigegebene Modellprobe zur Validierung desselben Ablaufs einplanen. Weitere Rollen, Parallelität und alternative Adapter an nachgewiesenen Bedarf knüpfen. Die Entscheidungen aus Findings 1–4 vor dem Festschreiben der jeweiligen Interfaces treffen.
