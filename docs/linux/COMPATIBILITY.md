# Linux Compatibility Matrix

Status vor dem eigentlichen Port. Diese Datei beschreibt, was wiederverwendbar ist und wo Windows-spezifische Implementierungen ersetzt werden müssen.

## Legende

- `REUSE`: voraussichtlich direkt wiederverwendbar
- `ADAPT`: Logik bleibt, Plattformzugriff muss abstrahiert werden
- `REPLACE`: Implementierung ist betriebssystemspezifisch
- `SPIKE`: zuerst technisch verifizieren

## Übersicht

| Bereich | Status | Linux-Aufgabe |
|---|---|---|
| `app.py` | ADAPT | Win32-MessageBox-Fallback entfernen/abstrahieren, Pfad-/Frozen-Logik prüfen |
| `great_sage/server.py` | REUSE | WebSocket-/Bridge-Verhalten unter Linux testen |
| Chat-/Model-Core | REUSE | Unit-Tests ergänzen |
| Voice/TTS/STT | ADAPT | PipeWire/PulseAudio/ALSA-Abhängigkeiten, CUDA/ROCm prüfen |
| `hud_prototype.html` | SPIKE | WebKitGTK + WebGL + Transparenz + Shader testen |
| `run_hud.py` | REPLACE/ADAPT | Win32 Window Styling und Positionierung auslagern, Linux-Host bauen |
| `overlay_window.py` | REPLACE | Win32 Click-through, Border- und Window-Style-Code durch Wayland-Lösung ersetzen |
| `great_sage/core/global_hotkey.py` | REPLACE | XDG Desktop Portal oder expliziten Compositor-Fallback verwenden |
| `great_sage/core/tools.py` | ADAPT | App-Discovery, Window-Liste, `os.startfile` und File-Manager-Aufruf abstrahieren |
| `build.py` / PyInstaller-Specs | ADAPT | getrennten Linux-Buildpfad ergänzen |
| Runtime-Daten im Repo-Verzeichnis | ADAPT | XDG-Pfade verwenden |

## Aktuelle Windows-Kopplungen

### `app.py`

Der Entry Point ist grundsätzlich wiederverwendbar. Die Fehleranzeige verwendet jedoch direkt `ctypes.windll.user32.MessageBoxW`.

Ziel: `app.py` darf keine OS-spezifische GUI-API mehr kennen. Fehleranzeigen gehen über einen kleinen Platform-Service oder fallen auf Logging/STDERR zurück.

### `run_hud.py`

Der Haupt-HUD verwendet pywebview, enthält aber viele direkte Win32-Annahmen:

- HWND-Ermittlung
- `FindWindowW`
- `GetWindowLongPtrW` / `SetWindowLongPtrW`
- `SetWindowPos`
- `MonitorFromWindow`
- `GetMonitorInfoW`
- `GetWindowRect`
- WinForms-spezifische `native.Handle`-/`Invoke`-Pfade
- Windows-Stylebits für Frame, Popup und Resize

Die WebSocket- und Chat-Integration soll bleiben. Nur der Window-Host wird getrennt.

### `overlay_window.py`

Der Overlay-Host ist konzeptionell gut getrennt, technisch aber Windows-spezifisch:

- PySide6 `QWebEngineView`
- `Qt.WindowStaysOnTopHint`
- `WA_TranslucentBackground`
- Win32 `WS_EX_TRANSPARENT` für Click-through
- HWND-/Style-Manipulation über `ctypes.windll.user32`
- Windows-spezifische Workarounds für WebView/ANGLE/Border-Verhalten

Für Wayland darf nicht versucht werden, Win32-Stilbits nachzubauen. Layer-Shell und Input-Regions sind das passende Modell.

### `global_hotkey.py`

Komplett Win32-basiert:

- `RegisterHotKey`
- `WM_HOTKEY`
- `GetAsyncKeyState`
- `PeekMessageW`
- `UnregisterHotKey`

Das Sicherheitsprinzip soll erhalten bleiben: kein permanenter systemweiter Keyboard-Hook.

Linux-Ziel:

1. Portal-Backend prüfen
2. Hold/Release-Semantik verifizieren
3. wenn nötig compositor-spezifischen Fallback explizit als Fallback behandeln

### `tools.py`

Wiederverwendbar:

- Zeit
- Disk/GPU-Status grundsätzlich
- Web-URLs
- YouTube
- Dateinamensuche

Windows-spezifisch:

- sichtbare Fenster über `EnumWindows`
- installierte Apps aus Start-Menu-`.lnk`
- `os.startfile`
- File-Explorer-Semantik

Linux-Ziel:

- App-Discovery über `.desktop`-Dateien
- Start über `Gio.AppInfo` oder `gtk-launch`
- Ordner über `Gio.AppInfo.launch_default_for_uri` oder `xdg-open`
- `list_running_apps` unter Wayland nicht als garantiert verfügbar behandeln

## GIF-Player als technische Referenz

Der GIF-Player ist für den Overlay-Port relevant, weil er bereits folgende Probleme auf Wayland löst:

- GTK3 + GtkLayerShell
- Layer-Shell-Surfaces
- monitorlokale logische Koordinaten
- Corner-/Margin-Positionierung
- kompakte und monitorfüllende Surface-Modi
- Click-through über Input-Regionen
- stabile Surface während Drag/Animation
- GLib/GIO-Main-Loop
- XDG Runtime/Config/Cache/Data-Struktur
- Runtime-Verzeichnis- und Socket-Rechte

Nicht direkt übertragbar ist das Rendering selbst. GIF-Player rendert Pillow-Frames über Cairo. Ciel muss WebKitGTK oder eine andere Web-Engine in einer Layer-Shell-Surface hosten. Genau das ist der wichtigste frühe Spike.

## Compositor-Matrix

| Umgebung | Erwartung | Hinweis |
|---|---|---|
| Hyprland | Ziel | Layer-Shell vorhanden |
| Sway | Ziel | wlroots + Layer-Shell |
| Niri | Ziel | vorhandener GIF-Player dient als Referenz |
| KDE Plasma Wayland | Ziel | Layer-Shell grundsätzlich verfügbar, testen |
| GNOME Wayland | eingeschränkt | kein regulärer Layer-Shell-Support, Fallback nötig |
| X11 | UNKLAR | nur implementieren, wenn ausdrücklich Release-Ziel |

## Erster technischer Spike

Der erste ausführbare Linux-Code soll bewusst klein bleiben:

1. GTK3-Fenster erzeugen
2. GtkLayerShell initialisieren
3. WebKitGTK einbetten
4. `hud_prototype.html?mini=1` laden
5. Alpha-Hintergrund aktivieren
6. top-right ankern
7. WebGL/Three.js-Szene prüfen
8. transparente Bereiche prüfen
9. Input-Region/Click-through prüfen
10. Animation über mehrere Minuten auf Flicker und Frame-Drops beobachten

Wenn dieser Spike funktioniert, ist der Linux-Overlay-Pfad technisch ausreichend ent-riskt, um mit der Plattformabstraktion weiterzumachen.

## Nicht im ersten Refactor

- keine komplette Umbenennung von `Great Sage`/`Ciel`
- keine Neuentwicklung des HUDs
- keine gleichzeitige Migration auf GTK4
- kein Rewrite des Python-Cores
- kein Zwang zu GNOME-Kompatibilität über fragile Hacks
- keine Entfernung der Windows-Implementierung
- kein Verschieben der `check_*.py`, bevor alle Build-Aufrufer angepasst wurden
