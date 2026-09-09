# Linux Native Dependencies

The Python requirements file intentionally does not try to install the native GTK stack.

The native overlay spike requires:

- GTK 3
- PyGObject / GObject Introspection
- WebKitGTK with WebKit2 introspection data
- gtk-layer-shell with introspection data
- a Wayland compositor with layer-shell support

WebKitGTK requires the parent window to use an RGBA visual and be app-paintable for transparent web view backgrounds. WebGL is explicitly enabled by the overlay host.

ROCm-enabled PyTorch is installed separately from `requirements.txt`, because the correct wheel depends on the host AMD GPU, ROCm version and supported Linux platform.

Use:

```bash
python ciel.py --diagnose
python ciel.py --overlay-spike
```

The overlay spike is not yet the production host. Its job is to prove WebGL, alpha transparency, Three.js animation and layer-shell placement before input regions, dragging and panel windows are ported.
