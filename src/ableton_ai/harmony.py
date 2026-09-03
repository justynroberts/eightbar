"""Harmonic variation: borrowed chords, passing chords, half-bar changes.

Four chords repeated for six minutes is the default failure of generated dance
music. The fix is not more complexity -- it is the small, conventional moves
producers actually use, applied sparingly:

  Borrowed    A chord taken from the parallel mode. The major V in a minor key
              is the clearest example: it pulls home far harder than the
              diatonic minor v, and it is why almost every trance record uses
              it.
  Secondary   A dominant belonging to the *next* chord rather than the key.
  dominant    V/vi before the vi makes the arrival sound intended.
  Passing     A chord between two others, usually diminished, filling a gap and
              giving the bass a step to walk through.
  Half-bar    Two chords in a bar where there was one. Doubling the harmonic
              rhythm at the end of a phrase is what makes a turnaround.

Each is one substitution on an existing progression, so the loop still sounds
like itself. Applying all of them at once is how you get something that sounds
like a jazz exercise, which is why the variation tools take a count.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import theory


@dataclass
class Step:
    """One chord in time. Duration lets a bar hold two chords."""

    degree: int
    bars: float = 1.0
    quality: str | None = None   # overrides the diatonic quality when borrowed
    label: str = ""              # why this chord is here

    def describe(self) -> str:
        text = str(self.degree)
        if self.bars != 1.0:
            text += f"({self.bars:g})"
        if self.label:
            text += f"[{self.label}]"
        return text


def as_steps(degrees: list[int], bars_each: float = 1.0) -> list[Step]:
    return [Step(degree=d, bars=bars_each) for d in degrees]


def total_bars(steps: list[Step]) -> float:
    return sum(s.bars for s in steps)


# ----------------------------------------------------------------------
# Variation operators
# ----------------------------------------------------------------------

# What the parallel mode offers. In a minor key these brighten or sharpen;
# in a major key they darken.
BORROWED_MINOR: dict[int, tuple[str, str]] = {
    5: ("major", "major V, from harmonic minor -- the strongest pull home"),
    4: ("major", "major IV, from dorian -- lifts without leaving the key"),
    1: ("major", "picardy third -- ends bright after a minor loop"),
    2: ("major", "II major, from lydian -- unexpected and bright"),
}

BORROWED_MAJOR: dict[int, tuple[str, str]] = {
    4: ("minor", "minor iv, from the parallel minor -- the classic sad turn"),
    6: ("minor", "flattened VI feel -- darkens the approach"),
    7: ("major", "flattened VII, from mixolydian -- rock and house staple"),
    3: ("major", "III major -- brief brightening before the turnaround"),
}


def borrow(
    steps: list[Step], scale: str, count: int = 1, seed: int | None = None
) -> list[Step]:
    """Swap a chord for the parallel mode's version of it."""
    rng = random.Random(seed)
    is_minor = "minor" in theory.normalise_scale(scale) or theory.normalise_scale(
        scale
    ) in ("aeolian", "phrygian", "dorian", "locrian")
    table = BORROWED_MINOR if is_minor else BORROWED_MAJOR

    out = [Step(s.degree, s.bars, s.quality, s.label) for s in steps]
    last = len(out) - 1
    candidates = [
        i for i, s in enumerate(out)
        if s.degree in table and not s.quality
        # A Picardy third brightens the *end* of a minor loop. On bar one it
        # is just a wrong chord -- a major tonic where the ear expects minor.
        and not (s.degree == 1 and i != last)
    ]
    rng.shuffle(candidates)

    for index in candidates[:count]:
        quality, why = table[out[index].degree]
        out[index].quality = quality
        out[index].label = "borrowed"
        out[index].why = why  # type: ignore[attr-defined]
    return out


def secondary_dominant(
    steps: list[Step], count: int = 1, seed: int | None = None
) -> list[Step]:
    """Put the dominant of the *next* chord in front of it.

    Splits the preceding bar so the loop keeps its length: the first half stays
    where it was, the second half becomes the approach chord.
    """
    rng = random.Random(seed)
    out = [Step(s.degree, s.bars, s.quality, s.label) for s in steps]

    # The dominant of degree d is the chord a fifth above it.
    fifth_above = {1: 5, 2: 6, 3: 7, 4: 1, 5: 2, 6: 3, 7: 4}

    positions = [i for i in range(len(out) - 1) if out[i].bars >= 1.0]
    rng.shuffle(positions)
    inserted = 0
    for index in sorted(positions, reverse=True):
        if inserted >= count:
            break
        target = out[index + 1].degree
        dominant = fifth_above.get(target)
        if not dominant or dominant == out[index].degree:
            continue
        half = out[index].bars / 2
        out[index].bars = half
        out.insert(index + 1, Step(
            degree=dominant, bars=half, quality="dominant7",
            label=f"V/{target}",
        ))
        inserted += 1
    return out


def passing_chord(
    steps: list[Step], count: int = 1, seed: int | None = None
) -> list[Step]:
    """Fill the gap between two chords a step apart with a diminished passing.

    Gives the bass somewhere to walk, and is short enough that it reads as
    movement rather than as a chord change.
    """
    rng = random.Random(seed)
    out = [Step(s.degree, s.bars, s.quality, s.label) for s in steps]

    positions = [
        i for i in range(len(out) - 1)
        if abs(out[i + 1].degree - out[i].degree) in (1, 2) and out[i].bars >= 1.0
    ]
    rng.shuffle(positions)
    inserted = 0
    for index in sorted(positions, reverse=True):
        if inserted >= count:
            break
        quarter = out[index].bars / 4
        out[index].bars -= quarter
        # The passing chord sits between the two, taking a quarter bar.
        between = out[index].degree + (
            1 if out[index + 1].degree > out[index].degree else -1
        )
        out.insert(index + 1, Step(
            degree=max(1, between), bars=quarter,
            quality="diminished", label="passing",
        ))
        inserted += 1
    return out


def half_bar_turnaround(steps: list[Step], seed: int | None = None) -> list[Step]:
    """Split the final bar into two chords -- the turnaround.

    Doubling the harmonic rhythm at the end of a phrase is the cheapest way to
    stop a four-bar loop sounding like a four-bar loop.
    """
    rng = random.Random(seed)
    out = [Step(s.degree, s.bars, s.quality, s.label) for s in steps]
    if not out:
        return out

    last = out[-1]
    if last.bars < 1.0:
        return out

    # Approach the first chord of the loop from a fifth above it.
    fifth_above = {1: 5, 2: 6, 3: 7, 4: 1, 5: 2, 6: 3, 7: 4}
    target = out[0].degree
    approach = fifth_above.get(target, 5)
    if approach == last.degree:
        approach = rng.choice([d for d in (4, 5, 7, 2) if d != last.degree])

    half = last.bars / 2
    last.bars = half
    out.append(Step(degree=approach, bars=half, quality="dominant7",
                    label="turnaround"))
    return out


def anticipate(steps: list[Step]) -> list[Step]:
    """Pull the last chord forward by half a bar -- a push into the next loop."""
    out = [Step(s.degree, s.bars, s.quality, s.label) for s in steps]
    if len(out) < 2 or out[-2].bars < 1.0:
        return out
    out[-2].bars -= 0.5
    out[-1].bars += 0.5
    out[-1].label = out[-1].label or "anticipated"
    return out


RECIPES: dict[str, str] = {
    "borrowed": "One chord swapped for its parallel-mode version.",
    "secondary": "A dominant inserted in front of the chord it belongs to.",
    "passing": "A diminished chord filling a step-wise gap.",
    "turnaround": "The last bar split in two, approaching the loop's first chord.",
    "anticipate": "The final chord pulled forward half a bar.",
    "rich": "Turnaround plus one borrowed chord -- interesting, still simple.",
    "moving": "Secondary dominant plus a turnaround; the harmony never sits still.",
    "extended": "Diatonic colour: gentle anticipation, no chromatic chords -- "
                "safe against a plainly-diatonic bass and melody.",
}


def vary(
    degrees: list[int],
    scale: str = "minor",
    recipe: str = "rich",
    seed: int | None = None,
    bars_each: float = 1.0,
) -> list[Step]:
    """Apply a named variation recipe to a plain degree list."""
    steps = as_steps(degrees, bars_each)
    if recipe == "borrowed":
        return borrow(steps, scale, 1, seed)
    if recipe == "secondary":
        return secondary_dominant(steps, 1, seed)
    if recipe == "passing":
        return passing_chord(steps, 1, seed)
    if recipe == "turnaround":
        return half_bar_turnaround(steps, seed)
    if recipe == "anticipate":
        return anticipate(steps)
    if recipe == "extended":
        # Diatonic on purpose: colour comes from the extension parameter
        # (7ths, 9ths), never from a borrowed or applied chord. Safe to play
        # under a plainly-diatonic bass and melody.
        return anticipate(steps)
    if recipe == "rich":
        return borrow(half_bar_turnaround(steps, seed), scale, 1, seed)
    if recipe == "moving":
        return half_bar_turnaround(
            secondary_dominant(steps, 1, seed), seed
        )
    raise ValueError(
        f"unknown variation {recipe!r}; try one of: {', '.join(sorted(RECIPES))}"
    )


def build(
    root: str, scale: str, steps: list[Step], octave: int = 3,
    extension: str = "triad", smooth: bool = True,
) -> tuple[list[theory.Chord], list[float]]:
    """Resolve steps into voiced chords plus their durations in bars."""
    chords = []
    for step in steps:
        chords.append(theory.build_chord(
            root, scale, step.degree, octave=octave,
            quality=step.quality, extension=extension,
        ))
    if smooth:
        chords = theory.voice_lead(chords)
    return chords, [s.bars for s in steps]
