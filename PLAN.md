# PLAN.md

## Ziel

Ciel wird als Linux-native, Wayland-first Desktop-Companion-Plattform weiterentwickelt. Windows ist kein Ziel mehr.

Die Anwendung soll nicht auf einen einzelnen Charakter wie Great Sage fest verdrahtet sein. Charaktere werden als deklarative Character-Packs geladen. Ein Pack beschreibt Identität, Prompt, Stimme, Theme und die erlaubten Ciel-Dienste. Die eigentlichen Fähigkeiten bleiben in einer gemeinsamen Engine und werden nicht pro Charakter dupliziert.

Hardware-Ziel ist AMD-first:

1. AMD Radeon / Ryzen mit ROCm, wenn verfügbar
2. CPU-Fallback ohne GPU-Pflicht
3. andere GPU-Backends sind später optional, aber kein Architekturtreiber

Der Overlay-Modus wird Linux-nativ über Wayland Layer-Shell umgesetzt. Der vorhandene GIF-Player bleibt Referenz für Layer-Shell, Input-Regionen, XDG-Pfade und Surface-Verhalten.

## Anforderungen

### Muss

- Linux nativ, kein Wine und keine Windows-Runtime
- Wayland-first
- Hyprland, Sway und Niri als erste Test-Compositoren
- KDE Plasma Wayland soweit Layer-Shell sauber funktioniert
- Linux-native XDG-Pfade für Config, Daten, Cache und Runtime
- transparentes Always-on-top-Overlay über Layer-Shell
- vorhandenes Three.js-HUD möglichst weiterverwenden
- AMD ROCm als primärer GPU-Pfad
- CPU-Fallback für Modell-, STT- und TTS-Komponenten soweit die jeweilige Bibliothek CPU unterstützt
- mehrere Character-Packs ohne Änderungen am Core
- Charakterwechsel ohne Codeänderung, über Config oder UI
- Stimme, Prompt, Theme und aktivierte Dienste pro Charakter konfigurierbar
- Dienste über eine zentrale Allowlist, kein beliebiger Python-Code aus Character-Packs
- lokale Daten und Secrets außerhalb des Repositorys

### Soll

- Charakter-spezifische Chats und UI-Einstellungen
- wahlweise gemeinsames oder charakter-spezifisches Langzeitgedächtnis
- Portal-basierte Screenshots und Screen-Capture
- globale Shortcuts über Portal oder explizite Compositor-Integration
- Audio über PipeWire/PulseAudio-kompatible Linux-Backends
- Nix als erstes reproduzierbares Entwickler- und Packaging-Ziel
- später AppImage oder distro-native Pakete

### Nicht-Ziele

- Windows-Unterstützung
- X11 als primäres Ziel
- GNOME-Layer-Shell-Hacks
- Character-Packs, die beliebigen Python-Code automatisch laden
- eine eigene KI-Engine pro Charakter
- Cloud-Zwang

### Sicherheit

- Character-Packs sind Daten, kein ausführbarer Plugin-Code
- Pfade innerhalb eines Character-Packs dürfen dessen Verzeichnis nicht verlassen
- Dienste werden gegen eine zentrale Service-Allowlist validiert
- Desktop-, Screen- und Web-Dienste bleiben permission-sensitiv
- keine Modellantwort darf direkt als Shell-Befehl ausgeführt werden
- Runtime-Sockets und Runtime-Verzeichnisse gehören nur dem aktuellen Benutzer
- Screen-Capture läuft über das Wayland-/Portal-Berechtigungsmodell

### Skalierbarkeit

Neue Charaktere sollen nur neue Daten und Assets benötigen. Der Chat-Core, das Overlay, der Model-Provider, STT/TTS und Desktop-Dienste werden gemeinsam genutzt. Dadurch wächst die Codebasis nicht linear mit der Zahl der Charaktere.

### Kosten

Die Basis bleibt lokal und Open Source. Character-Packs erzeugen keine laufenden Kosten. Cloud-Provider dürfen später optional als Services konfigurierbar sein. ROCm und CPU bleiben die primären lokalen Compute-Pfade.

## Architekturansätze

### Ansatz A: Modulare Python-Engine + deklarative Character-Packs + GTK/Layer-Shell

#### Tech-Stack

- Python 3.12 oder 3.13 als bevorzugte Baseline
- vorhandener Chat-/Model-/Voice-Core, schrittweise entkoppelt
- Character-Packs als JSON + Markdown + Assets
- GTK3/WebKitGTK für den ersten HUD-Port
- GtkLayerShell für Overlay-Surfaces
- PyGObject/GIO für Linux-Integration
- XDG Desktop Portals für Screen-Capture und globale Shortcuts, wo verfügbar
- PyTorch ROCm für AMD-Beschleunigung
- CPU als garantierter Fallback-Pfad auf Engine-Ebene
- Nix für reproduzierbare Dev-Umgebung und Packaging

#### Komponenten und Datenfluss

```text
ciel.py
  -> RuntimeContext
      -> CharacterLoader -> characters/<id>/character.json + prompt.md + assets
      -> ServiceRegistry -> erlaubte gemeinsame Dienste
      -> Accelerator     -> ROCm oder CPU
      -> LinuxPlatform   -> XDG + Wayland + Portal/Compositor
  -> Core
      -> ModelProvider
      -> Memory
      -> Tools/Services
      -> Voice/STT
  -> Linux UI Host
      -> WebKitGTK -> bestehendes Three.js-HUD
      -> GtkLayerShell -> Overlay
```

#### Vorteile

- einfachster Weg vom aktuellen Python-Core zu Linux
- GIF-Player-Wissen ist direkt nutzbar
- Charaktere bleiben leichtgewichtig
- keine Code-Duplizierung pro Charakter
- AMD/CPU kann unabhängig von der UI behandelt werden
- gute Testbarkeit, weil Loader, Registry und Hardware-Erkennung displayfrei sind

#### Nachteile

- WebKitGTK muss mit dem bestehenden Three.js/WebGL-HUD praktisch getestet werden
- GTK3 ist nicht die modernste GTK-Version
- Layer-Shell funktioniert nicht auf jedem Wayland-Compositor

#### Komplexität

Mittel

### Ansatz B: Rust Linux-Shell + Python AI-Core + Character-Packs

#### Tech-Stack

- Rust für Wayland-Fenster, Layer-Shell, Portals und Desktop-Integration
- Python als separater AI-/Voice-Dienst
- IPC zwischen Rust-Shell und Python-Core
- Character-Packs bleiben identisch deklarativ
- ROCm/CPU im Python-Compute-Service

#### Komponenten und Datenfluss

```text
Rust Shell
  -> Wayland / Layer-Shell / Portals
  -> WebView / UI
  -> IPC
Python Core Service
  -> Character Pack
  -> Model / Voice / Memory / Tools
  -> ROCm / CPU
```

#### Vorteile

- langfristig sehr native Linux-Shell
- starke Prozess- und Typgrenzen
- gute Kontrolle über Wayland

#### Nachteile

- deutlich größerer Rewrite
- zwei Sprachen und IPC von Anfang an
- viel Infrastruktur, bevor vorhandene Funktionen wieder laufen
- für den aktuellen Scope unnötig teuer

#### Komplexität

Hoch

## Architektur, gewählter Ansatz

Ansatz A wird umgesetzt.

Die Entscheidung folgt aus dem aktuellen Projekt und dem vorhandenen GIF-Player: Der wertvolle Teil von Ciel ist bereits Python, und die funktionierende Layer-Shell-Erfahrung existiert ebenfalls in Python/GTK. Ein Rust-Rewrite würde vor allem funktionierenden Code ersetzen, bevor der Linux-Port überhaupt bewiesen ist.

AMD-first wird als Compute-Policy behandelt, nicht in einzelne TTS-/STT-Module hartcodiert. `detect_accelerator()` liefert ROCm oder CPU. Wichtig: PyTorch verwendet auch unter ROCm den Device-String `cuda`; AMD wird deshalb über `torch.version.hip` erkannt.

## Character-Pack-Modell

```text
characters/
  great_sage/
    character.json
    prompt.md
    voice/
    ui/
```

`character.json` enthält nur deklarative Daten:

- `id`
- `display_name`
- `description`
- `prompt_file`
- `voice`
- `theme`
- `services`
- `metadata`

Character-Packs dürfen niemals automatisch Python-Dateien importieren. Neue Fähigkeiten kommen als geprüfte Ciel-Services in `great_sage/services/` hinzu. Ein Charakter darf diese Dienste nur auswählen.

Beispiel:

```json
{
  "id": "my_character",
  "display_name": "My Character",
  "prompt_file": "prompt.md",
  "services": ["chat", "voice", "memory", "overlay"]
}
```

Damit kann später ein weiterer Anime-inspirierter Charakter einen anderen Prompt, eine andere Stimme, Farben und andere freigeschaltete Dienste bekommen, ohne `ChatEngine` zu forken.

UNKLAR: Für Charakterbilder, Sprachsamples und andere geschützte Medien muss geklärt werden, welche Assets selbst erstellt, lizenziert oder nur lokal vom Nutzer eingebunden werden.

## Dateistruktur

```text
Ciel/
├── ciel.py
├── PLAN.md
├── pyproject.toml
├── requirements.txt
├── characters/
│   ├── README.md
│   └── great_sage/
│       ├── character.json
│       └── prompt.md
├── great_sage/
│   ├── characters/
│   │   ├── model.py
│   │   └── loader.py
│   ├── hardware/
│   │   └── accelerator.py
│   ├── services/
│   │   └── registry.py
│   ├── platform/
│   │   └── linux.py
│   ├── core/
│   ├── models/
│   ├── voice/
│   └── ui/
│       └── linux/
│           ├── main_window.py
│           └── overlay.py
├── tests/
│   ├── unit/
│   └── integration/
└── legacy/
    └── windows/
```

## Erste 3 Dateien

Die ersten drei produktiven Bausteine sind:

1. `great_sage/characters/loader.py`
   - lädt Character-Packs
   - validiert Manifest, IDs, Assets und Pfade
   - verhindert Path Traversal

2. `great_sage/hardware/accelerator.py`
   - zentrale AMD-first Compute-Erkennung
   - ROCm über `torch.version.hip`
   - CPU-Fallback
   - kein CUDA/NVIDIA-Zwang in Feature-Modulen

3. `great_sage/platform/linux.py`
   - XDG-Pfade
   - Wayland-/Compositor-Erkennung
   - Basis für Portal-, Shortcut- und Layer-Shell-Integration

## Umsetzungsschritte

1. Linux-native Branch als neue Entwicklungsbasis festlegen.
2. Character-Pack-Modell und Loader einführen.
3. Great Sage als erstes Character-Pack abbilden.
4. zentrale Service-Allowlist einführen.
5. AMD-first Accelerator-Erkennung einführen und CPU-Fallback definieren.
6. XDG- und Wayland-Runtime-Basis einführen.
7. neue Linux-native `ciel.py` als Entwicklungs-Entry-Point einführen.
8. Unit-Tests für Character-Loader, Path Traversal, Service-Allowlist und Accelerator ergänzen.
9. alte Windows-Packaging- und Window-Dateien nach `legacy/windows/` verschieben, sobald keine neue Linux-Datei sie mehr importiert.
10. Great-Sage-Prompt und Voice-Konfiguration vollständig aus `config/settings.py` in das Character-Pack ziehen.
11. Memory-, Chat- und HUD-Settings um Character-ID scopen.
12. Voice-Factory so umbauen, dass Character-Voice-Konfiguration statt globaler Great-Sage-Konstanten verwendet wird.
13. F5-TTS gegen ROCm testen. Device-Auswahl ausschließlich über die zentrale Accelerator-Schicht führen.
14. schnellen CPU-Voice-Fallback definieren, falls F5 auf CPU für den Alltag zu langsam ist.
15. bestehenden Three.js-HUD-Code auf Character-Theme und Character-Metadaten umstellen.
16. GTK/WebKitGTK-Hauptfenster-Spike bauen und WebGL/Shader testen.
17. GtkLayerShell-Overlay-Spike bauen, orientiert am GIF-Player.
18. Click-through über Wayland-Input-Regionen implementieren.
19. Overlay-Positionierung, Multi-Monitor und Scaling testen.
20. globale Push-to-talk-Integration über Portal prüfen, Hyprland/Niri/Sway-Fallbacks danach ergänzen.
21. Desktop-Tools auf Linux umstellen: XDG application discovery, `xdg-open`/GIO statt Start Menu und `os.startfile`.
22. Screen-Capture auf XDG Desktop Portal/PipeWire umstellen.
23. Windows-spezifische Core-Dateien entfernen, sobald Linux-Ersatz vorhanden ist.
24. Nix-DevShell und reproduzierbares Linux-Paket ergänzen.
25. Character-Auswahl ins HUD bringen.
26. zweites neutrales Test-Character-Pack hinzufügen, um sicherzustellen, dass kein Great-Sage-Hardcoding übrig ist.
27. danach erst echte weitere Charakter-Packs mit eigenen Assets ergänzen.

## Aktueller Entwicklungsstand

Bereits als Grundlage umgesetzt:

- `great_sage/characters/`: deklaratives Character-Modell und sicherer Loader
- `great_sage/services/`: zentrale Service-Allowlist
- `great_sage/hardware/`: AMD-first ROCm/CPU-Erkennung
- `great_sage/platform/linux.py`: XDG- und Wayland-Erkennung
- `great_sage/runtime.py`: zusammengesetzter RuntimeContext
- `characters/great_sage/`: erstes Character-Pack
- `ciel.py`: Linux-native Diagnose- und Character-Entry-Point-Basis
- Unit-Tests für Loader, Path Traversal, Services und CPU-Selection

## Offene Fragen / Unklarheiten

- UNKLAR: Welche AMD-GPU ist das erste konkrete Testgerät? ROCm-Support hängt vom genauen Radeon-/Ryzen-Modell ab.
- UNKLAR: Soll das erste Release nur Hyprland unterstützen oder Hyprland, Niri und Sway gleichzeitig als harte Release-Gates haben?
- UNKLAR: Soll Langzeitgedächtnis standardmäßig zwischen Charakteren geteilt werden oder pro Charakter getrennt sein?
- UNKLAR: Soll ein Charakter seinen bevorzugten Ollama-Modellnamen festlegen dürfen oder soll das Modell immer eine globale Nutzereinstellung bleiben?
- UNKLAR: Welche Voice-Engine soll der garantierte CPU-Fallback sein, wenn F5-TTS ohne ROCm nicht interaktiv schnell genug ist?
- UNKLAR: Werden Character-Packs nur lokal verwaltet oder soll später ein installierbares Pack-Format mit Registry entstehen?
