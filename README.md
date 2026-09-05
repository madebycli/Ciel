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

The chat persona (Great Sage, addressing you as "Master") lives in
`SYSTEM_PROMPT` in the same file.

## Voice cloning

Instead of a generic Windows voice, Great Sage can speak in a voice
cloned from a short recording of your own. Two engines are available;
**Pocket TTS is the default** and the one to use unless you specifically
need Japanese.

### Pocket TTS (default, `VOICE_ENGINE = "pocket"`)

[Kyutai's Pocket TTS](https://kyutai.org/blog/2026-01-13-pocket-tts/) -
CPU-only (no GPU/CUDA setup needed), ~150-200MB model, MIT licensed.
English, French, German, Spanish, Portuguese, and Italian only - **no
Japanese**.

**1. Record a reference clip - keep it short.** ~5-10 seconds of clean
audio of you talking normally, no background noise. **This is important:
a much longer clip (tested at 40-57s) produces garbled, screechy
output** - Pocket TTS is a small model and isn't robust to a
reference clip much longer than it expects, unlike XTTS below. Save it
as a `.wav`/`.mp3` under `voice_samples/`, and point
`CLONE_REFERENCE_AUDIO_PATH` in `config/settings.py` at it.

**2. Install it:**

```
pip install pocket-tts
```

**3. Get access to the voice-cloning weights.** These are gated
separately from the base model on Hugging Face (a responsible-use
agreement, not a paywall - license is CC-BY-4.0):
   - Create a free account at [huggingface.co](https://huggingface.co)
     if needed, visit
     [huggingface.co/kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts),
     and accept the terms shown there.
   - Generate an access token (Settings → Access Tokens, "Read" preset)
     and log in locally - the `hf` CLI (not the older `huggingface-cli`)
     ships with `huggingface_hub`, but its install folder often isn't on
     `PATH`:
     ```powershell
     & "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\Scripts\hf.exe" auth login --token hf_YOUR_TOKEN
     ```
     (`--token` avoids a masked-input paste issue some terminals have
     with the plain interactive prompt.) This persists to
     `~/.cache/huggingface/token`, so it's picked up automatically after
     that - no need to repeat it.

**4. Run it** - `VOICE_ENGINE = "pocket"` is already the default:

```
py main.py
```

If the voice sounds garbled/screechy, the first thing to check is
reference clip length (see step 1) - not a bug, a real model limitation
discovered the hard way.

### XTTS-v2 (alternate, `VOICE_ENGINE = "clone"`, GPU recommended)

Heavier than Pocket TTS (bigger install, multi-GB model download, needs
real compute) but multilingual, including Japanese - use this if you
need a language Pocket TTS doesn't support.

**1. Record a reference clip.** 15-30 seconds this time (XTTS tolerates
a longer clip fine, unlike Pocket TTS above) of clean audio, saved as a
`.wav` under `voice_samples/`.

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

**Speaking a different language than the chat text (e.g. Japanese).**
Set `CLONE_LANGUAGE` (e.g. `"ja"`) and `CLONE_TRANSLATE = True` in
`config/settings.py` to have each English reply translated right before
it's spoken. Japanese additionally needs two more packages beyond the
base list above:

```
pip install cutlet fugashi[unidic-lite]
```

`cutlet`'s dependency `mojimoji` has no precompiled wheel for any
platform — it compiles from source, which needs a C compiler. On Windows
without one installed already, this fails with `Microsoft Visual C++ 14.0
or greater is required`; install "Desktop development with C++" from the
[Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
installer, then retry the pip command above.

### Pre-recorded voice lines (either engine)

`VOICE_LINES` in `config/settings.py` maps fixed phrases the persona is
instructed to say (matched against the reply text before any
translation) to short pre-recorded audio clips that play instead of
being synthesized — see NOTES.md for the current setup and its known
limitations.

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
- Translating each reply before speaking it (`CLONE_TRANSLATE = True`)
  costs a second, full model round-trip per message — noticeably more
  latency before speech starts than speaking the reply text as-is.
- Voice-line audio triggers (`VOICE_LINES`) match exact fixed phrasing
  from the model's output; if the model varies that wording, a trigger
  just silently doesn't fire rather than erroring.
- `CLONE_LANGUAGE = "ja"` currently requires `cutlet`/`mojimoji`, which
  needs a C compiler to install on Windows (see "Voice cloning" above) —
  unresolved on at least one development machine as of this writing.

## What should be built next

**First: a stabilization pass**, not a new feature — the voice pipeline
picked up real technical debt in one fast-moving session (see NOTES.md
for detail): resolve the cutlet/Build-Tools decision above, add a
`pytest` suite for the pure text-splitting logic that's already broken
twice, and keep these docs in sync going forward.

After that, roughly in order of natural dependency, not obligation — pick
based on what's most useful next:

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
