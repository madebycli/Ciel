"""
Local WebSocket bridge between the HUD (hud_prototype.html, opened in a
pywebview window by run_hud.py) and the real AI backend - a ChatEngine
plus an optional VoiceOutput. Only used by the HUD app; main.py's CLI
mode doesn't import this at all.

Protocol (JSON text frames, one field "type" per message):

  Client -> Server:
    {"type": "chat", "text": "..."}       - user typed a message
    {"type": "audio_ended"}                - the HUD's <audio> element
                                              finished playing the most
                                              recently sent "audio" clip
    {"type": "subscribe_logs"}             - marks this connection as a
                                              logs viewer (log_console_client.py,
                                              run in its own OS console
                                              window) instead of the main
                                              HUD - it gets "log" messages,
                                              never audio/reply ones, and
                                              never becomes voice's active
                                              sink
    {"type": "open_console"}               - sent by the HUD's LOGS button;
                                              tells the server to spawn a
                                              brand-new console window
                                              running log_console_client.py
                                              (which then connects back and
                                              sends its own subscribe_logs)
    {"type": "set_voice_line_enabled",
     "pattern": "...", "enabled": bool}     - toggles one configured voice
                                              line between playing its clip
                                              (True) or falling through to
                                              live TTS (False); persisted
                                              to VOICE_LINE_PREFS_PATH
    {"type": "set_voice_line_set",
     "name": "english"|"japanese"}          - swaps which set of pre-recorded
                                              clips is used; per-line
                                              enabled/disabled prefs carry
                                              over, since they are keyed by
                                              pattern. Persisted to
                                              HUD_SETTINGS_PATH
    {"type": "reset_chat"}                 - the HUD's "New chat" button;
                                              clears ChatEngine's history
                                              (flushing anything unsaved to
                                              memory first) and starts fresh
    {"type": "set_reference_voice",
     "id": "..."}                          - switches the cloned voice to
                                              one of the candidates listed
                                              in VOICE_CANDIDATES_DIR;
                                              persisted to HUD_SETTINGS_PATH
    {"type": "save_settings",
     "settings": {...}}                     - opaque blob of everything else
                                              the settings panel controls;
                                              persisted verbatim to
                                              HUD_SETTINGS_PATH and echoed
                                              back on the next connect
    {"type": "ptt_start"}                  - push-to-talk key pressed;
                                              starts recording the mic
    {"type": "ptt_stop"}                   - push-to-talk key released;
                                              stops recording, transcribes
                                              locally (faster-whisper),
                                              and feeds the result into
                                              the chat pipeline exactly
                                              like a typed message
    {"type": "set_wake_word_enabled",
     "enabled": bool}                       - starts/stops the always-on
                                              background listener for your
                                              configured trigger phrases
                                              ("Hey Great Sage", etc.);
                                              persisted to HUD_SETTINGS_PATH
    {"type": "set_wake_words",
     "phrases": ["..."]}                    - the list of trigger phrases
                                              the wake-word listener
                                              checks each utterance
                                              against; persisted to
                                              HUD_SETTINGS_PATH
    {"type": "set_mic_device", "index": int or null}
                                            - which sounddevice input
                                              device push-to-talk/wake-word
                                              recording uses (null = system
                                              default); persisted to
                                              HUD_SETTINGS_PATH. Output
                                              (speaker) device selection is
                                              handled entirely client-side
                                              via HTMLMediaElement.setSinkId
                                              - there's no server message
                                              for it.

  Server -> Client:
    {"type": "reply_chunk", "text": "..."} - one streamed piece of the reply
    {"type": "reply_done", "text": "..."}  - reply text finished. "text" is
                                              the FINAL reply, which is not
                                              always the concatenation of
                                              the reply_chunks: core/
                                              guardrails.py may substitute
                                              one after generation ends.
                                              Clients should record this
                                              rather than what they
                                              streamed.
    {"type": "audio", "mime": "...", "data": "<base64>"}
                                            - a clip to play (voice line or
                                              synthesized speech); may be
                                              sent multiple times per reply
    {"type": "speaking_done"}              - all audio for this reply finished
    {"type": "error", "message": "..."}
    {"type": "log", "level": "...", "message": "..."}
                                            - one formatted log line, sent
                                              only to subscribe_logs clients
    {"type": "voice_lines", "lines": [...]} - sent right after connecting,
                                              one {pattern, label, enabled}
                                              per configured voice line, so
                                              the HUD can render toggles
    {"type": "candidate_voices", "voices": [...]}
                                            - sent right after connecting,
                                              one {id, file, preview, active}
                                              per candidate in
                                              VOICE_CANDIDATES_DIR
    {"type": "hud_settings", "settings": {...}}
                                            - sent right after connecting,
                                              whatever blob the HUD last
                                              saved via "save_settings"
                                              (empty {} on first run)
    {"type": "wake_word_settings", "enabled": bool, "phrases": [...]}
                                            - sent right after connecting,
                                              the persisted wake-word
                                              toggle state and phrase list
    {"type": "voice_text", "text": "..."}   - a transcript from push-to-
                                              talk or the wake-word
                                              listener, right before the
                                              normal reply_chunk/... flow
                                              for it starts - lets the HUD
                                              show what was heard the same
                                              way it shows a typed message
    {"type": "mic_level", "level": 0.0}    - live mic RMS while push-to-
                                              talk is recording; purely
                                              cosmetic (the HUD's outer
                                              cube ring pulses with it)
    {"type": "audio_devices", "inputs": [...], "selected_input": int or null}
                                            - sent right after connecting,
                                              one {index, name} per
                                              sounddevice input-capable
                                              device, plus whichever index
                                              is currently persisted (or
                                              null for system default)

Multiple connections are expected at once - the main HUD window, and one
subscribe_logs connection per open console window - but this is a local
companion app, not a multi-user service.
"""

import asyncio
import dataclasses
import json
import logging
import os
import queue
import subprocess
import sys
import threading

import sounddevice as sd
import websockets

from great_sage.config import settings
from great_sage.core import (ai_settings, chat_store, guardrails, modes,
                             state as sage_state,
                             hud_settings, memory,
                             tools as tool_layer,
                             personality,
                             voice_line_prefs)
from great_sage.core.metrics import ResponseTimer
from great_sage.log_broadcast import BroadcastLogHandler
from great_sage.models.base import ModelProviderError
from great_sage.voice.base import VoiceError
from great_sage.voice.speakable import cap_for_speech, speakable
from great_sage.voice.browser_sink import BrowserAudioSink
from great_sage.voice.speech_input import PushToTalkRecorder, WakeWordListener

HOST = "localhost"
PORT = 8765

log = logging.getLogger(__name__)


_GUARD_PROTECTED = None


def _log_exceptions(fn, label):
    """Wrap a thread target so a failure is LOGGED rather than lost.

    An unhandled exception in a bare thread prints to stderr and nothing
    else - and a windowed PyInstaller build has no stderr, so it goes
    nowhere at all. Push-to-talk failed exactly this way in a packaged
    build: faster_whisper logged that it processed the audio, transcribe()
    then raised, the thread died silently, and the only symptom was that
    Sage never responded to speech. Nothing in the log, nothing on screen.
    """
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:
            log.exception("%s failed", label)
    return run


def _guard_protected() -> str:
    """The prompt PROSE a reply must never recite back, built once.

    Excludes settings.PERSONA_PHRASES deliberately. Those lines exist to be
    reproduced near verbatim - the identity line alone is ~120 characters -
    so folding them in would make every correctly-phrased reply look like a
    leak. What remains is the prose that only the prompt should ever
    contain.
    """
    global _GUARD_PROTECTED
    if _GUARD_PROTECTED is None:
        _GUARD_PROTECTED = (settings.SYSTEM_PROMPT
                            + personality.render(personality.current_state()))
    return _GUARD_PROTECTED


def _spawn_log_console() -> None:
    """Opens log_console_client.py as its own separate window (like a
    Forge mod's separate log window) rather than anything inside the
    HUD's own page.

    CREATE_NO_WINDOW, not CREATE_NEW_CONSOLE: that script used to BE a
    console app printing to stdout, but it now opens its own styled
    pywebview window, so a console would just sit there empty behind it.
    """
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_console_client.py")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.Popen([sys.executable, script_path], creationflags=creationflags)


def _list_candidate_voices(active_path):
    """Scans VOICE_CANDIDATES_DIR for "<id>.wav"/"<id>_preview.wav" pairs.
    active_path is the voice engine's current reference audio path -
    compared by absolute path so the matching entry can be flagged
    "active" regardless of how each side happens to be spelled."""
    candidates_dir = settings.VOICE_CANDIDATES_DIR
    if not os.path.isdir(candidates_dir):
        return []
    active_abs = os.path.abspath(active_path) if active_path else None
    voices = []
    for fname in sorted(os.listdir(candidates_dir)):
        if not fname.endswith(".wav") or fname.endswith("_preview.wav"):
            continue
        voice_id = fname[: -len(".wav")]
        file_native = os.path.join(candidates_dir, fname)
        preview_native = os.path.join(candidates_dir, f"{voice_id}_preview.wav")
        voices.append({
            "id": voice_id,
            "file": file_native.replace(os.sep, "/"),
            "preview": preview_native.replace(os.sep, "/") if os.path.isfile(preview_native) else None,
            "active": active_abs is not None and os.path.abspath(file_native) == active_abs,
        })
    return voices


def _preview_clip_path(voice_id, voice):
    """The wav to audition the effects on: the named candidate's preview
    clip, falling back to whichever voice is currently active."""
    if voice_id:
        candidate = os.path.join(settings.VOICE_CANDIDATES_DIR, f"{voice_id}_preview.wav")
        if os.path.isfile(candidate):
            return candidate
    active = getattr(voice, "current_reference_audio", None)
    if active and os.path.isfile(active):
        return active
    return None


def _render_fx_preview(clip_path: str, voice) -> str:
    """Apply the live effect chain to a clip and return it base64-encoded.

    Deliberately reuses voice.fx - the same object the speech path uses -
    so a preview can never disagree with the real output.
    """
    import base64
    import io

    import soundfile as sf

    data, rate = sf.read(clip_path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    processed = voice.fx.apply(data, rate)
    buf = io.BytesIO()
    sf.write(buf, processed, rate, format="WAV")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _list_input_devices():
    """Every sounddevice device with at least one input channel, for the
    mic-picker setting. Index is whatever sounddevice itself assigns (not
    reindexed), since that's exactly what has to be passed back into
    InputStream(device=...) to actually select it.

    Windows exposes the same physical microphone once per audio host API
    (MME, DirectSound, WASAPI, WDM-KS), so the raw names come out as a
    list of identical-looking duplicates - and MME additionally truncates
    every name to 31 characters, which is why some arrive visibly cut
    off. The host API is appended to tell them apart; WASAPI is generally
    the one to pick on modern Windows.
    """
    devices = []
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        log.exception("Could not list audio host APIs")
        hostapis = []
    try:
        for idx, info in enumerate(sd.query_devices()):
            if info.get("max_input_channels", 0) <= 0:
                continue
            name = info.get("name", f"Device {idx}")
            api_idx = info.get("hostapi")
            if isinstance(api_idx, int) and 0 <= api_idx < len(hostapis):
                api_name = hostapis[api_idx].get("name")
                if api_name:
                    name = f"{name} [{api_name}]"
            devices.append({"index": idx, "name": name})
    except Exception:
        log.exception("Could not list audio input devices")
    return devices


def _transcript(messages, limit=60):
    """Flatten a chat into text for the model, newest turns kept.

    Capped because a long conversation would otherwise blow past the
    context window and get silently truncated at the front - losing the
    system instruction rather than the oldest chatter.
    """
    out = []
    for m in (messages or [])[-limit:]:
        if not isinstance(m, dict):
            continue
        who = "Master" if m.get("role") == "user" else "Great Sage"
        text = str(m.get("text", "")).strip()
        if text:
            out.append(f"{who}: {text}")
    return "\n".join(out)


def _summarise_chat(provider, title, messages):
    """Spec S8: topic + compact summary, NOT a replay of the conversation."""
    body = _transcript(messages)
    if not body:
        return None
    prompt = (
        "Summarise this conversation for a sidebar entry. Reply with "
        "exactly two lines and nothing else:\n"
        "Topic: <five words or fewer>\n"
        "Summary: <two sentences: the main point, any decision reached, "
        "and anything left unresolved>\n"
        "Do not copy the conversation back. Do not add commentary.\n\n"
        + body)
    return provider.send_message([{"role": "user", "content": prompt}]).strip()


def _apply_voice_provider(voice):
    """Point the voice engine at an API service, or leave it on F5.

    Set on the engine rather than swapped for a different one: F5 owns the
    voice lines, the effects chain and the sink handshake, and only the
    step that turns text into audio changes. Falling back to F5 is always
    safe and always says why - a voice that silently is not the one
    selected is exactly what made the menu misleading.
    """
    try:
        cfg = ai_settings.load(settings.AI_SETTINGS_PATH)
        from great_sage.voice import api_tts
        if api_tts.available(cfg):
            provider = cfg.get("tts_provider")
            key = (cfg.get("keys") or {}).get("tts", "")
            voice.api_tts = (provider, key)
            log.info("Voice: %s API (no local GPU work)", provider)
        else:
            voice.api_tts = None
    except Exception:
        log.exception("Could not apply the voice provider; staying on F5")
        try:
            voice.api_tts = None
        except Exception:
            pass


def _apply_mode(engine, cfg, send_json=None):
    """Put a mode's limits into effect (spec S39/S40).

    The one that does real work is GAMING: keep_alive 0 means Ollama drops
    the model the moment a reply finishes, which measured 4052MB of VRAM
    handed straight back. SLEEP does the same. Everything else leaves the
    model resident, because reloading costs about four seconds on the next
    message and that is only worth paying when the GPU is wanted
    elsewhere.
    """
    mode = modes.get((cfg or {}).get("mode"))
    provider = getattr(engine, "provider", None)
    if provider is not None and hasattr(provider, "keep_alive"):
        provider.keep_alive = 0 if not mode.keep_model_loaded else None
        if not mode.keep_model_loaded and hasattr(provider, "unload"):
            # Do not wait for the next reply to finish - the point of the
            # mode is to free the card NOW.
            threading.Thread(target=provider.unload, daemon=True).start()
    log.info("Mode: %s (model resident=%s, hud fps=%s, wake word=%s, "
             "web=%s, online=%s)", mode.label, mode.keep_model_loaded,
             mode.hud_fps or "normal", mode.wake_word, mode.allow_web,
             mode.allow_online)
    if send_json is not None:
        send_json({"type": "mode", "mode": mode.name, "label": mode.label,
                   "hud_fps": mode.hud_fps, "wake_word": mode.wake_word,
                   "description": mode.description})
    return mode


def _apply_provider(engine, cfg):
    """Point the engine at whichever provider the settings ask for.

    The LOCAL provider is remembered the first time, so switching away to
    an API and back again does not need it rebuilt - and so a bad key can
    always fall back to something that works.
    """
    if not hasattr(engine, "_local_provider"):
        engine._local_provider = engine.provider
    provider, label = ai_settings.build_provider(cfg, engine._local_provider)
    if provider is not engine.provider:
        log.info("Chat provider is now %s", label)
    engine.provider = provider
    engine.provider_label = label


def _installed_models():
    """Model names Ollama actually has, for the chat-mode selector.

    Asked at runtime rather than listed in settings, so pulling a new
    model makes it selectable without editing anything - spec S6 asks
    that Qwen3.5 not be hard-coded as the permanent model.
    """
    try:
        import requests
        r = requests.get(settings.OLLAMA_HOST + "/api/tags", timeout=6)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return sorted(n for n in names if n)
    except Exception:
        log.exception("Could not list installed models")
        return []


def _suggest_title(provider, messages):
    """A short topic name for the sidebar, from the opening exchange.

    The sidebar used the first 40 characters of the first message, so
    every chat was named things like "could you check why the render" -
    unreadable at a glance and useless for finding a conversation later.

    Generated ONCE, from the first exchange, and then left alone. A title
    that keeps rewriting itself as the topic drifts makes the list move
    under the user, which is worse than a slightly stale name.
    """
    body = _transcript(messages, limit=6)
    if not body:
        return None
    NL = chr(10)
    prompt = (
        "Name this conversation for a sidebar." + NL +
        "Two to four words. Title Case. No quotes, no punctuation, no "
        "explanation - reply with the title and nothing else." + NL +
        "Examples: Clevatess Sound Design, TTS Troubleshooting, "
        "AI Model Research." + NL + NL + body)
    try:
        out = provider.send_message([{"role": "user", "content": prompt}])
    except Exception:
        log.exception("Title generation failed")
        return None
    title = (out or "").strip().strip(chr(34) + chr(39) + ".")
    title = title.splitlines()[0].strip() if title else ""
    # A model that explains itself instead of naming the chat is worse
    # than the fallback, so anything sentence-length is discarded.
    if not title or len(title) > 48 or len(title.split()) > 6:
        log.info("Title discarded as unusable: %r", title[:60])
        return None
    return title


def _chat_takeaway(provider, title, messages):
    """Spec S18/S20: the durable idea worth keeping, compressed."""
    body = _transcript(messages)
    if not body:
        return None
    prompt = (
        "From this conversation, extract only what is worth remembering "
        "long-term about Master - preferences, decisions, how they work, "
        "ongoing projects. Reply with one to three short lines, each a "
        "single fact, no bullets or numbering. If there is nothing worth "
        "keeping, reply with exactly: NOTHING\n\n" + body)
    out = provider.send_message([{"role": "user", "content": prompt}]).strip()
    return None if out.upper().startswith("NOTHING") else out


def _deep_review(provider, question, draft):
    """Think Harder (spec S11 / S69): the model checks its OWN answer.

    Deliberately NOT the model native thinking mode, and not a bigger
    model. Both were measured on this machine:

        normal reply          ~1.3s
        native thinking       155-380s, and it TIMED OUT 2 runs in 3;
                              at the default 4096 context it also
                              returned empty content 3 times in 4,
                              having spent the whole budget deliberating

    Minutes per message is unusable for a spoken assistant, and a larger
    model would cost the VRAM headroom Krazaa keeps for games and calls.
    A second pass with the SAME model is one extra round trip - a few
    seconds - and no extra VRAM whatsoever.

    Returns a replacement answer, or None to keep the draft. The review
    is a private working step: it never reaches the page or the speaker,
    so no chain-of-thought is exposed (S11).
    """
    NL = chr(10)
    # TWO steps, not one. Asking a 4B model to judge and rewrite in a
    # single call was unreliable in both directions, measured here:
    #   with a checklist  -> caught both planted errors, but wrote ABOUT
    #                        the draft ("The draft contains a factual
    #                        error...") instead of answering, and shouted
    #   one blunt line    -> clean format, but caught NEITHER error and
    #                        rewrote a correct answer
    # Separating the verdict from the rewrite gives each call one job.
    verdict_prompt = (
        "Is the ANSWER below correct, and does it avoid claiming any "
        "ability or action it does not actually have?" + NL +
        "Reply with one word: YES or NO." + NL + NL +
        "QUESTION: " + question + NL + "ANSWER: " + draft)
    try:
        verdict = provider.send_message(
            [{"role": "user", "content": verdict_prompt}])
    except Exception:
        log.exception("Deep review verdict failed; keeping the draft")
        return None
    if "NO" not in (verdict or "").strip().upper()[:6]:
        return None                     # judged fine, or unparseable

    rewrite_prompt = (
        "The ANSWER below is wrong or claims something untrue. Write the "
        "correct answer." + NL +
        "Reply with the answer ONLY - no preamble, no mention of the "
        "original, no explanation. Keep it about the same length." + NL +
        "Never say you cannot do something; state what is missing "
        "instead, for example: No connection to that system exists."
        + NL + NL + "QUESTION: " + question + NL + "ANSWER: " + draft)
    try:
        out = provider.send_message(
            [{"role": "user", "content": rewrite_prompt}])
    except Exception:
        log.exception("Deep review rewrite failed; keeping the draft")
        return None
    out = (out or "").strip()
    if not out or out.upper().startswith("KEEP"):
        return None
    # A "revision" far longer than the draft is usually the model
    # explaining itself rather than answering, which would be worse than
    # what it replaced.
    if len(out) > max(400, len(draft) * 3):
        log.info("Deep review discarded: %d chars replacing %d",
                 len(out), len(draft))
        return None
    return out


def _handle_chat(text, engine, voice, sink, websocket, loop,
                 think=False) -> None:
    """Runs in its own thread so the async server loop stays free to
    receive the "audio_ended" acks that unblock voice.speak() below.

    Takes the specific `sink` this connection got (rather than reading it
    back off `voice`) so a later reconnect swapping voice's current sink
    can't get this in-flight reply crossed with a different connection.
    """

    def send(payload: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            websocket.send(json.dumps(payload)), loop
        ).result()

    timer = ResponseTimer(text[:48])

    # Internal state (spec S80). Great Sage never sees these numbers - it
    # receives at most one line about how to pitch this reply.
    st = getattr(engine, "sage_state", None)
    if st is not None:
        sage_state.on_user_message(st, text)
        # A push-back means the last answer missed. Detected from the
        # opening of the message, where a correction actually appears -
        # matching "no" anywhere would fire on every sentence containing
        # the word.
        low = (text or "").strip().lower()
        if low.startswith(("no,", "no ", "nope", "wrong", "that's wrong",
                           "thats wrong", "i meant", "i mean ", "not that",
                           "actually,")):
            sage_state.on_correction(st)
        engine.state_hint = st.reply_hint()

    # Explicit "remember this" / "forget that" is acted on BEFORE the
    # model is called, so the fact is already stored when recall runs for
    # this same turn - ask it to remember something and it can use it
    # immediately. The model still writes the acknowledgement itself, so
    # the reply stays in character instead of being a canned string.
    try:
        from main import handle_memory_command
        note = handle_memory_command(engine.provider, text)
        if note:
            log.info("Memory: %s", note)
    except Exception:
        # Memory is a nice-to-have; never let it break a reply.
        log.exception("Memory command failed for %r", text)

    # Speech is driven straight off the model's stream when the engine
    # supports it: the first sentence is synthesized while the rest of the
    # reply is still being written, instead of everything waiting for the
    # last token. The sink check that used to happen after generation has
    # to happen up front now, since speech starts during it.
    # Streaming is off by default now (VOICE_SINGLE_SHOT). It started
    # speech sooner, but every chunk boundary was a seam - gaps, cut
    # effect tails, audio elements racing over which was free - and the
    # result still dropped the ends of sentences. One clip has no seams.
    streaming_speech = (
        not getattr(settings, "VOICE_SINGLE_SHOT", True)
        and voice is not None
        and hasattr(voice, "speak_stream")
        and voice.current_sink is sink
    )
    text_q: "queue.Queue" = queue.Queue()
    speech_error = []
    speaker = None

    def speak_worker():
        try:
            voice.speak_stream(iter(text_q.get, None), timer=timer)
        except BaseException as exc:  # re-raised on this thread below
            speech_error.append(exc)

    def end_stream():
        """Idempotent: closes the text stream and waits for speech to drain."""
        if speaker is not None:
            text_q.put(None)
            speaker.join(timeout=180)

    try:
        if streaming_speech:
            speaker = threading.Thread(
                target=_log_exceptions(speak_worker, "speech playback"),
                daemon=True)
            speaker.start()
        reply_chunks = []
        used_tools = []
        _tools_ok = getattr(type(engine.provider), "supports_tools", None)
        _tools_ok = True if _tools_ok is None else bool(_tools_ok())
        if (getattr(settings, "TOOLS_ENABLED", True)
                and _tools_ok
                and tool_layer.might_need_tools(text)):
            # Tool-capable turns are NOT streamed. Ollama reports
            # tool_calls only on a complete message, so the call has to
            # finish before it is known whether one was requested at all.
            # No real loss: VOICE_SINGLE_SHOT means speech waits for the
            # whole reply anyway, and captions follow the audio.
            # Run the unambiguous tools ourselves first. Whether a 4B
            # model chooses to call one is close to a coin flip, and the
            # failure mode is a confident wrong answer - it invented four
            # different times in four runs. Spec S16: deterministic APIs
            # for deterministic tasks.
            pre_results = []
            for _name, _args in tool_layer.preroute(text):
                try:
                    _res = tool_layer.execute(_name, _args)
                except Exception as _exc:
                    _res = "FAILED: %s" % _exc
                pre_results.append((_name, _res))
                log.info("Pre-routed tool %s -> %s", _name, str(_res)[:100])
                send({"type": "tool_used", "name": _name,
                      "result": str(_res)[:400]})
            pre_images = tool_layer.take_pending_images() if pre_results else []
            reply, used_tools = engine.send_with_tools(
                text, tool_layer.ollama_schema(), tool_layer.execute,
                collect_images=tool_layer.take_pending_images,
                preroute_results=pre_results, preroute_images=pre_images)
            used_tools = [u for u in used_tools
                          if u[0] not in {n for n, _ in pre_results}] 
            for name, result in used_tools:
                log.info("Tool %s -> %s", name, str(result)[:120])
                send({"type": "tool_used", "name": name,
                      "result": str(result)[:400]})
            timer.first_token()
            reply_chunks.append(reply)
            send({"type": "reply_chunk", "text": reply})
            if speaker is not None:
                text_q.put(reply)
        else:
          for chunk in engine.send_streaming(text):
              timer.first_token()
              reply_chunks.append(chunk)
              send({"type": "reply_chunk", "text": chunk})
              if speaker is not None:
                  text_q.put(chunk)
        timer.text_done()
        # Guardrails run HERE: the full reply exists, but nothing has been
        # displayed or spoken yet. With captionFollowsSpeech the HUD holds
        # reply_chunk text back until audio starts, so a substitution is
        # invisible to Master rather than showing as text that changes on
        # screen mid-reply.
        draft = "".join(reply_chunks)
        if think and draft.strip():
            revised = _deep_review(engine.provider, text, draft)
            if revised:
                log.info("Think Harder revised the reply (%d -> %d chars)",
                         len(draft), len(revised))
                draft = revised
                reply_chunks = [revised]
                # Keep the model own history holding the delivered answer,
                # not the draft it replaced.
                engine.replace_last_reply(revised)
        guarded, note = guardrails.apply(
            draft,
            protected=_guard_protected(),
            send_message=(engine.provider.send_message
                          if getattr(settings, "GUARDRAILS_SELF_REVIEW", True)
                          else None),
        )
        if note:
            log.warning("Guardrail %s on reply to %r", note, text)
        if guarded != draft:
            reply_chunks = [guarded]
            # Keep the model's own context clean too, or a single reply that
            # slipped through would sit in history proving to the model that
            # it already broke character once.
            engine.replace_last_reply(guarded)
        if st is not None:
            sage_state.on_reply(st, guarded, bool(used_tools))
            log.debug("State: %s", st.snapshot())
        # `text` carries the FINAL reply so the HUD's transcript records what
        # was actually delivered, not the discarded draft it streamed.
        send({"type": "reply_done", "text": guarded})
    except ModelProviderError as exc:
        log.exception("Model provider error handling chat message %r", text)
        end_stream()
        send({"type": "error", "message": str(exc)})
        return
    except websockets.exceptions.ConnectionClosed:
        log.warning("Connection closed while streaming reply text for %r", text)
        end_stream()
        return

    try:
        if speaker is not None:
            end_stream()
            if speech_error:
                raise speech_error[0]
        elif voice is None:
            pass  # text-only; nothing to speak
        elif voice.current_sink is not sink:
            log.warning(
                "voice's active sink changed since this reply started (a newer "
                "connection replaced it) - skipping speech for %r to avoid "
                "sending audio down the wrong/closed socket.",
                text,
            )
        else:
            # Fallback for a voice engine without speak_stream (Pocket TTS):
            # unchanged behaviour, speech after the full reply.
            #
            # speakable() strips markup that has no spoken form - code
            # fences, list markers, headings - because every character
            # here is pronounced. Applied at this seam rather than inside
            # an engine so it holds for whichever VoiceOutput is
            # configured, and applied ONLY to speech: reply_chunks were
            # already sent to the HUD verbatim, so the transcript keeps
            # its formatting while the ear is spared. Smaller models need
            # this - qwen2.5:3b leaked markdown on 3/3 list-inviting
            # prompts, and hardening the prompt only reached 1/3.
            voice.speak(cap_for_speech(speakable("".join(reply_chunks))))
    except VoiceError as exc:
        log.exception("Voice/audio error speaking reply to %r", text)
        try:
            send({"type": "error", "message": str(exc)})
        except websockets.exceptions.ConnectionClosed:
            pass
    except websockets.exceptions.ConnectionClosed:
        log.warning("Connection closed while sending audio for reply to %r", text)
    finally:
        timer.finish()
        try:
            send({"type": "speaking_done"})
        except websockets.exceptions.ConnectionClosed:
            pass


def _start_chat_thread(text, engine, voice, sink, websocket, loop,
                       think=False) -> None:
    """Shared by the "chat" message handler and both voice-input paths
    (push-to-talk, wake-word) below - same background-thread dispatch
    either way, so a voice-originated message goes through the exact same
    reply/speech pipeline a typed one does."""
    # Wrapped, for the same reason push-to-talk is: an unhandled
    # exception in a bare thread goes to stderr, and a windowed
    # PyInstaller build HAS no stderr - so a reply that died on its way
    # out would leave no trace anywhere, exactly as the STT failure did.
    # _handle_chat guards its own body, but anything raised before that
    # try block would still escape.
    threading.Thread(
        target=_log_exceptions(_handle_chat, "chat reply"),
        args=(text, engine, voice, sink, websocket, loop),
        kwargs={"think": think},
        daemon=True,
    ).start()


async def run_server(engine, voice) -> None:
    """voice may be None (text-only) or a VoiceOutput with a set_sink()
    method (see PocketTTSVoiceOutput). A connection only becomes voice's
    active sink once it actually sends a "chat" message - not merely on
    connecting - so a logs-viewer connection (which never sends "chat")
    can never steal the active sink out from under the real HUD window.
    """
    loop = asyncio.get_running_loop()

    # Apply whichever cloned-voice candidate was last selected, once, at
    # startup - not per-connection, since it's real engine state (the
    # actual reference audio Pocket TTS conditions on), not per-client UI.
    if voice is not None and hasattr(voice, "set_reference_audio"):
        saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
        active_id = saved.get("active_voice")
        if active_id:
            for entry in _list_candidate_voices(None):
                if entry["id"] == active_id:
                    try:
                        voice.set_reference_audio(entry["file"])
                        log.info("Restored active voice: %s", active_id)
                    except VoiceError as exc:
                        log.warning("Could not restore active voice %r: %s", active_id, exc)
                    break

    # Same idea as the active-voice restore above: engine state, not
    # per-client UI state, so it's applied once at startup.
    if voice is not None and hasattr(voice, "set_fx"):
        saved_fx = hud_settings.load(settings.HUD_SETTINGS_PATH).get("voice_fx")
        if isinstance(saved_fx, dict):
            try:
                voice.set_fx(**saved_fx)
                log.info("Restored voice FX settings")
            except Exception:
                log.exception("Could not restore saved voice FX - continuing dry")

    log_handler = BroadcastLogHandler(loop)
    log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(log_handler)

    # Tracks whichever connection most recently actually talked (typed
    # chat, push-to-talk, or a wake-word match) - both voice-input paths
    # need somewhere to route their result, and only the wake-word
    # listener isn't tied to one specific connection by nature (it isn't
    # triggered by any particular websocket message), so it uses this to
    # find the HUD window's own connection rather than, say, a stray logs
    # viewer that never sends "chat" at all.
    active_connection = {"websocket": None, "sink": None}

    def _route_voice_text(text: str) -> None:
        ws = active_connection["websocket"]
        sink = active_connection["sink"]
        if ws is None or sink is None:
            log.warning("Voice input %r arrived with no active connection to send it to", text)
            return
        try:
            asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps({"type": "voice_text", "text": text})), loop
            ).result()
        except websockets.exceptions.ConnectionClosed:
            return
        if voice is not None:
            voice.set_sink(sink)
        _start_chat_thread(text, engine, voice, sink, ws, loop)

    def _send_mic_level(level: float) -> None:
        # Fire-and-forget (no .result()) - this is called from the audio
        # callback thread for every captured block, so it must not block
        # waiting on the websocket send; dropping an occasional frame of
        # this purely cosmetic signal is harmless.
        ws = active_connection["websocket"]
        if ws is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps({"type": "mic_level", "level": level})), loop
            )
        except websockets.exceptions.ConnectionClosed:
            pass

    engine.sage_state = sage_state.SageState()

    # ---- Autonomy (spec S63) ----
    # A fired task is SPOKEN through the normal reply path rather than
    # pushed as a notification, so it arrives in Great Sage's voice, in
    # the transcript, with the same guardrails as anything else.
    def _may_interrupt():
        mode = modes.get(
            ai_settings.load(settings.AI_SETTINGS_PATH).get("mode"))
        if not mode.wake_word:
            return False          # GAMING / SLEEP: do not interrupt
        st = getattr(engine, "sage_state", None)
        return not (st is not None and st.should_stay_quiet())

    def _on_task_fired(task):
        conn = active_connection.get("websocket")
        if conn is None:
            return                 # nobody listening; it stays pending
        if task.kind == "remind":
            prompt = ("[REMINDER DUE] Tell Master, in one sentence, that it "
                      "is time for: " + task.message)
        else:
            prompt = ("[FOLDER CHANGED] The folder %s has changed. Tell "
                      "Master in one sentence. %s"
                      % (task.path, task.message or ""))
        log.info("Task fired: %s", task.kind)
        _start_chat_thread(prompt, engine, voice,
                           active_connection.get("sink"), conn, loop)

    try:
        from great_sage.core.autonomy import Autonomy
        _tasks = Autonomy(settings.TASKS_PATH, on_fire=_on_task_fired,
                          may_speak=_may_interrupt)
        _tasks.start()
        tool_layer.set_autonomy(_tasks)
        log.info("Autonomy running (%d task(s) restored)", len(_tasks.tasks))
    except Exception:
        log.exception("Autonomy unavailable")

    engine.state_hint = ""

    ptt_recorder = PushToTalkRecorder(on_result=_route_voice_text, on_level=_send_mic_level)

    # The global hotkey drives the SAME recorder the button does, so a
    # voice message started from another window goes down the identical
    # path - transcription, memory, tools, reply, speech - with nothing
    # special-cased for it.
    _hotkey_listening = {"on": False}

    def _toggle_hotkey_listen():
        mode = modes.get(
            ai_settings.load(settings.AI_SETTINGS_PATH).get("mode"))
        if _hotkey_listening["on"]:
            _hotkey_listening["on"] = False
            log.info("Global hotkey: stop listening")
            threading.Thread(
                target=_log_exceptions(ptt_recorder.stop,
                                       "hotkey transcription"),
                daemon=True).start()
        else:
            # SLEEP is meant to do nothing until spoken to first, and this
            # IS speaking to it - so the hotkey works in every mode. That
            # is the point of S40: a way back in when everything else is
            # wound down.
            _hotkey_listening["on"] = True
            log.info("Global hotkey: listening (mode %s)", mode.label)
            try:
                ptt_recorder.start()
            except Exception:
                _hotkey_listening["on"] = False
                log.exception("Could not start recording from the hotkey")

    # ---- Automatic GAMING mode (Phase 10) ----
    # The mode in force before a game started, so it can be put back. None
    # means no game is running as far as this is concerned.
    _auto = {"restore_to": None}

    def _on_game_change(title):
        cfg = ai_settings.load(settings.AI_SETTINGS_PATH)
        if not cfg.get("auto_gaming", True):
            return
        if title:
            if _auto["restore_to"] is not None:
                return                      # already switched for this game
            was = cfg.get("mode", "companion")
            if was in ("gaming", "sleep"):
                return                      # already frugal; leave it alone
            _auto["restore_to"] = was
            cfg = ai_settings.apply_update(cfg, {"mode": "gaming"})
            ai_settings.save(settings.AI_SETTINGS_PATH, cfg)
            _apply_provider(engine, cfg)
            _apply_mode(engine, cfg)
            log.info("Game detected (%r) - switched to GAMING, will restore "
                     "%s afterwards", title[:60], was.upper())
        else:
            back = _auto["restore_to"]
            _auto["restore_to"] = None
            if back is None:
                return
            # If Krazaa changed mode by hand while the game was running,
            # that decision wins - do not undo it behind his back.
            if cfg.get("mode") != "gaming":
                log.info("Game ended, but the mode was changed by hand to "
                         "%s - leaving it", str(cfg.get("mode")).upper())
                return
            cfg = ai_settings.apply_update(cfg, {"mode": back})
            ai_settings.save(settings.AI_SETTINGS_PATH, cfg)
            _apply_provider(engine, cfg)
            _apply_mode(engine, cfg)
            log.info("Game closed - restored %s mode", back.upper())

    _watcher = None
    try:
        from great_sage.core.game_watch import GameWatcher
        _watcher = GameWatcher(_on_game_change)
        _watcher.start()
        log.info("Watching for fullscreen games (auto GAMING mode)")
    except Exception:
        log.exception("Game watcher unavailable")

    _hotkey = None
    if getattr(settings, "GLOBAL_HOTKEY", ""):
        try:
            from great_sage.core.global_hotkey import GlobalHotkey
            _hotkey = GlobalHotkey(settings.GLOBAL_HOTKEY,
                                   _toggle_hotkey_listen)
            _hotkey.start()
        except Exception:
            log.exception("Global hotkey unavailable")

    def _get_wake_phrases():
        saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
        phrases = saved.get("wake_words")
        if isinstance(phrases, list) and phrases:
            return phrases
        return ["hey great sage", "hey raphael", "hey ciel"]

    wake_word_listener = WakeWordListener(
        on_result=_route_voice_text, get_trigger_phrases=_get_wake_phrases
    )
    saved_mic_device = hud_settings.load(settings.HUD_SETTINGS_PATH).get("mic_device")
    if saved_mic_device is not None:
        ptt_recorder.device = saved_mic_device
        wake_word_listener.device = saved_mic_device
    if hud_settings.load(settings.HUD_SETTINGS_PATH).get("wake_word_enabled"):
        wake_word_listener.start()
        log.info("Wake-word listener started (restored from settings)")

    async def handler(websocket):
        sink = BrowserAudioSink(websocket, loop)
        is_log_subscriber = False
        log.info("Client connected")
        # Hand back the saved conversations. Sent on connect rather than
        # on request so the sidebar is populated by the time the page is
        # interactive - the HUD owns the list from then on.
        try:
            stored = chat_store.load(settings.CHAT_STORE_PATH)
            await websocket.send(json.dumps({"type": "chats", "chats": stored}))
            if stored:
                log.info("Restored %d saved chat(s)", len(stored))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            log.exception("Could not restore saved chats")
        try:
            # Identity header (spec correction S5): Great Sage is the name,
            # the model is technical detail. Sent from here so switching
            # models never means editing the page.
            _cfg = ai_settings.load(settings.AI_SETTINGS_PATH)
            _apply_provider(engine, _cfg)
            _mode = modes.get(_cfg.get("mode"))
            await websocket.send(json.dumps({
                "type": "mode", "mode": _mode.name, "label": _mode.label,
                "hud_fps": _mode.hud_fps, "wake_word": _mode.wake_word,
                "description": _mode.description}))
            _apply_mode(engine, _cfg)
            await websocket.send(json.dumps({
                "type": "ai_settings",
                "settings": ai_settings.public_view(
                    ai_settings.load(settings.AI_SETTINGS_PATH))}))
            await websocket.send(json.dumps({
                "type": "model_info",
                "model": getattr(engine.provider, "model",
                                 settings.OLLAMA_DEFAULT_MODEL),
                "provider": getattr(engine, "provider_label",
                                    "Ollama / Local")}))
            await websocket.send(json.dumps({
                "type": "memory",
                "facts": memory.load_memory(settings.MEMORY_FILE_PATH)}))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            log.exception("Could not send the memory list")
        if voice is not None and hasattr(voice, "list_voice_lines"):
            try:
                from main import active_voice_line_set

                await websocket.send(json.dumps({
                    "type": "voice_lines",
                    "lines": voice.list_voice_lines(),
                    "set": active_voice_line_set(),
                    "sets": sorted(getattr(settings, "VOICE_LINE_SETS", {}).keys()),
                }))
            except websockets.exceptions.ConnectionClosed:
                pass
        if voice is not None and hasattr(voice, "set_reference_audio"):
            try:
                active_path = getattr(voice, "current_reference_audio", None)
                await websocket.send(json.dumps({
                    "type": "candidate_voices",
                    "voices": _list_candidate_voices(active_path),
                }))
            except websockets.exceptions.ConnectionClosed:
                pass
        try:
            await websocket.send(json.dumps({
                "type": "hud_settings",
                "settings": hud_settings.load(settings.HUD_SETTINGS_PATH).get("hud", {}),
            }))
        except websockets.exceptions.ConnectionClosed:
            pass
        try:
            saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
            await websocket.send(json.dumps({
                "type": "wake_word_settings",
                "enabled": bool(saved.get("wake_word_enabled")),
                "phrases": _get_wake_phrases(),
            }))
        except websockets.exceptions.ConnectionClosed:
            pass
        try:
            await websocket.send(json.dumps({
                "type": "audio_devices",
                "inputs": _list_input_devices(),
                "selected_input": hud_settings.load(settings.HUD_SETTINGS_PATH).get("mic_device"),
            }))
        except websockets.exceptions.ConnectionClosed:
            pass
        if voice is not None and hasattr(voice, "fx"):
            try:
                await websocket.send(json.dumps({
                    "type": "voice_fx",
                    "fx": dataclasses.asdict(voice.fx.settings),
                }))
            except websockets.exceptions.ConnectionClosed:
                pass
        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Ignoring non-JSON message: %r", raw)
                    continue
                msg_type = data.get("type")
                if msg_type == "subscribe_logs":
                    is_log_subscriber = True
                    log_handler.add_client(websocket)
                    log.info("Logs viewer subscribed")
                elif msg_type == "open_console":
                    log.info("Opening a new log console window")
                    _spawn_log_console()
                elif msg_type == "set_voice_line_enabled":
                    pattern = data.get("pattern")
                    enabled = bool(data.get("enabled"))
                    if pattern and voice is not None and hasattr(voice, "set_voice_line_enabled"):
                        voice.set_voice_line_enabled(pattern, enabled)
                        voice_line_prefs.save_override(
                            settings.VOICE_LINE_PREFS_PATH, pattern, enabled
                        )
                        log.info("Voice line %r enabled=%s", pattern, enabled)
                elif msg_type == "set_voice_line_set":
                    name = data.get("name")
                    sets = getattr(settings, "VOICE_LINE_SETS", {})
                    if name in sets and voice is not None:
                        from main import build_voice_lines

                        # Swap the compiled clips in place. The per-line
                        # enabled/disabled preferences are keyed by
                        # pattern, and the patterns are shared between
                        # sets, so a line switched off stays off across
                        # the change.
                        voice.voice_lines = build_voice_lines(name)
                        saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
                        saved["voice_line_set"] = name
                        hud_settings.save(settings.HUD_SETTINGS_PATH, saved)
                        log.info("Voice line set switched to %r (%d clips)",
                                 name, len(voice.voice_lines))
                        # The HUD's toggle list is per-set, so it has to
                        # be redrawn from the new set rather than left
                        # showing the old one's rows.
                        if hasattr(voice, "list_voice_lines"):
                            try:
                                await websocket.send(json.dumps({
                                    "type": "voice_lines",
                                    "lines": voice.list_voice_lines(),
                                    "set": name,
                                    "sets": sorted(sets.keys()),
                                }))
                            except websockets.exceptions.ConnectionClosed:
                                pass
                elif msg_type == "reset_chat":
                    log.info("Resetting chat history")
                    engine.reset()
                elif msg_type == "set_reference_voice":
                    voice_id = data.get("id")
                    if voice_id and voice is not None and hasattr(voice, "set_reference_audio"):
                        match = next(
                            (v for v in _list_candidate_voices(None) if v["id"] == voice_id),
                            None,
                        )
                        if match is None:
                            log.warning("Unknown candidate voice id: %r", voice_id)
                        else:
                            try:
                                voice.set_reference_audio(match["file"])
                                hud_settings.save_active_voice(settings.HUD_SETTINGS_PATH, voice_id)
                                log.info("Switched active voice to %r", voice_id)
                            except VoiceError as exc:
                                log.exception("Could not switch to voice %r", voice_id)
                                try:
                                    await websocket.send(json.dumps({"type": "error", "message": str(exc)}))
                                except websockets.exceptions.ConnectionClosed:
                                    pass
                elif msg_type == "save_ai_settings":
                    # Keys arrive here and are written straight to the
                    # secrets file. They are never logged, and only the
                    # MASKED view is ever sent back - a saved key can be
                    # replaced from the UI but not read out of it.
                    try:
                        current = ai_settings.load(settings.AI_SETTINGS_PATH)
                        merged = ai_settings.apply_update(
                            current, data.get("settings") or {})
                        ai_settings.save(settings.AI_SETTINGS_PATH, merged)
                        _apply_provider(engine, merged)
                        _m = _apply_mode(engine, merged)
                        await websocket.send(json.dumps({
                            "type": "mode", "mode": _m.name,
                            "label": _m.label, "hud_fps": _m.hud_fps,
                            "wake_word": _m.wake_word,
                            "description": _m.description}))
                        log.info("AI settings saved (provider=%s, tts=%s, "
                                 "keys set: %s)",
                                 merged.get("chat_provider"),
                                 merged.get("tts_provider"),
                                 sorted(merged.get("keys", {}).keys()))
                        await websocket.send(json.dumps({
                            "type": "ai_settings",
                            "settings": ai_settings.public_view(merged)}))
                        await websocket.send(json.dumps({
                            "type": "model_info",
                            "model": getattr(engine.provider, "model", "?"),
                            "provider": getattr(engine, "provider_label",
                                                "Ollama / Local")}))
                    except Exception:
                        log.exception("Could not save AI settings")
                elif msg_type == "set_model":
                    # Great Sage's identity does not change with the model
                    # (spec S6) - only the brain underneath does, so this
                    # swaps the provider's model and leaves history, memory,
                    # persona and tools exactly as they were.
                    want = str(data.get("model") or "").strip()
                    if want and want in _installed_models():
                        engine.provider.model = want
                        log.info("Model switched to %s", want)
                        await websocket.send(json.dumps({
                            "type": "model_info", "model": want,
                            "provider": "Ollama / Local",
                            "available": _installed_models()}))
                    else:
                        log.warning("Refused model switch to %r", want)
                elif msg_type == "suggest_title":
                    def _title(payload=data):
                        t = _suggest_title(engine.provider,
                                           payload.get("messages"))
                        if not t:
                            return
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps(
                                {"type": "chat_title", "id": payload.get("id"),
                                 "title": t})), loop)
                    threading.Thread(target=_title, daemon=True).start()
                elif msg_type in ("summarize_chat", "remember_chat"):
                    # Both need the model, so they run off the event loop
                    # for the same reason _handle_chat does.
                    def _run(kind=msg_type, payload=data):
                        title = str(payload.get("title") or "this chat")
                        msgs = payload.get("messages")
                        try:
                            if kind == "summarize_chat":
                                out = _summarise_chat(engine.provider, title, msgs)
                                note = out or "Nothing to summarise yet."
                            else:
                                out = _chat_takeaway(engine.provider, title, msgs)
                                if out:
                                    memory.save_facts(
                                        settings.MEMORY_FILE_PATH,
                                        [l.strip() for l in out.splitlines() if l.strip()],
                                        settings.MEMORY_MAX_FACTS)
                                    note = "Remembered:\n" + out
                                else:
                                    note = "Nothing here was worth keeping."
                        except Exception as exc:
                            log.exception("%s failed", kind)
                            note = f"Could not complete that: {exc}"
                        payload_out = json.dumps({
                            "type": "chat_action_result", "action": kind,
                            "id": payload.get("id"), "text": note})
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(payload_out), loop)
                    threading.Thread(target=_run, daemon=True).start()
                elif msg_type in ("list_memory", "delete_memory",
                                  "clear_memory"):
                    # The memory manager (spec S46). Deletion rewrites the
                    # file rather than appending, so it goes through
                    # memory.write_facts, and the client is always sent the
                    # resulting list rather than trusting its own copy.
                    try:
                        facts = memory.load_memory(settings.MEMORY_FILE_PATH)
                        if msg_type == "delete_memory":
                            target = str(data.get("fact", ""))
                            facts = [f for f in facts if f != target]
                            memory.write_facts(settings.MEMORY_FILE_PATH, facts)
                            log.info("Memory: deleted 1 fact, %d remain",
                                     len(facts))
                        elif msg_type == "clear_memory":
                            memory.write_facts(settings.MEMORY_FILE_PATH, [])
                            facts = []
                            log.info("Memory: cleared")
                        await websocket.send(json.dumps(
                            {"type": "memory", "facts": facts}))
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    except Exception:
                        log.exception("%s failed", msg_type)
                elif msg_type == "save_chats":
                    # Whole-list save; see core/chat_store.py for why.
                    try:
                        n = chat_store.save(settings.CHAT_STORE_PATH,
                                            data.get("chats"))
                        log.debug("Saved %d chat(s)", n)
                    except Exception:
                        log.exception("Could not save chats")
                elif msg_type == "save_settings":
                    blob = data.get("settings")
                    if isinstance(blob, dict):
                        hud_settings.save_hud_settings(settings.HUD_SETTINGS_PATH, blob)
                elif msg_type == "chat":
                    active_connection["websocket"] = websocket
                    active_connection["sink"] = sink
                    if voice is not None:
                        voice.set_sink(sink)
                        _apply_voice_provider(voice)
                    # Attached images ride with this turn only. The
                    # vision model is the same qwen3.5:4b already loaded,
                    # so this costs no extra VRAM - verified by handing
                    # it a screenshot, which it read correctly.
                    imgs = data.get("images")
                    if imgs and hasattr(engine, "attach_images"):
                        engine.attach_images(imgs)
                        log.info("Chat turn carries %d image(s)", len(imgs))
                    _start_chat_thread(data.get("text", ""), engine,
                                       voice, sink, websocket, loop,
                                       think=bool(data.get("think")))
                elif msg_type == "audio_ended":
                    sink.notify_audio_ended()
                elif msg_type == "ptt_start":
                    active_connection["websocket"] = websocket
                    active_connection["sink"] = sink
                    ptt_recorder.start()
                elif msg_type == "ptt_stop":
                    # stop() blocks on local transcription (faster-whisper) -
                    # runs in its own thread so the event loop stays free,
                    # same reasoning as _handle_chat's own background thread.
                    threading.Thread(target=_log_exceptions(
                        ptt_recorder.stop, "push-to-talk transcription"),
                        daemon=True).start()
                elif msg_type == "set_wake_word_enabled":
                    enabled = bool(data.get("enabled"))
                    saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
                    saved["wake_word_enabled"] = enabled
                    hud_settings.save(settings.HUD_SETTINGS_PATH, saved)
                    if enabled:
                        wake_word_listener.start()
                        log.info("Wake-word listener enabled")
                    else:
                        wake_word_listener.stop()
                        log.info("Wake-word listener disabled")
                elif msg_type == "set_wake_words":
                    phrases = data.get("phrases")
                    if isinstance(phrases, list):
                        cleaned = [str(p).strip() for p in phrases if str(p).strip()]
                        saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
                        saved["wake_words"] = cleaned
                        hud_settings.save(settings.HUD_SETTINGS_PATH, saved)
                        log.info("Wake words updated: %r", cleaned)
                elif msg_type == "preview_voice_fx":
                    # Runs a candidate's preview clip through the REAL
                    # effect chain and ships the result back, so what you
                    # hear while dialling the sliders is exactly what the
                    # voice will sound like - not a browser-side
                    # approximation that would drift from the actual DSP.
                    #
                    # Sent as its own message type rather than the normal
                    # "audio" one: that path expects an "audio_ended" ack
                    # to unblock a waiting speak(), and a preview has no
                    # such waiter.
                    if voice is not None and hasattr(voice, "fx"):
                        clip = _preview_clip_path(data.get("id"), voice)
                        if clip:
                            try:
                                await websocket.send(json.dumps({
                                    "type": "fx_preview_audio",
                                    "mime": "audio/wav",
                                    "data": _render_fx_preview(clip, voice),
                                }))
                            except websockets.exceptions.ConnectionClosed:
                                pass
                            except Exception:
                                log.exception("Could not render the FX preview")
                elif msg_type == "set_voice_fx":
                    params = data.get("fx")
                    if isinstance(params, dict) and voice is not None and hasattr(voice, "set_fx"):
                        # Floats/bools straight off the HUD's sliders; the
                        # engine ignores any key it doesn't recognise.
                        voice.set_fx(**params)
                        saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
                        saved["voice_fx"] = params
                        hud_settings.save(settings.HUD_SETTINGS_PATH, saved)
                elif msg_type == "set_mic_device":
                    index = data.get("index")
                    ptt_recorder.device = index
                    wake_word_listener.device = index
                    # The listener only reads .device when it opens its
                    # InputStream, at the start of its (long-running) loop -
                    # restart it so a change takes effect immediately rather
                    # than only on the next manual toggle.
                    if wake_word_listener.running:
                        wake_word_listener.stop()
                        wake_word_listener.start()
                    saved = hud_settings.load(settings.HUD_SETTINGS_PATH)
                    saved["mic_device"] = index
                    hud_settings.save(settings.HUD_SETTINGS_PATH, saved)
                    log.info("Mic device set to %r", index)
        finally:
            log.info("Client disconnected")
            if is_log_subscriber:
                log_handler.remove_client(websocket)
            # Unblock anything still waiting on an ack from this connection
            # rather than leaving a background thread hung forever.
            sink.notify_audio_ended()

    # max_size: the websockets default is 1MB per frame, and an attached
    # image is one frame. A single desktop screenshot is ~0.3MB, so ONE
    # is fine and FOUR is not - the connection would close with a 1009
    # and the message would simply vanish, with the page reconnecting as
    # if nothing had been sent. Raised well clear of that; the real bound
    # on image size is applied in the page before sending.
    async with websockets.serve(handler, HOST, PORT, max_size=16 * 1024 * 1024):
        log.info("WebSocket bridge listening on ws://%s:%s", HOST, PORT)
        await asyncio.Future()  # run until the process exits
