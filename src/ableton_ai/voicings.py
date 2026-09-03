"""How a chord is spaced, which is most of what makes it sound good.

The notes in a chord matter far less than their arrangement. A close-position
Cm9 with every tone stacked inside an octave is mud; the same chord with the
root in the bass, the 7th dropped an octave and the 9th on top is the sound of
every progressive house record ever made.

The rules encoded here are the conventional ones:

  Omit the 5th in extended chords. It contributes almost nothing above a 7th
  and it crowds the middle where the vocal and lead live.
  Omit the 3rd under an 11th. A major 3rd against a natural 11 is a semitone
  clash; either drop the 3rd or write it as a sus.
  Drop the 7th an octave. Moving one inner voice down opens the chord and turns
  a block into something with a shape.
  Go rootless when there is a bassline. The bass already states the root, so
  repeating it in the chord just doubles the mud.
  Keep the top voice moving. Inversions across bars matter more than the chord
  symbols do -- the top note is the line people actually hear.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import theory
from .theory import Chord

# Extensions as scale steps above the root, in the order they are added.
# 9 = 14 semitones (a 2nd up an octave), 11 = 17, 13 = 21.
EXTENSION_LADDER: dict[str, tuple[str, ...]] = {
    "triad": (),
    "add9": ("9",),
    "sixth": ("6",),
    "seventh": ("7",),
    "ninth": ("7", "9"),
    "eleventh": ("7", "9", "11"),
    "thirteenth": ("7", "9", "13"),
}

# How far above the root each extension sits, in semitones, for a minor and a
# major chord. The 7th differs by quality; the rest are shared.
_MINOR_INTERVALS = {"6": 9, "7": 10, "9": 14, "11": 17, "13": 21}
_MAJOR_INTERVALS = {"6": 9, "7": 11, "9": 14, "11": 17, "13": 21}
_DOMINANT_INTERVALS = {"6": 9, "7": 10, "9": 14, "11": 17, "13": 21}


@dataclass(frozen=True)
class VoicingStyle:
    name: str
    rootless: bool
    drop_seventh: bool
    spread: bool
    quartal: bool
    max_notes: int
    description: str


STYLES: dict[str, VoicingStyle] = {
    "close": VoicingStyle(
        "close", False, False, False, False, 5,
        "Stacked inside an octave. Simple and tight; muddy below middle C.",
    ),
    "open": VoicingStyle(
        "open", False, True, True, False, 5,
        "Root low, the rest spread above with the 7th dropped. The default.",
    ),
    "rootless": VoicingStyle(
        "rootless", True, False, False, False, 4,
        "No root -- the bassline states it. Clears the low mids completely.",
    ),
    "shell": VoicingStyle(
        "shell", False, False, True, False, 3,
        "Root, 3rd and 7th only. The essential notes; leaves room for a vocal.",
    ),
    "spread": VoicingStyle(
        "spread", False, True, True, False, 5,
        "Wide -- root well below the upper structure. Pads and breakdowns.",
    ),
    "quartal": VoicingStyle(
        "quartal", True, False, True, True, 4,
        "Stacked fourths rather than thirds. Ambiguous and modern.",
    ),
    "cluster": VoicingStyle(
        "cluster", True, False, False, False, 4,
        "Tight upper-structure notes. Tense; use high and quiet.",
    ),
}

ALIASES = {
    "simple": "close", "basic": "close", "block": "close",
    "default": "open", "wide": "spread", "pad": "spread",
    "jazz": "rootless", "deep": "rootless", "progressive": "open",
    "minimal": "shell", "sparse": "shell",
    "modern": "quartal", "fourths": "quartal",
}


def resolve(name: str) -> VoicingStyle:
    key = (name or "open").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in STYLES:
        raise ValueError(
            f"unknown voicing {name!r}; try one of: "
            f"{', '.join(sorted(set(STYLES) | set(ALIASES)))}"
        )
    return STYLES[key]


def _intervals_for(quality: str) -> dict[str, int]:
    if "major" in quality or quality in ("augmented", "sus2", "sus4"):
        return _MAJOR_INTERVALS
    if "dominant" in quality:
        return _DOMINANT_INTERVALS
    return _MINOR_INTERVALS


# Chord-quality names read exactly like extension names, and the difference is
# an implementation detail nobody outside this file should have to know. Map
# the obvious ones rather than refusing them.
EXTENSION_ALIASES: dict[str, str] = {
    "minor9": "ninth", "major9": "ninth", "dominant9": "ninth", "min9": "ninth",
    "maj9": "ninth", "dom9": "ninth", "9": "ninth", "9th": "ninth",
    "minor7": "seventh", "major7": "seventh", "dominant7": "seventh",
    "min7": "seventh", "maj7": "seventh", "dom7": "seventh",
    "7": "seventh", "7th": "seventh",
    "minor11": "eleventh", "major11": "eleventh", "11": "eleventh",
    "11th": "eleventh",
    "minor13": "thirteenth", "major13": "thirteenth", "13": "thirteenth",
    "13th": "thirteenth",
    "minor6": "sixth", "major6": "sixth", "6": "sixth", "6th": "sixth",
    "add2": "add9", "added9": "add9",
    "none": "triad", "plain": "triad", "simple": "triad", "3": "triad",
}


def normalise_extension(name: str) -> str | None:
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in EXTENSION_LADDER:
        return key
    return EXTENSION_ALIASES.get(key)


def extension_vocabulary() -> list[str]:
    return sorted(set(EXTENSION_LADDER) | set(EXTENSION_ALIASES))


def extend(chord: Chord, extension: str = "triad",
           key: str | None = None, scale: str | None = None) -> list[int]:
    """Add extensions to a chord and drop the tones that crowd them.

    Returns absolute pitches, unvoiced -- spacing is applied separately.

    When `key` and `scale` are given, the added tones are the actual scale
    degrees a seventh/ninth/etc above the root -- diatonic by construction.
    This matters: a major triad on the bVII of a minor key (G in A minor)
    takes a flat seventh (F natural), not the major seventh (F#) its triad
    quality alone would imply, and getting that wrong put an out-of-key note
    in every extended chord. Without the key it falls back to quality-based
    intervals, which is right for a chord considered in isolation.
    """
    from . import theory

    resolved = normalise_extension(extension)
    if resolved is None:
        raise ValueError(
            f"unknown extension {extension!r}; one of: "
            f"{', '.join(EXTENSION_LADDER)}"
        )
    extension = resolved

    root = chord.root_pitch
    triad = list(chord.pitches[:3])
    wanted = EXTENSION_LADDER[extension]

    pitches = list(triad)
    if key is not None and scale is not None:
        # Stack real scale tones a 7th/9th/11th/13th above the chord root.
        step_for = {"7": 6, "9": 8, "11": 10, "13": 12}
        octave = (root // 12) - 2
        for name in wanted:
            offset = step_for.get(name)
            if offset is None:
                continue
            pitch = theory.degree_to_pitch(
                key, scale, chord.degree + offset, octave
            )
            while pitch <= root:
                pitch += 12
            pitches.append(pitch)
    else:
        intervals = _intervals_for(chord.quality)
        for name in wanted:
            pitches.append(root + intervals[name])

    # The 5th is the first thing to go once a 7th is present: it adds no colour
    # and it sits exactly where the mix is most crowded.
    if wanted and len(pitches) > 3:
        fifth = root + 7
        if fifth in pitches and len(pitches) > 3:
            pitches.remove(fifth)

    # A natural 11 a semitone above a major 3rd is a clash, not a colour.
    if "11" in wanted:
        third = root + (4 if "major" in chord.quality or "dominant" in chord.quality
                        else 3)
        if third in pitches and "major" in chord.quality:
            pitches.remove(third)

    # A 13th chord with an 11th in it is unusably dense.
    if "13" in wanted and (root + 17) in pitches:
        pitches.remove(root + 17)

    return sorted(set(pitches))


def voice(
    pitches: list[int],
    style: str = "open",
    centre: int = 60,
    bass_octave: int | None = None,
    quality: str = "minor",
) -> list[int]:
    """Space a set of chord tones according to a voicing style."""
    spec = resolve(style)
    if not pitches:
        return []

    working = sorted(pitches)
    root = working[0]
    intervals = _intervals_for(quality)
    third = root + (4 if "major" in quality or "dominant" in quality else 3)
    seventh = root + intervals["7"]

    # A shell voicing is defined by *which* notes it keeps: root, 3rd and 7th.
    # Thinning it generically drops the 7th, which is the whole point of it.
    if spec.name == "shell":
        shell = [p for p in (root, third, seventh) if p is not None]
        working = sorted(set(shell))

    if spec.rootless and len(working) > 2:
        working = [p for p in working if p != root] or working

    if spec.quartal:
        # Stack fourths, but snap each to the nearest tone the chord actually
        # contains -- a chromatic fourths stack leaves the harmony entirely.
        available = sorted({p % 12 for p in pitches})
        base = working[0]
        stacked = []
        candidate = base
        for _ in range(min(spec.max_notes, max(3, len(working)))):
            nearest = min(
                (candidate + d for d in range(-2, 3)),
                key=lambda p: (
                    0 if p % 12 in available else 1, abs(p - candidate)
                ),
            )
            stacked.append(nearest)
            candidate = nearest + 5
        working = sorted(set(stacked))

    if spec.drop_seventh and len(working) >= 3:
        # Drop the second voice from the top an octave -- the drop-2 move.
        working = sorted(working)
        target = working[-2]
        working.remove(target)
        working.append(target - 12)
        working.sort()

    if len(working) > spec.max_notes and spec.name != "shell":
        # Keep the outer voices and thin the middle; the edges carry the sound.
        keep = [working[0], working[-1]]
        middle = working[1:-1]
        step = max(1, len(middle) // max(1, spec.max_notes - 2))
        keep.extend(middle[::step][: spec.max_notes - 2])
        working = sorted(set(keep))

    # Sit the whole thing around the target register.
    if working:
        current = sum(working) / len(working)
        shift = round((centre - current) / 12) * 12
        working = [p + shift for p in working]

    if spec.spread and bass_octave is not None:
        # Put the root well below the upper structure rather than adjacent.
        low = root
        while low >= (bass_octave + 3) * 12:
            low -= 12
        while low < (bass_octave + 2) * 12:
            low += 12
        working = sorted({low, *[p for p in working if p > low + 7]})

    return [max(0, min(127, p)) for p in sorted(set(working))]


def build_progression(
    root: str,
    scale: str,
    degrees: list[int],
    extension: str = "seventh",
    style: str = "open",
    octave: int = 3,
    smooth: bool = True,
) -> list[Chord]:
    """Build an extended, voiced progression.

    `extension` is the complexity dial -- triad, add9, sixth, seventh, ninth,
    eleventh, thirteenth. `style` is the spacing.
    """
    base = theory.build_progression(root, scale, degrees, octave=octave,
                                    smooth=False)
    centre = (octave + 2) * 12 + 4

    voiced: list[Chord] = []
    for chord in base:
        pitches = voice(extend(chord, extension), style=style, centre=centre,
                        quality=chord.quality)
        voiced.append(Chord(chord.degree, chord.root_pitch, chord.quality,
                            tuple(pitches)))

    # Voice leading still applies on top: the top note is the line people hear.
    return theory.voice_lead(voiced, centre=centre) if smooth else voiced


def describe() -> dict:
    return {
        "extensions": {
            name: ("triad" if not tones else " + ".join(tones))
            for name, tones in EXTENSION_LADDER.items()
        },
        "styles": {n: s.description for n, s in STYLES.items()},
        "aliases": ALIASES,
    }
