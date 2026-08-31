"""What popular melodies actually do, encoded from the songs that proved it.

The motif engine develops its material -- restate, sequence, invert -- which is
classical practice. Popular melody writing does something simpler and harder:
it repeats *literally*. The same pitches return over changing chords, and the
harmony moving underneath a fixed line is what creates the emergent colour --
the note that was a root becomes a seventh, the third becomes a ninth. Chord-
anchored "repetition" that transposes with the harmony (what the motif engine
does) destroys exactly the thing that makes a hook memorable.

The patterns below are the shapes that recur across decades of hits, written
as scale degrees relative to the tonic (negative = below) and 16-step rhythm
strings. They are not transcriptions of any one song; they are the shared
skeletons dozens of songs hang on, the way "four on the floor" is nobody's
drum pattern and everybody's.

The other rules encoded here are as settled as counterpoint:

  gap-fill        after a leap, the line turns back stepwise (Meyer's rule --
                  a leap opens a gap, the ear expects it filled)
  anticipation    hitting the chord an eighth early is most of what "pop
                  phrasing" means; on-the-grid downbeats read as stiff
  AABA'           state it, state it again, answer it, come home changed.
                  Two literal repeats minimum before anything varies.
  small range     hit hooks live inside a sixth or so; singability is the
                  filter even when nobody sings
"""

from __future__ import annotations

import random
from typing import Any, Sequence

from . import theory

BEATS_PER_BAR = 4.0
SIXTEENTH = 0.25
Note = dict[str, Any]

# ------------------------------------------------------------- the catalog
#
# degrees: scale degrees relative to the tonic of the key (1 = tonic in the
# hook's octave, 0 = leading tone below, negative continues down). rhythm:
# a 16-step string per bar of the phrase, "x" = onset, "X" = accent, "-" =
# held, "." = rest. Both describe ONE phrase (1-2 bars); rendering repeats it.

HOOK_PATTERNS: dict[str, dict[str, Any]] = {
    # Down from the fifth: the single most common hook skeleton there is.
    # Dance anthems, stadium pop, folk -- the 5-4-3 fall onto a held 1.
    "falling_fifth": {
        "styles": ("anthem", "pop", "trance", "house"),
        "degrees": (5, 4, 3, 1),
        "rhythm": "X..x..x.X-------",
        "feel": "resolved, singable, open-air",
    },
    # The 1-2-3 climb answered by falling back: question up, answer down.
    "climb_and_fall": {
        "styles": ("pop", "progressive", "melodic_techno"),
        "degrees": (1, 2, 3, 2, 1),
        "rhythm": "X..x..x.x..x....",
        "feel": "gentle, narrative",
    },
    # Oscillating 5-3: two notes, all rhythm. Whole choruses run on this.
    "two_note_engine": {
        "styles": ("edm", "pop", "big_room", "future_bass"),
        "degrees": (5, 3, 5, 3, 5, 6, 5, 3),
        "rhythm": "X.x.X.x.X.x.X.x.",
        "feel": "chant, festival",
    },
    # The minor pentatonic circle: 1-b3-4-b3-1, endlessly loopable.
    "penta_loop": {
        "styles": ("house", "uk_garage", "dark_pop", "trap"),
        "degrees": (1, 3, 4, 3, 1, 0),
        "rhythm": "X..x.x..x..x.x..",
        "feel": "circular, hypnotic",
    },
    # Root octave leap, filled stepwise on the way down: the gap-fill shape
    # itself, and the backbone of countless drops.
    "leap_and_fill": {
        "styles": ("trance", "anthem", "progressive"),
        "degrees": (1, 8, 7, 6, 5),
        "rhythm": "X..X...x.x.x----",
        "feel": "heroic, arriving",
    },
    # Sit on the fifth, decorate with the sixth above and fourth below:
    # the static hook that lets the chords do the emotional work.
    "drone_fifth": {
        "styles": ("melodic_techno", "deep_house", "ambient", "score"),
        "degrees": (5, 6, 5, 4, 5),
        "rhythm": "X...x..x..x.....",
        "feel": "still, chords carry the colour",
    },
    # 3-2-1 sighs, twice: the lament shape at hook scale.
    "descending_sigh": {
        "styles": ("dark_pop", "melodic_techno", "cinematic", "rnb"),
        "degrees": (3, 2, 1, 3, 2, 1),
        "rhythm": "x.x.X...x.x.X...",
        "feel": "wistful, falling",
    },
    # Up the arpeggio, land on the ninth: bright modern dance-pop.
    "arpeggio_reach": {
        "styles": ("future_bass", "pop", "progressive"),
        "degrees": (1, 3, 5, 9, 8),
        "rhythm": "x.x.x.X.--------",
        "feel": "lifted, sparkling",
    },
    # The 6-5 hang: never states the tonic, floats on the pre-resolution.
    "suspended_six": {
        "styles": ("deep_house", "rnb", "lo_fi", "chill"),
        "degrees": (6, 5, 6, 5, 4, 5),
        "rhythm": "X..x....X..x....",
        "feel": "unresolved on purpose",
    },
    # Call on 1-4, response on 5-1: the oldest question/answer there is.
    "call_answer": {
        "styles": ("house", "garage", "pop", "funk"),
        "degrees": (1, 4, 3, 5, 4, 1),
        "rhythm": "X.x..X..x.x..X..",
        "feel": "conversational, bouncing",
    },
}


def catalog(style: str | None = None) -> dict[str, dict]:
    """The patterns, optionally filtered to a style word."""
    if style is None:
        return {k: {a: b for a, b in v.items() if a != "degrees"}
                for k, v in HOOK_PATTERNS.items()}
    wanted = style.lower()
    out = {
        k: {a: b for a, b in v.items() if a != "degrees"}
        for k, v in HOOK_PATTERNS.items()
        if any(wanted in s or s in wanted for s in v["styles"])
    }
    return out or catalog(None)


def pattern_for(style: str | None, seed: int | None = None) -> str:
    """Pick a pattern that suits the style, deterministically per seed."""
    names = sorted(catalog(style))
    rng = random.Random(seed)
    return names[rng.randrange(len(names))]


# --------------------------------------------------------------- rendering

def _degree_to_pitch(degree: int, root: str, scale: str, octave: int) -> int:
    """A tonic-relative degree to MIDI, degrees continuing below 1 and above 7."""
    scale_key = theory.normalise_scale(scale)
    intervals = theory.SCALES[scale_key]
    size = len(intervals)
    step = degree - 1
    octaves, index = divmod(step, size)
    return (theory.note_to_pitch_class(root)
            + (octave + 1 + octaves) * 12
            + intervals[index])


def _onsets(rhythm: str) -> list[tuple[float, float, bool]]:
    """(start, duration, accented) per onset of a 16-step string."""
    out = []
    for index, symbol in enumerate(rhythm):
        if symbol in "xX":
            length = SIXTEENTH
            probe = index + 1
            while probe < len(rhythm) and rhythm[probe] == "-":
                length += SIXTEENTH
                probe += 1
            out.append((index * SIXTEENTH, length, symbol == "X"))
    return out


def gap_fill(pitches: list[int], root: str, scale: str) -> list[int]:
    """After a leap, turn back stepwise -- Meyer's rule, applied gently.

    A leap of more than a fourth followed by *another* move in the same
    direction reads as disjointed; hit melodies leap once and then walk back
    into the gap. Only the note after the leap is touched.
    """
    scale_key = theory.normalise_scale(scale)
    root_class = theory.note_to_pitch_class(root)
    in_scale = sorted({(root_class + i) % 12 for i in theory.SCALES[scale_key]})

    def step_back(from_pitch: int, direction: int) -> int:
        probe = from_pitch + direction
        while probe % 12 not in in_scale:
            probe += direction
        return probe

    out = list(pitches)
    for i in range(1, len(out) - 1):
        leap = out[i] - out[i - 1]
        follow = out[i + 1] - out[i]
        if abs(leap) > 5 and follow != 0 and (follow > 0) == (leap > 0):
            # Same direction after a big leap: fill the gap instead.
            out[i + 1] = step_back(out[i], -1 if leap > 0 else 1)
    return out


def render_hook(
    root: str,
    scale: str,
    chords: Sequence[theory.Chord],
    bars: float = 8,
    pattern: str | None = None,
    style: str | None = None,
    octave: int = 5,
    velocity: int = 104,
    anticipate: bool = True,
    seed: int | None = None,
) -> list[Note]:
    """A hook the popular way: literal repeats over the moving harmony.

    The phrase is stated with the SAME pitches every time -- the chords
    changing under a fixed line is what makes the colour, and re-anchoring
    the melody to each chord (what motif development does) is precisely what
    makes generated hooks forgettable. Shape: A A A A' -- the final statement
    alone bends its last notes to cadence on a chord tone of the closing
    harmony.

    `anticipate` pushes phrase-crossing chord arrivals an eighth early,
    which is most of what "pop phrasing" means.
    """
    rng = random.Random(seed)
    name = pattern or pattern_for(style, seed)
    if name not in HOOK_PATTERNS:
        raise ValueError(
            f"unknown hook pattern {name!r}; one of: "
            f"{', '.join(sorted(HOOK_PATTERNS))}"
        )
    spec = HOOK_PATTERNS[name]

    pitches = [_degree_to_pitch(d, root, scale, octave)
               for d in spec["degrees"]]
    pitches = gap_fill(pitches, root, scale)
    onsets = _onsets(spec["rhythm"])
    phrase_bars = max(1, len(spec["rhythm"]) // 16)

    statements = max(1, int(bars / phrase_bars))
    total_beats = bars * BEATS_PER_BAR

    # Which chord sounds at a beat, for the cadence bend at the end.
    def chord_at(beat: float) -> theory.Chord:
        span = total_beats / max(1, len(chords))
        return chords[min(len(chords) - 1, int(beat / span))]

    notes: list[Note] = []
    for statement in range(statements):
        base = statement * phrase_bars * BEATS_PER_BAR
        last = statement == statements - 1
        for position, (start, duration, accented) in enumerate(onsets):
            index = position % len(pitches)
            pitch = pitches[index]
            at = base + start

            # The anticipation: an onset landing exactly on a later downbeat
            # is pushed an eighth early. Never the very first note -- the
            # first downbeat is the anchor the pushes play against.
            if anticipate and at > 0 and (at % BEATS_PER_BAR) == 0.0:
                at -= 0.5
                duration += 0.5

            if at >= total_beats:
                continue

            # A' -- only the final statement changes, and only its tail: the
            # last two notes bend to the nearest tone of the closing chord,
            # so the hook cadences instead of stopping.
            if last and position >= len(onsets) - 2:
                closing = chord_at(at)
                pitch = min(
                    closing.pitches,
                    key=lambda p: abs(
                        (p + 12 * round((pitch - p) / 12)) - pitch
                    ),
                )
                pitch += 12 * round((pitches[index] - pitch) / 12)

            notes.append({
                "pitch": int(max(0, min(127, pitch))),
                "start": round(at, 4),
                "duration": round(min(duration, total_beats - at), 4),
                "velocity": int(max(1, min(127,
                    velocity + (8 if accented else 0)
                    + rng.uniform(-2, 2)))),
            })

    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes
