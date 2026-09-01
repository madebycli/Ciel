# Great Sage — Project Notes for Claude Code

This file summarizes everything decided and discovered while building Great
Sage in a prior chat session, so a fresh Claude Code session has full
context without re-deriving it. Read this alongside the actual code and
README.md — this file covers reasoning, environment quirks, and
troubleshooting history that aren't otherwise written down anywhere.

## Project

Great Sage is a local-first Windows desktop AI companion. Long-term vision:
persistent AI companion with conversation, computer assistance, memory,
voice, screen understanding, tools, coding assistance. **Current state is
still an early prototype** — text chat + voice output are working; nothing
else from the long-term vision is built yet.

## Core principles (apply to all future work on this project)

1. Local-first.
2. AI model must be replaceable — never hard-code to one provider.
3. Modular architecture; every major feature gets a clear interface/API.
4. Incremental features. Don't build ahead of what's needed.
5. Computer actions go through explicit tools, never unrestricted LLM
   access to the machine.
6. Before a major architectural decision, explain the approach first.

## Architecture as built so far

```
great_sage/                      <- outer project folder, any name is fine
  main.py                        <- entry point; wires config -> provider/voice -> engine -> UI
  requirements.txt
  README.md                      <- kept up to date with setup + "what's next"
  voice_samples/
    my_voice.wav                 <- user's own recorded voice sample for cloning
  great_sage/                    <- the actual Python package (must be named exactly this)
    config/
      settings.py                <- ALL tunable values live here
    models/
      base.py                    <- ModelProvider abstract interface (send_message, stream_response, get_available_models)
      ollama_provider.py         <- Ollama implementation (only provider so far)
    core/
      chat_engine.py             <- owns conversation history, drives a ModelProvider, provider-agnostic
    voice/
      base.py                    <- VoiceOutput abstract interface (speak, stop)
      tts_engine.py               <- pyttsx3/SAPI5 implementation (default engine, Windows built-in voices)
      cloned_voice_engine.py      <- XTTS-v2 voice-cloning implementation (VOICE_ENGINE = "clone")
    ui/
      cli.py                    <- terminal chat loop; prints + speaks replies
```

Not yet built (intentionally, per incremental principle): `memory/`,
`tools/`, `vision/`, `state/`, any GUI, voice **input** (speech-to-text).

### Interface pattern used throughout

Every swappable piece (AI provider, voice engine) is an abstract base class
in a `base.py` with a small number of methods, and concrete
implementations elsewhere. `main.py` is the only file that knows how to
build a concrete instance from `config/settings.py` — `core/` and `ui/`
only ever talk to the abstract interfaces. Follow this same pattern for
any new provider/engine/tool going forward.

## What's working right now

- Text chat with a local Ollama model (`llama3`), streamed token-by-token,
  session-only conversation history (no persistence yet).
- Voice output via Windows SAPI5 (`pyttsx3`) — default, always works,
  generic-sounding voice.
- Voice output via a **cloned voice** (the user's own, XTTS-v2) — works,
  but took significant troubleshooting to get running (see below). Set
  via `VOICE_ENGINE = "clone"` in `config/settings.py`.

## User's environment (Windows specifics — relevant for any future debugging)

- Windows, PowerShell as primary shell.
- Path used during setup: `C:\Users\shogu\Desktop\Project Sage\GREAT SAGE`
  (the outer folder can be named anything; must contain `main.py` and a
  `great_sage/` package folder as siblings).
- Python 3.14.7, installed such that the bare `python` command hits the
  Microsoft Store stub and fails ("Python was not found..."). **Use `py`
  instead of `python` for all commands on this machine.**
- NVIDIA GPU. `torch==2.13.0+cu126` (CUDA 12.6) confirmed working with
  `torch.cuda.is_available() == True`.
- Ollama model is pulled as `llama3`, which Ollama internally lists as
  `llama3:latest`. `main.py`'s exact-match check against
  `settings.OLLAMA_DEFAULT_MODEL` doesn't account for the `:latest` tag,
  so it prints a harmless `[Warning] 'llama3' was not found...` on every
  startup even though it works fine. **Known cosmetic bug, not yet
  fixed** — worth fixing the match logic in `main.py` to strip/ignore the
  tag suffix.

## Voice cloning setup — dependency chain that had to be solved

Getting `VOICE_ENGINE = "clone"` working required resolving several
stacked issues, in this order. If voice cloning breaks again (e.g. after
a fresh install), check these in order:

1. **PyTorch must be installed with CUDA support *before* other
   requirements**, or `pip install -r requirements.txt` will pull in a
   CPU-only torch and voice cloning will be extremely slow.
   ```
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
   ```
   Verify: `py -c "import torch; print(torch.cuda.is_available())"` must
   print `True`.

2. **`coqui-tts` (PyPI package name, not `TTS`) conflicts with
   `transformers` 5.x** — it imports `isin_mps_friendly` from
   `transformers.pytorch_utils`, which was removed in transformers 5.
   Fix: pin `transformers>=4.57,<5`. This is already in
   `requirements.txt` with a comment explaining why — **don't remove
   that pin** without checking if upstream coqui-tts has fixed it.

3. **`coqui-tts` needs the `[codec]` extra** for audio I/O via
   `torchcodec`:
   ```
   pip install coqui-tts[codec]
   ```

4. **The Coqui CPML license prompt** appears once, the first time
   speech is actually synthesized (not at import time). It's a
   `[y/n]` terminal prompt agreeing to non-commercial terms. Answer
   `y` for personal use. Only prompts once per install.

5. **FFmpeg must be a specific kind of build**, or `torchcodec` (used by
   `coqui-tts` for audio decoding) fails with a long
   `Could not load libtorchcodec` traceback probing FFmpeg versions 4-9.
   Two separate gotchas stacked here:
   - Must be a **shared** build (ships separate `avcodec-XX.dll` /
     `avformat-XX.dll` / etc. files), not a **static** build (bundles
     everything into `ffmpeg.exe` alone, no DLLs). Gyan.dev's plain
     "full_build" is static; you need the one explicitly labeled
     "full-shared" / "-shared".
   - Must be a **stable release** in the 4-7 major version range — torchcodec
     does not support FFmpeg 8+ on Windows (only Mac/Linux). A "git
     master" nightly build (e.g. one reporting `avcodec 63`) is too new
     and its DLL filenames won't match what torchcodec's precompiled
     `libtorchcodec_coreN.dll` binaries expect.
   - Known-good direct download used during setup:
     `https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-full_build-shared.zip`
   - **Separately**: even with the DLLs present and FFmpeg's `bin`
     folder added to the Windows `Path` environment variable, Windows
     Python 3.8+ does **not** consult `PATH` when resolving a native
     library's DLL dependencies (this is a real, documented Python/Windows
     behavior change, not a mistake the user made). The only reliable
     fixes are `os.add_dll_directory(path)` called before the import, or
     placing the DLLs directly next to the loading `.dll` in
     site-packages.
   - **Status: user reported "i fixed it" but did not confirm the exact
     mechanism.** A partial code fix (adding
     `_register_ffmpeg_dll_directory()` to
     `great_sage/voice/cloned_voice_engine.py`, calling
     `os.add_dll_directory()` on the FFmpeg bin folder before importing
     `torch`/`TTS`) was in progress but may not have been finished or
     shipped in the last zip the user downloaded. **First thing to check
     if voice cloning breaks on a fresh machine/install: whether this
     fix made it into the user's actual working copy of
     `cloned_voice_engine.py`, and if not, finish adding it** (the
     partial version, reference to what it should look like, is
     reasoning above — a function that calls
     `os.add_dll_directory()` on FFmpeg's bin directory, using
     `shutil.which("ffmpeg")` to auto-detect it, called at the top of
     `XTTSClonedVoiceOutput.__init__` before the `torch`/`TTS` import).

## Reference audio

User's cloned-voice reference clip lives at `voice_samples/my_voice.wav`
(path configurable via `CLONE_REFERENCE_AUDIO_PATH` in
`config/settings.py`). User was advised 15-90 seconds, clean audio, no
background noise, for best cloning quality.

## Explicitly declined / out of scope

- Cloning a specific named anime voice actor's voice was explicitly
  declined (real person's voice without consent) — the user pivoted to
  cloning **their own** voice instead, which is what's implemented.

## Discussed next steps (not yet started)

In rough priority order, per the original spec's roadmap:

1. **Voice input (speech-to-text)** — the user's actual next goal
   discussed in chat: wants to talk instead of type. Should follow the
   same interface-based pattern (new abstract base in `voice/` or a
   sibling, feeding transcribed text into the existing
   `ChatEngine.send_streaming()` — no changes needed to `core/` or
   `models/`).
2. Persistent memory (SQLite) — `memory/` module reserved but empty.
3. A second model provider (OpenAI/Anthropic) to prove out the
   `ModelProvider` abstraction.
4. Minimal desktop UI / system tray instead of terminal.
5. A first constrained tool (e.g. read a file) to start `tools/` on the
   right foundation — explicit, not open-ended computer access, per
   core principle 5.
6. Fix the cosmetic `llama3` vs `llama3:latest` startup warning in
   `main.py`.

## Working style notes from the prior session

- Explain proposed architecture before building it, especially for
  anything that adds a new module or a new heavy dependency.
- Test what's testable (interface contracts, error paths) even without
  the full runtime available; be upfront about what couldn't be verified
  end-to-end and needs the user to confirm.
- Keep `README.md` in sync with any new setup steps or config options —
  it's the source of truth for "how do I run this."
