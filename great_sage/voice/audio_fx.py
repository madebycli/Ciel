"""
Post-processing effects for synthesized speech.

Sits between the TTS engine and the audio sink: the engine hands over a
plain float32 waveform, this shapes it, and the sink plays the result.
Nothing here is TTS-specific, so it works the same for any engine.

Built on Spotify's `pedalboard` - C++ DSP with Python bindings, fast
enough to be inaudible next to synthesis (a few ms against a synthesis
pass measured in seconds) and, notably, able to host real VST3 plugins as well as its own
built-ins. See load_vst3() at the bottom for that.

A flanger isn't a named effect in pedalboard; it IS a chorus with a very
short, deeply-modulated delay and feedback, which is what
_build_flanger() sets up.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


def _split(value: float):
    """Split a 0..2 intensity into (0..1 base, 0..1 excess).

    The HUD's sliders go to 200%, but every pedalboard parameter is
    strictly 0..1 - passing 1.4 would just be clamped, so the whole top
    half of the slider would do nothing. Instead the first 100% drives
    the effect's mix, and anything beyond it is routed into a SECOND
    parameter that genuinely intensifies that effect (a bigger room, more
    feedback), so the extra travel is audible rather than decorative.
    """
    value = max(0.0, float(value))
    return min(value, 1.0), max(0.0, min(value - 1.0, 1.0))


@dataclass
class FXSettings:
    """Everything the HUD's audio-FX panel can drive.

    Mixes/depths run 0..2 (the HUD shows them as 0-200%); see _split for
    what the range past 100% does. Anything at 0 is skipped entirely
    rather than run at zero strength, so an unused effect costs nothing.
    """

    enabled: bool = False

    reverb_mix: float = 0.0        # 0 = dry, 1 = drenched
    reverb_room: float = 0.55      # small room -> cathedral
    reverb_damping: float = 0.5    # high-frequency absorption

    flanger_mix: float = 0.0
    flanger_depth: float = 0.35    # how far the delay sweeps
    flanger_rate_hz: float = 0.4   # sweep speed
    flanger_feedback: float = 0.35  # the metallic "jet" character

    phaser_mix: float = 0.0
    phaser_rate_hz: float = 0.6

    delay_mix: float = 0.0
    delay_seconds: float = 0.22
    delay_feedback: float = 0.25

    pitch_semitones: float = 0.0   # +/- semitones, 0 = unchanged

    # --- Output level ---
    # Applied to the SPEECH only, unlike the HUD's master volume which
    # also governs sound effects. Deliberately applied even when the
    # effect chain is switched off - a volume control that stops working
    # because you disabled reverb would be baffling.
    voice_gain_db: float = 0.0     # -24..+12 dB

    # --- 4-band EQ ---
    # Two shelves at the ends and two peaks in between, the same layout a
    # simple channel EQ gives you. Any band left at 0 dB is skipped
    # entirely rather than built at unity, so an unused band costs
    # nothing.
    #
    # The 3 kHz peak is the one that matters for the harshness in a
    # cloned voice: sibilance and presence glare live around 2-5 kHz, so
    # a couple of dB of cut there is usually what takes the edge off
    # without dulling the whole voice.
    eq_low_hz: float = 120.0       # low shelf
    eq_low_db: float = 0.0
    eq_lowmid_hz: float = 500.0    # peak
    eq_lowmid_db: float = 0.0
    eq_lowmid_q: float = 1.0
    eq_highmid_hz: float = 3000.0  # peak - the harshness band
    eq_highmid_db: float = 0.0
    eq_highmid_q: float = 1.0
    eq_high_hz: float = 8000.0     # high shelf
    eq_high_db: float = 0.0

    # --- "Digital intelligence" character macro ---
    # One dial, 0..1.5, driving a whole chain at once. The brief for it
    # was a calm human voice that has been transformed into a digital
    # entity - artificial in harmonic structure and dynamics rather than
    # in any obvious robot effect. If a listener can point at it and say
    # "vocoder", it has gone too far.
    #
    # What the dial actually moves, and why each part is there:
    #   pitch      slight downward shift, which darkens the formants with
    #              it - that is most of the "neutralised" character
    #   resonance  a narrow peak near 2.8 kHz for a faint metallic ring
    #   comb       a very short delay; the comb filtering it creates is
    #              what reads as synthetic without sounding vocoded
    #   compress   hard, consistent dynamics - the flat, unwavering level
    #              is a bigger part of this voice than any effect
    #   drive      barely-there saturation for harmonic thickening
    #   reverb     a small, short, controlled space
    #   limiter    keeps the result level regardless of dial position
    #
    # TWO THINGS THIS CANNOT DO, and they are worth knowing:
    #   * True INDEPENDENT formant control. pedalboard has no formant
    #     shifter; PitchShift moves pitch and formants together. The
    #     downward shift approximates the described darkening, but it is
    #     not the same as holding pitch and moving formants alone.
    #   * A real vocoder or spectral layer. Nothing here does spectral
    #     resynthesis. The comb delay is a stand-in that lands in a
    #     similar perceptual place; it is not the same process.
    raphael_amount: float = 0.0

    # Absolute paths to VST3 plugins to run after the built-ins above.
    vst3_paths: List[str] = field(default_factory=list)


class VoiceFX:
    """Holds a compiled effect chain and applies it to audio buffers.

    The chain is rebuilt only when settings actually change - building it
    allocates DSP objects, and speech arrives in a stream of chunks that
    would otherwise rebuild it for every one.
    """

    def __init__(self, settings: Optional[FXSettings] = None):
        self._settings = settings or FXSettings()
        self._board = None
        self._board_sample_rate: Optional[int] = None
        self._dirty = True
        self._fresh = True

    def begin_utterance(self) -> None:
        """Marks the start of a new reply, so the next buffer resets the
        chain. Without this, a previous reply's reverb tail would bleed
        into the opening of the next one."""
        self._fresh = True

    @property
    def settings(self) -> FXSettings:
        return self._settings

    def update(self, **kwargs) -> None:
        """Apply a partial settings change from the HUD."""
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        self._dirty = True
        self._fresh = True  # a rebuilt board starts with no tail state

    def _build(self):
        from pedalboard import (
            Chorus, Compressor, Delay, Distortion, HighShelfFilter, Limiter,
            LowShelfFilter, Mix, Pedalboard, PeakFilter, Phaser, PitchShift,
            Reverb,
        )

        s = self._settings
        chain = []

        # The character macro's pitch is folded in with the user's own,
        # so the two do not stack into two separate shift stages - each
        # PitchShift pass costs quality as well as time.
        k = max(0.0, min(1.5, float(s.raphael_amount)))
        total_pitch = float(s.pitch_semitones) + (-2.0 * k)
        if total_pitch:
            chain.append(PitchShift(semitones=total_pitch))

        if k > 0:
            # Narrow peak for the faint metallic resonance. High Q on
            # purpose - a wide boost here just sounds harsh, which is the
            # opposite of the intent.
            chain.append(PeakFilter(cutoff_frequency_hz=2800.0,
                                    gain_db=3.0 * k, q=3.5))

        # EQ goes FIRST, before the modulation and space effects, so what
        # the reverb and delay pick up is the already-shaped tone. Cutting
        # harshness after a reverb only cleans the dry signal and leaves
        # the tail glaring.
        if s.eq_low_db:
            chain.append(LowShelfFilter(
                cutoff_frequency_hz=float(s.eq_low_hz),
                gain_db=float(s.eq_low_db), q=0.7))
        if s.eq_lowmid_db:
            chain.append(PeakFilter(
                cutoff_frequency_hz=float(s.eq_lowmid_hz),
                gain_db=float(s.eq_lowmid_db),
                q=max(0.1, float(s.eq_lowmid_q))))
        if s.eq_highmid_db:
            chain.append(PeakFilter(
                cutoff_frequency_hz=float(s.eq_highmid_hz),
                gain_db=float(s.eq_highmid_db),
                q=max(0.1, float(s.eq_highmid_q))))
        if s.eq_high_db:
            chain.append(HighShelfFilter(
                cutoff_frequency_hz=float(s.eq_high_hz),
                gain_db=float(s.eq_high_db), q=0.7))

        if s.flanger_mix > 0:
            # Flanger = chorus with a very short delay and feedback. The
            # short delay is what produces comb filtering (the "whoosh")
            # rather than the thicker detune a longer chorus delay gives.
            mix, extra = _split(s.flanger_mix)
            chain.append(
                Chorus(
                    rate_hz=float(s.flanger_rate_hz),
                    # Past 100%, deepen the sweep - a wider sweep is what
                    # makes a flanger more pronounced.
                    depth=min(1.0, float(s.flanger_depth) + extra * 0.6),
                    centre_delay_ms=2.5,
                    feedback=min(0.95, float(s.flanger_feedback) + extra * 0.5),
                    mix=mix,
                )
            )

        if s.phaser_mix > 0:
            mix, extra = _split(s.phaser_mix)
            chain.append(
                Phaser(
                    rate_hz=float(s.phaser_rate_hz),
                    depth=min(1.0, 0.5 + extra * 0.5),
                    feedback=min(0.9, 0.0 + extra * 0.7),
                    mix=mix,
                )
            )

        if s.delay_mix > 0:
            mix, extra = _split(s.delay_mix)
            chain.append(
                Delay(
                    delay_seconds=float(s.delay_seconds),
                    feedback=min(0.9, float(s.delay_feedback) + extra * 0.5),
                    mix=mix,
                )
            )

        if s.reverb_mix > 0:
            mix, extra = _split(s.reverb_mix)
            chain.append(
                Reverb(
                    # reverb_room IS the tail length: a bigger room rings
                    # for longer. Past 100% mix, push it further so the
                    # extra travel lengthens the tail as well as wetting it.
                    room_size=min(1.0, float(s.reverb_room) + extra * 0.4),
                    damping=float(s.reverb_damping),
                    wet_level=mix,
                    dry_level=float(max(0.0, 1.0 - mix * 0.5)),
                )
            )

        if k > 0:
            # Comb layer: a very short delay whose reflections interfere
            # with the dry signal. That interference is the synthetic
            # colour - a stand-in for the spectral layer described in the
            # brief, since nothing here can do real resynthesis.
            chain.append(Delay(delay_seconds=0.011,
                               feedback=0.25 * k,
                               mix=0.16 * k))
            # Hard, consistent dynamics. This does more for the
            # "artificial intelligence" read than any effect: a human
            # voice breathes in level, and this one deliberately does not.
            # Threshold and ratio both ramp from "barely touching it" so
            # the dial is progressive. An earlier tuning started at
            # -18 dB / 1.9:1, which already squashed a 10 dB spread down
            # to 2.4 dB at a quarter turn - everything above that did
            # nothing, making the control effectively a switch.
            chain.append(Compressor(threshold_db=-4.0 - 16.0 * k,
                                    ratio=1.0 + 3.2 * k,
                                    attack_ms=5.0,
                                    release_ms=120.0))
            # Saturation kept genuinely subtle - drive past a few dB
            # stops sounding like harmonic thickening and starts sounding
            # like distortion.
            chain.append(Distortion(drive_db=3.0 * k))
            # Small, short, controlled space.
            chain.append(Reverb(room_size=0.18, damping=0.6,
                                wet_level=0.09 * k,
                                dry_level=1.0 - 0.05 * k,
                                width=0.5))
            # Final catch, so moving the dial does not also change how
            # loud the voice is. Eases in with the dial rather than
            # snapping to a hard ceiling the moment it leaves zero.
            chain.append(Limiter(threshold_db=-1.0 - 2.0 * (1.0 - k),
                                 release_ms=100.0))

        for path in s.vst3_paths:
            plugin = load_vst3(path)
            if plugin is not None:
                chain.append(plugin)

        self._board = Pedalboard(chain)
        self._dirty = False

    def apply(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Run the chain over one buffer. Returns the input untouched if
        FX are off or the chain is empty, so this is safe to call
        unconditionally on every chunk."""
        s = self._settings

        # Speech level first, and unconditionally - see voice_gain_db.
        # A plain scalar multiply rather than a pedalboard Gain stage, so
        # it costs nothing and does not depend on the chain existing.
        if s.voice_gain_db:
            samples = np.asarray(samples, dtype=np.float32) * (
                10.0 ** (float(s.voice_gain_db) / 20.0))

        if not s.enabled:
            return samples
        if self._dirty or self._board is None:
            try:
                self._build()
            except Exception:
                # Bad plugin path or parameter - keep speaking dry rather
                # than losing the voice entirely over a cosmetic effect.
                self._settings.enabled = False
                return samples
        if not len(self._board):
            return samples

        audio = np.asarray(samples, dtype=np.float32)
        mono_in = audio.ndim == 1
        if mono_in:
            audio = audio.reshape(1, -1)  # pedalboard wants (channels, frames)
        try:
            # reset only on the FIRST buffer of an utterance. Resetting
            # every buffer truncates each effect's tail at the chunk
            # boundary and restarts the chain cold on the next one, which
            # is audible as the speech being clipped and then stuttering.
            # Harmless when a reply was one buffer; very audible once
            # replies are streamed sentence by sentence. reset=False
            # carries reverb/delay/flanger state across, so a tail
            # started in one chunk rings out over the next.
            out = self._board(audio, float(sample_rate), reset=self._fresh)
            self._fresh = False
        except Exception:
            return samples
        out = out[0] if mono_in else out
        # Effects with feedback (delay, flanger, reverb tails) can push
        # peaks past full scale, which clips harshly on playback.
        peak = float(np.max(np.abs(out))) if out.size else 0.0
        if peak > 1.0:
            out = out / peak
        return out.astype(np.float32)


def load_vst3(path: str):
    """Load a VST3 plugin, or return None if it can't be loaded.

    Kept deliberately forgiving: a missing or incompatible plugin should
    drop out of the chain, not take the voice down with it.
    """
    try:
        from pedalboard import VST3Plugin

        return VST3Plugin(path)
    except Exception:
        return None
