"""Melody writing with phrase structure, tension notes and breath.

A line made only of chord tones on even eighths is correct and forgettable.
What makes a melody memorable is the things around the chord tones:

  Phrase structure   Four bars that ask, four that answer. The first phrase
                     ends unresolved -- on the 2nd, 5th or 7th degree -- so the
                     ear waits; the second lands on the tonic.
  A peak, placed     One highest note, and it belongs about two-thirds of the
                     way through, not at the end. A line that peaks on its last
                     note has nowhere to resolve to.
  Non-chord tones    Passing notes, neighbours, suspensions and appoggiaturas.
                     These are the notes that create tension, and every one of
                     them resolves by step. That resolution is the melody.
  Rhythmic identity  One rhythm, restated. Change the pitches between phrases
                     and keep the rhythm, and the line reads as a single idea.
  Space              Rests are structural. A phrase that never stops to breathe
                     cannot be sung, and anything that cannot be sung is not a
                     melody.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import theory
from .theory import Chord

SIXTEENTH = 0.25
BEATS_PER_BAR = 4.0

Note = dict[str, float | int | bool]

# Where a note falls decides what it is allowed to be. Chord tones belong on
# strong beats; tension notes belong between them.
STRONG = 0
MEDIUM = 1
WEAK = 2


def beat_strength(position: float) -> int:
    """How strong the beat at this position within a bar is."""
    within = round((position % BEATS_PER_BAR) / SIXTEENTH)
    if within % 16 == 0 or within % 8 == 0:
        return STRONG
    if within % 4 == 0:
        return MEDIUM
    return WEAK


# Rhythmic cells as sixteenth positions in a bar. These are the shapes that
# recur in dance melodies -- deliberately not a uniform grid.
# Two-bar cells, so the rhythm has a shape rather than resetting every bar.
# Every one contains at least one long note: a line made only of eighths has no
# rhythmic identity, however good its pitches are. The gaps between onsets are
# the rhythm, and a wide spread of note lengths is what makes it memorable.
RHYTHMS: dict[str, tuple[int, ...]] = {
    "even":         (0, 4, 8, 12, 16, 20, 24),
    "anticipated":  (0, 3, 8, 16, 19, 24),
    "gallop":       (0, 3, 4, 8, 16, 19, 20, 26),
    "long_short":   (0, 6, 8, 16, 22, 24),
    "syncopated":   (0, 3, 6, 14, 16, 19, 22, 28),
    "driving":      (0, 2, 4, 6, 8, 12, 16, 18, 20, 22, 24),
    "sparse":       (0, 8, 16, 26),
    "held":         (0, 12, 16, 28),
    "trance":       (0, 2, 3, 6, 8, 16, 18, 19, 22, 24),
    "answer":       (0, 4, 6, 16, 20, 26),
    "vocal":        (0, 3, 4, 10, 16, 19, 20, 24),
}

# How a phrase ends. The antecedent leaves the ear waiting; the consequent
# settles. Values are scale degrees the final note may land on.
CADENCES: dict[str, tuple[int, ...]] = {
    "open": (2, 5, 7),      # unresolved -- asks a question
    "half": (5, 3),         # rests on the dominant
    "closed": (1,),         # home
    "soft": (1, 3),         # home, or its third
}


@dataclass
class PhrasePlan:
    bars: float
    rhythm: str
    cadence: str
    contour: str
    peak_at: float          # 0..1 through the phrase
    rest_share: float
    register_shift: int = 0  # scale steps relative to the melody's home


def default_plan(bars: float = 8, rhythm: str = "syncopated") -> list[PhrasePlan]:
    """The standard eight-bar shape: a question, then an answer.

    The peak sits at about two-thirds through the answering phrase, which is
    where the ear expects a climax -- late enough to have been earned, early
    enough to leave room to resolve.
    """
    half = bars / 2
    return [
        PhrasePlan(half, rhythm, "open", "arch", 0.55, 0.12, 0),
        # 0.35 of the answering phrase is about two-thirds of the whole melody,
        # which is where a climax is expected -- earned, but with room left to
        # resolve.
        PhrasePlan(half, rhythm, "closed", "arch", 0.35, 0.10, 1),
    ]


def _scale_pitches(root: str, scale: str, octave: int) -> list[int]:
    """Two octaves is a melody's working range. Three invites a line that
    wanders rather than one that can be sung."""
    return theory.scale_pitches(root, scale, octave=octave, octaves=2)


def _nearest_index(pitches: list[int], target: int) -> int:
    return min(range(len(pitches)), key=lambda i: abs(pitches[i] - target))


def _chord_indices(pitches: list[int], chord: Chord) -> list[int]:
    classes = {p % 12 for p in chord.pitches}
    return [i for i, p in enumerate(pitches) if p % 12 in classes]


def write(
    root: str,
    scale: str,
    chords: list[Chord],
    bars: float = 8,
    octave: int = 5,
    rhythm: str = "syncopated",
    plans: list[PhrasePlan] | None = None,
    velocity: int = 96,
    tension: float = 0.35,
    seed: int | None = None,
    durations: list[float] | None = None,
) -> list[Note]:
    """Write a melody over a progression.

    `tension` is how often a note is allowed to be a non-chord tone. At zero the
    line is safe and dull; above about 0.5 it stops sounding like the harmony.
    Every tension note resolves by step, which is what stops it being a mistake.

    `durations` is how long each chord lasts, in bars. Pass it whenever the
    harmony is not evenly divided -- a progression with a half-bar turnaround
    or a passing chord is not, and a melody written against an assumed even
    split will pick its notes from the wrong chord.
    """
    rng = random.Random(seed)
    scale_key = theory.normalise_scale(scale)
    pitches = _scale_pitches(root, scale_key, octave)
    phrases = plans or default_plan(bars, rhythm)

    # Where each chord actually starts and ends, in beats. Without this the
    # melody assumes an even split and lands on the wrong harmony wherever the
    # progression has an uneven bar.
    if durations and len(durations) == len(chords):
        total = sum(durations) or 1.0
        scaled = [d * bars / total for d in durations]
    else:
        scaled = [bars / max(1, len(chords))] * len(chords)

    boundaries: list[tuple[float, float, Chord]] = []
    at_bar = 0.0
    for chord, length in zip(chords, scaled):
        boundaries.append((at_bar * BEATS_PER_BAR,
                           (at_bar + length) * BEATS_PER_BAR, chord))
        at_bar += length

    def chord_at(beat: float) -> Chord:
        for start, end, chord in boundaries:
            if start - 1e-6 <= beat < end:
                return chord
        return boundaries[-1][2]

    total_planned = sum(p.bars for p in phrases) or bars
    stretch = bars / total_planned

    notes: list[Note] = []
    cursor = 0.0
    peak_index: int | None = None
    peak_target = 0

    # The peak belongs to the whole melody, not to each phrase: find the
    # highest register the line will reach, and reserve it for one moment.
    for phrase in phrases:
        peak_target = max(peak_target, 8 + phrase.register_shift)

    for phrase_number, phrase in enumerate(phrases):
        # Only the last phrase may reach the top. An answering phrase that
        # cannot out-sing the question is the whole point of the shape -- if
        # phrase one peaks highest, the climax has already happened.
        is_final = phrase_number == len(phrases) - 1
        ceiling = peak_target if is_final else max(2, peak_target - 3)
        span = phrase.bars * stretch
        cell = RHYTHMS.get(phrase.rhythm, RHYTHMS["syncopated"])
        cell_bars = max(1, (max(cell) // 16) + 1)
        onsets: list[float] = []
        for block in range(max(1, int(round(span / cell_bars)))):
            for step in cell:
                at = cursor + block * cell_bars * BEATS_PER_BAR + step * SIXTEENTH
                if at < cursor + span * BEATS_PER_BAR - 1e-6:
                    onsets.append(at)
        onsets.sort()

        if not onsets:
            cursor += span * BEATS_PER_BAR
            continue

        # Which onset carries the phrase's highest note.
        phrase_peak = int(len(onsets) * phrase.peak_at)
        last_index = len(onsets) - 1
        previous: int | None = None
        pending_resolution: int | None = None
        repeats = 0

        for position, at in enumerate(onsets):
            # Breath. Never on the first note, and never on the cadence.
            if (position not in (0, last_index)
                    and position != phrase_peak
                    and rng.random() < phrase.rest_share):
                previous = None
                continue

            chord = chord_at(at)
            tones = _chord_indices(pitches, chord)
            # Only consider chord tones the phrase is allowed to reach.
            under = [t for t in tones if t <= ceiling]
            tones = under or tones
            if not tones:
                continue
            strength = beat_strength(at)

            # A tension note from the previous step must resolve now, by step,
            # and in the direction it was leaning.
            if pending_resolution is not None:
                index = pending_resolution
                pending_resolution = None
            elif position == last_index:
                # Land the phrase on a degree its cadence allows, below the
                # peak and close to where the line already is. A cadence that
                # leaps upward does not sound like an ending.
                wanted = CADENCES[phrase.cadence]
                intervals = theory.SCALES[scale_key]
                allowed = {intervals[(d - 1) % len(intervals)] for d in wanted}
                here = previous if previous is not None else tones[0]
                candidates = [
                    i for i in range(len(pitches))
                    if ((pitches[i] - theory.note_to_pitch_class(root)) % 12) in allowed
                    and i <= here
                ]
                if not candidates:
                    candidates = [
                        i for i in range(len(pitches))
                        if ((pitches[i] - theory.note_to_pitch_class(root)) % 12)
                        in allowed
                    ]
                index = (min(candidates, key=lambda i: abs(i - here))
                         if candidates else tones[0])
            elif position == phrase_peak:
                # The climax: the highest chord tone in reach.
                reachable = [t for t in tones if t <= ceiling]
                index = max(reachable) if reachable else min(tones, key=lambda i: abs(i - ceiling))
            elif strength == STRONG or previous is None:
                # Strong beats take chord tones -- that is what makes the line
                # sound like it belongs to the harmony. But a third repeat of
                # the same note reads as a stuck sequencer, so move to the next
                # chord tone instead.
                target = previous if previous is not None else tones[len(tones) // 2]
                index = min(tones, key=lambda i: abs(i - target))
                if index == previous and repeats >= 1:  # noqa: SIM102
                    others = [t for t in tones if t != index]
                    if others:
                        index = min(others, key=lambda i: abs(i - target))
            elif rng.random() < tension:
                # A tension note, one step from where we are, which the next
                # note will resolve back into the chord.
                direction = 1 if rng.random() < 0.5 else -1
                index = max(0, min(len(pitches) - 1, previous + direction))
                resolve_to = min(tones, key=lambda i: abs(i - index))
                pending_resolution = resolve_to
            else:
                # Ordinary weak-beat motion: step toward a chord tone. If we
                # are already on one, step away rather than repeat -- movement
                # is what makes it a line instead of a pulse.
                target = min(tones, key=lambda i: abs(i - previous))
                if target != previous:
                    index = previous + (1 if target > previous else -1)
                elif repeats >= 1:
                    # Step away rather than sit; direction toward the middle of
                    # the range so the line does not drift to an extreme.
                    middle = len(pitches) // 2
                    index = previous + (1 if previous < middle else -1)
                else:
                    index = previous

            if index > ceiling:
                # Over the ceiling: fold down an octave rather than flatten
                # onto it, which would repeat the same capped note.
                octave_steps = len(theory.SCALES[scale_key])
                index = index - octave_steps if index - octave_steps >= 0 else ceiling
            index = max(0, min(len(pitches) - 1, index))
            pitch = pitches[index]

            length = (
                onsets[position + 1] - at if position + 1 < len(onsets)
                else max(SIXTEENTH * 2, cursor + span * BEATS_PER_BAR - at)
            )
            # The peak and the cadence are held right through -- that hold is
            # what makes them read as arrival rather than as two more notes.
            if position == last_index:
                length = max(length, BEATS_PER_BAR * 0.75)
                gate = 1.0
            elif position == phrase_peak:
                length = max(length, 1.5)
                gate = 0.98
            else:
                gate = 0.72

            notes.append({
                "pitch": max(0, min(127, pitch)),
                "start": at,
                "duration": max(0.08, length * gate),
                "velocity": int(max(1, min(127,
                    velocity
                    + (10 if position == phrase_peak else 0)
                    + (6 if strength == STRONG else -4)
                    + rng.uniform(-3, 3)))),
            })
            repeats = repeats + 1 if index == previous else 0
            previous = index

        cursor += span * BEATS_PER_BAR

    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes
