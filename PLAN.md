# PLAN.md

## Ziel

Ciel soll Linux-nativ lauffähig werden, mit Fokus auf Wayland-Desktops wie Hyprland, Sway, Niri und KDE Plasma. Die bestehende Windows-Version soll dabei weiter funktionieren.

Der Linux-Port soll zwei Modi abdecken:

1. normales HUD-Fenster
2. kompaktes transparentes Always-on-top-Overlay mit Animation, Click-through und sauberer Positionierung

Die vorhandene Three.js-HUD-Oberfläche soll möglichst wiederverwendet werden. Der bestehende GIF-Player dient als technische Referenz für Wayland Layer-Shell, stabile Surface-Geometrie, monitorlokale Koordinaten und Click-through-Verhalten.

## Anforderungen

### Muss

- Linux-native Ausführung ohne Wine
- Wayland zuerst, insbesondere Hyprland/wlroots-kompatible Compositoren
- bestehende Windows-Funktionalität nicht kaputtrefaktorieren
- vorhandene Three.js-HUD-Oberfläche wiederverwenden
- normales Hauptfenster
- transparentes Always-on-top-Overlay
- Overlay in einer Ecke positionierbar
- Overlay möglichst durchklickbar außerhalb interaktiver Bereiche
- Animationen und WebGL weiter nutzbar
- lokale KI-, Voice- und WebSocket-Logik weiterverwenden
- plattformspezifische Funktionen hinter klaren Interfaces isolieren
- saubere XDG-Pfade für Config, Cache, State und Runtime-Daten
- Linux-Paketierung reproduzierbar machen

### Soll

- Hyprland, Sway, Niri und KDE Plasma auf Wayland unterstützen
- GNOME nicht durch fragile Sonderlösungen erzwingen
- globales Push-to-talk ohne systemweiten Keylogger-artigen Hook lösen
- Screensharing oder Screenshots auf Wayland über Portals/PipeWire lösen
- Testbarkeit der plattformunabhängigen Logik verbessern
- CI für mindestens Syntax, Unit-Tests und Linux-Build ergänzen

### Sicherheit

- keine beliebigen Modellstrings an Shells weiterreichen
- bestehende Tool-Validierung beibehalten
- Runtime-Sockets nur für den aktuellen Benutzer zugänglich machen
- globale Shortcuts bevorzugt über Desktop-Portals oder explizite Compositor-Integration
- Screen-Capture nur mit Wayland-/Portal-Berechtigungsmodell
- keine Secrets oder persönliche Laufzeitdaten im Repository

### Skalierbarkeit

Skalierbarkeit bedeutet hier vor allem Wartbarkeit über mehrere Plattformen. Windows- und Linux-Code dürfen nicht über den gesamten Core verteilt werden. Plattformlogik soll über kleine Adapter austauschbar sein.

### Kosten

Die Architektur soll lokal und Open Source bleiben. Zusätzliche laufende Cloud-Kosten sind nicht vorgesehen. Kosten entstehen hauptsächlich durch CI, Packaging-Arbeit und die Pflege mehrerer Linux-Distributionen.

## Architekturansätze

### Ansatz A: Bestehenden Core behalten, Plattformadapter einführen

#### Tech-Stack

- Python bleibt Hauptsprache
- bestehender Chat-/Voice-/WebSocket-Core bleibt erhalten
- Haupt-HUD unter Linux über pywebview mit GTK/WebKitGTK oder direkt WebKitGTK
- Overlay unter Wayland über GTK3 + WebKitGTK + GtkLayerShell
- PyGObject für GTK/GIO/Portal-Anbindung
- XDG Desktop Portals für Screen-Capture und möglichst Global Shortcuts
- vorhandenes HTML/Three.js bleibt gemeinsame UI
- GIF-Player dient als Referenz für GtkLayerShell und Surface-Verhalten

#### Komponenten und Datenfluss

```text
app.py
  -> platform factory
      -> WindowsAdapter
      -> LinuxWaylandAdapter
  -> Chat/Voice/Core
  -> WebSocket bridge
  -> HUD Web UI

LinuxWaylandAdapter
  -> main window host
  -> overlay host
      -> GTK Window
      -> WebKitGTK
      -> GtkLayerShell
  -> global shortcut backend
  -> desktop/application backend
  -> screen capture portal backend
```

#### Vorteile

- geringster Eingriff in funktionierende Core-Logik
- Windows kann unverändert weiterlaufen
- vorhandenes HUD bleibt nutzbar
- GIF-Player-Technik kann gezielt übernommen werden
- einzelne Plattformfeatures können nacheinander portiert werden
- überschaubares Risiko

#### Nachteile

- Linux-Hauptfenster und Overlay können unterschiedliche Host-Details haben
- WebKitGTK muss mit dem vorhandenen Three.js/WebGL-HUD verifiziert werden
- GNOME unterstützt Layer-Shell nicht nativ
- einige Desktop-Funktionen brauchen Portal- oder Compositor-Fallbacks

#### Komplexität

Mittel

### Ansatz B: Linux-GUI komplett als eigene Qt/Wayland-Schicht bauen

#### Tech-Stack

- Python-Core bleibt erhalten
- PySide6 + QtWebEngine für Hauptfenster und Overlay
- eigene Wayland-/Layer-Shell-Integration oder zusätzliche Qt-Layer-Shell-Bibliothek
- Portals für Screen-Capture und Shortcuts
- gemeinsames Three.js-HUD

#### Komponenten und Datenfluss

```text
Python Core
  -> Qt application host
      -> main QWebEngineView
      -> overlay QWebEngineView
          -> Wayland Layer-Shell integration
  -> platform services
  -> portals / compositor APIs
```

#### Vorteile

- ein GUI-Toolkit für Hauptfenster und Overlay
- vorhandener Windows-Overlay-Code ist konzeptionell näher an Qt
- Chromium/QtWebEngine verhält sich näher am bisherigen QWebEngineView

#### Nachteile

- Wayland Layer-Shell ist in normalem Qt nicht direkt die Standardlösung
- deutlich mehr eigene Native-/Wayland-Integration
- höheres Risiko bei Click-through, Positionierung und Compositor-Unterschieden
- größere Abhängigkeit und aufwendigeres Packaging
- weniger direkte Wiederverwendung der bereits funktionierenden GIF-Player-Schicht

#### Komplexität

Hoch

## Architektur, empfohlener Ansatz

Empfehlung: Ansatz A.

Begründung: Der funktionierende Teil von Ciel ist bereits klar vom Fensterproblem unterscheidbar. Der größte Portierungsaufwand steckt in `run_hud.py`, `overlay_window.py`, `global_hotkey.py` und einzelnen Desktop-Tools. Ein Plattformadapter erlaubt, diese Stellen gezielt zu ersetzen, ohne Chat, Modellprovider, Voice, WebSocket-Bridge und HUD neu zu schreiben.

Der GIF-Player zeigt bereits, dass GTK3 + GtkLayerShell für Wayland-Overlays in deinem Umfeld funktioniert. Diese technische Vorarbeit reduziert das Risiko gegenüber einer neuen Qt-Layer-Shell-Lösung deutlich.

UNKLAR: Diese Empfehlung ist noch keine Architekturentscheidung. Vor dem ersten produktiven Refactor soll bestätigt werden, ob GTK/WebKitGTK das bestehende Three.js-HUD inklusive Transparenz, WebGL und Shadern sauber rendert. Wenn dieser Spike scheitert, ist Ansatz B die naheliegende Alternative.

## Dateistruktur

Zielstruktur nach dem ersten Refactor:

```text
Ciel/
├── app.py
├── PLAN.md
├── requirements.txt
├── docs/
│   └── linux/
│       ├── COMPATIBILITY.md
│       └── DECISIONS.md
├── great_sage/
│   ├── core/
│   ├── models/
│   ├── voice/
│   ├── platform/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── windows.py
│   │   └── linux_wayland.py
│   └── ui/
│       ├── host.py
│       └── overlay/
│           └── linux_gtk.py
├── tests/
│   ├── unit/
│   └── integration/
└── tools/
    └── checks/
```

UNKLAR: Die bestehenden `check_*.py` werden erst verschoben, nachdem `build.py` und Doku auf die neuen Pfade angepasst wurden. Kein blindes Verschieben auf Kosten eines funktionierenden Builds.

### Erste 3 Dateien

1. `great_sage/platform/base.py`
   - kleine Interfaces/Protocols für WindowHost, OverlayHost, GlobalShortcut, ApplicationLauncher und ScreenCapture
   - keine Betriebssystemlogik

2. `great_sage/platform/linux_wayland.py`
   - Linux-/Wayland-Implementierung der ersten Plattformservices
   - zunächst nur Capability Detection und XDG-Pfade, danach Shortcut/Desktop-Funktionen

3. `great_sage/ui/overlay/linux_gtk.py`
   - GTK/WebKitGTK/GtkLayerShell-Prototyp
   - lädt zuerst nur das bestehende HUD im Mini-Modus
   - testet Transparenz, WebGL, Layer-Shell, Positionierung und Click-through

## Umsetzungsschritte

1. Bestehenden Windows-Stand einfrieren und Smoke-Test-Dokumentation festhalten.
2. Linux-Compatibility-Matrix pflegen und alle Win32-Abhängigkeiten katalogisieren.
3. Einen minimalen GTK/WebKitGTK/GtkLayerShell-Spike bauen, der `hud_prototype.html?mini=1` rendert.
4. Spike auf Hyprland oder einem anderen wlroots-Compositor testen: Transparenz, WebGL, Shader, Animation, Always-on-top und Positionierung.
5. Bei Erfolg Ansatz A bestätigen. Bei WebKit-/WebGL-Problemen Ansatz B evaluieren.
6. `great_sage/platform/base.py` mit kleinen Interfaces einführen.
7. Windows-spezifische Logik aus `run_hud.py`, `overlay_window.py`, `global_hotkey.py` und `tools.py` schrittweise hinter Adapter ziehen.
8. Linux-Hauptfenster implementieren, ohne Overlay-Sonderlogik in den Host zu mischen.
9. Linux-Overlay mit GtkLayerShell implementieren. Surface-Modell des GIF-Players als Referenz verwenden.
10. Click-through über Input-Regionen lösen, nicht über Win32-Stilbits nachbauen.
11. Globale Push-to-talk-Shortcuts über Portal-Lösung prüfen. Falls der Ziel-Compositor keine passende Deaktivierungs-/Release-Semantik bietet, klaren compositor-spezifischen Fallback definieren.
12. `list_running_apps`, App-Discovery, `os.startfile`-Nutzung und File-Manager-Start plattformneutral abstrahieren.
13. Screen-Capture auf Wayland über XDG Desktop Portal/PipeWire portieren.
14. Laufzeitdaten auf Linux nach XDG Base Directory Specification verschieben.
15. Unit-Tests für plattformunabhängige Logik ergänzen und Platform-Contracts mockbar machen.
16. Bestehende `check_*.py` in eine saubere Tool-/Teststruktur überführen, erst nachdem alle Aufrufer angepasst sind.
17. Linux-Abhängigkeiten getrennt dokumentieren und Packaging wählen.
18. CI für Windows und Linux einrichten.
19. Linux-Paketformat zunächst auf ein reproduzierbares Ziel begrenzen, zum Beispiel Nix oder AppImage. Weitere Distributionen erst danach.
20. Erst nach erfolgreichem Linux-Smoke-Test README und Installationsdoku als offiziell unterstützte Plattform aktualisieren.

## Portierungsaufwand nach Bereich

| Bereich | Aufwand | Grund |
|---|---|---|
| Chat-/Model-Core | niedrig | überwiegend plattformneutral |
| WebSocket-Bridge | niedrig | plattformneutral |
| Three.js-HUD | niedrig bis mittel | wiederverwendbar, WebKitGTK/WebGL muss getestet werden |
| Voice/TTS/STT | mittel | Python-Code weitgehend portabel, Linux-Audio-/GPU-Abhängigkeiten prüfen |
| Hauptfenster | mittel | pywebview-/WinForms-spezifische Window Controls ersetzen |
| Overlay | mittel bis hoch | Wayland Layer-Shell, Transparenz, Input-Regionen und WebGL kombinieren |
| Global Hotkey | mittel bis hoch | Wayland verbietet klassische globale Hooks bewusst |
| App-/Window-Tools | mittel | Windows Start Menu, EnumWindows und `os.startfile` ersetzen |
| Screen Capture | mittel | Portal/PipeWire statt klassischer Desktop-Capture-APIs |
| Packaging | mittel | PyInstaller-Setup ist aktuell Windows-zentriert |
| GNOME Overlay | hoch / eingeschränkt | Layer-Shell wird dort nicht regulär unterstützt |

## Offene Fragen / Unklarheiten

- UNKLAR: Soll Linux offiziell nur Wayland unterstützen oder zusätzlich X11?
- UNKLAR: Muss GNOME voll unterstützt werden oder reicht ein normaler Fenster-Fallback ohne echtes Layer-Shell-Overlay?
- UNKLAR: Welche Distributionen sind Release-Ziele, NixOS, Arch, Fedora, Debian/Ubuntu?
- UNKLAR: Soll der Linux-Port dieselbe Python-Version wie Windows verwenden oder darf Linux auf einer stabileren Version wie 3.12/3.13 laufen?
- UNKLAR: Muss Push-to-talk exakt dieselbe Hold/Release-Semantik wie unter Windows haben?
- UNKLAR: Soll der Overlay-Prozess weiterhin separat bleiben oder darf Linux Hauptfenster und Overlay in einem GTK-Prozess hosten?
- UNKLAR: Ist NVIDIA/CUDA auf Linux Pflichtziel oder soll AMD/ROCm beziehungsweise CPU als gleichwertiges Ziel behandelt werden?
- UNKLAR: Soll der GIF-Player nur Referenz bleiben oder dürfen gemeinsame Overlay-Hilfsbausteine später in ein kleines Shared-Modul ausgelagert werden?
