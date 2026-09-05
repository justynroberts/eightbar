"""Scales, chords and voicings.

Everything here is pure arithmetic on MIDI note numbers -- no Ableton, no LLM.
The point is that the model asks for "a rising i-VI-III-VII in C minor" and this
module decides which notes that actually is, so the model never guesses pitches.
"""

from __future__ import annotations

from dataclasses import dataclass

# MIDI note 60 is C3 in Ableton's display (C4 in scientific pitch notation).
MIDDLE_C = 60

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_NOTE_ALIASES = {
    "DB": 1, "EB": 3, "FB": 4, "GB": 6, "AB": 8, "BB": 10, "CB": 11,
    "E#": 5, "B#": 0,
}

SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "ionian": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "natural_minor": (0, 2, 3, 5, 7, 8, 10),
    "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "pentatonic_major": (0, 2, 4, 7, 9),
    "pentatonic_minor": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "chromatic": tuple(range(12)),

    # Idioms outside pop and dance harmony.
    "whole_tone": (0, 2, 4, 6, 8, 10),
    "octatonic": (0, 2, 3, 5, 6, 8, 9, 11),          # diminished
    "hungarian_minor": (0, 2, 3, 6, 7, 8, 11),
    "double_harmonic": (0, 1, 4, 5, 7, 8, 11),       # byzantine
    "phrygian_dominant": (0, 1, 4, 5, 7, 8, 10),     # spanish / klezmer
    "lydian_dominant": (0, 2, 4, 6, 7, 9, 10),
    "altered": (0, 1, 3, 4, 6, 8, 10),               # jazz, over a dominant
    "bebop_dominant": (0, 2, 4, 5, 7, 9, 10, 11),
    "japanese": (0, 1, 5, 7, 8),                     # in scale
    "hirajoshi": (0, 2, 3, 7, 8),
    "egyptian": (0, 2, 5, 7, 10),
}

# Chord quality -> semitone offsets from the chord root.
CHORD_QUALITIES: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "diminished": (0, 3, 6),
    "augmented": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "major7": (0, 4, 7, 11),
    "minor7": (0, 3, 7, 10),
    "dominant7": (0, 4, 7, 10),
    "half_diminished7": (0, 3, 6, 10),
    "diminished7": (0, 3, 6, 9),
    "minor_major7": (0, 3, 7, 11),
    "major9": (0, 4, 7, 11, 14),
    "minor9": (0, 3, 7, 10, 14),
    "dominant9": (0, 4, 7, 10, 14),
    "minor11": (0, 3, 7, 10, 14, 17),
    "major6": (0, 4, 7, 9),
    "minor6": (0, 3, 7, 9),
    "power": (0, 7),
}

# Which triad quality sits on each degree of a seven-note scale.
_TRIADS_BY_SCALE: dict[str, tuple[str, ...]] = {
    "major": ("major", "minor", "minor", "major", "major", "minor", "diminished"),
    "minor": ("minor", "diminished", "major", "minor", "minor", "major", "major"),
    "harmonic_minor": (
        "minor", "diminished", "augmented", "minor", "major", "major", "diminished",
    ),
    "melodic_minor": (
        "minor", "minor", "augmented", "major", "major", "diminished", "diminished",
    ),
    "dorian": ("minor", "minor", "major", "major", "minor", "diminished", "major"),
    "phrygian": ("minor", "major", "major", "minor", "diminished", "major", "minor"),
    "lydian": ("major", "major", "minor", "diminished", "major", "minor", "minor"),
    "mixolydian": ("major", "minor", "diminished", "major", "minor", "minor", "major"),
    "locrian": ("diminished", "major", "minor", "minor", "major", "major", "minor"),
}

# Named progressions, written as scale degrees (1-based).
#
# These are the ones that actually recur in dance music. Degrees are relative
# to the chosen scale, so "edm" in C minor gives Cm-Ab-Eb-Bb (i-VI-III-VII) --
# the single most common progression in the genre -- and the same name in a
# major scale gives its major-mode equivalent.
PROGRESSIONS: dict[str, tuple[int, ...]] = {
    # -- the workhorses -------------------------------------------------
    "edm":              (1, 6, 3, 7),   # i-VI-III-VII: the EDM progression
    "axis":             (1, 5, 6, 4),   # I-V-vi-IV: the "four chords" loop
    "sensitive":        (6, 4, 1, 5),   # vi-IV-I-V: longing, vocal-led
    "fifties":          (1, 6, 4, 5),
    "andalusian":       (1, 7, 6, 5),   # descending, dark
    "pop_minor":        (1, 6, 3, 7),
    "epic_minor":       (6, 4, 1, 5),

    # -- house and tech house -------------------------------------------
    "house_classic":    (1, 4, 5, 4),
    "deep_house":       (1, 7, 6, 7),
    "rolling":          (1, 7, 3, 7),
    "jazzy_house":      (2, 5, 1, 1),   # ii-V-I, use with seventh/ninth
    "garage":           (1, 4, 6, 5),

    # -- trance and progressive -----------------------------------------
    "trance_uplift":    (6, 4, 1, 5),
    "trance_classic":   (1, 6, 3, 7),
    "trance_minor":     (1, 7, 6, 7),
    "emotional":        (1, 3, 6, 4),
    "hands_up":         (6, 7, 1, 1),   # VI-VII-i: the big lift home
    "anthem":           (1, 6, 7, 1),

    # -- the trance progression library, named by shape ------------------
    # From a trance construction ruleset. Several share chords and differ
    # only in where they START -- the entry degree sets the feeling more than
    # the chord choice. Quality is diatonic-from-scale, so in a minor key
    # (1,5,6,4) sounds as i-v-VI-iv automatically.
    "workhorse_1564":   (1, 5, 6, 4),   # i-v-VI-iv: neutral, always works
    "minor_axis":       (1, 6, 3, 7),   # the pop axis, uplifting
    "sine_edm":         (1, 7, 6, 7),   # descend then re-ascend
    "dark_turnaround":  (1, 6, 4),      # i-VI-iv: the 6 dropping to the hard iv
    "step_down":        (1, 7, 6, 5),   # descending; the 5 wants to be MAJOR
                                        # (harmonic-minor V) for the pull home
    "lydian_lift":      (6, 7, 1),      # starts on VI: the bright trance lift
    "children":         (6, 4, 1),      # VI-iv-i: same shape, much darker
    "dorian_lift":      (4, 6, 1),      # starts on iv, ascends home
    "mixolydian_lift":  (7, 1, 3, 1),   # starts unresolved on VII, lands home

    # -- darker / techno -------------------------------------------------
    "hypnotic":         (1, 1, 7, 1),
    "minor_climb":      (1, 3, 4, 6),
    "descending":       (1, 7, 6, 5),
    "dorian_vamp":      (1, 4, 1, 4),   # pair with the dorian scale
    "phrygian_dark":    (1, 2, 1, 7),   # pair with phrygian

    # -- progressive / melodic house ------------------------------------
    # These want extended chords: play them as sevenths or ninths, not triads.
    "prog_classic":     (1, 6, 4, 5),   # I-vi-IV-V, the progressive cornerstone
    "prog_lift":        (6, 4, 1, 5),
    "prog_suspended":   (1, 4, 1, 5),   # pair with sus2 / add9 colour
    "anjuna":           (6, 4, 5, 1),   # melodic-deep staple, minor 9ths
    "floating":         (1, 3, 6, 4),
    "night_drive":      (6, 7, 1, 3),

    # -- future bass / melodic ------------------------------------------
    "future_bass":      (4, 5, 3, 6),
    "melodic_dubstep":  (6, 4, 1, 5),
    "wistful":          (1, 5, 6, 4),

    # -- longer forms ----------------------------------------------------
    "twelve_bar_blues": (1, 1, 1, 1, 4, 4, 1, 1, 5, 4, 1, 1),
    "eight_bar_minor":  (1, 6, 3, 7, 1, 6, 4, 5),
    "two_five_one":     (2, 5, 1, 1),

    # --- outside dance music --------------------------------------------
    # Classical and cinematic writing leans on cadences and stepwise bass,
    # not on four bars that loop. These are the moves that do the work.
    "pachelbel": (1, 5, 6, 3, 4, 1, 4, 5),   # the canon, still undefeated
    "romanesca": (3, 7, 1, 5, 6, 3, 4, 5),   # renaissance ground bass
    "lament": (1, 7, 6, 5),                  # descending tetrachord, minor
    "folia": (1, 5, 1, 7, 3, 7, 1, 5),
    "passamezzo": (1, 7, 1, 5, 1, 7, 1, 5),
    "plagal": (1, 4, 1),                     # the amen cadence
    "authentic": (1, 4, 5, 1),
    "deceptive": (1, 4, 5, 6),               # the resolution that is withheld
    "circle_of_fifths": (6, 2, 5, 1, 4, 7, 3, 6),
    "neapolitan": (1, 4, 2, 5, 1),
    "picardy": (1, 6, 4, 5),                 # minor throughout, major at the end
    "hymn": (1, 4, 1, 5, 1, 4, 5, 1),
    "baroque_sequence": (1, 4, 7, 3, 6, 2, 5, 1),
    # Cinematic: suspended, slow-moving, resolving late.
    "cinematic_rise": (1, 6, 4, 5, 1, 6, 4, 7),
    "epic_build": (1, 1, 6, 6, 4, 4, 5, 5),
    "heroic": (1, 5, 6, 4),
    "tension": (1, 2, 1, 7),
    "unresolved": (1, 4, 1, 2),
    "modal_drone": (1, 7, 1, 4),
    "aeolian_fall": (1, 7, 6, 7),
    "trailer_stabs": (1, 1, 7, 7, 6, 6, 5, 5),
    # Jazz and song forms.
    "rhythm_changes": (1, 6, 2, 5),
    "coltrane_ish": (1, 3, 6, 2, 5),
    "minor_two_five": (2, 5, 1, 1),
    "blues_jazz": (1, 4, 1, 5, 4, 1),
    "doo_wop": (1, 6, 4, 5),
    "canon_pop": (1, 5, 6, 4),
    "folk": (1, 4, 5, 4),
}

# What each progression is for, so the model can choose on musical grounds
# rather than picking the first name it recognises.
PROGRESSION_NOTES: dict[str, str] = {
    "edm": "The default. Dark but uplifting; works in any four-to-the-floor genre.",
    "axis": "Bright, familiar, instantly singable. Best in a major scale.",
    "sensitive": "Starts on the relative minor -- longing, good under vocals.",
    "andalusian": "Steadily descending, tense; flamenco origin, common in dark techno.",
    "house_classic": "Simple and functional, leaves room for the groove to carry it.",
    "deep_house": "Two-chord sway, understated. Use sevenths and ninths.",
    "rolling": "Barely moves -- built for a rolling bassline to do the work.",
    "jazzy_house": "ii-V-I. Needs seventh or ninth chords or it sounds plain.",
    "trance_uplift": "The euphoric one. Big pads, long chords, high register.",
    "hands_up": "VI-VII-i. A hard lift resolving home; classic drop payoff.",
    "emotional": "Wistful and open; suits breakdowns more than drops.",
    "hypnotic": "Nearly static. For techno where timbre, not harmony, develops.",
    "dorian_vamp": "Pair with the dorian scale for a brighter minor.",
    "phrygian_dark": "Pair with phrygian; the b2 makes it genuinely menacing.",
    "future_bass": "Starts away from the tonic, so it never quite settles.",
    "prog_classic": "The progressive cornerstone. Use sevenths or ninths, not triads.",
    "anjuna": "Melodic-deep staple. Minor 9ths, open voicing, high register.",
    "prog_suspended": "Barely moves; the colour comes from sus2 and add9, not the roots.",
    "floating": "Never lands hard on the tonic. Good under a long breakdown.",
    "eight_bar_minor": "Eight bars, so a section can breathe before repeating.",
}

ALIASES = {
    "natural minor": "minor",
    "harmonic minor": "harmonic_minor",
    "melodic minor": "melodic_minor",
    "major pentatonic": "pentatonic_major",
    "minor pentatonic": "pentatonic_minor",
    "maj": "major",
    "min": "minor",
    "m": "minor",
}


def normalise_scale(name: str) -> str:
    key = (name or "minor").strip().lower().replace("-", "_")
    key = ALIASES.get(key.replace("_", " "), key)
    if key not in SCALES:
        raise ValueError(
            f"unknown scale {name!r}; try one of: {', '.join(sorted(SCALES))}"
        )
    return key


def note_to_pitch_class(note: str) -> int:
    """'C' -> 0, 'Eb' -> 3, 'F#' -> 6."""
    text = (note or "C").strip().upper().replace("♯", "#").replace("♭", "B")
    if text in _NOTE_ALIASES:
        return _NOTE_ALIASES[text]
    if text in NOTE_NAMES:
        return NOTE_NAMES.index(text)
    # Handle a trailing octave digit, e.g. "C3".
    stripped = text.rstrip("-0123456789")
    if stripped in _NOTE_ALIASES:
        return _NOTE_ALIASES[stripped]
    if stripped in NOTE_NAMES:
        return NOTE_NAMES.index(stripped)
    raise ValueError(f"unknown note name {note!r}")


def pitch_name(pitch: int) -> str:
    """60 -> 'C3', matching Ableton's octave numbering."""
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 2}"


def scale_pitches(root: str, scale: str, octave: int = 3, octaves: int = 2) -> list[int]:
    """Ascending scale tones across `octaves`, starting at the given octave."""
    intervals = SCALES[normalise_scale(scale)]
    base = note_to_pitch_class(root) + (octave + 2) * 12
    return [
        base + o * 12 + i
        for o in range(octaves)
        for i in intervals
    ]


def degree_to_pitch(root: str, scale: str, degree: int, octave: int = 3) -> int:
    """Scale degree (1-based, may exceed the scale length) to a MIDI pitch."""
    intervals = SCALES[normalise_scale(scale)]
    size = len(intervals)
    index = degree - 1
    octave_shift, step = divmod(index, size)
    base = note_to_pitch_class(root) + (octave + 2) * 12
    return base + octave_shift * 12 + intervals[step]


def triad_quality(scale: str, degree: int) -> str:
    """The diatonic chord quality on a degree, e.g. degree 1 of minor -> 'minor'."""
    key = normalise_scale(scale)
    table = _TRIADS_BY_SCALE.get(key)
    if table is None:
        # Pentatonic/blues/chromatic have no standard diatonic harmony; guess by ear.
        return "minor" if "minor" in key or key == "blues" else "major"
    return table[(degree - 1) % len(table)]


@dataclass(frozen=True)
class Chord:
    """A chord resolved to concrete MIDI pitches."""

    degree: int
    root_pitch: int
    quality: str
    pitches: tuple[int, ...]

    @property
    def name(self) -> str:
        suffix = {
            "major": "", "minor": "m", "diminished": "dim", "augmented": "aug",
            "major7": "maj7", "minor7": "m7", "dominant7": "7",
            "half_diminished7": "m7b5", "diminished7": "dim7",
            "sus2": "sus2", "sus4": "sus4", "power": "5",
            "major9": "maj9", "minor9": "m9", "dominant9": "9",
            "minor11": "m11", "major6": "6", "minor6": "m6",
            "minor_major7": "mMaj7",
        }.get(self.quality, self.quality)
        return f"{NOTE_NAMES[self.root_pitch % 12]}{suffix}"

    def describe(self) -> str:
        return f"{self.name} [{' '.join(pitch_name(p) for p in self.pitches)}]"


def build_chord(
    root: str,
    scale: str,
    degree: int,
    octave: int = 3,
    quality: str | None = None,
    extension: str = "triad",
    inversion: int = 0,
) -> Chord:
    """Build one diatonic chord on a scale degree.

    `extension` upgrades the default triad to "seventh" or "ninth"; `quality`
    overrides the diatonic quality outright when you want a borrowed chord.
    """
    scale_key = normalise_scale(scale)
    root_pitch = degree_to_pitch(root, scale_key, degree, octave)

    # Extensions are stacked from ACTUAL scale tones, not chosen by the
    # triad's quality name. A major triad on the bVII of a minor key (G in A
    # minor) takes a *dominant* seventh (F natural), not a major seventh
    # (F#) -- deriving the seventh from "this triad is major, so major7"
    # injected an out-of-key F# and made the chords sound dischordant against
    # a diatonic bass. Stacking diatonic degrees keeps every extension in key
    # by construction.
    diatonic_extension = quality is None and extension in ("seventh", "ninth",
                                                           "eleventh",
                                                           "thirteenth")
    if quality is None:
        quality = triad_quality(scale_key, degree)

    if diatonic_extension:
        wanted = {"seventh": (1, 3, 5, 7), "ninth": (1, 3, 5, 7, 9),
                  "eleventh": (1, 3, 5, 7, 9, 11),
                  "thirteenth": (1, 3, 5, 7, 9, 11, 13)}[extension]
        pitches = [
            degree_to_pitch(root, scale_key, degree + step - 1, octave)
            for step in wanted
        ]
    else:
        if quality not in CHORD_QUALITIES:
            raise ValueError(
                f"unknown chord quality {quality!r}; "
                f"try one of: {', '.join(sorted(CHORD_QUALITIES))}"
            )
        pitches = [root_pitch + i for i in CHORD_QUALITIES[quality]]
    for _ in range(inversion % max(1, len(pitches))):
        pitches = pitches[1:] + [pitches[0] + 12]

    return Chord(degree, root_pitch, quality, tuple(pitches))


def voice_lead(chords: list[Chord], centre: int = MIDDLE_C) -> list[Chord]:
    """Re-voice a progression so consecutive chords move as little as possible.

    Keeps the bass motion intact but inverts upper voicings to sit near `centre`,
    which is what stops a generated progression sounding like block-chord stabs
    jumping an octave at a time.
    """
    if not chords:
        return []

    result: list[Chord] = []
    previous: tuple[int, ...] | None = None

    for chord in chords:
        size = len(chord.pitches)
        best: tuple[int, ...] | None = None
        best_cost = float("inf")

        for inversion in range(size):
            voicing = list(chord.pitches)
            for _ in range(inversion):
                voicing = voicing[1:] + [voicing[0] + 12]
            for shift in (-12, 0, 12):
                candidate = tuple(p + shift for p in voicing)
                if min(candidate) < 24 or max(candidate) > 100:
                    continue
                drift = abs(sum(candidate) / size - centre)
                if previous is None:
                    cost = drift
                else:
                    # Semitone travel, plus the pull back toward centre. The
                    # pull has to be strong: weighted lightly, a four- or
                    # five-note extended chord minimises movement by sinking an
                    # octave, and the whole part ends up in the mud.
                    cost = sum(
                        min(abs(p - q) for q in previous) for p in candidate
                    ) + drift * 1.2
                if cost < best_cost:
                    best_cost, best = cost, candidate

        chosen = best or chord.pitches
        result.append(Chord(chord.degree, chord.root_pitch, chord.quality, chosen))
        previous = chosen

    return result


def build_progression(
    root: str,
    scale: str,
    degrees: list[int],
    octave: int = 3,
    extension: str = "triad",
    smooth: bool = True,
) -> list[Chord]:
    """Resolve a list of scale degrees into voiced chords."""
    chords = [
        build_chord(root, scale, d, octave=octave, extension=extension)
        for d in degrees
    ]
    return voice_lead(chords) if smooth else chords


def parse_degrees(spec: str | list[int]) -> list[int]:
    """Accept '1-6-4-5', '1,6,4,5', 'i-VI-IV-V' or a plain list."""
    if isinstance(spec, list):
        return [int(d) for d in spec]

    roman = {
        "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
    }
    out: list[int] = []
    for token in str(spec).replace(",", "-").replace(" ", "-").split("-"):
        token = token.strip()
        if not token:
            continue
        if token.isdigit():
            out.append(int(token))
        elif token.lower() in roman:
            out.append(roman[token.lower()])
        else:
            raise ValueError(f"cannot read scale degree {token!r}")
    if not out:
        raise ValueError(f"no scale degrees found in {spec!r}")
    return out
