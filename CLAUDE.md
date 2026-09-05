# Great Sage

Local-first Windows AI companion (Ollama chat + optional cloned-voice TTS).
Full history, environment quirks, and troubleshooting detail live in
[NOTES.md](NOTES.md) — read it before touching `voice/` or
`config/settings.py`. This file is just the short, always-apply version.

## Core principles

1. Local-first.
2. Model must stay replaceable - go through `ModelProvider`, never hard-code a provider.
3. Every swappable piece gets an abstract interface (see `models/base.py`, `voice/base.py`).
4. Incremental - don't build ahead of what's needed.
5. Computer actions go through explicit tools only, never unrestricted LLM access.
6. Explain an architectural approach before building it.

## Environment gotchas

- Use `py`, not `python` - the bare `python` command hits the Microsoft
  Store stub on this machine.
- XTTS-v2 truncates/garbles text past a per-language character limit
  (71 for Japanese, 250 for English, etc.) - always chunk before
  `inference_stream()`. See `_CHAR_LIMITS` in `cloned_voice_engine.py`.
- `CLONE_LANGUAGE` and `CLONE_TRANSLATE` must agree: if translation is
  off, the on-screen reply language and `CLONE_LANGUAGE` must match or
  speech comes out mispronounced.

## Run / test

```bash
py main.py
```

No automated test suite exists yet (see NOTES.md's technical-debt notes) -
verify changes by running the app and checking behavior manually until
one exists.
