# Linux Native

Ciel targets Wayland first. Hyprland, Niri and Sway are primary compositor targets, with KDE Wayland also expected to be viable.

The overlay path is GTK3 + WebKitGTK + gtk-layer-shell. The existing Three.js HUD remains web content inside the native layer surface.

The first overlay milestone is deliberately small: prove WebGL, alpha transparency, animation and layer-shell placement before porting drag, input regions, panel windows and global shortcuts.

XDG directories are used for config, data, cache and runtime files.
