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
from great_sage.core import guardrails, hud_settings, personality, voice_line_prefs
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


def _handle_chat(text, engine, voice, sink, websocket, loop) -> None:
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
            speaker = threading.Thread(target=speak_worker, daemon=True)
            speaker.start()
        reply_chunks = []
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


def _start_chat_thread(text, engine, voice, sink, websocket, loop) -> None:
    """Shared by the "chat" message handler and both voice-input paths
    (push-to-talk, wake-word) below - same background-thread dispatch
    either way, so a voice-originated message goes through the exact same
    reply/speech pipeline a typed one does."""
    threading.Thread(
        target=_handle_chat,
        args=(text, engine, voice, sink, websocket, loop),
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

    ptt_recorder = PushToTalkRecorder(on_result=_route_voice_text, on_level=_send_mic_level)

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
                elif msg_type == "save_settings":
                    blob = data.get("settings")
                    if isinstance(blob, dict):
                        hud_settings.save_hud_settings(settings.HUD_SETTINGS_PATH, blob)
                elif msg_type == "chat":
                    active_connection["websocket"] = websocket
                    active_connection["sink"] = sink
                    if voice is not None:
                        voice.set_sink(sink)
                    _start_chat_thread(data.get("text", ""), engine, voice, sink, websocket, loop)
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

    async with websockets.serve(handler, HOST, PORT):
        log.info("WebSocket bridge listening on ws://%s:%s", HOST, PORT)
        await asyncio.Future()  # run until the process exits
