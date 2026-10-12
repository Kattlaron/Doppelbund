# Doppelbund

Eine browserbasierte Doppelbund-Runde gegen drei Computergegner oder drei
weitere menschliche Spieler.

## Start

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Danach im Browser `http://127.0.0.1:8000` öffnen.

## Spieltische

Im Startmenü stehen zwei Modi zur Verfügung:

- **Gegen KI spielen**: Der erstellende Spieler startet sofort mit drei Bots.
- **Tisch erstellen**: Der erstellende Spieler erhält einen Tisch-Code.
  Diesen Code können drei weitere Browser über „Bestehendem Tisch beitreten“
  verwenden. Alternativ kann ein offener Tisch aus der Liste ausgewählt werden.

Ein Tisch startet die Partie erst, wenn alle vier Plätze belegt sind.
Jeder Browser verbindet sich mit einem eigenen Sitzplatz und sieht nur seine
eigenen Handkarten. Die Spielaktionen werden anschließend per WebSocket an alle
vier Browser verteilt.

## Enthalten

- 44 Karten, vier Spieler, elf Karten pro Spieler
- vereinfachte Stichlogik
- Schwarz und Rot als Trumpf
- Kartenfarben werden als farbige Karten dargestellt, ohne Farbsymbole
- Computer-Spieler heißen Anna, Ben und Clara
- Die beiden schwarzen 6er bilden ein gemeinsames Team
- Kartenwerte: 1 = 3 Punkte, 2 = 2 Punkte, 3 = 1 Punkt, 4/5 und Schwarz = 0 Punkte
- Computer spielen mit zweisekündiger Verzögerung
- Die Computer-KI spielt beim Anspiel bevorzugt 5er-Farbkarten,
  übernimmt Trumpfstiche mit dem knapp ausreichenden Trumpf und schützt
  erkannte Partner.
- Die KI berücksichtigt zusätzlich ihren gewählten Auftrag und versucht,
  passende Karten, Stiche und Punktwerte gezielt zu erreichen.
- Vor jeder Partie wählt jeder Spieler einen von zwei zufälligen Aufträgen.
- Erfüllte Aufträge werden sofort aufgedeckt und geben Siegpunkte.
- Ein Match umfasst vier Partien; ein Partiegewinn gibt jedem Spieler des
  Gewinnerteams zwei zusätzliche Siegpunkte.
- `assets/cards/cards.xlsx` enthält die Auftragsdefinitionen und die im Code
  verwendeten Auftragsnamen.
- Bedienpflicht für Farbe bzw. Trumpf
- einfache Computergegner
- Spielstand und Ereignisprotokoll
- WebSocket-Kommunikation
- neues Spiel ohne Seitenreload

Die Regeln und die KI sind bewusst einfach gehalten und sollen als Grundlage für die weitere Entwicklung dienen.
