# PLAN.md

## Ziel

Ciel wird als Linux-native, Wayland-first Desktop-Companion-Plattform weiterentwickelt. Windows ist kein Ziel mehr.

Die Anwendung besteht aus einer gemeinsamen Engine und austauschbaren Character-Packs. Ein Character-Pack beschreibt Identität, Prompt, Stimme, Theme, Assets und erlaubte Dienste. Die eigentlichen Fähigkeiten bleiben gemeinsame Module und werden nicht pro Charakter dupliziert.

Hardware-Priorität:

1. AMD Radeon / Ryzen mit ROCm, wenn unterstützt und verfügbar
2. CPU-Fallback
3. weitere Backends später optional, aber nicht als Architekturtreiber

Für großen zukünftigen AI-Kontext wird ein Graph-System verwendet. Menschen und Coding-AIs arbeiten mit kleinen Markdown-Kontextknoten und expliziten Beziehungen. Zur Laufzeit wird daraus ein schneller, wegwerfbarer SQLite-Index erzeugt.

## Anforderungen

### Muss

- Linux nativ, kein Wine
- Wayland-first
- Hyprland, Niri und Sway als erste harte Zielumgebungen
- KDE Plasma Wayland soweit Layer-Shell sauber funktioniert
- XDG-Pfade für Config, Daten, Cache und Runtime
- transparentes Always-on-top-Overlay über gtk-layer-shell
- vorhandenes Three.js-HUD möglichst weiterverwenden
- AMD ROCm als primärer GPU-Pfad
- CPU-Fallback auf Engine-Ebene
- mehrere Character-Packs ohne Core-Forks
- Charakterwechsel über Konfiguration oder UI
- Stimme, Prompt, Theme und Dienste pro Charakter konfigurierbar
- zentrale Service-Allowlist
- Context-Graph mit begrenztem Retrieval statt vollständiger Prompt-Injektion
- lokale Daten und Secrets außerhalb des Repositorys

### Soll

- charakter-spezifische Chats und UI-Einstellungen
- gemeinsames oder charakter-spezifisches Langzeitgedächtnis
- Graph-Namespaces für `project`, `character:<id>`, `user` und spätere Sessions
- XDG Desktop Portal für Screenshots und Screen-Capture
- globale Shortcuts über Portal oder explizite Compositor-Integration
- Audio über PipeWire/PulseAudio-kompatible Linux-Backends
- Nix als erstes reproduzierbares Entwickler- und Packaging-Ziel
- später optional Embedding-Retrieval als zusätzlicher Graph-Seed-Generator

### Nicht-Ziele

- Windows-Unterstützung
- X11 als primäres Ziel
- GNOME-Layer-Shell-Hacks
- Character-Packs mit automatisch ausführbarem Python-Code
- eigene KI-Engine pro Charakter
- Context-Datenbank als Source of Truth
- Cloud-Zwang

## Sicherheit

- Character-Packs sind Daten, kein ausführbarer Plugin-Code
- Pfade innerhalb eines Character-Packs dürfen dessen Verzeichnis nicht verlassen
- Context-Dateipfade dürfen `context/` nicht verlassen
- Dienste werden gegen eine zentrale Allowlist validiert
- Desktop-, Screen- und Web-Dienste bleiben permission-sensitiv
- Modellantworten werden niemals direkt als Shell-Befehl ausgeführt
- Runtime-Verzeichnisse und spätere Sockets gehören nur dem aktuellen Benutzer
- Screen-Capture folgt dem Wayland-/Portal-Berechtigungsmodell
- `legacy/windows/` ist Referenzcode und darf von neuem Runtime-Code nicht importiert werden

## Skalierbarkeit

### Character-System

Neue Charaktere sollen hauptsächlich Daten und Assets hinzufügen. Chat-Core, Model-Provider, Voice, STT, Overlay, Desktop-Dienste und Context-Retrieval bleiben gemeinsam.

### Context-System

Der Wissensbestand darf wachsen, ohne dass der Prompt linear wächst.

Retrieval-Pipeline:

```text
User/Agent query
  -> FTS5 lexical seed search
  -> kleine Seed-Menge
  -> begrenzte Graph-Expansion
  -> Ranking
  -> hartes Gesamtbudget + Pro-Knoten-Budget
  -> Model context bundle
```

Die Markdown-Dateien bleiben Source of Truth. SQLite ist nur ein Cache und kann jederzeit neu gebaut werden.

Die Datenbank unterstützt Namespaces, damit später Projektwissen, Charakterwissen und Nutzerwissen ohne ID-Kollisionen gemeinsam indexiert werden können.

## Kosten

Die Basis bleibt lokal und Open Source. Character-Packs und der SQLite-Context-Graph erzeugen keine laufenden Cloud-Kosten. ROCm und CPU bleiben die primären lokalen Compute-Pfade.

## Architekturansätze

### Ansatz A: Modulare Python-Engine + Character-Packs + GTK/Layer-Shell + Context-Graph

#### Tech-Stack

- Python 3.12 oder 3.13 als bevorzugte Baseline
- vorhandener provider-unabhängiger Chat-Core
- Character-Packs als JSON + Markdown + Assets
- GTK3 + WebKitGTK für die erste native UI
- gtk-layer-shell für Overlay-Surfaces
- PyGObject/GIO für Linux-Integration
- XDG Desktop Portals für sensitive Desktop-Funktionen
- PyTorch ROCm für AMD-Beschleunigung
- CPU als Fallback
- SQLite + FTS5 für Context-Retrieval
- Markdown + `context/graph.json` als Context-Source-of-Truth
- Nix später für reproduzierbare Umgebung und Packaging

#### Komponenten und Datenfluss

```text
ciel.py
  -> RuntimeContext
      -> CharacterLoader
      -> ServiceRegistry
      -> Accelerator
      -> LinuxPlatform
      -> ContextGraph path

  -> Core
      -> ModelProvider
      -> ChatEngine
      -> Memory
      -> Voice/STT
      -> Tools/Services

  -> ContextGraph
      -> Markdown nodes
      -> graph.json
      -> SQLite FTS5 cache
      -> bounded graph retrieval

  -> Linux UI Host
      -> GTK3
      -> WebKitGTK
      -> Three.js HUD
      -> gtk-layer-shell overlay
```

#### Vorteile

- nutzt den wertvollen bestehenden Python-Core
- kein unnötiger Rewrite
- Character-Packs bleiben leichtgewichtig
- Context wächst ohne lineares Prompt-Wachstum
- GIF-Player-Technik ist für Layer-Shell direkt relevant
- AMD/CPU bleibt von UI und Charakter getrennt
- displayfreie Module sind gut testbar

#### Nachteile

- WebKitGTK + Three.js muss auf realer AMD-/Wayland-Hardware visuell getestet werden
- GTK3 ist nicht die modernste GTK-Version
- Layer-Shell ist compositorabhängig
- bestehender Voice-Code enthält noch globale Great-Sage-Konfiguration

#### Komplexität

Mittel

### Ansatz B: Rust Linux-Shell + Python AI-Core

Rust übernimmt Wayland, Layer-Shell und Portals, Python bleibt AI-/Voice-Dienst über IPC.

Vorteile:

- langfristig starke native Shell
- klare Prozessgrenzen

Nachteile:

- deutlich größerer Rewrite
- zwei Sprachen und IPC sofort nötig
- langsamere Wiederverwendung des vorhandenen Projekts

Komplexität: hoch

## Gewählter Ansatz

Ansatz A wird umgesetzt.

Der aktuelle Code bestätigt die Richtung: Character-Loader, Service-Registry, AMD-first Hardware-Erkennung, XDG-/Wayland-Erkennung, Context-Graph und der erste GTK/WebKitGTK/Layer-Shell-Overlay-Spike existieren bereits.

Ein Rust-Shell-Rewrite bleibt nur eine spätere Alternative, falls GTK/WebKitGTK nach echten Hardwaretests grundlegende Probleme zeigt.

## Character-Pack-Modell

```text
characters/
  <id>/
    character.json
    prompt.md
    voice/      optional
    ui/         optional
```

`character.json` bleibt deklarativ und enthält unter anderem:

- `id`
- `display_name`
- `description`
- `prompt_file`
- `voice`
- `theme`
- `services`
- `metadata`

Neue Fähigkeiten werden in `great_sage/services/` implementiert und danach von Character-Packs ausgewählt.

UNKLAR: Für Anime-bezogene Bilder, Sprachsamples und andere geschützte Assets muss geklärt werden, welche Inhalte selbst erstellt, lizenziert oder nur lokal vom Nutzer eingebunden werden.

## Context-Graph

### Source of Truth

```text
AI_CONTEXT.md
AGENTS.md
CLAUDE.md
context/
  graph.json
  nodes/
    project.md
    architecture.md
    linux-native.md
    characters.md
    services.md
    context-graph.md
    compute.md
```

`AGENTS.md` und `CLAUDE.md` enthalten bewusst keine zweite vollständige Architektur. Sie verweisen auf denselben Graphen.

### Runtime-Index

```text
$XDG_CACHE_HOME/ciel/context/graph.sqlite3
```

Der Index nutzt:

- SQLite WAL
- FTS5, wenn verfügbar
- LIKE-Fallback ohne FTS5
- indizierte Kanten in beide Traversierungsrichtungen
- Namespaces
- maximal begrenzte Graph-Hops
- hartes Rendering-Budget

CLI:

```bash
python ciel.py --context-index
python ciel.py --context-search "Wayland overlay"
python ciel.py --context-bundle "character services"
python ciel.py --context-node context-graph
```

## Dateistruktur

```text
Ciel/
├── AI_CONTEXT.md
├── AGENTS.md
├── CLAUDE.md
├── PLAN.md
├── ciel.py
├── context/
│   ├── graph.json
│   └── nodes/
├── characters/
│   ├── great_sage/
│   └── ciel/
├── great_sage/
│   ├── characters/
│   ├── context_graph/
│   ├── hardware/
│   ├── services/
│   ├── platform/
│   ├── core/
│   ├── models/
│   ├── voice/
│   └── ui/
│       └── linux/
│           └── overlay.py
├── docs/
│   └── linux/
├── tests/
└── legacy/
    └── windows/
```

## Umsetzungsschritte

1. [x] Linux-native Entwicklungsbranch erstellen.
2. [x] Character-Pack-Modell und sicheren Loader einführen.
3. [x] Great Sage als Character-Pack abbilden.
4. [x] zweites neutrales `ciel`-Pack als Hardcoding-Test ergänzen.
5. [x] zentrale Service-Allowlist einführen.
6. [x] AMD-first ROCm/CPU-Erkennung einführen.
7. [x] XDG- und Wayland-Runtime-Basis einführen.
8. [x] Linux-native `ciel.py` einführen.
9. [x] Windows-Host und Packaging nach `legacy/windows/` verschieben.
10. [x] AI-Einstiegspunkte auf einen gemeinsamen Context-Graph vereinheitlichen.
11. [x] Markdown + `context/graph.json` als versionierte Context-Basis einführen.
12. [x] SQLite FTS5 + Graph-Retrieval implementieren.
13. [x] hartes Context-Bundle-Budget implementieren.
14. [x] nativen GTK/WebKitGTK/gtk-layer-shell Overlay-Spike implementieren.
15. [ ] Overlay-Spike auf Hyprland mit realer AMD-GPU testen: Transparenz, WebGL, Shader, Animation.
16. [ ] Danach Input-Regionen und Click-through aus dem GIF-Player-Konzept portieren.
17. [ ] Dragging, Positionierung, Multi-Monitor und Scaling portieren.
18. [ ] Hauptfenster als Linux-nativen WebKitGTK-Host implementieren.
19. [ ] Three.js-HUD von Great-Sage-Hardcoding auf Character-Theme und Character-Metadaten umstellen.
20. [ ] Great-Sage-Prompt und Voice-Konfiguration vollständig aus `config/settings.py` herausziehen.
21. [ ] Memory, Chats und HUD-Settings um Character-ID scopen.
22. [ ] zentrale Voice-Factory mit RuntimeContext bauen.
23. [ ] F5-TTS an den zentralen Accelerator anbinden und ROCm praktisch testen.
24. [ ] CPU-Voice-Fallback definieren und messen.
25. [ ] Desktop-Tools Linux-nativ machen: `.desktop`, GIO, `xdg-open`.
26. [ ] Screen-Capture über XDG Desktop Portal/PipeWire implementieren.
27. [ ] globale Push-to-talk-Integration über Portal prüfen, Compositor-Fallbacks danach ergänzen.
28. [ ] Character-Auswahl ins HUD bringen.
29. [ ] Context-Namespaces für Character- und Nutzerwissen produktiv verdrahten.
30. [ ] optional Embedding-Retrieval als zusätzlichen Seed-Generator evaluieren.
31. [ ] Nix-DevShell und reproduzierbares Linux-Paket ergänzen.
32. [ ] verbleibende alte Windows-/Great-Sage-Core-Abhängigkeiten entfernen.

## Teststrategie

Displayfrei:

- Character-Manifest-Validierung
- Path-Traversal-Schutz
- Service-Allowlist
- Accelerator-Auswahl
- Context-Manifest-Validierung
- Graph-Suche und Hop-Limit
- Context-Budget

Mit Wayland-Display:

- WebKitGTK WebGL
- Transparenz
- Three.js Shader
- Layer-Shell-Platzierung
- Click-through/Input-Region
- Multi-Monitor
- HiDPI/Scaling
- Audio und Portal-Dialoge

## Offene Fragen / Unklarheiten

- UNKLAR: Welches konkrete AMD-GPU-/APU-Modell ist das erste ROCm-Testgerät?
- UNKLAR: Welche Distribution ist die primäre Dev-/Release-Basis, Arch, NixOS, Fedora oder Ubuntu?
- UNKLAR: Soll Release 1 nur Hyprland als hartes Gate haben oder zusätzlich Niri und Sway?
- UNKLAR: Soll Langzeitgedächtnis standardmäßig zwischen Charakteren geteilt oder getrennt sein?
- UNKLAR: Soll ein Character-Pack ein bevorzugtes Ollama-Modell vorschlagen dürfen oder bleibt das Modell immer Nutzerkonfiguration?
- UNKLAR: Welche Voice-Engine ist der garantierte CPU-Fallback, wenn F5-TTS ohne ROCm nicht interaktiv schnell genug ist?
- UNKLAR: Werden Character-Packs später als installierbares Pack-Format mit Registry verteilt oder ausschließlich lokal verwaltet?
