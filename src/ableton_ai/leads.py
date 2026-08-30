"""Leads: the sixteenth-note lines that carry a trance or progressive record.

A trance lead is not a melody in the classical sense. It is a continuous
sixteenth-note stream that stays inside the chord, climbs across the phrase,
and reaches its highest note exactly where the drop lands. The tension comes
from the *arc* over eight or sixteen bars, not from the notes in any one bar --
which is why a lead written a bar at a time never soars.

  soaring     Continuous sixteenths rising across the whole phrase, peaking at
              the end. The classic uplifting trance lead.
  pluck       Gated sixteenths with gaps; the same motion, less legato.
  rolling     Repeated notes with occasional chord-tone moves. Hypnotic.
  arp_climb   Arpeggio that shifts up an octave each phrase.
  call        A phrase and its answer, sitting in the gaps of the drums.
  stab        Sparse, rhythmic, chord-tone hits rather than a line.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import groove as groove_mod, theory
from .theory import Chord

SIXTEENTH = 0.25
BEATS_PER_BAR = 4.0

# Above roughly G7 a lead stops soaring and starts whistling. Real supersaw
# leads live between C4 and C7; this is the ceiling the pool is clamped to.
MAX_LEAD_PITCH = 103

Note = dict[str, float | int | bool]


@dataclass(frozen=True)
class LeadStyle:
    name: str
    rate: float          # note spacing in beats
    gate: float          # length as a share of the spacing
    contour: str         # climb | arch | flat | wave
    octave_span: int     # how far it travels across the phrase
    repeat_bias: float   # 0 = always move, 1 = mostly repeat the same note
    rest_share: float    # share of steps left silent
    velocity: int
    description: str


STYLES: dict[str, LeadStyle] = {
    "soaring": LeadStyle(
        "soaring", SIXTEENTH, 0.92, "climb", 2, 0.25, 0.0, 100,
        "Continuous sixteenths climbing across the phrase, peaking at the drop.",
    ),
    "pluck": LeadStyle(
        "pluck", SIXTEENTH, 0.45, "climb", 2, 0.3, 0.12, 98,
        "The same motion, gated short so it reads as a pluck rather than a pad.",
    ),
    "rolling": LeadStyle(
        "rolling", SIXTEENTH, 0.7, "flat", 1, 0.6, 0.05, 96,
        "Mostly one repeated note, moving occasionally. Hypnotic rather than melodic.",
    ),
    "arp_climb": LeadStyle(
        "arp_climb", SIXTEENTH, 0.8, "climb", 3, 0.0, 0.0, 98,
        "Straight arpeggio shifting up an octave each phrase.",
    ),
    "call": LeadStyle(
        "call", 0.5, 0.85, "arch", 1, 0.2, 0.35, 102,
        "A phrase and its answer, leaving gaps for the drums.",
    ),
    "stab": LeadStyle(
        "stab", 0.5, 0.35, "flat", 1, 0.4, 0.5, 106,
        "Sparse rhythmic hits on chord tones rather than a continuous line.",
    ),
}

ALIASES = {
    "trance": "soaring", "uplifting": "soaring", "supersaw": "soaring",
    "gate": "pluck", "gated": "pluck", "plucked": "pluck",
    "hypnotic": "rolling", "techno": "rolling",
    "arp": "arp_climb", "climb": "arp_climb",
    "response": "call", "answer": "call",
    "hits": "stab", "chord_stab": "stab",
}


def resolve(name: str) -> LeadStyle:
    key = (name or "soaring").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in STYLES:
        raise ValueError(
            f"unknown lead style {name!r}; try one of: "
            f"{', '.join(sorted(set(STYLES) | set(ALIASES)))}"
        )
    return STYLES[key]


def _contour_position(shape: str, progress: float) -> float:
    """Where in its range the line should sit, 0..1, at this point in the phrase."""
    if shape == "climb":
        return progress
    if shape == "arch":
        return 1.0 - abs(progress - 0.5) * 2
    if shape == "wave":
        import math
        return (math.sin(progress * math.pi * 2) + 1) / 2
    return 0.5


def generate(
    chords: list[Chord],
    root: str = "C",
    scale: str = "minor",
    style: str = "soaring",
    bars_per_chord: float = 1.0,
    octave: int = 5,
    seed: int | None = None,
    groove: str = "straight",
    velocity: int | None = None,
) -> list[Note]:
    """Build a lead line that arcs across the whole phrase.

    Notes are drawn from the sounding chord, so the line always agrees with the
    harmony, but the *register* is driven by position in the phrase rather than
    by the bar -- which is what makes it climb.
    """
    spec = resolve(style)
    rng = random.Random(seed)
    base_velocity = velocity if velocity is not None else spec.velocity

    total_beats = len(chords) * bars_per_chord * BEATS_PER_BAR
    step_count = max(1, int(round(total_beats / spec.rate)))
    notes: list[Note] = []
    previous: int | None = None
    highest = 0

    for step in range(step_count):
        at = step * spec.rate
        progress = at / total_beats if total_beats else 0.0

        if spec.rest_share and rng.random() < spec.rest_share:
            previous = None
            continue

        chord_index = min(
            len(chords) - 1, int(at / (bars_per_chord * BEATS_PER_BAR))
        )
        chord = chords[chord_index]

        # The line runs through the *scale*, not only the chord. Three chord
        # tones inside an octave is too sparse a ladder to climb -- the contour
        # ends up stepping between four notes and the arc disappears. Trance
        # leads run the scale and land on chord tones where it matters, which
        # is what this does: the pool is scale degrees, and strong beats snap
        # to the nearest note of the sounding chord.
        target_low = (octave + 2) * 12
        # An octave and a fourth is the range a top line can own without
        # crowding whatever sits above or below it.
        ceiling = min(MAX_LEAD_PITCH, target_low + 17)
        pool = [
            p for p in theory.scale_pitches(root, scale, octave=octave, octaves=3)
            if target_low <= p <= ceiling
        ]
        if not pool:
            pool = [target_low]

        chord_classes = {p % 12 for p in chord.pitches}

        if previous is not None and rng.random() < spec.repeat_bias:
            pitch = previous
        else:
            target = _contour_position(spec.contour, progress)
            index = int(target * (len(pool) - 1))
            # Wobble by one chord tone so it is a line rather than a ramp --
            # but only when the pool is big enough to absorb it. On a five-note
            # pool a step of one is a fifth of the whole range, and the arc the
            # contour is drawing disappears into the noise.
            if len(pool) >= 8:
                index = max(0, min(len(pool) - 1, index + rng.randint(-1, 1)))
            pitch = pool[index]

            # Strong beats belong to the harmony; the notes between them are
            # free to be passing tones. Without this the line is merely in-key
            # and never sounds like it belongs to the chord underneath.
            if step % 4 == 0 and chord_classes:
                nearby = [p for p in pool if p % 12 in chord_classes]
                if nearby:
                    pitch = min(nearby, key=lambda p: abs(p - pitch))

        # A climbing line lands on its highest note at the very end -- that
        # arrival is the whole point of the phrase, so it has to clear
        # everything before it, not merely top its own chord's pool.
        if spec.contour == "climb" and step == step_count - 1:
            pitch = max(pool[-1], highest + 1 if highest else pool[-1])
            pitch = min(pitch, ceiling)

        highest = max(highest, pitch)
        notes.append({
            "pitch": max(0, min(127, pitch)),
            "start": at,
            "duration": max(0.04, spec.rate * spec.gate),
            # Velocity climbs with the line so the arrival is louder too.
            "velocity": int(max(1, min(127,
                base_velocity - 12 + 24 * progress + rng.uniform(-3, 3)))),
        })
        previous = pitch

    if groove and groove != "straight":
        notes = groove_mod.apply(notes, groove, seed=seed, rigid=())

    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def describe() -> dict:
    return {
        name: {"description": s.description, "rate": s.rate,
               "contour": s.contour, "octave_span": s.octave_span}
        for name, s in STYLES.items()
    }
