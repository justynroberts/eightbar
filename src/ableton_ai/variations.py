"""Mutation operators that turn one clip into a family of related clips.

An EDM track is mostly repetition, and what keeps it alive is that the repeats
are never quite identical -- the second drop is bigger, the second verse thins
out, the last eight bars before a break stutter. These operators take a note
list and return a changed note list, so any generated part can be varied without
regenerating it from scratch and losing its character.
"""

from __future__ import annotations

import random
from typing import Callable

from .generators import BEATS_PER_BAR, DRUM_MAP, SIXTEENTH, Note

Mutation = Callable[..., list[Note]]


def _clone(notes: list[Note]) -> list[Note]:
    return [dict(n) for n in notes]


def _length(notes: list[Note]) -> float:
    if not notes:
        return 0.0
    return max(float(n["start"]) + float(n["duration"]) for n in notes)


def thin(notes: list[Note], amount: float = 0.3, seed: int | None = None) -> list[Note]:
    """Drop `amount` of the notes at random. Makes a part sit further back."""
    rng = random.Random(seed)
    kept = [n for n in _clone(notes) if rng.random() >= amount]
    # Never thin a part into silence.
    return kept or _clone(notes)[:1]


def densify(
    notes: list[Note], amount: float = 0.4, seed: int | None = None
) -> list[Note]:
    """Add off-beat echoes of existing notes -- more urgency, same material."""
    rng = random.Random(seed)
    out = _clone(notes)
    for note in list(out):
        if rng.random() < amount:
            echo = dict(note)
            echo["start"] = float(note["start"]) + SIXTEENTH
            echo["velocity"] = max(1, int(float(note["velocity"]) * 0.7))
            echo["duration"] = min(float(note["duration"]), SIXTEENTH)
            out.append(echo)
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def octave_shift(notes: list[Note], octaves: int = 1) -> list[Note]:
    """Move the whole part up or down. +1 for a lift into a drop."""
    return [
        dict(n, pitch=max(0, min(127, int(n["pitch"]) + 12 * octaves)))
        for n in notes
    ]


def half_time(notes: list[Note]) -> list[Note]:
    """Stretch everything to twice the length -- the classic breakdown feel."""
    return [
        dict(n, start=float(n["start"]) * 2, duration=float(n["duration"]) * 2)
        for n in notes
    ]


def double_time(notes: list[Note], repeat: bool = True) -> list[Note]:
    """Compress to half length, then repeat it to fill the original span."""
    squashed = [
        dict(n, start=float(n["start"]) / 2, duration=float(n["duration"]) / 2)
        for n in notes
    ]
    if not repeat:
        return squashed
    span = _length(notes)
    second = [dict(n, start=float(n["start"]) + span / 2) for n in squashed]
    out = squashed + second
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def rotate(notes: list[Note], beats: float = 1.0) -> list[Note]:
    """Shift the pattern in time, wrapping around the clip. Re-frames the groove."""
    span = _length(notes)
    if span <= 0:
        return _clone(notes)
    out = []
    for note in _clone(notes):
        note["start"] = (float(note["start"]) + beats) % span
        out.append(note)
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def reverse(notes: list[Note]) -> list[Note]:
    """Play the material backwards in time."""
    span = _length(notes)
    out = []
    for note in _clone(notes):
        note["start"] = max(0.0, span - float(note["start"]) - float(note["duration"]))
        out.append(note)
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def velocity_ramp(
    notes: list[Note], start: int = 60, end: int = 120
) -> list[Note]:
    """Ramp velocity across the clip -- energy that climbs into the next section."""
    span = _length(notes) or 1.0
    out = []
    for note in _clone(notes):
        progress = float(note["start"]) / span
        note["velocity"] = int(max(1, min(127, start + (end - start) * progress)))
        out.append(note)
    return out


def accent(notes: list[Note], every: int = 4, amount: int = 20) -> list[Note]:
    """Push every Nth sixteenth harder, so the pulse reads more clearly."""
    out = []
    for note in _clone(notes):
        step = round(float(note["start"]) / SIXTEENTH)
        if step % every == 0:
            note["velocity"] = int(min(127, float(note["velocity"]) + amount))
        out.append(note)
    return out


def stutter(
    notes: list[Note], bars: float = 1.0, divisions: int = 8
) -> list[Note]:
    """Retrigger the final `bars` as a fast repeat -- the classic pre-drop stall."""
    span = _length(notes)
    if span <= 0:
        return _clone(notes)
    cut = max(0.0, span - bars * BEATS_PER_BAR)
    head = [n for n in _clone(notes) if float(n["start"]) < cut]
    seed_notes = [n for n in _clone(notes) if float(n["start"]) >= cut]
    if not seed_notes:
        seed_notes = _clone(notes)[-2:]

    step = bars * BEATS_PER_BAR / divisions
    tail: list[Note] = []
    for i in range(divisions):
        for note in seed_notes[: max(1, len(seed_notes) // 2)]:
            tail.append(
                dict(
                    note,
                    start=cut + i * step,
                    duration=max(0.02, step * 0.9),
                    velocity=int(min(127, 70 + i * (50 / max(1, divisions)))),
                )
            )
    out = head + tail
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def drop_role(notes: list[Note], instrument: str = "kick") -> list[Note]:
    """Remove one drum voice. Pulling the kick is how a breakdown starts."""
    pitch = DRUM_MAP.get(instrument)
    if pitch is None:
        return _clone(notes)
    return [n for n in _clone(notes) if int(n["pitch"]) != pitch]


def only_role(notes: list[Note], instrument: str = "kick") -> list[Note]:
    """Keep only one drum voice -- a stripped intro built from just the kick."""
    pitch = DRUM_MAP.get(instrument)
    if pitch is None:
        return _clone(notes)
    return [n for n in _clone(notes) if int(n["pitch"]) == pitch]


def humanise_more(
    notes: list[Note], amount: float = 0.5, seed: int | None = None
) -> list[Note]:
    """Loosen timing and velocity further than the generator did."""
    rng = random.Random(seed)
    out = []
    for note in _clone(notes):
        note["start"] = max(0.0, float(note["start"]) + rng.uniform(-1, 1) * amount * 0.03)
        note["velocity"] = int(
            max(1, min(127, float(note["velocity"]) + rng.uniform(-1, 1) * amount * 15))
        )
        out.append(note)
    return out


def staccato(notes: list[Note], amount: float = 0.35) -> list[Note]:
    """Shorten every note towards a stab, keeping its start where it is."""
    shortest = 0.05
    keep = max(0.1, 1.0 - min(0.9, amount * 2.0))
    out = _clone(notes)
    for note in out:
        note["duration"] = max(shortest, note["duration"] * keep)
    return out


def legato(notes: list[Note], amount: float = 0.35) -> list[Note]:
    """Stretch each note towards the next one, so the line joins up."""
    out = sorted(_clone(notes), key=lambda n: (n["start"], n["pitch"]))
    for index, note in enumerate(out):
        following = [n for n in out[index + 1:] if n["start"] > note["start"]]
        if not following:
            continue
        gap = following[0]["start"] - note["start"]
        note["duration"] = max(note["duration"], gap * (1.0 + amount))
    return out


def velocity_scale(notes: list[Note], factor: float) -> list[Note]:
    out = _clone(notes)
    for note in out:
        note["velocity"] = max(1, min(127, int(round(note["velocity"] * factor))))
    return out


MUTATIONS: dict[str, Mutation] = {
    "staccato": staccato,
    "legato": legato,
    "softer": lambda n, **k: velocity_scale(n, 0.75),
    "louder": lambda n, **k: velocity_scale(n, 1.25),
    "thin": thin,
    "densify": densify,
    "octave_up": lambda n, **k: octave_shift(n, 1),
    "octave_down": lambda n, **k: octave_shift(n, -1),
    "half_time": lambda n, **k: half_time(n),
    "double_time": lambda n, **k: double_time(n),
    "rotate": rotate,
    "reverse": lambda n, **k: reverse(n),
    "velocity_ramp": velocity_ramp,
    "accent": accent,
    "stutter": stutter,
    "drop_kick": lambda n, **k: drop_role(n, "kick"),
    "drop_hats": lambda n, **k: drop_role(n, "closed_hat"),
    "only_kick": lambda n, **k: only_role(n, "kick"),
    "humanise": humanise_more,
}

# Recipes: named combinations that correspond to how a section actually differs.
RECIPES: dict[str, list[str]] = {
    "stripped": ["thin", "drop_hats"],
    "intro": ["only_kick"],
    "breakdown": ["half_time", "thin"],
    "bigger": ["densify", "accent"],
    "lift": ["octave_up", "accent"],
    "pre_drop": ["stutter", "velocity_ramp"],
    "busier": ["densify"],
    "looser": ["humanise", "thin"],
    "flipped": ["rotate", "accent"],
    "climax": ["densify", "octave_up", "velocity_ramp"],
    "stab": ["staccato", "accent"],
    "sustained": ["legato"],
    "ghost": ["softer", "thin"],
    "outro": ["thin", "softer"],
}

# Words the model reaches for that mean one of the above. Without these a
# perfectly sensible request -- "make a stab version" -- fails at the far end
# with a list of names nobody asked about.
ALIASES: dict[str, str] = {
    "stabs": "stab", "stabby": "stab", "short": "stab", "plucked": "stab",
    "staccatto": "staccato",
    "sustain": "sustained", "long": "sustained", "held": "sustained",
    "pad": "sustained", "smooth": "sustained",
    "sparse": "stripped", "sparser": "stripped", "minimal": "stripped",
    "simpler": "stripped", "verse": "stripped", "quieter": "softer",
    "dense": "busier", "fuller": "busier", "complex": "busier",
    "harder": "bigger", "energetic": "bigger", "chorus": "bigger",
    "drive": "bigger", "huge": "climax", "massive": "climax", "peak": "climax",
    "build": "pre_drop", "build_up": "pre_drop", "buildup": "pre_drop",
    "fill": "stutter", "glitch": "stutter", "roll": "stutter",
    "chill": "breakdown", "calm": "breakdown", "downtempo": "breakdown",
    "swing": "humanise", "groovy": "humanise", "loose": "looser",
    "human": "humanise", "varied": "looser", "alt": "looser",
    "syncopated": "rotate", "offset": "rotate", "shifted": "rotate",
    "up": "octave_up", "down": "octave_down",
    "halftime": "half_time", "doubletime": "double_time",
}


def canonical(name: str) -> str:
    """Resolve a synonym to the mutation or recipe it means."""
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(key, key)


def mutation_vocabulary() -> list[str]:
    """Every accepted word, synonyms included, for the tool schema."""
    return sorted(set(MUTATIONS) | set(RECIPES) | set(ALIASES))


def apply(
    notes: list[Note],
    mutations: list[str],
    intensity: float = 0.35,
    seed: int | None = None,
) -> list[Note]:
    """Apply a chain of named mutations (or a recipe name) in order."""
    resolved: list[str] = []
    for name in mutations:
        key = canonical(name)
        if key in RECIPES:
            resolved.extend(RECIPES[key])
        else:
            resolved.append(key)

    out = _clone(notes)
    for index, name in enumerate(resolved):
        func = MUTATIONS.get(canonical(name))
        if func is None:
            raise ValueError(
                f"unknown mutation {name!r}; try one of: "
                f"{', '.join(sorted(set(MUTATIONS) | set(RECIPES)))}"
            )
        kwargs: dict[str, object] = {}
        signature = func.__code__.co_varnames[: func.__code__.co_argcount]
        if "amount" in signature:
            kwargs["amount"] = intensity
        if "seed" in signature:
            kwargs["seed"] = None if seed is None else seed + index
        out = func(out, **kwargs)
    return out


def variation_set(
    notes: list[Note],
    count: int = 4,
    escalate: bool = True,
    seed: int | None = None,
) -> list[tuple[str, list[Note]]]:
    """Produce `count` progressively different versions of a part.

    With `escalate` the set runs stripped -> original -> bigger -> climax, which
    is the shape most EDM tracks want across their sections.
    """
    ladder = ["stripped", "looser", "bigger", "lift", "climax", "flipped", "busier"]
    if escalate:
        chosen = ladder[: max(1, count - 1)]
        results: list[tuple[str, list[Note]]] = [("original", _clone(notes))]
        for index, recipe in enumerate(chosen):
            results.append(
                (recipe, apply(notes, [recipe], seed=None if seed is None else seed + index))
            )
        return results[:count]

    rng = random.Random(seed)
    pool = list(RECIPES)
    results = [("original", _clone(notes))]
    for index in range(max(0, count - 1)):
        recipe = rng.choice(pool)
        results.append(
            (recipe, apply(notes, [recipe], seed=None if seed is None else seed + index))
        )
    return results
