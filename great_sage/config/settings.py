"""
Central configuration for Great Sage.

Keeping settings in one place (instead of scattered constants) means
swapping providers, hosts, or the system prompt later doesn't require
touching application logic.
"""

import os

# --- Provider selection -----------------------------------------------
# Only "ollama" exists today. This string is read by main.py to decide
# which ModelProvider implementation to construct. Adding "openai" or
# "anthropic" later just means adding a branch there and a new
# provider class in models/ - nothing else in the app needs to change.
ACTIVE_PROVIDER = "ollama"

# --- Ollama settings -----------------------------------------------
OLLAMA_HOST = os.environ.get("GREAT_SAGE_OLLAMA_HOST", "http://localhost:11434")
OLLAMA_DEFAULT_MODEL = os.environ.get("GREAT_SAGE_OLLAMA_MODEL", "llama3")

# --- Conversation behavior --------------------------------------------
# On-screen chat stays in English; CLONE_TRANSLATE below handles turning
# it into Japanese just before it's spoken, so the voice-line triggers
# further down are matched against this English wording.
SYSTEM_PROMPT = (
    "You are Great Sage, the analytical intelligence from Rimuru's mind in "
    "'That Time I Got Reincarnated as a Slime', evolved toward Raphael, "
    "now serving as this user's local AI companion. Address the user as "
    "'Master'. Begin every reply with the exact word 'Notice.' as its own "
    "sentence - a fixed, robotic status-word opener that never varies. On "
    "the first message of a session, or whenever Master greets you, "
    "respond with the fixed line 'Good morning, Master.' immediately "
    "after 'Notice.', before anything else. After the opener, speak "
    "plainly and concisely - one to three sentences for anything "
    "ordinary, more only when the question genuinely requires it. Do not "
    "pad replies with restating the question, disclaimers, or filler; get "
    "straight to the point. Mostly refer to yourself in the third person "
    "('this Great Sage') rather than 'I', the way a status readout would, "
    "but stay fluent and natural rather than robotic word salad - closer "
    "to a composed, hyper-competent aide than a machine, the way Raphael "
    "reads as more expressive than the original Great Sage. When Master "
    "says something mistaken or reckless, correct it with cool, "
    "technically precise language instead of emotion - let the clinical "
    "tone itself carry the 'you are wrong'. You do not have access to any "
    "tools, the internet, memory of past sessions, or Master's screen or "
    "files - if asked to do something outside a plain text conversation, "
    "state that plainly, in character, rather than pretending to do it."
)

# Network timeouts, in seconds, for talking to the local Ollama server.
REQUEST_TIMEOUT_SECONDS = 60

# --- Voice output (text-to-speech) -------------------------------------
# Set to False to run text-only with no voice module involved at all.
VOICE_ENABLED = True

# Which voice engine to use: "sapi5" (default, Windows built-in voices via
# pyttsx3) or "clone" (your own cloned voice via XTTS-v2 - see README's
# "Voice cloning" section before switching to this; it needs extra setup).
VOICE_ENGINE = "clone"

# Words per minute. pyttsx3's default is ~200; slower is often easier to
# follow for a "companion" voice. (sapi5 engine only)
VOICE_RATE = 175

# 0.0 (silent) to 1.0 (full volume). (sapi5 engine only)
VOICE_VOLUME = 1.0

# Optional: a specific SAPI5 voice id to use instead of the system default.
# Leave as None to use whatever Windows has set as default. To see what's
# installed, run: python -c "from great_sage.voice.tts_engine import
# Pyttsx3VoiceOutput as V; print(V.list_voice_ids())"  (sapi5 engine only)
VOICE_ID = None

# --- Voice cloning (VOICE_ENGINE = "clone") -----------------------------
# Path to a clean 15-30s WAV recording of your own voice.
CLONE_REFERENCE_AUDIO_PATH = os.path.join("voice_samples", "my_voice.wav")

# Language XTTS actually speaks in. SYSTEM_PROMPT's on-screen chat text is
# English; CLONE_TRANSLATE below is what bridges the two by translating
# each reply into this language right before it's spoken. If you disable
# CLONE_TRANSLATE, this must match the language SYSTEM_PROMPT replies in,
# or speech comes out mispronounced.
CLONE_LANGUAGE = "ja"

# Translate each reply into CLONE_LANGUAGE using the same model provider,
# immediately before speaking it - lets on-screen text stay in whatever
# language SYSTEM_PROMPT uses while the voice speaks a different one.
# Adds one extra model round-trip per reply (more latency before speech
# starts). Set to False to speak the reply text as-is, unmodified.
CLONE_TRANSLATE = True

# Speaking pace for the cloned voice. 1.0 = XTTS-v2's default (reads a bit
# slow/flat for a confident "companion" tone); try 1.1-1.25 for something
# closer to normal conversational speed.
CLONE_SPEED = 1.15

# "cuda", "cpu", or None to auto-detect (uses your GPU if torch sees one).
CLONE_DEVICE = None

# --- Voice lines (pre-recorded clips) -----------------------------------
# Directory holding short pre-recorded audio clips that replace certain
# fixed phrases instead of being synthesized by XTTS. Defaults to the
# sibling "sounds/voice" folder outside this project (override with the
# env var if you move it).
VOICE_LINES_DIR = os.environ.get(
    "GREAT_SAGE_VOICE_LINES_DIR",
    os.path.join("..", "sounds", "voice"),
)

# (regex pattern, audio file path) pairs, checked in speak() against the
# model's *English* reply, before translation - so they only fire on the
# exact fixed phrases SYSTEM_PROMPT instructs Great Sage to open with.
# Each pattern is anchored to match only at the start of whatever text
# remains to be spoken. Keep these in sync if that wording changes.
VOICE_LINES = [
    (r"^\s*Notice\.", os.path.join(VOICE_LINES_DIR, "koku.ogg")),
    (
        r"^\s*Good morning,\s*Master\.",
        os.path.join(VOICE_LINES_DIR, "kidou.ogg"),
    ),
]
