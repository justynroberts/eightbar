"""Bass articulation: how a bassline behaves, not just which notes it uses.

Two basslines on the same chords can be unrecognisable as relatives. What
separates them is rarely the pitches -- it is where the notes sit against the
kick, how long they ring, whether they climb to the octave, and whether they
bother with the root at all.

  rolling    Sixteenths in the gaps between kicks, short and relentless. The
             engine of tech house and techno.
  driving    Straight eighths, medium length. Progressive house.
  offbeat    Only the "and" of each beat. Classic house; the kick owns the
             downbeat and the bass answers it.
  drag       Behind the beat and legato. Deep house -- the laziness is the
             point, and quantising it kills the track.
  octave     Root and octave alternating. Trance and hands-up.
  walking    Steps through chord tones and passing notes toward the next root,
             so the line leads somewhere instead of restating the chord.
  syncopated A 3-3-2 sixteenth grouping, which pulls against the four.
  stab       Short and sparse; leaves room for everything else.
  sustained  One long note per chord. Reese and dubstep territory.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import groove as groove_mod
from .theory import Chord

SIXTEENTH = 0.25
BEATS_PER_BAR = 4.0

Note = dict[str, float | int | bool]


@dataclass(frozen=True)
class BassStyle:
    """How one bassline behaves."""

    name: str
    pattern: str              # one bar of sixteenths
    gate: float               # note length as a share of the step
    note_choice: str          # root | octave | fifth | chord | walk
    kick_mode: str            # avoid | shorten | lock | ignore
    groove: str
    velocity: int
    accent_first: int         # extra velocity on the downbeat
    description: str


STYLES: dict[str, BassStyle] = {
    "rolling": BassStyle(
        "rolling", "x.xxx.xxx.xxx.xx", 0.55, "root", "avoid", "tech_house",
        104, 10,
        "Sixteenths threaded between the kicks. The gaps are the groove.",
    ),
    "driving": BassStyle(
        "driving", "x.x.x.x.x.x.x.x.", 0.7, "root", "shorten", "house",
        100, 8,
        "Straight eighths, medium length. Pushes without crowding.",
    ),
    "offbeat": BassStyle(
        "offbeat", "..x...x...x...x.", 0.8, "root", "ignore", "house",
        102, 6,
        "Only the offbeats. The kick owns the downbeat; the bass answers.",
    ),
    "drag": BassStyle(
        "drag", "x..x..x...x.....", 0.95, "chord", "shorten", "laid_back",
        92, 4,
        "Behind the beat and legato. Quantising this kills it.",
    ),
    "octave": BassStyle(
        "octave", "x.x.x.x.x.x.x.x.", 0.6, "octave", "shorten", "pushed",
        106, 10,
        "Root and octave alternating -- trance and hands-up.",
    ),
    "walking": BassStyle(
        "walking", "x...x...x...x...", 0.9, "walk", "ignore", "straight",
        96, 6,
        "Steps through chord tones toward the next root, so it leads somewhere.",
    ),
    "syncopated": BassStyle(
        "syncopated", "x..x..x.x..x..x.", 0.6, "chord", "avoid", "mpc_swing",
        100, 8,
        "A 3-3-2 grouping pulling against the four.",
    ),
    "stab": BassStyle(
        "stab", "x.......x.......", 0.4, "root", "ignore", "straight",
        108, 6,
        "Short and sparse. Leaves room for everything else.",
    ),
    "sustained": BassStyle(
        "sustained", "x...............", 1.0, "root", "ignore", "straight",
        96, 0,
        "One long note per chord. Reese and dubstep territory.",
    ),
}

ALIASES = {
    "roll": "rolling", "tech": "rolling", "techno": "rolling",
    "laid_back": "drag", "deep": "drag", "lazy": "drag",
    "trance": "octave", "hands_up": "octave",
    "eighths": "driving", "progressive": "driving",
    "house": "offbeat", "classic": "offbeat",
    "reese": "sustained", "long": "sustained",
    "walk": "walking", "jazz": "walking",
    "sync": "syncopated", "garage": "syncopated",
}


def resolve(name: str) -> BassStyle:
    key = (name or "rolling").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in STYLES:
        raise ValueError(
            f"unknown bass style {name!r}; try one of: "
            f"{', '.join(sorted(set(STYLES) | set(ALIASES)))}"
        )
    return STYLES[key]


def _steps(pattern: str) -> list[int]:
    return [i for i, ch in enumerate(pattern) if ch not in "._ "]


def _in_register(pitch: int, octave: int) -> int:
    """Fold a pitch into the bass register for the given octave."""
    low = (octave + 2) * 12
    while pitch >= low + 12:
        pitch -= 12
    while pitch < low:
        pitch += 12
    return pitch


def _choose_pitch(
    chord: Chord,
    next_chord: Chord | None,
    position: int,
    total: int,
    style: BassStyle,
    octave: int,
    rng: random.Random,
) -> int:
    """Pick the note for this step.

    A bassline that only ever plays the root is the giveaway of generated
    music. Real ones move between chord tones, jump the octave, and approach
    the next chord from a step away.
    """
    root = _in_register(chord.root_pitch, octave)
    tones = sorted({_in_register(p, octave) for p in chord.pitches})

    if style.note_choice == "root":
        # Even a root-based line lifts to the octave occasionally.
        if position and position % 8 == 7 and rng.random() < 0.35:
            return root + 12
        return root

    if style.note_choice == "octave":
        return root + 12 if position % 2 else root

    if style.note_choice == "fifth":
        return root + 7 if position % 2 else root

    if style.note_choice == "chord":
        # Land on the root on strong beats, other chord tones between them.
        if position % 4 == 0:
            return root
        return rng.choice(tones or [root])

    if style.note_choice == "walk":
        if position == 0:
            return root
        if next_chord is not None and position == total - 1:
            # Approach the next root by a semitone -- the walking-bass move.
            target = _in_register(next_chord.root_pitch, octave)
            return target - 1 if target > root else target + 1
        return tones[min(position, len(tones) - 1)] if tones else root

    return root


def generate(
    chords: list[Chord],
    style: str = "rolling",
    bars_per_chord: float = 1.0,
    octave: int = 1,
    against_drums: list[Note] | None = None,
    humanise: float = 0.2,
    seed: int | None = None,
    velocity: int | None = None,
) -> list[Note]:
    """Build a bassline in a named articulation style."""
    spec = resolve(style)
    rng = random.Random(seed)
    steps = _steps(spec.pattern)
    base_velocity = velocity if velocity is not None else spec.velocity

    notes: list[Note] = []
    for index, chord in enumerate(chords):
        next_chord = chords[index + 1] if index + 1 < len(chords) else chords[0]
        chord_start = index * bars_per_chord * BEATS_PER_BAR

        for bar in range(max(1, int(bars_per_chord))):
            bar_start = chord_start + bar * BEATS_PER_BAR
            for position, step in enumerate(steps):
                pitch = _choose_pitch(
                    chord, next_chord, position, len(steps), spec, octave, rng
                )
                # A note lasts until the next onset, scaled by the gate.
                span = (
                    (steps[position + 1] - step) * SIXTEENTH
                    if position + 1 < len(steps)
                    else BEATS_PER_BAR - step * SIXTEENTH
                )
                notes.append({
                    "pitch": max(0, min(127, pitch)),
                    "start": bar_start + step * SIXTEENTH,
                    "duration": max(0.04, span * spec.gate),
                    "velocity": max(
                        1, min(127, base_velocity + (spec.accent_first if step == 0 else 0))
                    ),
                })

    if against_drums and spec.kick_mode != "ignore":
        onsets = groove_mod.kick_onsets(against_drums)
        notes = groove_mod.duck_against(notes, onsets, mode=spec.kick_mode)

    if spec.groove != "straight":
        notes = groove_mod.apply(notes, spec.groove, seed=seed, rigid=())

    if humanise:
        for note in notes:
            note["velocity"] = int(max(1, min(127,
                float(note["velocity"]) + rng.uniform(-1, 1) * humanise * 12)))

    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def describe() -> dict:
    return {
        name: {"description": s.description, "pattern": s.pattern,
               "note_choice": s.note_choice, "kick": s.kick_mode}
        for name, s in STYLES.items()
    }
