"""Feel: microtiming and velocity shaping.

Quantised MIDI at a flat velocity is the single clearest giveaway of generated
music. Real parts sit slightly off the grid in *systematic* ways -- a snare
laid back a few milliseconds, hats pushed, a shuffle on the off-sixteenths --
and their velocities follow a repeating accent shape rather than a constant.

Random jitter does not fix this. Jitter makes a part sound sloppy; groove makes
it sound played. These are templates, applied per part.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

SIXTEENTH = 0.25
BEATS_PER_BAR = 4.0

# Declared here rather than imported: generators imports this module, so
# importing it back would be circular. Ableton's Drum Rack puts the kick on C1.
KICK_PITCH = 36

Note = dict[str, float | int | bool]


@dataclass(frozen=True)
class Groove:
    """A feel template.

    `swing` delays odd sixteenths by a fraction of half a sixteenth.
    `push` shifts the whole part early (negative) or late (positive), in beats.
    `accents` is a 16-step velocity offset curve applied per bar.
    `timing` is a 16-step microtiming offset curve, in fractions of a sixteenth.
    """

    name: str
    swing: float = 0.0
    push: float = 0.0
    accents: tuple[int, ...] = field(default_factory=tuple)
    timing: tuple[float, ...] = field(default_factory=tuple)
    jitter: float = 0.0


# The accent curves are what give each style its pulse. A house hat pattern is
# not flat -- the offbeats are the loud ones, and that is most of the feel.
GROOVES: dict[str, Groove] = {
    "straight": Groove("straight"),

    "house": Groove(
        "house",
        swing=0.10,
        accents=(6, -8, 2, -10, 4, -8, 2, -10, 6, -8, 2, -10, 4, -8, 2, -10),
        jitter=0.15,
    ),
    # Offbeat hats carry the lift, so they get the accent, not the downbeats.
    "house_hats": Groove(
        "house_hats",
        swing=0.12,
        accents=(-14, -6, 10, -6, -12, -6, 8, -6, -14, -6, 10, -6, -12, -6, 8, -6),
        jitter=0.2,
    ),
    "tech_house": Groove(
        "tech_house",
        swing=0.16,
        push=-0.004,
        accents=(8, -10, 3, -12, 5, -10, 4, -12, 7, -10, 3, -12, 5, -10, 6, -8),
        jitter=0.25,
    ),
    "techno": Groove(
        "techno",
        swing=0.0,
        accents=(8, -12, 0, -12, 6, -12, 0, -12, 8, -12, 0, -12, 6, -12, 2, -10),
        jitter=0.08,
    ),
    # Deliberately behind the beat -- the "dragging" feel of deep house.
    "laid_back": Groove(
        "laid_back",
        swing=0.18,
        push=0.012,
        accents=(4, -8, 2, -10, 3, -8, 2, -10, 4, -8, 2, -10, 3, -8, 4, -6),
        jitter=0.3,
    ),
    # Slightly ahead -- urgency, used on builds and DnB.
    "pushed": Groove(
        "pushed",
        push=-0.015,
        accents=(7, -6, 4, -8, 6, -6, 4, -8, 7, -6, 4, -8, 6, -6, 5, -4),
        jitter=0.2,
    ),
    "shuffle": Groove(
        "shuffle",
        swing=0.34,
        accents=(6, -10, 4, -8, 5, -10, 4, -8, 6, -10, 4, -8, 5, -10, 5, -6),
        jitter=0.3,
    ),
    "mpc_swing": Groove(
        "mpc_swing",
        swing=0.24,
        accents=(8, -12, 5, -6, 4, -12, 6, -6, 8, -12, 5, -6, 4, -12, 7, -4),
        jitter=0.35,
    ),
    "dnb": Groove(
        "dnb",
        swing=0.06,
        push=-0.008,
        accents=(9, -12, 3, -10, 7, -12, 3, -10, 9, -12, 3, -10, 7, -12, 5, -8),
        jitter=0.2,
    ),
    "trap": Groove(
        "trap",
        swing=0.30,
        accents=(8, -14, 2, -10, 5, -14, 2, -10, 8, -14, 2, -10, 5, -14, 3, -8),
        jitter=0.25,
    ),
}

ALIASES = {
    "deep_house": "laid_back",
    "swing": "shuffle",
    "mpc": "mpc_swing",
    "drum_and_bass": "dnb",
    "none": "straight",
    "": "straight",
}


def resolve(name: str) -> Groove:
    key = (name or "straight").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in GROOVES:
        raise ValueError(
            f"unknown groove {name!r}; try one of: "
            f"{', '.join(sorted(set(GROOVES) | set(ALIASES) - {''}))}"
        )
    return GROOVES[key]


def _step_of(start: float) -> int:
    """Which sixteenth of the bar a note falls on."""
    position = (start % BEATS_PER_BAR) / SIXTEENTH
    return int(round(position)) % 16


# The kick anchors the grid. Swinging or jittering it smears the downbeat,
# breaks sidechain timing and is not what a groove template is for -- the feel
# lives in the hats and percussion around a rigid kick.
RIGID_PITCHES: tuple[int, ...] = (KICK_PITCH,)


def apply(
    notes: list[Note],
    groove: str | Groove = "straight",
    strength: float = 1.0,
    seed: int | None = None,
    rigid: tuple[int, ...] = RIGID_PITCHES,
) -> list[Note]:
    """Apply a groove template to a part.

    `strength` scales the whole effect, so the same template can be dialled
    back on a busy part and pushed on a sparse one.

    Pitches in `rigid` keep their exact timing and only take the velocity
    shaping -- by default that is the kick, which must stay on the grid.
    """
    template = groove if isinstance(groove, Groove) else resolve(groove)
    if strength <= 0:
        return [dict(n) for n in notes]

    rng = random.Random(seed)
    out: list[Note] = []

    for note in notes:
        entry = dict(note)
        start = float(entry["start"])
        step = _step_of(start)
        anchored = int(entry["pitch"]) in rigid

        offset = template.push
        # Swing: delay the odd sixteenths.
        if template.swing and step % 2 == 1:
            offset += SIXTEENTH * 0.5 * template.swing
        # Per-step microtiming curve.
        if template.timing:
            offset += template.timing[step % len(template.timing)] * SIXTEENTH
        # A touch of jitter on top, so repeats are not identical.
        if template.jitter:
            offset += rng.uniform(-1.0, 1.0) * template.jitter * 0.01

        # An anchored voice keeps its position; only its velocity is shaped.
        entry["start"] = start if anchored else max(0.0, start + offset * strength)

        if template.accents:
            shift = template.accents[step % len(template.accents)]
            entry["velocity"] = int(
                max(1, min(127, float(entry["velocity"]) + shift * strength))
            )

        out.append(entry)

    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out


def velocity_contour(
    notes: list[Note], start: float = 0.7, end: float = 1.0, curve: str = "linear"
) -> list[Note]:
    """Scale velocity across a part -- a crescendo into the next section."""
    if not notes:
        return []
    span = max(float(n["start"]) for n in notes) or 1.0
    out = []
    for note in notes:
        progress = float(note["start"]) / span
        if curve == "exponential":
            progress = progress ** 2
        elif curve == "logarithmic":
            progress = progress ** 0.5
        factor = start + (end - start) * progress
        entry = dict(note)
        entry["velocity"] = int(max(1, min(127, float(note["velocity"]) * factor)))
        out.append(entry)
    return out


def kick_onsets(drum_notes: list[Note], kick_pitch: int = KICK_PITCH) -> list[float]:
    """Where the kick actually lands. Used to keep bass out of its way."""
    return sorted(
        {round(float(n["start"]), 4) for n in drum_notes if int(n["pitch"]) == kick_pitch}
    )


def duck_against(
    notes: list[Note],
    onsets: list[float],
    window: float = 0.12,
    mode: str = "avoid",
) -> list[Note]:
    """Make a part sit around a set of onsets -- almost always the kick.

    "avoid" removes notes that collide with a kick, which is what produces the
    rolling between-the-kicks bass that defines house and techno.
    "shorten" keeps them but clips them so they release before the kick lands.
    "lock" does the opposite and keeps only the notes that hit with the kick.
    """
    if not onsets:
        return [dict(n) for n in notes]

    def near(t: float) -> bool:
        return any(abs(t - o) < window for o in onsets)

    out: list[Note] = []
    for note in notes:
        start = float(note["start"])
        hit = near(start)
        if mode == "avoid":
            if hit:
                continue
            entry = dict(note)
        elif mode == "lock":
            if not hit:
                continue
            entry = dict(note)
        elif mode == "shorten":
            entry = dict(note)
            # Trim so the note releases before the next kick.
            later = [o for o in onsets if o > start]
            if later:
                entry["duration"] = max(0.05, min(float(note["duration"]),
                                                  later[0] - start - 0.02))
        else:
            entry = dict(note)
        out.append(entry)

    return out or [dict(n) for n in notes]
