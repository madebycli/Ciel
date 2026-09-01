# Great Sage (Prototype 1)

A local-first Windows desktop AI companion. This is the **first working
prototype**: a text chat interface backed by a local Ollama model. Voice,
memory, screen vision, autonomous computer control, and online models are
intentionally not implemented yet — see "What's next" below.

## Requirements

- Python 3.9+
- [Ollama](https://ollama.com) installed and running locally
- At least one Ollama model pulled (default expected: `llama3`)
- Windows, for voice output (uses the built-in SAPI5 speech engine via `pyttsx3`)

## Install

```bash
# From the great_sage/ project folder
pip install -r requirements.txt
```

Make sure Ollama is running and has a model pulled:

```bash
ollama serve          # if not already running in the background
ollama pull llama3    # or whichever model you want to use
```

## Configure (optional)

By default Great Sage talks to Ollama at `http://localhost:11434` and uses
the `llama3` model. Override either with environment variables before
running:

```bash
set GREAT_SAGE_OLLAMA_MODEL=mistral
set GREAT_SAGE_OLLAMA_HOST=http://localhost:11434
```

(`set` is for Windows `cmd`; use `$env:VAR="value"` in PowerShell, or
`export VAR=value` on macOS/Linux.)

Voice output is on by default. To turn it off entirely, open
`great_sage/config/settings.py` and set `VOICE_ENABLED = False`. To use a
different installed Windows voice, adjust `VOICE_ID` in the same file (see
the comment there for how to list installed voices). You can also type
`voice off` inside a running session to stop speaking without restarting.

## Voice cloning (optional, GPU recommended)

Instead of a generic Windows voice, Great Sage can speak in a voice
cloned from a short recording of your own voice, using the local
open-source XTTS-v2 model. This is heavier than the default voice
(bigger install, multi-GB model download, needs real compute) — set it
up once you've confirmed the basic prototype works.

**1. Record a reference clip.** 15-30 seconds of clean audio of you
talking normally — no music, no background noise, no other people
talking. Windows' built-in Voice Recorder app works fine. Export/save it
as a `.wav` file, and place it at `voice_samples/my_voice.wav` in the
project folder (or update `CLONE_REFERENCE_AUDIO_PATH` in
`config/settings.py` to point wherever you saved it).

**2. Install PyTorch with CUDA support *before* the other requirements.**
Check your GPU's CUDA version with `nvidia-smi` in PowerShell (top right
of the output), then run the matching command — use the selector at
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally)
to get the exact command for your setup. As of writing, CUDA 12.6 is a
safe common choice:

```
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
```

Verify it worked:

```
python -c "import torch; print(torch.cuda.is_available())"
```

This should print `True`. If it prints `False`, the voice cloning engine
will still work but will run on CPU — much slower.

**3. Install the rest of the requirements** (this picks up `coqui-tts`
and `sounddevice`; it'll leave your CUDA-enabled torch alone since it's
already installed):

```
pip install -r requirements.txt
```

**4. Switch the engine.** In `great_sage/config/settings.py`, set:

```python
VOICE_ENGINE = "clone"
```

**5. Run it.**

```
py main.py
```

The first response will be slow — it downloads the XTTS-v2 model (a few
GB) the first time it runs. After that, each reply gets synthesized in
your cloned voice.

If anything fails to load, Great Sage prints why and falls back to
text-only rather than crashing — check the message, it'll usually say
exactly what's missing (reference audio not found, package not
installed, model download failed, etc).

## Run

```bash
python main.py
```

You'll get a plain text prompt:

```
You: hello
Great Sage: Hi there! How can I help you today?
```

Type `reset` to clear the conversation history, or `exit` / `quit` to
leave.

If Ollama isn't running or the configured model isn't pulled, Great Sage
prints a clear error and exits (or warns and continues) rather than
crashing with a raw traceback.

## Testing it

There's no formal test suite yet (deliberately — see "known limitations").
To sanity-check the install:

1. Run `python main.py` with Ollama **stopped** — you should get a clean
   `[Startup error]` message, not a traceback.
2. Start Ollama, run `python main.py` again — you should get a chat prompt.
3. Send a message and confirm a streamed response appears.
4. Type `reset`, then ask "what did I just say?" — the model should have
   no memory of the prior message.

## Project structure

```
great_sage/
  main.py                       # Entry point; wires config -> provider -> engine -> UI
  requirements.txt
  great_sage/
    config/
      settings.py                # Host, model, system prompt, active provider
    models/
      base.py                    # ModelProvider abstract interface (provider-agnostic)
      ollama_provider.py         # Ollama implementation of that interface
    core/
      chat_engine.py             # Owns conversation history, drives a ModelProvider
    voice/
      base.py                    # VoiceOutput abstract interface (engine-agnostic)
      tts_engine.py               # pyttsx3 (SAPI5) implementation - default
      cloned_voice_engine.py      # XTTS-v2 voice-cloning implementation - optional
    ui/
      cli.py                    # Plain text chat loop; now also triggers speech
  voice_samples/                  # Put your reference .wav here for voice cloning
```

### What each file does

- **`main.py`** — the only file that knows how to build a concrete
  provider from config. Fails fast with a clear message if Ollama is
  unreachable before ever opening the chat prompt.
- **`config/settings.py`** — all tunable values (host, model, system
  prompt, timeouts) in one place.
- **`models/base.py`** — the `ModelProvider` abstract base class:
  `send_message()`, `stream_response()`, `get_available_models()`. This is
  the seam that keeps the app from being hard-coded to Ollama. A future
  `OpenAIProvider` or `AnthropicProvider` just implements this same
  interface.
- **`models/ollama_provider.py`** — talks to a local Ollama server over
  its REST API (`/api/chat`, `/api/tags`). Translates connection/timeout/
  HTTP errors into a single `ModelProviderError` so the rest of the app
  doesn't need to know anything about `requests` or HTTP status codes.
- **`core/chat_engine.py`** — holds the conversation history (including
  the system prompt) and calls whichever provider it's given. Contains no
  Ollama-specific code at all — it only ever touches `ModelProvider`.
- **`voice/base.py`** — the `VoiceOutput` abstract interface: `speak()`,
  `stop()`. Same pattern as `models/base.py` — keeps the app from being
  hard-coded to one speech engine.
- **`voice/tts_engine.py`** — `pyttsx3`-based implementation, using
  Windows' built-in SAPI5 voices. Fully offline, no cloud service. The
  default engine.
- **`voice/cloned_voice_engine.py`** — XTTS-v2-based implementation that
  speaks in a voice cloned from your own reference recording. Also fully
  local, but needs a GPU-class PyTorch install and more setup — see
  "Voice cloning" above. Selected by setting `VOICE_ENGINE = "clone"`.
- **`ui/cli.py`** — the text chat loop: read input, stream a reply, print
  it, speak it (if voice is on), repeat. Talks to `ChatEngine` and
  `VoiceOutput`, never to a provider or TTS engine directly.

## Known limitations

- You still type your side of the conversation — there's no voice
  *input* (speech-to-text) yet, only voice *output*. That's the next
  planned milestone.
- Still a terminal window, no GUI (a proper desktop UI is a later
  milestone).
- Voice output is blocking — Great Sage finishes speaking before you can
  type your next message. Fine for a prototype, worth revisiting once
  the desktop UI exists.
- No persistent memory. Conversation history lives only in memory for the
  current run; closing the app loses it. SQLite-backed memory is planned
  but not built yet.
- Single provider (Ollama only). The `ModelProvider` interface is ready
  for more, but no OpenAI/Anthropic implementation exists yet.
- No tool-use / computer-control capability. The model can only talk —
  it cannot take any action on the machine.
- No screen understanding / vision.
- No automated test suite — only the manual checks above. Once the
  architecture stabilizes a bit more, adding `pytest` with a fake
  `ModelProvider` (as used for manual smoke-testing during development)
  is a natural next step.
- Single hardcoded system prompt with no persona customization UI.
- No packaging/installer — this runs from source via `python main.py`,
  not as a standalone `.exe`.

## What should be built next

Roughly in order of natural dependency, not obligation — pick based on
what's most useful next:

1. **Voice input (speech-to-text)** — so you can talk instead of typing.
   The natural next step now that output works; it slots into the same
   `voice/` module behind its own interface, feeding transcribed text
   into the same `ChatEngine.send_streaming()` the CLI already uses.
2. **Basic persistent memory** — SQLite-backed conversation history so
   sessions survive restarts. `memory/` is already reserved in the
   architecture and `ChatEngine` already isolates history management in
   one place.
3. **A second model provider** (e.g. OpenAI or Anthropic) to prove out
   the `ModelProvider` abstraction under real conditions and add the
   "user selects provider/model" capability described in the core
   principles.
4. **A minimal desktop UI / system tray presence** instead of a terminal
   window — makes sense once both voice directions work.
5. **A first explicit tool** (e.g. "read a file" or "run a shell
   command") implemented as a constrained, explicit function the model
   can request — not open-ended computer access — to start the `tools/`
   module on the right foundation.
6. Screen vision remains a later-stage milestone once the above is
   solid.
