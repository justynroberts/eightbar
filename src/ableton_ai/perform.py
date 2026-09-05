"""Turn correct notes into played notes.

The generators decide *what* to play and get that right: the harmony is sound,
the lines follow the chords, the rhythms suit the style. What they produced was
still obviously machine-made, and measurement said exactly why -- flat velocity,
identical note lengths, every top line in one octave hitting the same sixteenths.

Those are performance faults, not composition faults, so they are fixed in one
place and applied to everything rather than patched into each generator.

Four things happen here:

  accents      strong beats are louder, and the curve depends on the part
  phrasing     a line rises into the end of a phrase and falls away after it
  articulation note length follows the accent, so a part breathes
  register     each role gets a band, so two lines are never in one octave

None of it changes a pitch or a rhythm. It changes how they are played.
"""

from __future__ import annotations

import random
from typing import Any, Iterable, Sequence

BEATS_PER_BAR = 4.0
Note = dict[str, Any]

# Velocity offsets across the sixteen sixteenths of a bar. The shape is the
# point: where the weight falls is what makes a part feel like a genre rather
# than a grid.
ACCENT_CURVES: dict[str, tuple[int, ...]] = {
    # Downbeat heavy, backbeat answered. The default for anything rhythmic.
    "straight": (10, -8, -3, -6, 4, -8, -2, -6, 7, -8, -3, -6, 3, -8, -2, -5),
    # Offbeat weight: house and garage sit on the "and".
    "offbeat":  (2, -9, 8, -7, 0, -9, 6, -7, 1, -9, 7, -7, -1, -9, 5, -6),
    # Even sixteenths with a gentle swell: arps and plucks.
    "rolling":  (6, -4, 2, -5, 3, -4, 1, -5, 5, -4, 2, -5, 2, -4, 0, -6),
    # Long notes: the accent is the entrance, then it settles.
    "sustain":  (5, 0, 0, 0, -2, 0, 0, 0, 2, 0, 0, 0, -3, 0, 0, 0),
    # Melodic writing: weight on 1 and 3, light everywhere else, so the tune
    # reads as phrased rather than hammered.
    "melodic":  (7, -5, -2, -4, 3, -5, -1, -4, 5, -5, -2, -4, 1, -5, -3, -3),
}

ROLE_CURVES: dict[str, str] = {
    "kick": "straight", "drums": "straight", "perc": "offbeat",
    "snare": "straight", "hat": "offbeat",
    "bass": "straight", "sub": "sustain", "808": "sustain",
    "chords": "offbeat", "pad": "sustain", "pulse": "offbeat",
    "strings": "sustain",
    "choir": "sustain", "organ": "sustain", "atmos": "sustain",
    "lead": "melodic", "melody": "melodic", "hook": "melodic",
    "piano": "melodic", "guitar": "melodic", "woodwind": "melodic",
    "brass": "melodic", "vocal": "melodic",
    "arp": "rolling", "mallet": "rolling", "harp": "rolling",
    "riser": "rolling", "fx": "rolling",
}

# How far a part is allowed to swing dynamically, as a velocity multiplier on
# the accent curve. A pad that jumps 20 velocity is distracting; a drum part
# that does not is lifeless.
ROLE_INTENSITY: dict[str, float] = {
    "kick": 0.7, "drums": 1.2, "perc": 1.3, "hat": 1.4, "snare": 1.0,
    "bass": 0.9, "sub": 0.4, "chords": 0.8, "pad": 0.35, "pulse": 0.7,
    "strings": 0.5,
    "choir": 0.4, "lead": 1.0, "melody": 1.1, "hook": 1.0, "arp": 0.9,
    "piano": 1.2, "guitar": 1.1, "mallet": 1.0, "harp": 0.9, "organ": 0.5,
}

# Where each role lives, as MIDI note numbers. Two top lines in one octave was
# the single most common ensemble fault: lead, melody, hook and arp were all
# written around C5 and fought each other for the same air.
REGISTER_BANDS: dict[str, tuple[int, int]] = {
    # The bass lives an octave above the sub. Octave 1 put trance bass
    # fundamentals at 27-55Hz: crowding the kick, invisible on small
    # speakers, and measured at 36% of the whole mix's energy -- "truly
    # awful" begins down here, not in the melody.
    "sub": (24, 43), "808": (24, 43), "bass": (33, 57),
    "guitar": (45, 76), "piano": (48, 84), "organ": (48, 79),
    # The four harmonic layers are deliberately pulled into different octaves
    # so they do not cluster into one muddy band: pad low and wide, chords the
    # mid comp, pulse above it, strings high.
    "chords": (52, 72), "pad": (40, 62), "pulse": (60, 79),
    "strings": (64, 88),
    "choir": (55, 79), "harp": (55, 91), "mallet": (72, 96),
    # The four top-line roles get bands whose *centres* are spaced apart so
    # two lines never sit in the same octave, but kept in MUSICAL range --
    # the earlier bands pushed the lead to E6 and the hook to D7, an octave
    # above where a trance lead actually sits, which reads as shrill. These
    # centres (melody C5, arp G5, lead C6, hook G6) span the singable top
    # without climbing into the painful register.
    "melody": (60, 74),     # centre 67, the singing register (C4-D5)
    "arp":    (67, 86),     # centre 76, sparkling above the chords (G4-D6)
    "lead":   (74, 90),     # centre 82, the trance lead register (D5-F#6)
    "hook":   (81, 98),     # centre 89, over the top so it cuts (A5-D7)
    "brass": (52, 79), "woodwind": (60, 88), "vocal": (55, 79),
}


# Where each role sits against the grid, in beats. A real rhythm section is
# not aligned: the bass and snare sit fractionally behind the kick (the
# pocket), hats push fractionally ahead (the drive), pads breathe late. At
# 125bpm, 0.02 beats is about 10ms -- felt, not heard as an offset.
ROLE_POCKET: dict[str, float] = {
    "bass": 0.018, "sub": 0.02, "808": 0.02,
    "snare": 0.012, "clap": 0.012,
    "hat": -0.012, "perc": -0.008,
    "chords": 0.01, "pad": 0.025, "pulse": 0.004, "strings": 0.03, "choir": 0.03,
    "piano": 0.008, "guitar": 0.012,
    "kick": 0.0, "drums": 0.0,          # the anchors stay on the grid
    "lead": 0.006, "melody": 0.008, "hook": 0.0, "arp": -0.006,
}


def pocket(notes: Sequence[Note], role: str) -> list[Note]:
    """Sit the part where its role sits: behind the kick, or pushing it."""
    shift = ROLE_POCKET.get(role, 0.0)
    if not notes or shift == 0.0:
        return list(notes)
    out = []
    for note in notes:
        copy = dict(note)
        # The very first downbeat stays anchored -- everything is relative
        # to something, and bar one beat one is the something.
        if float(note["start"]) > 0:
            copy["start"] = round(max(0.0, float(note["start"]) + shift), 4)
        out.append(copy)
    return out


def _curve_for(role: str) -> tuple[int, ...]:
    return ACCENT_CURVES[ROLE_CURVES.get(role, "straight")]


def _clamp(value: float, low: int = 1, high: int = 127) -> int:
    return int(max(low, min(high, round(value))))


def accent(notes: Sequence[Note], role: str, intensity: float | None = None,
           seed: int | None = None) -> list[Note]:
    """Weight each note by where it falls in the bar.

    Flat velocity is not a quiet dynamic, it is the absence of one, and it is
    the loudest signal that nobody played this.
    """
    if not notes:
        return []
    curve = _curve_for(role)
    strength = ROLE_INTENSITY.get(role, 1.0) if intensity is None else intensity
    rng = random.Random(seed)

    out = []
    for note in notes:
        copy = dict(note)
        step = int(round((float(note["start"]) % BEATS_PER_BAR) * 4)) % 16
        # A little jitter, so repeated bars are not bit-identical. Small enough
        # that it reads as a player rather than as noise.
        wobble = rng.uniform(-2.0, 2.0)
        copy["velocity"] = _clamp(
            float(note.get("velocity", 100)) + curve[step] * strength + wobble
        )
        out.append(copy)
    return out


def phrase_dynamics(notes: Sequence[Note], bars: float, depth: float = 0.14,
                    phrase_bars: float = 4.0) -> list[Note]:
    """Rise into the end of each phrase and settle after it.

    A part with a correct accent curve still sounds flat over eight bars if
    every bar is equally loud. This is the arc on top of the accent.
    """
    if not notes or bars <= 0:
        return list(notes)

    out = []
    for note in notes:
        copy = dict(note)
        position = (float(note["start"]) % (phrase_bars * BEATS_PER_BAR)) / (
            phrase_bars * BEATS_PER_BAR
        )
        # Rise across the phrase, then drop back at the very end: the last
        # eighth of a phrase is the breath before the next one.
        shape = position if position < 0.875 else (1.0 - position) * 4.0
        copy["velocity"] = _clamp(
            float(copy.get("velocity", 100)) * (1.0 + depth * (shape - 0.5) * 2)
        )
        out.append(copy)
    return out


def articulate(notes: Sequence[Note], role: str, spread: float = 0.25
               ) -> list[Note]:
    """Vary note length with the accent, so the part is not a row of blocks.

    Accented notes ring; the ones between are clipped. It is the same idea as
    velocity and just as audible, and every generator was emitting one duration
    for every note in the part.
    """
    if not notes:
        return []
    curve = _curve_for(role)
    sustained = role in ("pad", "strings", "choir", "organ", "sub", "atmos")

    out = []
    for note in notes:
        copy = dict(note)
        step = int(round((float(note["start"]) % BEATS_PER_BAR) * 4)) % 16
        # Curve runs about -9..+10; map it onto a length multiplier.
        lift = curve[step] / 10.0
        factor = 1.0 + spread * lift * (0.4 if sustained else 1.0)
        copy["duration"] = max(0.05, float(note["duration"]) * factor)
        out.append(copy)
    return out


def spread_voices(notes: Sequence[Note], amount: float = 0.12,
                  seed: int | None = None) -> list[Note]:
    """Let the voices of a chord differ in length, and enter fractionally apart.

    Every note of every chord being exactly the same length is why a generated
    pad sounds like an organ stop rather than like players. Real voices release
    at slightly different moments, and the top of a voicing usually rings on
    after the inner parts have gone.
    """
    if not notes:
        return []

    rng = random.Random(seed)
    by_onset: dict[float, list[Note]] = {}
    for note in notes:
        by_onset.setdefault(round(float(note["start"]), 3), []).append(note)

    out: list[Note] = []
    for _, voices in sorted(by_onset.items()):
        ordered = sorted(voices, key=lambda n: int(n["pitch"]))
        for index, note in enumerate(ordered):
            copy = dict(note)
            # The top voice rings longest, the inner voices least.
            from_top = (len(ordered) - 1 - index) / max(1, len(ordered) - 1)
            copy["duration"] = max(
                0.05,
                float(note["duration"]) * (1.0 + amount * (0.5 - from_top) * 2)
                + rng.uniform(-0.02, 0.02),
            )
            out.append(copy)
    return out


def breathe(notes: Sequence[Note], bars: float, keep: float = 0.85,
            phrase_bars: float = 4.0, seed: int | None = None) -> list[Note]:
    """Take notes out at the end of phrases so a dense line has punctuation.

    A lead that fills all 128 sixteenths of eight bars has no phrases in it --
    there is nowhere for one to end. Removing the weakest notes just before each
    phrase boundary puts the breath back without touching the shape.
    """
    if not notes or keep >= 1.0:
        return list(notes)

    rng = random.Random(seed)
    window = phrase_bars * BEATS_PER_BAR
    out = []
    for note in notes:
        into_phrase = float(note["start"]) % window
        # Weight removal towards the end of the phrase, but do not confine it
        # there: a line that only rests in its last bar still has no
        # punctuation in the first three.
        lateness = into_phrase / window
        step = int(round(float(note["start"]) * 4)) % 4
        weak = step in (1, 3)          # the off-sixteenths
        if weak and rng.random() > keep * (1.0 - 0.55 * lateness):
            continue
        out.append(dict(note))
    return out


def fit_register(notes: Sequence[Note], role: str) -> list[Note]:
    """Move a part, by octaves, into the register its role belongs in.

    Transposing by octaves only: the notes and their relationships are the
    generator's decision, and this must not change them. It only decides where
    the part sits, which is what stops four top lines sharing one octave.
    """
    band = REGISTER_BANDS.get(role)
    if not notes or band is None:
        return list(notes)

    low, high = band
    pitches = [int(n["pitch"]) for n in notes]
    centre = sum(pitches) / len(pitches)
    target = (low + high) / 2.0

    # Pick the octave shift that lands the part's centre nearest its band's,
    # rather than nudging while outside a tolerance. Stepping by twelve towards
    # a tolerance narrower than twelve never settles -- it oscillates past the
    # target and back.
    def cost(shift: int) -> tuple[float, int]:
        low_edge = min(pitches) + shift * 12
        high_edge = max(pitches) + shift * 12
        # How far outside the band the part would stick out, then how far its
        # centre is from the band's centre.
        spill = max(0, low - low_edge) + max(0, high_edge - high)
        return (spill * 2 + abs(centre + shift * 12 - target), abs(shift))

    shift = min(range(-3, 4), key=cost)
    if shift == 0:
        return list(notes)

    return [{**n, "pitch": int(n["pitch"]) + shift * 12} for n in notes]


def fold_into_band(notes: Sequence[Note], role: str) -> list[Note]:
    """Fold stray notes back inside the role's register, by octaves.

    Choosing the best octave for a part is not enough when the part is wider
    than its band on one side: a melody generated across 69-81 sits two
    semitones into the lead's territory however it is transposed. Folding the
    offenders keeps every pitch class, so the harmony is untouched -- only the
    octave a few notes are played in changes.

    Skipped when the part is wider than its band, because folding would then
    destroy the contour rather than tidy it: an arpeggio climbing two octaves
    is meant to.
    """
    band = REGISTER_BANDS.get(role)
    if not notes or band is None:
        return list(notes)

    low, high = band
    pitches = [int(n["pitch"]) for n in notes]
    if max(pitches) - min(pitches) > high - low:
        return list(notes)

    out = []
    for note in notes:
        pitch = int(note["pitch"])
        while pitch > high:
            pitch -= 12
        while pitch < low:
            pitch += 12
        out.append({**note, "pitch": pitch})
    return out


def perform(
    notes: Iterable[Note],
    role: str,
    bars: float = 8.0,
    *,
    seed: int | None = None,
    register: bool = True,
    space: bool = True,
) -> list[Note]:
    """Everything above, in the order that matters.

    Register first, because it changes pitches; then space, because removing a
    note should not disturb the dynamics of the ones that stay; then accent,
    phrasing and articulation, which each build on the last.
    """
    played = [dict(n) for n in notes]
    if not played:
        return played

    if register:
        played = fit_register(played, role)
        played = fold_into_band(played, role)

    if space and role not in ("pad", "strings", "choir", "organ", "sub",
                              "kick", "drums", "perc", "hat", "snare"):
        # Only thin what is dense enough to need it.
        slots = max(1, int(round(bars * 16)))
        onsets = {round(float(n["start"]) * 4) for n in played}
        density = len(onsets) / slots
        if density > 0.75:
            # Aim for roughly two thirds occupancy: dense enough to drive,
            # sparse enough to have phrases in it. Removing a quarter of the
            # weak notes near phrase ends was not nearly enough for a line
            # that filled all 128 sixteenths of eight bars.
            played = breathe(played, bars, keep=0.45, seed=seed)
            played = breathe(played, bars, keep=0.55, phrase_bars=2.0,
                             seed=None if seed is None else seed + 1)

    played = accent(played, role, seed=seed)
    played = phrase_dynamics(played, bars)
    played = articulate(played, role)
    if role in ("chords", "pad", "strings", "choir", "organ", "piano", "guitar"):
        played = spread_voices(played, seed=seed)
    played = pocket(played, role)
    return played
