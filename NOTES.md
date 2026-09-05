# Great Sage — Project Notes for Claude Code

This file summarizes everything decided and discovered while building Great
Sage in a prior chat session, so a fresh Claude Code session has full
context without re-deriving it. Read this alongside the actual code and
README.md — this file covers reasoning, environment quirks, and
troubleshooting history that aren't otherwise written down anywhere.

There's also a short `CLAUDE.md` in this folder — it's the terse,
always-applied version (core principles, environment gotchas, run/test
commands) that gets auto-loaded every session. This file is the detailed
narrative/troubleshooting history behind it; CLAUDE.md points here for
depth instead of duplicating it.

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
      tts_engine.py               <- pyttsx3/SAPI5 implementation (VOICE_ENGINE = "sapi5")
      pocket_tts_engine.py        <- Kyutai Pocket TTS voice-cloning implementation (VOICE_ENGINE = "pocket", current default)
      cloned_voice_engine.py      <- XTTS-v2 voice-cloning implementation (VOICE_ENGINE = "clone")
      voice_lines.py              <- shared voice-line trigger-matching logic, used by both cloning engines
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
- The cloned-voice engine now streams audio (plays as it's synthesized,
  not after the whole reply finishes) and caches the reference-audio
  voice conditioning once at startup instead of recomputing it every
  message — both were real latency/glitch fixes, not just tuning. See
  "Persona, language, and voice-line pipeline" below.
- A Great Sage / Raphael persona (from *That Time I Got Reincarnated as a
  Slime*) is live in `SYSTEM_PROMPT` — see that section for details and
  the current known blocker on the Japanese-speech half of it.
- Git version control — see "Version control" below.
- **Default voice engine is now `VOICE_ENGINE = "pocket"`** (Kyutai
  Pocket TTS), not XTTS-v2 — see "Pocket TTS integration" below. English
  only for now; the user decided they only need English (they can't
  understand Japanese themselves, only the specific pre-recorded voice
  lines they made). XTTS ("clone") is kept in the codebase as an
  alternative, not deleted.

## Persona, language, and voice-line pipeline

Added in a later session, on top of the original prototype above.

**Persona.** `SYSTEM_PROMPT` in `config/settings.py` now writes Great Sage
as the analytical AI from Rimuru's mind in *That Time I Got Reincarnated
as a Slime* (evolved toward its later form, Raphael): addresses the user
as "Master", opens every reply with the fixed word "Notice.", greets with
a fixed "Good morning, Master." line, stays to 1-3 sentences unless the
question genuinely needs more, and mostly refers to itself in third
person rather than "I". On-screen chat text is deliberately kept in
**English** — see next.

**English text, Japanese speech.** The user wants to read English but
hear Japanese. `CLONE_LANGUAGE = "ja"` controls what language XTTS
actually pronounces; `CLONE_TRANSLATE = True` (in `settings.py`) is the
bridge - `main.py`'s `build_translator()` wraps the *same* `ModelProvider`
already in use and makes a second, independent, history-free
`send_message()` call per reply to translate it into `CLONE_LANGUAGE`
right before speaking, without polluting the visible chat history. This
means a normal reply now costs two Ollama generations (one streamed and
visible, one for translation) - a real latency cost, not free.

**Voice-line audio triggers.** The user has (and is populating with their
own Japanese recordings) a ~949-file voice-line library at
`../sounds/` — a sibling folder *outside* this project, deliberately not
version-controlled (see "Version control"). Two files are wired up so
far: `sounds/voice/koku.ogg` ("Notice" in Japanese) and
`sounds/voice/kidou.ogg` ("Good morning, Master" in Japanese).
`config/settings.py`'s `VOICE_LINES` is a list of (regex, audio path)
pairs, matched against the model's **English** reply text (before
translation) and anchored to the start of whatever text remains to be
spoken. A match gets spliced out and played as a raw pre-recorded clip
instead of being synthesized - the remaining text still goes through
translation + XTTS normally. `VOICE_LINES_DIR` can be overridden via the
`GREAT_SAGE_VOICE_LINES_DIR` env var if the sounds folder moves.

**XTTS per-language character limits.** XTTS-v2 truncates/garbles input
past a per-language character count, and the limit is much stricter for
CJK languages than for English - the earlier "just try `ja`" test broke
specifically because of this. Japanese is 71 characters vs. English's
250. `cloned_voice_engine.py`'s `_CHAR_LIMITS` table hardcodes Coqui's
internal per-language limits (captured against `coqui-tts>=0.27` - recheck
if that version ever bumps significantly) and `_split_into_chunks()`
splits on sentence boundaries first, then hard-wraps anything still too
long, before every `inference_stream()` call.

**Known blocker: `cutlet` / `mojimoji` needs a C compiler.** Getting
`CLONE_LANGUAGE = "ja"` actually working requires the `cutlet` package
(Japanese text romanization for XTTS), which pulls in `mojimoji` - a
Cython extension with **zero precompiled wheels for any platform**
(confirmed via `pip install --only-binary=:all: mojimoji` — no matching
distribution). It must build from source, which fails on this machine
with `error: Microsoft Visual C++ 14.0 or greater is required`. Two ways
forward, presented to the user, not yet resolved as of this writing:
1. Install Microsoft C++ Build Tools ("Desktop development with C++"
   workload) — a multi-GB, admin-elevated install — then
   `pip install "cutlet>=0.4" "fugashi[unidic-lite]>=1.3" "soundfile>=0.12"`.
2. Fall back: set `CLONE_LANGUAGE = "en"` and `CLONE_TRANSLATE = False` in
   `config/settings.py` to keep speaking English until willing to install
   the build tools. The two Japanese trigger clips still work either way
   since they're raw audio, unaffected by this.

**Known caveat, unverified by ear:** pre-recorded clips play via
`sounddevice.play()`/`wait()`, while streamed TTS chunks are written into
a separately-held `sounddevice.OutputStream`. These are two different
playback mechanisms; whether switching between them mid-reply (e.g. a
clip immediately followed by translated speech) produces an audible gap
or glitch has not been confirmed either way. This only applies to the
XTTS ("clone") engine - Pocket TTS (the current default) sidesteps it
entirely, see below.

## F5-TTS integration (current default engine)

Pocket TTS (below) and a Qwen3-TTS experiment were both replaced by
[F5-TTS](https://github.com/SWivid/F5-TTS) on 2026-09-02, after the user
asked for ElevenLabs-grade cloning with at most ~1s of delay.

**Why it won.** Qwen3-TTS cloned beautifully but was unusably slow, and
the cause turned out to be architectural rather than tuning: it decodes
autoregressively, one token at a time, so it's memory-bandwidth-bound
and its cost scales with output length. Dropping 1.7B -> 0.6B changed
nothing, and the GPU sat at 94% util drawing only 99W of 170W - the
signature of a bandwidth wall, not a compute one. F5 is a
non-autoregressive flow-matching model, so it generates the whole
utterance at once. Measured head-to-head on this machine's RTX 3060,
same reference clip and lines, both warmed up:

| line   | Qwen3-TTS 1.7B | F5-TTS (NFE 8) |
|--------|----------------|----------------|
| short  | 7.62s          | 1.15s          |
| medium | 15.10s         | 1.49s          |
| long   | 40.34s         | 2.27s          |

That's ~11.5x faster on average, and F5 gets *relatively* faster on
longer text (up to 4.07x realtime) where Qwen stayed pinned at 0.27x.

**NFE is the speed/quality dial** and has no autoregressive equivalent:
8 -> 2.16s, 16 -> 3.94s, 32 -> 8.19s on one 9.5s line. The user A/B'd
all three and heard no difference, so `F5_NFE_STEP = 8`.

**Japanese was dropped.** The base F5 checkpoint mangles Japanese, and
the workaround (a blank `ref_text` so F5 self-transcribes) fails
because the bundled Whisper is English-only and transcribes a Japanese
clip into English, mismatching the vocab. Rather than carry a second
JA-specific checkpoint, the Japanese voice candidate and the whole
text/spoken language-switching UI were removed - `set_reply_language`
on ChatEngine, the two `set_*_language` websocket handlers, and the
LANGUAGE settings section all went with it.

**Also removed:** `great_sage/voice/qwen_tts_engine.py`,
`voice_samples/qwen/`, and `run_hud.py`'s `_build_speech_translator`.
The four candidate previews were re-rendered with F5 so the settings
picker plays what the engine will actually sound like.

See `great_sage/voice/f5_tts_engine.py`'s docstring for the reference
caching (`preprocess_ref_audio_text` split out of `infer_process` so
conditioning is computed once at startup, not per reply) and the
producer/consumer threading that overlaps generating chunk N+1 with
playing chunk N.

## Pocket TTS integration (previous default engine; still the fallback)

The user found [Kyutai's Pocket TTS](https://kyutai.org/blog/2026-01-13-pocket-tts/)
and asked to integrate it as an alternative to XTTS-v2. Confirmed via
their GitHub repo/HF model card: 100M parameters, ~150-200MB, **MIT
licensed** (the base repo), **CPU-only** (no CUDA/GPU install needed),
clones a voice from **~5-10s** of reference audio (much shorter than
XTTS's 15-30s), first audio in ~200ms, **English/French/German/Spanish/
Portuguese/Italian only - no Japanese**. Given the user only needs
English (see above), this was a clean fit. Package: `pip install
pocket-tts` (installed clean, no compiler/CUDA fuss at all - a nice
contrast to the XTTS dependency chain below).

**Architecture:** new `great_sage/voice/pocket_tts_engine.py`
(`PocketTTSVoiceOutput`), same `VoiceOutput` interface as the XTTS
engine. The voice-line trigger-matching logic (`_split_voice_lines`) was
factored out of `cloned_voice_engine.py` into a shared
`great_sage/voice/voice_lines.py` (`split_voice_lines()`), since both
engines now need it - this was a deliberate, justified refactor (real
duplication, not speculative). `main.py`'s `build_voice()` picks the
engine class based on `VOICE_ENGINE` ("sapi5" / "pocket" / "clone").

**API notes (verified by inspecting the installed package directly,
not just docs - see the pattern of getting burned by guessed APIs
earlier this session):**
- `TTSModel.load_model()` → model; `model.get_state_for_audio_prompt(path)`
  → a conditioning state dict; `model.generate_audio(state, text)` →
  one complete `torch.Tensor` (not a generator).
- There IS a real streaming generator, `generate_audio_stream()` - but
  `generate_audio()` already loops over it internally and
  `torch.cat()`s the result, so text is never truncated by length
  regardless of which one you call. Deliberately used the *blocking*
  `generate_audio()`, not manual chunk-streaming, specifically to avoid
  reintroducing the buffer-underrun choppiness XTTS's chunk-by-chunk
  `inference_stream()` caused. Each segment (whether a voice-line clip
  or generated speech) plays via a single `sd.play()`/`wait()` call -
  one playback mechanism for everything, unlike XTTS's split design.

**Voice-cloning weights are separately gated on Hugging Face.** The base
(non-cloning, stock-voice) model downloads freely; the voice-cloning
checkpoint requires accepting terms at
`huggingface.co/kyutai/pocket-tts` (a responsible-use agreement - no
cloning others' voices without consent, etc. - license is CC-BY-4.0,
permissive) and authenticating locally. **The modern CLI is `hf`, not
the deprecated `huggingface-cli`** - install location often isn't on
PATH, so call it by full path or persist login via
`hf auth login --token <token>` (non-interactive, avoids a masked-input
paste-corruption issue hit during setup - the interactive prompt failed
with real tokens twice before switching to `--token`). Login persists to
`~/.cache/huggingface/token`, so it's available process-wide, not just
one shell session.

**Reference-clip length matters a lot more than for XTTS - this was the
actual bug behind two rounds of "it sounds like gibberish."** Fed a
40-57 second reference clip (matching XTTS's 15-30s+ comfort zone) and
got screechy, garbled output both times, regardless of clip language.
Kyutai's docs say Pocket TTS wants **~5-10 seconds**. Confirmed
empirically: `get_state_for_audio_prompt()` took 7.5-12.6s to process
the 40-57s clips vs. **0.9-0.7s** for an 8s and a 6.5s clip - it's
processing the whole clip, not sampling a short snippet internally, and
the small 100M-param model isn't robust to a much-longer-than-expected
reference. Trimming to 8s fixed most of it; a fresh, cleanly-recorded
6.5s clip (`voice_samples/my_voice_clean.wav`, now the active
`CLONE_REFERENCE_AUDIO_PATH`) fixed it completely. **Takeaway: if
Pocket TTS ever sounds garbled again, check reference clip length
first, before anything else.** The original two backup recordings
(`voice_samples/my_voice.wav`, `voice_samples/my_voice_en.mp3`) are kept
around unused, in case they're wanted again for XTTS.

## Gotcha: the HUD's Web Audio graph vs. Chromium's autoplay policy

Cost a long debugging session on 2026-09-02, and the symptom is
misleading enough to be worth writing down.

**Symptom:** no speech at all, and `great_sage_hud.log` full of
`VoiceError: Timed out waiting for the HUD to finish playing audio`
after a 30s stall. TTS itself was fine - a websocket probe confirmed the
server was sending a valid, correctly-decoding WAV.

**Cause:** `hud_prototype.html` routes `voiceLineAudio` through
`createMediaElementSource(...) -> analyser -> audioCtx.destination` so
the ring pulses to real amplitude. Once an element is wired into a Web
Audio graph, its sound reaches the speakers ONLY through that graph.
Chromium starts every `AudioContext` **suspended** until a user gesture,
and a suspended context doesn't merely mute the element - the element
stops progressing, so **`ended` never fires either**. Hence the double
failure: silence AND a hung ack.

`audioCtx.resume()` was only being called from the two test buttons and
`sendChatMessage()`, so *typing* worked while **push-to-talk, the wake
word, and the startup greeting were all silent**. That asymmetry is what
made it look intermittent.

**Fixes (all three, they cover different gaps):**
1. `playServerAudio()` resumes the context before `play()` - the single
   choke point every audio path funnels through.
2. `run_hud.py` sets `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=
   --autoplay-policy=no-user-gesture-required` before `webview.start()`.
   Required for the startup greeting, which happens before any gesture
   can exist. Must be set before the WebView2 environment is created.
3. A first-`pointerdown`/`keydown` listener resumes the context as a
   backstop.

**Related lesson:** the original `play().catch(() => {})` swallowed the
rejection entirely, which is *why* this presented as "no sound, no
clue". That path now logs the reason and sends the ack anyway, so a bad
clip costs one clip instead of stalling the queue - and the ack is
latched, since a duplicate would unblock the NEXT clip early (Python
clears its wait flag before each send) and truncate it.

## Version control

Git was initialized at this folder (`GREAT SAGE/`) specifically, **not**
the outer `Project Sage` folder - `GREAT SAGE/` is the actual project
root (see the architecture diagram above), and rooting the repo here
naturally keeps the sibling `sounds/` voice-line library (949 files, not
project code) out of version control entirely, rather than needing to
gitignore it after the fact. `.gitignore` excludes `__pycache__/`,
virtualenvs, `.env` files, and `voice_samples/*.wav` specifically (a
`.gitkeep` there is tracked instead - the personal reference recording is
sensitive and machine-specific, so the folder structure is versioned but
the audio itself isn't). Commit identity (`user.name`/`user.email`) is
configured locally for this repo only, per the user's explicit request -
never set globally.

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

## Voice cloning setup — dependency chain that had to be solved (XTTS only)

Everything in this section is specific to `VOICE_ENGINE = "clone"`
(XTTS-v2). The current default, `VOICE_ENGINE = "pocket"` (Pocket TTS),
has none of this - `pip install pocket-tts` and it just works, no CUDA
install order, no FFmpeg hunt, no license prompt. See "Pocket TTS
integration" above for its own (much shorter) setup notes.

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

Active cloned-voice reference clip is `voice_samples/my_voice_clean.wav`
(~6.5s), configured via `CLONE_REFERENCE_AUDIO_PATH` in
`config/settings.py`. **Ideal length depends on the engine** - see
"Pocket TTS integration" above for why this matters more than expected:
Pocket TTS (current default) wants ~5-10s; XTTS-v2 ("clone" engine)
wants 15-30s. Two older recordings are kept as unused backups:
`voice_samples/my_voice.wav` (original, longer) and
`voice_samples/my_voice_en.mp3` (57s - too long for Pocket TTS, caused
garbled output).

## Explicitly declined / out of scope

- Cloning a specific named anime voice actor's voice was explicitly
  declined (real person's voice without consent) — the user pivoted to
  cloning **their own** voice instead, which is what's implemented.

## Discussed next steps (not yet started)

**Before any of the roadmap below**: a stabilization pass was agreed on,
since the voice pipeline changed a lot in one session and picked up real
technical debt (see "Persona, language, and voice-line pipeline" above) -
building voice input on top of it now would be building on sand.
Concretely: resolve the cutlet/Build-Tools decision, add a `pytest` suite
for the pure logic that already broke twice (`_split_voice_lines`,
`_split_into_chunks`, the char-limit table), and keep NOTES.md/README.md
in sync going forward instead of letting them drift again.

Once that's done, in rough priority order, per the original spec's roadmap:

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

---

## Packaging: two bundles, two Pythons

`py build.py` produces `dist/GreatSage/`, and it builds **twice**, with
**two different interpreters**:

| bundle | spec | interpreter | contents |
|---|---|---|---|
| `GreatSage.exe` | `great_sage.spec` | Python 3.14 (default) | HUD, Ollama client, F5-TTS, Whisper, torch/CUDA |
| `overlay/GreatSageOverlay.exe` | `overlay.spec` | Python 3.11 (`.overlay-venv`) | the transparent overlay, PySide6 6.4.3 |

### Why it cannot be one bundle

The overlay needs **PySide6 6.4.3**. Everything newer flickers: Qt moved
QtWebEngine onto the ANGLE backend in 6.5.1, and translucent windows have
flickered on it ever since. But PySide6 only gained Python 3.14 support in
6.10, so **6.4.3 cannot be installed next to the app's interpreter at
all**, and the app is staying on 3.14 where its CUDA torch already works.

So the split is not a packaging preference — it is forced, and it mirrors
exactly how the app already runs from source, where `run_hud.py` launches
the overlay under `.overlay-venv`'s Python while the app stays on 3.14.

Migrating everything down to 3.11 was checked and would work — all twelve
dependencies have 3.11 wheels — but it buys nothing the split does not
already give, at the cost of reinstalling the whole CUDA torch stack.

### The trap this replaced

`great_sage.spec` used to list `overlay_window` and the PySide6 modules in
`hiddenimports`. That could never have worked: built on 3.14 it picks up
PySide6 6.10.1, so the packaged overlay would **flicker** — and only after
being watched for a while, long past the point anyone would look at the
spec. PySide6 is now in that spec's `excludes` (not merely omitted —
`preflight.py` imports it, and that alone pulls in all of Qt), which also
takes ~450 MB out of the app bundle.

### Consequences elsewhere

- `run_hud._overlay_command()` frozen → `overlay/GreatSageOverlay.exe`
  beside the main exe. It must **not** fall back to re-invoking
  `sys.executable --overlay`; that only works when both halves share an
  interpreter, which they now never do.
- `overlay_window._panel_command()` frozen → the overlay exe re-invoked
  with `--panel`, since spawning the `.py` has no interpreter to run it.
- `preflight.check_overlay_host()` has three paths: frozen (does the
  overlay exe exist), source-with-venv (ask **that** Python its Qt
  version), and plain source. Before this it imported PySide6 in-process
  and cheerfully reported 6.10.1 — a Qt the overlay never actually runs.

`build.py verify()` checks the shipped layout rather than the build log:
both exes, the HUD page and vendored three.js, voice assets, the Qt
WebEngine helper *and* its `.pak` resources (absent, the overlay starts and
dies with no window), plus the two that prove the split held — no Qt in the
app bundle, no torch in the overlay's.

### Two bundle-only failures, and why neither showed up in testing

Both were invisible from source and invisible in the build log. Each took
a full 5-minute rebuild to observe, so both now have a `verify()` check.

**1. Missing package METADATA (not modules) — the mute build.**
`torchcodec`'s module was bundled, so transformers' `find_spec` reported
it available; reading its version then raised `PackageNotFoundError`,
which transformers re-raised as `ModuleNotFoundError: Could not import
module 'pipeline'`. f5_tts's `from transformers import pipeline` failed,
the engine fell back to Pocket TTS (excluded), and the app ran **mute in
text-only mode with nothing on screen saying why**.

Bundling a module does not bundle its `.dist-info`. Fixed with
`copy_metadata(..., recursive=True)` over the voice stack.

Three wrong theories were chased first (`huggingface_hub`, `filelock`,
`transformers.models.auto` all "missing") — every one disproved by
reading the bundle's embedded `PYZ.pyz`, which holds 12,280 modules. The
loose files in `_internal/` are only half the bundle; anything concluded
from listing that folder alone is wrong.

**2. `torch.jit.script` needs .py SOURCE at runtime.**
`x_transformers/attend.py` decorates `softclamp` with `@torch.jit.script`
at import time, and scripting reads the function's source back via
`inspect.getsourcelines()`. A bundle carries `.pyc` only, so:
`OSError: could not get source code`. That is not an `ImportError`, so
the voice engine's guard did not catch it and **the whole app failed to
start** — worse than the mute build. Fixed with
`collect_data_files(pkg, include_py_files=True)` for the packages that
actually script something (`x_transformers`, `einops`; verified by grep).

**Diagnosing this class of bug:** the voice engine's import guard now
reports the whole `__cause__` chain (`_describe_cause_chain`). It used to
print only the outermost exception, which named `pipeline` — a module
that was present and not the problem. The real cause was three links
down. Without the chain, every theory is a guess.

### Two more bundle-only failures: no sound, and deafness

**3. Native libraries are not bundled with their package.**
`torchaudio.load()` routes through torchcodec, which dlopens
`libtorchcodec_core*.dll` / `_custom_ops*.dll` / `_image*.dll` from its
own directory. Only the `.dist-info` from fix #1 had been added, so first
synthesis died with `ImportError: No spec found for libtorchcodec_image`
and every reply arrived with **no audio**. Fixed with
`collect_dynamic_libs`, and `binaries` is now a real list in the spec
rather than a literal `[]` passed to Analysis.

**4. Non-Python data assets.** `faster_whisper/assets/silero_vad_v6.onnx`
was absent, and `transcribe(vad_filter=True)` needs it. Push-to-talk
recorded and ran, and Sage never heard a word. Fixed with
`collect_data_files('faster_whisper')`.

**Why #4 took so long to see: two threads were swallowing exceptions.**
`server.py` ran `ptt_recorder.stop` as a bare `threading.Thread` target,
and `WakeWordListener._loop` re-raised inside its own thread. An unhandled
exception in a thread goes to stderr, and a **windowed PyInstaller build
has no stderr** - so it went nowhere. The log showed faster_whisper
processing audio and then simply nothing. Both now log. `transcribe()`
also logs its result, including the empty case, which was previously
indistinguishable from silence.

**Build hygiene:** `_check_locked` now asks `tasklist` whether the exe is
running instead of probing the file - renaming a running .exe to its own
name SUCCEEDS on Windows, so the old check passed and the build then died
mid-way with `PermissionError` on an unrelated file in `_internal`.

### torchcodec, round two - and how the test lied twice

`collect_dynamic_libs('torchcodec')` collected 14 `.dll` files and MISSED
`libtorchcodec_pybind_ops.pyd`. PyInstaller treats `.pyd` as an importable
extension module, but torchcodec never imports it - it locates every
library by path:

    FileFinder(Path(__file__).parent).find_spec(lib_name)
    over EXTENSION_SUFFIXES + ['.dll', '.pyd']

So each binary must sit in the torchcodec package folder under its own
name. The fix copies every `.dll` and `.pyd` from torchcodec's directory
explicitly (25 files, including the image codecs jpeg8 / libwebp* / zlib /
libzstd, which are also loaded by name). The spec now FAILS the build if
that glob comes back empty, and `verify()` names the three files rather
than checking that "some libtorchcodec_* exists" - the vague check passed
while the `.pyd` was missing and the build shipped mute a second time.

**The testing trap, which cost two false "voice works" claims.**
An end-to-end test that sends a chat and counts returned audio bytes
proves nothing on its own: short replies match `VOICE_LINE_SETS` patterns
and play a PRE-RECORDED clip, bypassing F5 entirely. "Good morning,
Master." and "Not yet acquired." are both canned lines, and both returned
healthy-looking audio from a build whose synthesis was broken.

A real synthesis test must check the reply text against every pattern in
`VOICE_LINE_SETS` and require a miss. Ask for a long, specific sentence -
the lighthouse prompt gives ~18 words and ~326KB of audio, versus ~25KB
for a canned clip.
