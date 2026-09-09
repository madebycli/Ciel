# Installing Great Sage

Windows only. Roughly 30 minutes, most of it downloads.

Three things live outside the app and cannot be bundled: **Ollama**, the
**chat model** it serves, and Microsoft's **WebView2** runtime. The voice
weights are a fourth - they download themselves the first time Great Sage
speaks. That is what most of the size and most of the waiting is.

---

## Before you start

| | Why |
|---|---|
| **Windows 10 or 11** | The overlay, the global hotkey and the window handling are Win32 |
| **An NVIDIA GPU** | Not strictly required, but the voice is unusably slow without one. Developed on a 3060 (12GB) |
| **~10 GB free** | App, chat model, and voice weights |
| **Python 3.14** | The app itself |
| **Python 3.11** | A second install, for the overlay window only |

**Both Python versions, really.** The transparent overlay needs PySide6
6.4.3, which has no build for 3.14, and newer Qt flickers on a
translucent always-on-top window. So the overlay runs as its own process
on 3.11. Get both from [python.org](https://www.python.org/downloads/windows/)
and tick **"Add python.exe to PATH"** on the first one.

---

## Step 1 - Ollama and the model

Install [Ollama](https://ollama.com/download), then in a terminal:

```bash
ollama pull qwen3.5:4b
```

About 2.5 GB. Ollama runs as a background service after install; you do
not need to start it by hand.

Check it worked:

```bash
ollama list
```

`qwen3.5:4b` should be in the output.

---

## Step 2 - WebView2

Most Windows 11 machines already have it. If the setup window says it is
missing, get the **Evergreen Bootstrapper** from
[Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/),
run it, and carry on.

---

## Step 3 - The code

```bash
git clone https://github.com/shogunyan12/The-GREAT-SAGE.git
cd The-GREAT-SAGE
```

---

## Step 4 - PyTorch, before anything else

**This order matters.** Install torch on its own first, with the CUDA
build for your GPU. If you let `requirements.txt` resolve it, pip gives
you the CPU build and the voice is too slow to use.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

Check which CUDA version your driver supports with `nvidia-smi` (top
right of the output) and adjust `cu121` if you need a different one -
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally)
gives the exact command.

Then verify, before going further:

```bash
py -c "import torch; print(torch.cuda.is_available())"
```

**This must print `True`.** If it prints `False`, stop and fix it here -
everything else will install fine and the voice will not work. See
troubleshooting below.

---

## Step 5 - Everything else

```bash
pip install -r requirements.txt
```

---

## Step 6 - The overlay's own environment

```bash
py -3.11 -m venv .overlay-venv
.overlay-venv\Scripts\pip install PySide6==6.4.3
```

The main app runs fine without this - you just cannot enter overlay mode.

---

## Step 7 - Run it

```bash
py app.py
```

On a machine that is not set up, this opens the **setup window** instead
of the HUD, listing what is missing and installing what it can. Once
everything is green it goes straight to the HUD from then on.

**The first reply will be slow.** Great Sage downloads the F5-TTS voice
model and the Vocos vocoder the first time it speaks - about 3.6 GB. It
is not frozen. After that it is local and fast.

---

## Using it

| | |
|---|---|
| **Hold Alt+1** | talk; release to send. Works from any window, including inside a game |
| **Click the core** | the radial menu: settings, chat, logs, overlay |
| **F1** | show/hide the developer chrome |
| **Esc** | close whatever is open |

---

# Troubleshooting

## No voice - it replies in text but never speaks

Almost always one of three things, in this order of likelihood.

**1. torch is the CPU build.**

```bash
py -c "import torch; print(torch.cuda.is_available())"
```

If this prints `False`, that is the problem. Reinstall:

```bash
pip uninstall torch
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

**2. The voice weights could not download.**

Look in `great_sage.log` (next to the exe, or in the project folder when
run from source) for lines mentioning `huggingface.co`. If you see
`CERTIFICATE_VERIFY_FAILED` or `Hostname mismatch`, something on the
network is intercepting HTTPS - usually antivirus with HTTPS scanning
enabled, or a corporate proxy. Turn off HTTPS/SSL scanning for
`huggingface.co` and restart, or install on a different network.

This one is quiet: the app starts normally and simply never speaks.

**3. No NVIDIA GPU.** F5-TTS on CPU is too slow to speak in real time.
Great Sage falls back to a lighter engine and then to text-only rather
than hanging, so a machine with no GPU still works - just without the
cloned voice.

**If none of those:** `great_sage.log` records why the voice engine
failed on startup. Search it for `Voice` and the reason will be there in
plain English.

## The setup window is stuck / nothing happens when I click Install

The buttons that install things run `winget` underneath and need it
present. If a row will not go green, install that one by hand from the
link the window shows, then press **Re-check**. Nothing is lost -
detection is shared with the app's own startup check, so once it is
actually installed the window will see it.

If the setup window itself will not open, WebView2 is missing - it is the
thing that draws it. Install the Evergreen Bootstrapper (step 2) and try
again.

## "No connection to Ollama" / replies never arrive

```bash
ollama list
```

- **Command not found** - Ollama is not installed, or not on PATH. Reinstall.
- **Empty list** - run `ollama pull qwen3.5:4b`.
- **Lists the model but Great Sage still cannot reach it** - the service is
  not running. Start it with `ollama serve`, or reboot.

## Push-to-talk does nothing

Another application has claimed **Alt+1**. Open settings and pick a
different key - the panel says whether the one you chose was actually
registered. Great Sage keeps retrying every few seconds, so if the other
app releases the key it will pick it up on its own.

Also check the **Microphone** setting is the right device.

## Overlay mode does nothing

`.overlay-venv` is missing or has the wrong PySide6. Redo step 6. The
version matters - `PySide6==6.4.3` exactly, not the latest.

## It answers but will not open or search anything

Check `great_sage.log` for `Pre-routed tool`. If those lines are there
and it still did nothing, say so in an issue with the line - that is a
routing bug and there is a test file for exactly this
(`check_routing.py`).

---

## Where your data lives

Conversations, memories, settings and API keys are written **next to the
exe**, or in the project folder when run from source. Nothing is sent
anywhere. Deleting that folder resets Great Sage to a fresh install.
