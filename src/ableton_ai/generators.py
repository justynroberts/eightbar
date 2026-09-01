"""Turn musical intent into concrete MIDI note lists.

A "note" here is the dict shape the remote script expects:
    {"pitch": 60, "start": 0.0, "duration": 1.0, "velocity": 100}
Times are in beats from the start of the clip.
"""

from __future__ import annotations

import random

from . import groove as groove_mod
from . import motif, theory
from .theory import Chord

BEATS_PER_BAR = 4.0
SIXTEENTH = 0.25

Note = dict[str, float | int | bool]

# Ableton's default Drum Rack layout (C1 = 36 = kick).
DRUM_MAP: dict[str, int] = {
    "kick": 36,
    "rim": 37,
    "snare": 38,
    "clap": 39,
    "snare2": 40,
    "hat": 42,
    "closed_hat": 42,
    "pedal_hat": 44,
    "open_hat": 46,
    "low_tom": 41,
    "mid_tom": 45,
    "high_tom": 48,
    "crash": 49,
    "ride": 51,
    "shaker": 70,
    "cowbell": 56,
    "perc": 63,
}

# One bar of sixteenths. 'x' = hit, '.' = rest, 'o' = accent, 'g' = ghost.
DRUM_PATTERNS: dict[str, dict[str, str]] = {
    "four_on_floor": {
        "kick":  "x...x...x...x...",
        "clap":  "....x.......x...",
        "hat":   "..x...x...x...x.",
    },
    "house": {
        "kick":      "x...x...x...x...",
        "clap":      "....x.......x...",
        "closed_hat": "..x...x...x...x.",
        "open_hat":  "..............x.",
    },
    "tech_house": {
        "kick":      "x...x...x...x...",
        "clap":      "....x.......x...",
        "closed_hat": "..x.x.x...x.x.x.",
        "open_hat":  "......x.......x.",
        "shaker":    "x.x.x.x.x.x.x.x.",
    },
    "techno": {
        "kick":      "x...x...x...x...",
        "closed_hat": "..x...x...x...x.",
        "open_hat":  "......x.......x.",
        "perc":      "...x......x.....",
    },
    "deep_house": {
        "kick":      "x...x...x...x...",
        "clap":      "....x.......x...",
        "closed_hat": "..x...x...x...x.",
        "shaker":    ".x.x.x.x.x.x.x.x",
    },
    "breakbeat": {
        "kick":  "x.....x..x......",
        "snare": "....x.......x...",
        "hat":   "x.x.x.x.x.x.x.x.",
    },
    "dnb": {
        "kick":  "x.........x.....",
        "snare": "....x.......x...",
        "hat":   "x.x.x.x.x.x.x.x.",
    },
    "hip_hop": {
        "kick":  "x.......x.x.....",
        "snare": "....x.......x...",
        "hat":   "x.x.x.x.x.x.x.x.",
    },
    "trap": {
        "kick":  "x.....x...x.....",
        "snare": "........x.......",
        "hat":   "x.xxx.xxx.xxx.xx",
    },
    "rock": {
        "kick":  "x...x...x...x...",
        "snare": "....x.......x...",
        "hat":   "x.x.x.x.x.x.x.x.",
    },
    "disco": {
        "kick":     "x...x...x...x...",
        "snare":    "....x.......x...",
        "open_hat": "..x...x...x...x.",
    },
    "minimal": {
        "kick": "x...x...x...x...",
        "rim":  "..........x.....",
    },
}

# Single-element patterns. "offbeat hats" is a thing people ask for and the
# table only held whole kits, so the request had nowhere to land.
DRUM_PATTERNS.update({
    "offbeat_hats":   {"open_hat":   "..x...x...x...x."},
    "closed_hats":    {"closed_hat": "x.x.x.x.x.x.x.x."},
    "sixteenth_hats": {"closed_hat": "xxxxxxxxxxxxxxxx"},
    "offbeat_kick":   {"kick":       "..x...x...x...x."},
    "kick_only":      {"kick":       "x...x...x...x..."},
    "clap_backbeat":  {"clap":       "....x.......x..."},
    "shaker":         {"shaker":     "x.x.x.x.x.x.x.x."},
    "ride":           {"ride":       "..x...x...x...x."},
    "percussion":     {"perc":       "..x..x..x..x.x..",
                       "rim":        "....x.......x..."},
    # Dembow: reggaeton's spine -- and via dancehall, the groove under a
    # remarkable share of modern pop.
    "reggaeton":      {"kick":       "x...x...x...x...",
                       "snare":      "...x..x....x..x.",
                       "closed_hat": "x.x.x.x.x.x.x.x."},
    # Afrobeats: kick off the grid, rim carrying the tresillo.
    "afrobeats":      {"kick":       "x..x..x...x..x..",
                       "rim":        "..x..x.x..x..x..",
                       "shaker":     "x.x.x.x.x.x.x.x."},
    # Amapiano: the log drum is the melody-adjacent bass voice; here the
    # kit is sparse kick, late claps and running shaker under it.
    "amapiano":       {"kick":       "x......x..x.....",
                       "clap":       "....x.......x..x",
                       "shaker":     "x.xxx.xxx.xxx.xx"},
    # UK garage 2-step: the kick skips beat three entirely.
    "two_step":       {"kick":       "x.....x...x.....",
                       "snare":      "....x.......x...",
                       "closed_hat": "x.x.x.xxx.x.x.xx"},
    "half_time":      {"kick":       "x.......x.......",
                       "clap":       "........x.......",
                       "closed_hat": "..x...x...x...x."},
    "broken":         {"kick":       "x.....x...x.....",
                       "clap":       "....x.......x...",
                       "closed_hat": "x.x.x.x.x.x.x.x."},
})

# Words that mean one of the above.
PATTERN_ALIASES: dict[str, str] = {
    "offbeat": "offbeat_hats", "off_beat_hats": "offbeat_hats",
    "open_hats": "offbeat_hats", "hats": "closed_hats", "hat": "closed_hats",
    "sixteenths": "sixteenth_hats", "16ths": "sixteenth_hats",
    "rolling_hats": "sixteenth_hats", "driving_hats": "sixteenth_hats",
    "four_to_the_floor": "four_on_floor", "4x4": "four_on_floor",
    "fourfour": "four_on_floor", "straight": "four_on_floor",
    "clap": "clap_backbeat", "claps": "clap_backbeat",
    "snare": "clap_backbeat", "backbeat": "clap_backbeat",
    "kick": "kick_only", "kicks": "kick_only",
    "perc": "percussion", "percs": "percussion", "shakers": "shaker",
    "halftime": "half_time", "garage": "broken", "ukg": "broken",
    "twostep": "broken", "two_step": "broken",
    "dembow": "reggaeton", "reggae": "reggaeton", "dancehall": "reggaeton",
    "latin": "reggaeton", "afro": "afrobeats", "afrobeat": "afrobeats",
    "piano_house": "amapiano", "garage_beat": "two_step",
    "ukg_beat": "two_step",
    "drum_and_bass": "dnb", "drum_n_bass": "dnb", "jungle": "dnb",
    "hiphop": "hip_hop", "boom_bap": "hip_hop", "trapstep": "trap",
    "deephouse": "deep_house", "techhouse": "tech_house",
    "minimal_techno": "minimal", "breaks": "breakbeat",
}


def normalise_pattern(name: str) -> str:
    """Resolve a pattern synonym to a real key."""
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return PATTERN_ALIASES.get(key, key)


def pattern_vocabulary() -> list[str]:
    """Every accepted pattern word, synonyms included, for the tool schema."""
    return sorted(set(DRUM_PATTERNS) | set(PATTERN_ALIASES))


# Bass/lead rhythms, also one bar of sixteenths.
RHYTHM_PATTERNS: dict[str, str] = {
    "four_on_floor": "x...x...x...x...",
    "offbeat":       "..x...x...x...x.",
    "eighths":       "x.x.x.x.x.x.x.x.",
    "sixteenths":    "xxxxxxxxxxxxxxxx",
    "quarters":      "x...x...x...x...",
    "halves":        "x.......x.......",
    "whole":         "x...............",
    "house_bass":    "..x...x...x...x.",
    "rolling":       "x.xxx.xxx.xxx.xx",
    "syncopated":    "x..x..x...x..x..",
    "funk":          "x..x.x..x..x.x..",
    "dotted":        "x.....x.....x...",
    "reggae":        "....x.......x...",
    "stab":          "x.......x.......",
}

# When a pattern lacks a requested voice, use its nearest relative instead.
VOICE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "perc": ("shaker", "rim", "cowbell", "closed_hat"),
    "shaker": ("closed_hat", "perc"),
    "rim": ("snare", "clap"),
    "clap": ("snare", "rim"),
    "snare": ("clap", "rim"),
    "open_hat": ("closed_hat",),
    "closed_hat": ("hat", "shaker"),
    "ride": ("closed_hat",),
    "crash": ("open_hat",),
    "tom": ("low_tom", "mid_tom", "high_tom"),
    "cowbell": ("rim", "perc"),
}

# Chord comping patterns, two bars each so they vary rather than repeat every
# bar, with the gate that suits them. A stab must be short or it is a pad.
CHORD_COMPS: dict[str, tuple[str, float]] = {
    "pad":        ("x...............x...............", 1.0),
    "held":       ("x...............x...............", 0.98),
    "half":       ("x.......x.......x.......x.......", 0.9),
    "stab":       ("..x...x.....x.......x...x.....x.", 0.35),
    "offbeat":    ("..x...x...x...x.....x...x...x...", 0.4),
    "house":      ("..x...x...x...x...x...x.....x...", 0.42),
    "syncopated": ("x..x..x.....x...x..x.....x..x...", 0.45),
    "sparse":     ("x.......    ....x.......x.......", 0.85),
    "anthem":     ("x.......x.......x...x...x.......", 0.8),
    "pumping":    ("x.x.x.x.x.x.x.x.x.x.x.x.x.x.x.x.", 0.3),
}

ARP_STYLES = (
    "up", "down", "up_down", "down_up", "random", "alberti", "octaves", "thirds",
)


def _steps(pattern: str) -> list[tuple[int, str]]:
    """Yield (step index, symbol) for every non-rest step in a pattern string."""
    return [(i, ch) for i, ch in enumerate(pattern) if ch not in "._ "]


def _velocity_for(symbol: str, base: int) -> int:
    if symbol == "o":
        return min(127, base + 20)
    if symbol == "g":
        return max(1, base - 45)
    return base


def _humanise(
    notes: list[Note],
    amount: float,
    rng: random.Random,
    rigid: tuple[int, ...] = (36,),
) -> list[Note]:
    """Nudge timing and velocity so a pattern doesn't sound machine-stamped.

    Pitches in `rigid` -- the kick by default -- keep their exact timing. A
    drifting kick reads as a mistake, not as feel.
    """
    if amount <= 0:
        return notes
    for note in notes:
        if int(note["pitch"]) not in rigid:
            jitter = rng.uniform(-amount, amount) * 0.02
            note["start"] = max(0.0, float(note["start"]) + jitter)
        note["velocity"] = int(
            max(1, min(127, float(note["velocity"]) + rng.uniform(-amount, amount) * 12))
        )
    return notes


def _swing(
    notes: list[Note], amount: float, rigid: tuple[int, ...] = (36,)
) -> list[Note]:
    """Delay every off-beat sixteenth. `amount` is 0..1 of a half-sixteenth.

    The kick is exempt: swing belongs to the voices around it.
    """
    if amount <= 0:
        return notes
    delay = SIXTEENTH * 0.5 * amount
    for note in notes:
        if int(note["pitch"]) in rigid:
            continue
        step = round(float(note["start"]) / SIXTEENTH)
        if step % 2 == 1:
            note["start"] = float(note["start"]) + delay
    return notes


def generate_drums(
    pattern: str = "four_on_floor",
    bars: int = 4,
    velocity: int = 100,
    swing: float = 0.0,
    humanise: float = 0.0,
    fill_last_bar: bool = False,
    vary: bool = True,
    seed: int | None = None,
    groove: str = "straight",
    groove_strength: float = 1.0,
    instruments: list[str] | None = None,
) -> list[Note]:
    """Build a drum clip from a named pattern.

    `groove` applies a feel template -- microtiming and a per-step accent curve.
    That accent curve is most of what separates a programmed loop from a played
    one: house hats are loud on the offbeats, not flat across the bar.

    `instruments` restricts the pattern to named voices, e.g. ["kick"]. This
    matters when a track holds a single instrument rather than a full Drum
    Rack: a kick device given the clap and hat notes of a pattern just plays
    the kick sample at other pitches, which is where "the kick sounds bad"
    usually comes from.
    """
    key = pattern.strip().lower().replace(" ", "_").replace("-", "_")
    key = normalise_pattern(key)
    if key not in DRUM_PATTERNS:
        raise ValueError(
            f"unknown drum pattern {pattern!r}; "
            f"try one of: {', '.join(sorted(DRUM_PATTERNS))}"
        )
    rng = random.Random(seed)
    grid = DRUM_PATTERNS[key]

    if instruments:
        wanted = {i.strip().lower() for i in instruments}

        def select(names: set[str]) -> dict[str, str]:
            # Accept aliases: "hat" should match "closed_hat" and vice versa.
            return {
                name: line
                for name, line in DRUM_PATTERNS[key].items()
                if name in names or any(w in name or name in w for w in names)
            }

        chosen = select(wanted)
        if not chosen:
            # A voice this pattern does not carry falls back to its nearest
            # relative rather than failing: a Perc track on a house pattern
            # should get the shaker, not an exception.
            for voice in sorted(wanted):
                for substitute in VOICE_FALLBACKS.get(voice, ()):
                    chosen = select({substitute})
                    if chosen:
                        break
                if chosen:
                    break
        if not chosen:
            raise ValueError(
                f"pattern {pattern!r} has no {' or '.join(sorted(wanted))}; "
                f"it contains: {', '.join(sorted(DRUM_PATTERNS[key]))}. "
                "Pick a pattern that has it, or pass instruments explicitly."
            )
        grid = chosen

    notes: list[Note] = []

    for bar in range(bars):
        bar_start = bar * BEATS_PER_BAR
        is_fill = fill_last_bar and bar == bars - 1
        # Where this bar sits in the phrase. Dance drums mark the EIGHT-bar
        # phrase, and they mark it once, with one small gesture -- a dropped
        # kick, a single open hat. The first version stacked an open hat, a
        # clap stutter, ghost notes and extra perc every four bars, and the
        # user's verdict was the right spec: "some small variation at the end
        # of bar 8 is enough. Doesn't need big rolls, be more subtle."
        in_phrase = bar % 8
        turnaround = vary and in_phrase == 7 and not is_fill
        half = False

        for instrument, line in grid.items():
            pitch = DRUM_MAP.get(instrument)
            if pitch is None:
                continue
            # During a fill, thin out the hats so the tom run reads clearly.
            if is_fill and instrument in ("closed_hat", "hat", "shaker"):
                continue
            for step, symbol in _steps(line):
                # A turnaround bar drops the last kick, so the next downbeat
                # arrives rather than merely continuing.
                if turnaround and instrument == "kick" and step >= 14:
                    continue
                notes.append(
                    {
                        "pitch": pitch,
                        "start": bar_start + step * SIXTEENTH,
                        "duration": SIXTEENTH,
                        "velocity": _velocity_for(symbol, velocity),
                    }
                )

        if turnaround and "closed_hat" in grid:
            # One open hat lifting into the next phrase. That is the whole
            # gesture: the dropped last kick above plus this single hat is
            # what "the drummer marked the phrase" sounds like in a club.
            notes.append({"pitch": DRUM_MAP["open_hat"],
                          "start": bar_start + 3.5, "duration": SIXTEENTH * 2,
                          "velocity": min(127, velocity + 6)})
        if is_fill:
            for i, pitch in enumerate([41, 45, 45, 48, 48, 50]):
                notes.append(
                    {
                        "pitch": pitch,
                        "start": bar_start + 2.0 + i * (SIXTEENTH * 4 / 3),
                        "duration": SIXTEENTH,
                        "velocity": min(127, velocity + 10 + i * 3),
                    }
                )

    notes = _swing(notes, swing)
    notes = _humanise(notes, humanise, rng)
    if groove and groove != "straight":
        notes = groove_mod.apply(notes, groove, strength=groove_strength, seed=seed)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def generate_chords(
    chords: list[Chord],
    bars_per_chord: float = 1.0,
    rhythm: str = "pad",
    velocity: int = 85,
    duration_scale: float | None = None,
    spread: float = 0.0,
    humanise: float = 0.0,
    seed: int | None = None,
    groove: str = "straight",
) -> list[Note]:
    """Lay a voiced progression out in time.

    `rhythm` picks a two-bar comping pattern so the part varies rather than
    hammering the same cell every bar, and it carries its own gate -- a stab
    that rings for most of its step is a pad, not a stab.

    `spread` strums the chord: each successive voice is delayed by that many
    sixteenths, which is how you get a rolled piano-house chord.
    """
    rng = random.Random(seed)
    key = rhythm.lower()
    if key in CHORD_COMPS:
        pattern, gate = CHORD_COMPS[key]
    else:
        pattern = RHYTHM_PATTERNS.get(key, CHORD_COMPS["pad"][0])
        gate = 0.9
    if duration_scale is not None:
        gate = duration_scale

    cycle_bars = max(1, len(pattern) // 16)
    hits = _steps(pattern)
    notes: list[Note] = []

    for index, chord in enumerate(chords):
        chord_start = index * bars_per_chord * BEATS_PER_BAR
        span = max(1, int(round(bars_per_chord)))
        for bar in range(span):
            # The comp runs across its own cycle, so a two-bar pattern is not
            # restarted every bar.
            offset_bar = bar % cycle_bars
            bar_start = chord_start + bar * BEATS_PER_BAR
            for position, (step, symbol) in enumerate(hits):
                if step // 16 != offset_bar:
                    continue
                local = step % 16
                start = bar_start + local * SIXTEENTH
                if position + 1 < len(hits):
                    length = (hits[position + 1][0] - step) * SIXTEENTH
                else:
                    length = BEATS_PER_BAR - local * SIXTEENTH
                length = max(SIXTEENTH, min(length, BEATS_PER_BAR * cycle_bars))
                for voice, pitch in enumerate(chord.pitches):
                    notes.append(
                        {
                            "pitch": pitch,
                            "start": start + voice * spread * SIXTEENTH,
                            "duration": max(0.05, length * gate),
                            "velocity": _velocity_for(symbol, velocity),
                        }
                    )

    notes = _humanise(notes, humanise, rng)
    if groove and groove != "straight":
        notes = groove_mod.apply(notes, groove, seed=seed)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def generate_bassline(
    chords: list[Chord],
    bars_per_chord: float = 1.0,
    rhythm: str = "offbeat",
    octave: int = 1,
    velocity: int = 100,
    style: str = "root",
    swing: float = 0.0,
    humanise: float = 0.0,
    seed: int | None = None,
    groove: str = "straight",
    against_drums: list[Note] | None = None,
    kick_mode: str = "avoid",
) -> list[Note]:
    """Build a bassline that follows the progression.

    `style` picks which chord tones get used: "root" stays on the root, "fifth"
    alternates root/fifth, "octave" bounces the octave, "walk" steps through the
    whole voicing.

    Pass the drum part as `against_drums` and the bass is placed *relative to
    the kick* rather than independently of it. That interaction is the whole
    rolling feel of house and techno: "avoid" keeps the bass in the gaps
    between kicks, "shorten" lets it ring but release before each kick, and
    "lock" doubles the kick instead.
    """
    rng = random.Random(seed)
    pattern = RHYTHM_PATTERNS.get(rhythm.lower(), RHYTHM_PATTERNS["offbeat"])
    hits = _steps(pattern)
    notes: list[Note] = []

    for index, chord in enumerate(chords):
        # Drop the chord root into bass register.
        root = chord.root_pitch
        while root >= (octave + 3) * 12:
            root -= 12
        while root < (octave + 2) * 12:
            root += 12
        tones = sorted({p % 12 for p in chord.pitches})
        chord_start = index * bars_per_chord * BEATS_PER_BAR

        for bar in range(max(1, int(bars_per_chord))):
            bar_start = chord_start + bar * BEATS_PER_BAR
            for position, (step, symbol) in enumerate(hits):
                if style == "root":
                    pitch = root
                elif style == "fifth":
                    pitch = root + (7 if position % 2 else 0)
                elif style == "octave":
                    pitch = root + (12 if position % 2 else 0)
                elif style == "walk":
                    interval = tones[position % len(tones)]
                    pitch = root + ((interval - root % 12) % 12)
                else:
                    pitch = root

                if position + 1 < len(hits):
                    length = (hits[position + 1][0] - step) * SIXTEENTH
                else:
                    length = BEATS_PER_BAR - step * SIXTEENTH

                notes.append(
                    {
                        "pitch": pitch,
                        "start": bar_start + step * SIXTEENTH,
                        "duration": max(0.05, length * 0.9),
                        "velocity": _velocity_for(symbol, velocity),
                    }
                )

    notes = _swing(notes, swing)
    notes = _humanise(notes, humanise, rng)

    if against_drums:
        onsets = groove_mod.kick_onsets(against_drums)
        notes = groove_mod.duck_against(notes, onsets, mode=kick_mode)

    if groove and groove != "straight":
        notes = groove_mod.apply(notes, groove, seed=seed)

    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def generate_arpeggio(
    chords: list[Chord],
    bars_per_chord: float = 1.0,
    style: str = "up",
    rate: float = SIXTEENTH,
    octaves: int = 1,
    velocity: int = 90,
    gate: float = 0.9,
    swing: float = 0.0,
    seed: int | None = None,
) -> list[Note]:
    """Arpeggiate a progression."""
    rng = random.Random(seed)
    notes: list[Note] = []

    for index, chord in enumerate(chords):
        base = list(chord.pitches)
        pool = [p + o * 12 for o in range(max(1, octaves)) for p in base]

        if style == "down":
            sequence = list(reversed(pool))
        elif style == "up_down":
            sequence = pool + list(reversed(pool[1:-1])) if len(pool) > 2 else pool
        elif style == "down_up":
            rev = list(reversed(pool))
            sequence = rev + pool[1:-1] if len(pool) > 2 else rev
        elif style == "random":
            sequence = pool[:]
            rng.shuffle(sequence)
        elif style == "alberti":
            sequence = (
                [pool[0], pool[2], pool[1], pool[2]] if len(pool) >= 3 else pool
            )
        elif style == "octaves":
            sequence = [p for pitch in base for p in (pitch, pitch + 12)]
        elif style == "thirds":
            sequence = pool[::2] + pool[1::2]
        else:
            sequence = pool

        chord_start = index * bars_per_chord * BEATS_PER_BAR
        total_beats = bars_per_chord * BEATS_PER_BAR
        step_count = int(round(total_beats / rate))

        for step in range(step_count):
            notes.append(
                {
                    "pitch": sequence[step % len(sequence)],
                    "start": chord_start + step * rate,
                    "duration": max(0.05, rate * gate),
                    "velocity": velocity,
                }
            )

    notes = _swing(notes, swing)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def generate_melody(
    root: str,
    scale: str,
    bars: int = 4,
    octave: int = 4,
    rhythm: str = "eighths",
    velocity: int = 90,
    contour: str = "arch",
    range_steps: int = 8,
    seed: int | None = None,
    chords: list[Chord] | None = None,
    motif_shape: str | None = None,
    motif_rhythm: str = "straight_eighths",
    development: list[str] | None = None,
    groove: str = "straight",
) -> list[Note]:
    """Write a melody by stating a motif and developing it.

    A contour-following random walk stays in key but says nothing -- every bar
    is new material, so there is nothing to remember. Instead this builds a
    short cell and develops it across the phrase (repeat, sequence, invert,
    answer), which is how melodies are actually written.

    `contour` still selects the motif's shape, so existing callers keep working.
    """
    shape = motif_shape or {
        "rise": "rise", "fall": "fall", "arch": "arch",
        "valley": "valley", "random": "call",
    }.get(contour, "arch")

    # Without harmony to follow, walk a plain tonic-anchored progression so the
    # motif still moves rather than sitting on one degree.
    if chords is None:
        degrees = [1, 6, 4, 5][: max(1, min(4, bars))]
        while len(degrees) < bars:
            degrees += degrees[: bars - len(degrees)]
        chords = theory.build_progression(root, scale, degrees[:bars])

    bars_per_chord = bars / max(1, len(chords))
    notes = motif.build_phrase(
        root=root,
        scale=scale,
        chords=chords,
        bars_per_chord=bars_per_chord,
        shape=shape,
        rhythm=motif_rhythm,
        octave=octave,
        velocity=velocity,
        plan=development,
        seed=seed,
    )
    if groove and groove != "straight":
        notes = groove_mod.apply(notes, groove, seed=seed)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def transpose(notes: list[Note], semitones: int) -> list[Note]:
    return [dict(n, pitch=int(n["pitch"]) + semitones) for n in notes]


def summarise(notes: list[Note]) -> str:
    """A one-line description for feeding back to the model."""
    if not notes:
        return "no notes"
    pitches = [int(n["pitch"]) for n in notes]
    end = max(float(n["start"]) + float(n["duration"]) for n in notes)
    return (
        f"{len(notes)} notes, {theory.pitch_name(min(pitches))}"
        f"-{theory.pitch_name(max(pitches))}, "
        f"{end / BEATS_PER_BAR:.2f} bars"
    )


# ----------------------------------------------------------------------
# EDM transition material: build-ups, risers, impacts, hooks
# ----------------------------------------------------------------------

def generate_buildup(
    bars: int = 8,
    instrument: str = "snare",
    velocity_start: int = 55,
    velocity_end: int = 127,
    divisions: tuple[int, ...] | None = None,
    add_hats: bool = True,
    add_kick: bool = False,
    seed: int | None = None,
) -> list[Note]:
    """An accelerating drum roll -- the classic EDM build.

    The roll subdivides faster as it approaches the drop (eighths, then
    sixteenths, then thirty-seconds) while velocity ramps up across the whole
    build. `divisions` overrides the acceleration curve: one entry per
    equal-length stage, each the number of hits per bar.
    """
    rng = random.Random(seed)
    pitch = DRUM_MAP.get(instrument, DRUM_MAP["snare"])
    stages = list(divisions) if divisions else _buildup_divisions(bars)
    notes: list[Note] = []

    bars_per_stage = bars / len(stages)
    total_beats = bars * BEATS_PER_BAR

    for stage_index, hits_per_bar in enumerate(stages):
        stage_start = stage_index * bars_per_stage * BEATS_PER_BAR
        stage_beats = bars_per_stage * BEATS_PER_BAR
        step = BEATS_PER_BAR / hits_per_bar
        count = int(round(stage_beats / step))
        for i in range(count):
            start = stage_start + i * step
            progress = start / total_beats if total_beats else 0.0
            velocity = velocity_start + (velocity_end - velocity_start) * progress
            notes.append(
                {
                    "pitch": pitch,
                    "start": start,
                    "duration": max(0.02, step * 0.8),
                    "velocity": int(max(1, min(127, velocity))),
                }
            )

    if add_hats:
        for bar in range(bars):
            for i in range(8):
                start = bar * BEATS_PER_BAR + i * 0.5
                progress = start / total_beats if total_beats else 0.0
                notes.append(
                    {
                        "pitch": DRUM_MAP["closed_hat"],
                        "start": start,
                        "duration": SIXTEENTH,
                        "velocity": int(50 + 60 * progress),
                    }
                )

    if add_kick:
        # Kick drops out over the final bar to open space for the drop.
        for bar in range(max(0, bars - 1)):
            for beat in range(4):
                notes.append(
                    {
                        "pitch": DRUM_MAP["kick"],
                        "start": bar * BEATS_PER_BAR + beat,
                        "duration": SIXTEENTH,
                        "velocity": 105,
                    }
                )

    _humanise(notes, 0.3, rng)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def _buildup_divisions(bars: int) -> list[int]:
    """Pick an acceleration curve that suits the build's length."""
    if bars <= 2:
        return [4, 8, 16]
    if bars <= 4:
        return [4, 8, 16, 32]
    if bars <= 8:
        return [4, 4, 8, 8, 16, 16, 32, 32]
    return [4, 4, 4, 8, 8, 8, 16, 16, 16, 32, 32, 32]


def generate_riser(
    root: str,
    scale: str,
    bars: int = 8,
    octave: int = 4,
    direction: str = "up",
    rate: float = SIXTEENTH,
    velocity_start: int = 40,
    velocity_end: int = 120,
    octaves: int = 3,
) -> list[Note]:
    """A pitch-climbing scale run for a riser synth or FX placeholder.

    Feed this into a noise/saw patch with a filter envelope and it reads as a
    tension riser; on a pluck it reads as a build arp.
    """
    pitches = theory.scale_pitches(root, scale, octave=octave, octaves=octaves)
    if direction == "down":
        pitches = list(reversed(pitches))

    total_beats = bars * BEATS_PER_BAR
    steps = max(1, int(round(total_beats / rate)))
    notes: list[Note] = []

    for i in range(steps):
        progress = i / steps
        index = min(len(pitches) - 1, int(progress * len(pitches)))
        notes.append(
            {
                "pitch": pitches[index],
                "start": i * rate,
                "duration": max(0.05, rate * 0.95),
                "velocity": int(
                    velocity_start + (velocity_end - velocity_start) * progress
                ),
            }
        )
    return notes


def generate_impact(
    bars: int = 1,
    crash: bool = True,
    sub_drop: bool = True,
    sub_pitch: int = 24,
) -> list[Note]:
    """A downbeat crash plus a sub hit -- what lands on the first bar of a drop."""
    notes: list[Note] = []
    if crash:
        notes.append(
            {"pitch": DRUM_MAP["crash"], "start": 0.0, "duration": 4.0, "velocity": 120}
        )
    if sub_drop:
        notes.append(
            {"pitch": sub_pitch, "start": 0.0, "duration": bars * BEATS_PER_BAR,
             "velocity": 110}
        )
    return notes


def generate_hook(
    chords: list[Chord],
    bars_per_chord: float = 1.0,
    octave: int = 5,
    rhythm: str = "syncopated",
    velocity: int = 105,
    call_and_response: bool = True,
    root: str = "C",
    scale: str = "minor",
    shape: str = "hook",
    groove: str = "straight",
    seed: int | None = None,
) -> list[Note]:
    """A short, high, repetitive top-line -- the bit people hum afterwards.

    Built from a motif so the hook is one idea restated, not four different
    bars. With `call_and_response` the second phrase answers the first: same
    rhythm, contour inverted, resolving back to the tonic.
    """
    plan = (
        ["repeat", "repeat", "sequence_up", "answer"]
        if call_and_response
        else ["repeat", "repeat", "repeat", "fragment"]
    )
    notes = motif.build_phrase(
        root=root,
        scale=scale,
        chords=chords,
        bars_per_chord=bars_per_chord,
        shape=shape,
        rhythm={"syncopated": "syncopated", "eighths": "straight_eighths"}.get(
            rhythm, "syncopated"
        ),
        octave=octave,
        velocity=velocity,
        plan=plan,
        seed=seed,
    )
    if groove and groove != "straight":
        notes = groove_mod.apply(notes, groove, seed=seed)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


# ----------------------------------------------------------------------
# Transitions: how one section hands over to the next
# ----------------------------------------------------------------------

def generate_snare_roll(
    bars: float = 2,
    instrument: str = "snare",
    start_division: int = 4,
    end_division: int = 32,
    velocity_start: int = 60,
    velocity_end: int = 127,
    seed: int | None = None,
) -> list[Note]:
    """A snare roll that accelerates into the next section.

    The staple transition in dance music, and the one most often done badly:
    the acceleration has to be smooth and the velocity has to climb with it,
    or it reads as a stutter rather than a hand-over.
    """
    rng = random.Random(seed)
    pitch = DRUM_MAP.get(instrument, DRUM_MAP["snare"])
    total = bars * BEATS_PER_BAR
    notes: list[Note] = []

    at = 0.0
    while at < total - 1e-6:
        progress = at / total
        # Divisions per bar climb geometrically, which is what makes the
        # acceleration sound even rather than lurching.
        divisions = start_division * (end_division / start_division) ** progress
        step = BEATS_PER_BAR / divisions
        notes.append({
            "pitch": pitch,
            "start": at,
            "duration": max(0.02, step * 0.8),
            "velocity": int(max(1, min(127,
                velocity_start + (velocity_end - velocity_start) * progress
                + rng.uniform(-3, 3)))),
        })
        at += step

    return notes


def generate_clap_build(
    bars: float = 4,
    layers: int = 3,
    seed: int | None = None,
) -> list[Note]:
    """Claps thickening toward a change -- offbeats, then doubles, then a run.

    Layering claps is the subtler cousin of the snare roll: it raises tension
    without announcing itself, so it works under a breakdown where a roll would
    be too obvious.
    """
    rng = random.Random(seed)
    clap = DRUM_MAP["clap"]
    notes: list[Note] = []

    for bar in range(int(bars)):
        bar_start = bar * BEATS_PER_BAR
        progress = bar / max(1, bars - 1)

        # Always the backbeat.
        for beat in (1, 3):
            notes.append({"pitch": clap, "start": bar_start + beat,
                          "duration": SIXTEENTH,
                          "velocity": int(96 + 20 * progress)})
        # Then doubles, then sixteenth runs as the change approaches.
        if progress > 0.3 and layers >= 2:
            for beat in (1, 3):
                notes.append({"pitch": clap, "start": bar_start + beat + 0.5,
                              "duration": SIXTEENTH,
                              "velocity": int(70 + 25 * progress)})
        if progress > 0.7 and layers >= 3:
            for i in range(4):
                notes.append({"pitch": clap,
                              "start": bar_start + 3 + i * SIXTEENTH,
                              "duration": SIXTEENTH,
                              "velocity": int(80 + 40 * (i / 4))})

    _humanise(notes, 0.25, rng)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def generate_drum_fill(
    bars: float = 1,
    style: str = "toms",
    velocity: int = 105,
    seed: int | None = None,
) -> list[Note]:
    """A one-bar fill to mark the end of a phrase.

    "toms" is the descending run, "snare" a straight sixteenth burst, "stutter"
    a retrigger on the last beat only.
    """
    rng = random.Random(seed)
    total = bars * BEATS_PER_BAR
    notes: list[Note] = []

    if style == "toms":
        run = [DRUM_MAP["high_tom"], DRUM_MAP["high_tom"], DRUM_MAP["mid_tom"],
               DRUM_MAP["mid_tom"], DRUM_MAP["low_tom"], DRUM_MAP["low_tom"]]
        step = total / len(run)
        for i, pitch in enumerate(run):
            notes.append({"pitch": pitch, "start": i * step,
                          "duration": max(0.05, step * 0.9),
                          "velocity": min(127, velocity + i * 3)})
    elif style == "snare":
        count = int(total / SIXTEENTH)
        for i in range(count):
            notes.append({"pitch": DRUM_MAP["snare"], "start": i * SIXTEENTH,
                          "duration": SIXTEENTH * 0.8,
                          "velocity": int(70 + 50 * (i / max(1, count - 1)))})
    elif style == "stutter":
        # Only the final beat, retriggered fast.
        for i in range(8):
            notes.append({"pitch": DRUM_MAP["snare"],
                          "start": total - 1.0 + i * 0.125,
                          "duration": 0.1,
                          "velocity": int(80 + 45 * (i / 7))})
    else:
        raise ValueError(f"unknown fill style {style!r}; try toms, snare or stutter")

    _humanise(notes, 0.2, rng)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes
