"""Motif development -- melodies that go somewhere.

A random walk inside a scale produces notes that are individually correct and
collectively meaningless. Real melodies state a short idea and then develop it:
repeat it, move it to fit the new chord, turn it upside down, stretch it, cut it
in half, answer it. That is what makes a line memorable rather than merely
in-key.

This module builds a cell -- a few notes with a rhythm -- and then applies those
classical development operations to it across a progression.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import theory

SIXTEENTH = 0.25
BEATS_PER_BAR = 4.0

Note = dict[str, float | int | bool]


@dataclass
class Cell:
    """A motif: scale-degree offsets paired with a rhythm, both in one unit.

    Degrees are *scale steps* relative to the cell's anchor, not semitones, so
    transposing a motif keeps it diatonic automatically.
    """

    degrees: tuple[int, ...]
    rhythm: tuple[float, ...]   # start times in beats, relative to the cell
    durations: tuple[float, ...]
    accents: tuple[int, ...]

    @property
    def length(self) -> float:
        return max(s + d for s, d in zip(self.rhythm, self.durations))

    def __len__(self) -> int:
        return len(self.degrees)


# Rhythmic cells that actually appear in dance music, as sixteenth positions.
RHYTHM_CELLS: dict[str, tuple[int, ...]] = {
    "straight_eighths": (0, 2, 4, 6),
    "gallop":           (0, 3, 4, 7),
    "anticipation":     (0, 3, 6, 8),
    "offbeat_pair":     (2, 6, 10, 14),
    "long_short":       (0, 6, 8, 12),
    "three_against":    (0, 3, 6, 9, 12),
    "stab":             (0, 4, 10),
    "syncopated":       (0, 3, 8, 11),
    "driving":          (0, 2, 3, 6, 8, 10, 11, 14),
    "sparse":           (0, 8),
}

# Melodic shapes as scale-step offsets from the anchor.
SHAPE_CELLS: dict[str, tuple[int, ...]] = {
    "rise":        (0, 1, 2, 4),
    "fall":        (4, 2, 1, 0),
    "arch":        (0, 2, 4, 2),
    "valley":      (4, 2, 0, 2),
    "neighbour":   (0, 1, 0, -1),
    "leap_return": (0, 4, 2, 0),
    "step_up":     (0, 1, 2, 3),
    "pedal":       (0, 0, 2, 0),
    "call":        (0, 2, 1, 4),
    "hook":        (4, 4, 2, 0),
}


def make_cell(
    shape: str = "arch",
    rhythm: str = "straight_eighths",
    seed: int | None = None,
) -> Cell:
    """Build a motif from a named melodic shape and rhythmic cell."""
    rng = random.Random(seed)
    degrees = SHAPE_CELLS.get(shape)
    steps = RHYTHM_CELLS.get(rhythm)
    if degrees is None:
        raise ValueError(
            f"unknown motif shape {shape!r}; try: {', '.join(sorted(SHAPE_CELLS))}"
        )
    if steps is None:
        raise ValueError(
            f"unknown rhythm cell {rhythm!r}; try: {', '.join(sorted(RHYTHM_CELLS))}"
        )

    # Match lengths: cycle the shorter of the two so every onset gets a pitch.
    count = max(len(degrees), len(steps))
    degrees = tuple(degrees[i % len(degrees)] for i in range(count))
    starts = tuple(steps[i % len(steps)] * SIXTEENTH + (i // len(steps)) * BEATS_PER_BAR
                   for i in range(count))

    durations = []
    for i, start in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else start + SIXTEENTH * 2
        durations.append(max(SIXTEENTH * 0.5, (nxt - start) * 0.9))

    # A motif needs one clear stress, or every note reads as equally important.
    accents = tuple(12 if i == 0 else (6 if i == len(degrees) - 1 else 0)
                    for i in range(count))
    return Cell(tuple(degrees), starts, tuple(durations), accents)


# ----------------------------------------------------------------------
# Development operations
# ----------------------------------------------------------------------

def transpose(cell: Cell, steps: int) -> Cell:
    """Move the whole motif up or down the scale, keeping its shape."""
    return Cell(
        tuple(d + steps for d in cell.degrees),
        cell.rhythm, cell.durations, cell.accents,
    )


def invert(cell: Cell) -> Cell:
    """Turn the motif upside down around its first note."""
    anchor = cell.degrees[0]
    return Cell(
        tuple(anchor - (d - anchor) for d in cell.degrees),
        cell.rhythm, cell.durations, cell.accents,
    )


def retrograde(cell: Cell) -> Cell:
    """Play the pitches backwards over the same rhythm."""
    return Cell(
        tuple(reversed(cell.degrees)),
        cell.rhythm, cell.durations, cell.accents,
    )


def augment(cell: Cell, factor: float = 2.0) -> Cell:
    """Stretch the motif in time -- half-time restatement."""
    return Cell(
        cell.degrees,
        tuple(t * factor for t in cell.rhythm),
        tuple(d * factor for d in cell.durations),
        cell.accents,
    )


def diminish(cell: Cell, factor: float = 0.5) -> Cell:
    """Compress the motif -- double-time restatement."""
    return augment(cell, factor)


def fragment(cell: Cell, keep: int = 2) -> Cell:
    """Keep only the opening of the motif. The classic build-up device."""
    keep = max(1, min(len(cell), keep))
    return Cell(
        cell.degrees[:keep], cell.rhythm[:keep],
        cell.durations[:keep], cell.accents[:keep],
    )


def sequence(cell: Cell, steps: int, times: int) -> list[Cell]:
    """Restate the motif at rising or falling scale steps -- a sequence."""
    return [transpose(cell, steps * i) for i in range(times)]


def answer(cell: Cell) -> Cell:
    """A responding phrase: same rhythm, contour inverted and resolved down."""
    inverted = invert(cell)
    resolved = list(inverted.degrees)
    resolved[-1] = 0  # land back on the tonic degree
    return Cell(tuple(resolved), cell.rhythm, cell.durations, cell.accents)


def cell_from_learned(learned: dict, seed: int | None = None) -> Cell:
    """Turn a corpus-extracted motif into a Cell the composer can develop.

    The corpus stores what it heard: semitone intervals and the gaps between
    onsets. A Cell wants scale-step offsets and a rhythm inside one bar, so
    intervals are mapped to their nearest diatonic step (7 steps per 12
    semitones) and the rhythm is normalised to start at zero. The result is a
    motif with the *contour and rhythm* of the reference, ready to be
    rendered into any key and developed like a written one.
    """
    intervals = list(learned.get("intervals") or [])
    gaps = list(learned.get("rhythm") or [])
    if not intervals or not gaps:
        raise ValueError("learned motif has no intervals or rhythm")

    degrees = [0]
    for semitones in intervals[:7]:
        step = round(semitones * 7 / 12)
        degrees.append(degrees[-1] + step)

    starts = [0.0]
    for gap in gaps[: len(degrees) - 1]:
        starts.append(starts[-1] + max(0.125, min(2.0, float(gap))))
    # Normalise into one bar so development operations behave.
    span = max(starts) or 1.0
    if span > BEATS_PER_BAR - 0.25:
        scale_by = (BEATS_PER_BAR - 0.5) / span
        starts = [round(t * scale_by * 4) / 4 for t in starts]
    durations = [
        max(0.125, (starts[i + 1] - starts[i]) * 0.9) if i + 1 < len(starts)
        else 0.75
        for i in range(len(starts))
    ]
    accents = [1 if i == 0 else 0 for i in range(len(starts))]
    return Cell(
        degrees=tuple(degrees[: len(starts)]),
        rhythm=tuple(starts),
        durations=tuple(durations),
        accents=tuple(accents),
    )


DEVELOPMENTS = (
    "repeat", "sequence_up", "sequence_down", "invert", "retrograde",
    "augment", "fragment", "answer",
)


def develop(cell: Cell, operation: str, seed: int | None = None) -> Cell:
    rng = random.Random(seed)
    if operation == "repeat":
        return cell
    if operation == "sequence_up":
        return transpose(cell, 1)
    if operation == "sequence_down":
        return transpose(cell, -1)
    if operation == "invert":
        return invert(cell)
    if operation == "retrograde":
        return retrograde(cell)
    if operation == "augment":
        return augment(cell)
    if operation == "diminish":
        return diminish(cell)
    if operation == "fragment":
        return fragment(cell, rng.choice([2, 3]))
    if operation == "answer":
        return answer(cell)
    raise ValueError(
        f"unknown development {operation!r}; try: {', '.join(DEVELOPMENTS)}"
    )


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

def render(
    cell: Cell,
    root: str,
    scale: str,
    octave: int = 4,
    anchor_degree: int = 1,
    at: float = 0.0,
    velocity: int = 92,
    chord_tones: tuple[int, ...] | None = None,
) -> list[Note]:
    """Turn a motif into notes in a key.

    When `chord_tones` is given, notes land on chord tones where they can, so
    the line agrees with the harmony underneath instead of merely staying in
    the scale.
    """
    scale_key = theory.normalise_scale(scale)
    notes: list[Note] = []

    for i, degree in enumerate(cell.degrees):
        absolute = anchor_degree + degree
        pitch = theory.degree_to_pitch(root, scale_key, absolute, octave)

        if chord_tones:
            # Nudge to the nearest chord tone -- at most a step, so the shape holds.
            classes = {t % 12 for t in chord_tones}
            if pitch % 12 not in classes:
                for delta in (-1, 1, -2, 2):
                    if (pitch + delta) % 12 in classes:
                        pitch += delta
                        break

        notes.append({
            "pitch": max(0, min(127, pitch)),
            "start": at + cell.rhythm[i],
            "duration": cell.durations[i],
            "velocity": max(1, min(127, velocity + cell.accents[i])),
        })

    return notes


def build_phrase(
    root: str,
    scale: str,
    chords: list[theory.Chord],
    bars_per_chord: float = 1.0,
    shape: str = "arch",
    rhythm: str = "straight_eighths",
    octave: int = 4,
    velocity: int = 92,
    plan: list[str] | None = None,
    seed: int | None = None,
) -> list[Note]:
    """State a motif and develop it across a progression.

    The default plan -- state, repeat, sequence, answer -- is the standard
    four-bar antecedent/consequent shape that almost every dance hook uses.
    """
    rng = random.Random(seed)
    cell = make_cell(shape, rhythm, seed=seed)
    operations = plan or ["repeat", "repeat", "sequence_up", "answer"]

    notes: list[Note] = []
    for index, chord in enumerate(chords):
        at = index * bars_per_chord * BEATS_PER_BAR
        operation = operations[index % len(operations)]
        variant = develop(cell, operation, seed=None if seed is None else seed + index)

        # Anchor the motif on the chord's own scale degree so it moves with the
        # harmony rather than sitting stubbornly on the tonic.
        rendered = render(
            variant,
            root, scale,
            octave=octave,
            anchor_degree=chord.degree,
            at=at,
            velocity=velocity,
            chord_tones=chord.pitches,
        )
        # Trim anything that overruns its chord.
        limit = at + bars_per_chord * BEATS_PER_BAR
        for note in rendered:
            if float(note["start"]) < limit:
                note["duration"] = min(
                    float(note["duration"]), limit - float(note["start"])
                )
                notes.append(note)

    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes
