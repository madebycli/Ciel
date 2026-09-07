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
py app.py          # the real app - HUD, voice, tools
py main.py         # the old CLI, still works
py build.py        # packaged exe; runs the checks below first and
                   # refuses to ship if any fail
```

Three checks exist and the build gates on them. Run them after touching
what they cover:

```bash
py check_js.py         # the inline HUD script parses at all
py check_shaders.py    # GLSL template literals are balanced
py check_routing.py    # asking it to do something actually does it
```

`check_routing.py` matters most: the failure it guards is not a crash but
a confident "I cannot do that" for something it can do. Several of its
cases are transcripts of real requests that were refused. Add the phrasing
to it whenever a request is mishandled - that is what stops the next
change undoing the fix.

Beyond those, verify by running the app. The Python core has no unit
tests, and the HUD can only be checked by looking at it: `great_sage.log`
(beside the exe, or in the project folder from source) is the first place
to look when something is wrong, and page-side JavaScript errors are
forwarded into it as PAGE ERROR.
