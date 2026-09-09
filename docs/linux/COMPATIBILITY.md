# Linux Compatibility Matrix

Ciel ist auf dieser Entwicklungsbasis Linux-only. Windows ist kein Release-Ziel mehr. Alte Window-, Installer- und Packaging-Dateien liegen nur noch unter `legacy/windows/` und dürfen von neuem Code nicht importiert werden.

## Legende

- `REUSE`: plattformneutraler Core, weiterverwenden
- `ADAPT`: fachliche Logik behalten, Linux-Zugriff ersetzen
- `REPLACE`: alte Implementierung vollständig durch Linux-native Lösung ersetzen
- `SPIKE`: vor dem produktiven Port praktisch testen

## Übersicht

| Bereich | Status | Linux-Aufgabe |
|---|---|---|
| Chat-/Model-Core | REUSE | Character-Kontext und Tests ergänzen |
| WebSocket-Bridge | REUSE | Linux-Integration testen |
| Character-System | REUSE | neue Packs nur deklarativ ergänzen |
| Voice/TTS/STT | ADAPT | ROCm/CPU über zentrale Accelerator-Schicht, Linux-Audio testen |
| `hud_prototype.html` | SPIKE | WebKitGTK + WebGL + Transparenz + Shader testen |
| Hauptfenster | REPLACE | GTK/WebKitGTK Host bauen |
| Overlay | REPLACE | GtkLayerShell + Wayland-Input-Regionen |
| globaler Hotkey | REPLACE | Portal oder expliziter Compositor-Fallback |
| Desktop-Tools | ADAPT | GIO, `.desktop`-Dateien, Portals statt Win32 |
| Screen-Capture | REPLACE | XDG Desktop Portal + PipeWire |
| Runtime-Daten | ADAPT | vollständig auf XDG-Pfade umstellen |
| Packaging | REPLACE | Nix zuerst, später optional AppImage/distro-native Pakete |

## Hardware-Ziel

Die automatische Compute-Reihenfolge ist:

1. AMD ROCm
2. CPU

`great_sage/hardware/accelerator.py` erkennt ROCm über `torch.version.hip`. PyTorch verwendet bei ROCm trotzdem den Device-String `cuda`, daher darf die Zeichenkette `cuda` allein niemals als NVIDIA-Erkennung verwendet werden.

NVIDIA CUDA ist aktuell kein Release-Gate und kein Architekturtreiber.

UNKLAR: Das konkrete erste Radeon-/Ryzen-Testgerät muss noch festgelegt werden. ROCm-Unterstützung hängt vom genauen GPU/APU-Modell ab.

## Character-Packs

Character-Packs liegen unter `characters/<id>/` und sind reine Daten:

- `character.json`
- `prompt.md`
- optionale Voice-Assets
- optionale UI-Assets

Sie können bekannte Ciel-Services aktivieren, aber keinen Python-Code automatisch importieren. Das schützt die Vertrauensgrenze zwischen Persona und Systemzugriff.

Der alte Great-Sage-Hardcode in `config/settings.py` wird schrittweise abgebaut. Das erste neue Pack unter `characters/great_sage/` beweist bereits den Zielpfad.

## Noch vorhandene Windows-Kopplungen im Core

### `great_sage/core/global_hotkey.py`

Komplett Win32-basiert und zu ersetzen:

- `RegisterHotKey`
- `WM_HOTKEY`
- `GetAsyncKeyState`
- `PeekMessageW`
- `UnregisterHotKey`

Ziel: kein permanenter systemweiter Keyboard-Hook. Erst Portal prüfen, danach gezielte Hyprland/Niri/Sway-Fallbacks.

### `great_sage/core/tools.py`

Plattformneutral beziehungsweise gut wiederverwendbar:

- Uhrzeit
- Disk-Status
- HTTP/HTTPS-URLs
- YouTube
- Dateinamensuche

Zu ersetzen:

- `EnumWindows`
- Start-Menu-`.lnk`-Discovery
- `os.startfile`
- File-Explorer-spezifisches Verhalten

Linux-Ziel:

- App-Discovery über `.desktop`-Dateien beziehungsweise `Gio.AppInfo`
- Öffnen über GIO oder `xdg-open`
- sichtbare Fenster unter Wayland nur anbieten, wenn der Compositor eine saubere API bereitstellt

### Voice

`f5_tts_engine.py` wählt derzeit `cuda` bei `torch.cuda.is_available()`. Das kann auf ROCm bereits funktionieren, weil PyTorch ROCm dieselbe Device-API nutzt. Die Auswahl wird trotzdem in einem nächsten Schritt auf die zentrale Accelerator-Schicht umgestellt, damit AMD/CPU-Policy nicht in einzelnen Voice-Modulen steckt.

## GIF-Player als technische Referenz

Der GIF-Player löst bereits mehrere relevante Wayland-Probleme:

- GTK3 + GtkLayerShell
- Layer-Shell-Surfaces
- monitorlokale logische Koordinaten
- Corner-/Margin-Positionierung
- kompakte und monitorfüllende Surface-Modi
- Click-through über Input-Regionen
- stabile Surface während Drag/Animation
- GLib/GIO-Main-Loop
- XDG Runtime/Config/Cache/Data-Struktur
- restriktive Runtime-/Socket-Rechte

Nicht direkt übertragbar ist das Rendering. GIF-Player zeichnet Pillow/Cairo. Ciel muss das bestehende WebGL/Three.js-HUD in WebKitGTK einbetten.

## Compositor-Matrix

| Umgebung | Zielstatus | Hinweis |
|---|---|---|
| Hyprland | primär | Layer-Shell, erstes Release-Gate |
| Niri | primär | GIF-Player-Erfahrung vorhanden |
| Sway | primär | wlroots + Layer-Shell |
| KDE Plasma Wayland | sekundär | Layer-Shell testen |
| GNOME Wayland | kein erstes Ziel | kein regulärer Layer-Shell-Pfad |
| X11 | kein Ziel | nicht implementieren, solange kein konkreter Bedarf entsteht |

## Erster UI-Spike

1. GTK-Fenster erzeugen
2. GtkLayerShell initialisieren
3. WebKitGTK einbetten
4. `hud_prototype.html?mini=1` laden
5. Alpha-Hintergrund aktivieren
6. top-right ankern
7. Three.js/WebGL und Shader prüfen
8. transparente Flächen prüfen
9. Click-through/Input-Region prüfen
10. Multi-Monitor und Scaling prüfen
11. Animation auf Flicker und Frame-Drops beobachten

Wenn dieser Spike sauber läuft, wird der Linux-Overlay-Host produktiv gebaut.

## Aufräumregel

Neue Linux-Komponenten dürfen nichts aus `legacy/windows/` importieren. Weitere Windows-spezifische Core-Stellen werden entfernt, sobald ihr Linux-Ersatz vorhanden und getestet ist. Die Git-Historie bleibt die Referenz, deshalb gibt es keinen Grund, alte Windows-Implementierungen dauerhaft im aktiven Codepfad mitzuschleppen.
