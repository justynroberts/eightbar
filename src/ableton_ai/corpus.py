"""Learn from reference MIDI: key, harmony, voicing, rhythm, groove, motif.

Generated music sounds generated because it is built from rules someone wrote
down. The way past that is to take the material from records that already work
-- not to copy them, but to extract the *shapes*: which chord movements recur,
how the voicings are spaced, where the bass sits against the beat, how far off
the grid the parts actually are.

Everything here lands in the formats the generators already speak: progressions
become degree lists, rhythms become sixteen-step strings, feel becomes a Groove
template, melodies become motif cells. So a learned reference is not a separate
system -- it is new material for the machinery that already exists.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import mido

from . import theory
from .groove import Groove

BEATS_PER_BAR = 4.0
SIXTEENTH = 0.25


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------

@dataclass
class MidiNote:
    pitch: int
    start: float        # beats from the start of the file
    duration: float
    velocity: int
    track: int


def read_midi(path: str | Path) -> tuple[list[MidiNote], float]:
    """Flatten a MIDI file into notes with times in beats.

    Ticks are converted using the file's own resolution, so a file written at
    any PPQ lands on the same beat grid.
    """
    midi = mido.MidiFile(str(path))
    ticks = midi.ticks_per_beat or 480
    tempo = 120.0
    notes: list[MidiNote] = []

    for index, track in enumerate(midi.tracks):
        clock = 0
        sounding: dict[tuple[int, int], tuple[float, int]] = {}
        for message in track:
            clock += message.time
            if message.type == "set_tempo":
                tempo = round(60_000_000 / message.tempo, 3)
            elif message.type == "note_on" and message.velocity > 0:
                sounding[(message.channel, message.note)] = (
                    clock / ticks, message.velocity
                )
            elif message.type == "note_off" or (
                message.type == "note_on" and message.velocity == 0
            ):
                key = (message.channel, message.note)
                if key not in sounding:
                    continue
                start, velocity = sounding.pop(key)
                notes.append(
                    MidiNote(message.note, start, max(0.01, clock / ticks - start),
                             velocity, index)
                )

    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes, tempo


# ----------------------------------------------------------------------
# Key
# ----------------------------------------------------------------------

# Krumhansl-Schmuckler profiles: how strongly each scale degree is expected in
# a major and a minor key. Correlating these against a piece's pitch-class
# durations is the standard way to guess a key, and it is reliable enough on
# four bars of harmony.
_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _correlate(histogram: list[float], profile: tuple[float, ...]) -> float:
    n = len(profile)
    mean_h = sum(histogram) / n
    mean_p = sum(profile) / n
    num = sum((histogram[i] - mean_h) * (profile[i] - mean_p) for i in range(n))
    den_h = sum((histogram[i] - mean_h) ** 2 for i in range(n)) ** 0.5
    den_p = sum((profile[i] - mean_p) ** 2 for i in range(n)) ** 0.5
    return num / (den_h * den_p) if den_h and den_p else 0.0


def detect_key(notes: list[MidiNote]) -> tuple[str, str, float]:
    """Guess the key. Returns (root name, "major"|"minor", confidence 0..1)."""
    if not notes:
        return "C", "minor", 0.0

    weights = [0.0] * 12
    for note in notes:
        # Weight by how long the note sounds -- a passing note counts for less.
        weights[note.pitch % 12] += note.duration

    best = ("C", "minor", -2.0)
    for root in range(12):
        rotated = weights[root:] + weights[:root]
        for name, profile in (("major", _MAJOR), ("minor", _MINOR)):
            score = _correlate(rotated, profile)
            if score > best[2]:
                best = (theory.NOTE_NAMES[root], name, score)

    # Correlation runs -1..1; report it as a 0..1 confidence.
    return best[0], best[1], round(max(0.0, best[2]), 3)


# ----------------------------------------------------------------------
# Harmony
# ----------------------------------------------------------------------

# Templates as pitch-class offsets, ordered so richer chords are preferred when
# they explain the same notes.
_TEMPLATES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("major9", (0, 4, 7, 11, 2)),
    ("minor9", (0, 3, 7, 10, 2)),
    ("dominant9", (0, 4, 7, 10, 2)),
    ("major7", (0, 4, 7, 11)),
    ("minor7", (0, 3, 7, 10)),
    ("dominant7", (0, 4, 7, 10)),
    ("half_diminished7", (0, 3, 6, 10)),
    ("diminished7", (0, 3, 6, 9)),
    ("minor6", (0, 3, 7, 9)),
    ("major6", (0, 4, 7, 9)),
    ("major", (0, 4, 7)),
    ("minor", (0, 3, 7)),
    ("diminished", (0, 3, 6)),
    ("augmented", (0, 4, 8)),
    ("sus4", (0, 5, 7)),
    ("sus2", (0, 2, 7)),
    ("power", (0, 7)),
)


def identify_chord(pitches: list[int]) -> tuple[int, str, float] | None:
    """Name a set of sounding pitches. Returns (root pitch class, quality, fit)."""
    classes = {p % 12 for p in pitches}
    if len(classes) < 2:
        return None

    best: tuple[int, str, float] | None = None
    for root in range(12):
        for quality, offsets in _TEMPLATES:
            template = {(root + o) % 12 for o in offsets}
            covered = len(classes & template)
            # Reward explaining the notes; penalise template notes that are
            # absent, so a triad is not "explained" by a ninth chord.
            fit = covered / len(classes | template)
            if best is None or fit > best[2]:
                best = (root, quality, round(fit, 3))
    return best


@dataclass
class ChordEvent:
    bar: float
    degree: int
    quality: str
    root_name: str
    pitches: list[int]
    inversion: int
    spread_semitones: int
    fit: float


def extract_chords(
    notes: list[MidiNote],
    key_root: str,
    key_scale: str,
    window_beats: float = BEATS_PER_BAR,
) -> list[ChordEvent]:
    """Slice the piece into windows and name the harmony in each."""
    if not notes:
        return []

    end = max(n.start + n.duration for n in notes)
    root_class = theory.note_to_pitch_class(key_root)
    scale_key = theory.normalise_scale(key_scale)
    intervals = theory.SCALES[scale_key]

    events: list[ChordEvent] = []
    window = 0.0
    while window < end:
        sounding = [
            n for n in notes
            if n.start < window + window_beats and n.start + n.duration > window + 0.01
        ]
        # A chord that rings on into the next window is the same chord, not a
        # new one. Require an onset inside the window before naming it.
        starts_here = any(window - 0.01 <= n.start < window + window_beats
                          for n in sounding)
        if sounding and (starts_here or not events):
            identified = identify_chord([n.pitch for n in sounding])
            if identified:
                chord_root, quality, fit = identified
                # Which scale degree is this chord built on?
                offset = (chord_root - root_class) % 12
                degree = next(
                    (i + 1 for i, iv in enumerate(intervals) if iv == offset), 0
                )
                pitches = sorted(n.pitch for n in sounding)
                bass_class = pitches[0] % 12
                inversion = 0
                if bass_class != chord_root:
                    template = next(
                        (o for q, o in _TEMPLATES if q == quality), (0, 4, 7)
                    )
                    for i, o in enumerate(template):
                        if (chord_root + o) % 12 == bass_class:
                            inversion = i
                            break
                events.append(ChordEvent(
                    bar=round(window / BEATS_PER_BAR, 3),
                    degree=degree,
                    quality=quality,
                    root_name=theory.NOTE_NAMES[chord_root],
                    pitches=pitches,
                    inversion=inversion,
                    spread_semitones=pitches[-1] - pitches[0],
                    fit=fit,
                ))
        window += window_beats

    return events


# ----------------------------------------------------------------------
# Rhythm, feel and articulation
# ----------------------------------------------------------------------

def extract_rhythm(notes: list[MidiNote], bars: int = 1) -> str:
    """Reduce onsets to the sixteen-step string the generators use."""
    steps = ["."] * (16 * max(1, bars))
    for note in notes:
        index = int(round((note.start % (BEATS_PER_BAR * bars)) / SIXTEENTH))
        if 0 <= index < len(steps):
            steps[index] = "x"
    return "".join(steps)


def extract_groove(notes: list[MidiNote], name: str = "learned") -> Groove:
    """Measure how far off the grid a part actually sits.

    Two things carry feel: how late each sixteenth lands, and how the accents
    fall across the bar. Both are averaged per step so one bar of noise does
    not define the template.
    """
    timing: dict[int, list[float]] = defaultdict(list)
    velocity: dict[int, list[int]] = defaultdict(list)

    for note in notes:
        position = (note.start % BEATS_PER_BAR) / SIXTEENTH
        step = int(round(position)) % 16
        # Deviation as a fraction of a sixteenth, signed.
        timing[step].append(position - round(position))
        velocity[step].append(note.velocity)

    all_velocities = [v for values in velocity.values() for v in values]
    mean_velocity = statistics.fmean(all_velocities) if all_velocities else 100.0

    timing_curve = tuple(
        round(statistics.fmean(timing[s]), 4) if timing.get(s) else 0.0
        for s in range(16)
    )
    accents = tuple(
        int(round(statistics.fmean(velocity[s]) - mean_velocity))
        if velocity.get(s) else 0
        for s in range(16)
    )

    # Swing shows up as the odd sixteenths landing consistently late.
    odd = [timing_curve[s] for s in range(1, 16, 2) if timing.get(s)]
    swing = round(max(0.0, statistics.fmean(odd) * 2), 3) if odd else 0.0

    deviations = [d for values in timing.values() for d in values]
    jitter = round(statistics.pstdev(deviations) * 10, 3) if len(deviations) > 1 else 0.0

    return Groove(
        name=name,
        swing=swing,
        push=round(statistics.fmean(deviations), 4) if deviations else 0.0,
        accents=accents,
        timing=timing_curve,
        jitter=min(1.0, jitter),
    )


# How a bassline actually behaves, which is mostly about density and length.
BASS_STYLES = ("sustained", "simple", "offbeat", "driving", "rolling")


def classify_bass(notes: list[MidiNote]) -> dict:
    """Describe a bassline's articulation the way a producer would."""
    if not notes:
        return {"style": "simple", "onsets_per_bar": 0.0, "legato": 0.0}

    end = max(n.start + n.duration for n in notes)
    bars = max(1.0, end / BEATS_PER_BAR)
    per_bar = len(notes) / bars
    mean_length = statistics.fmean(n.duration for n in notes)
    # Legato: how much of the gap between onsets the note actually fills.
    gaps = [
        b.start - a.start for a, b in zip(notes, notes[1:]) if b.start > a.start
    ]
    mean_gap = statistics.fmean(gaps) if gaps else mean_length
    legato = min(2.0, mean_length / mean_gap) if mean_gap else 1.0

    offbeat_share = sum(
        1 for n in notes
        if abs(((n.start % 1.0) - 0.5)) < 0.12
    ) / len(notes)

    if per_bar <= 1.5 and mean_length >= 2.0:
        style = "sustained"
    elif offbeat_share > 0.55:
        style = "offbeat"
    elif per_bar >= 12:
        style = "rolling"
    elif per_bar >= 6:
        style = "driving"
    else:
        style = "simple"

    return {
        "style": style,
        "onsets_per_bar": round(per_bar, 2),
        "mean_length_beats": round(mean_length, 3),
        "legato": round(legato, 2),
        "offbeat_share": round(offbeat_share, 2),
        "octave_jumps": sum(
            1 for a, b in zip(notes, notes[1:]) if abs(b.pitch - a.pitch) == 12
        ),
    }


def extract_motif(notes: list[MidiNote], max_length: int = 8) -> dict:
    """The opening melodic cell, as scale-independent intervals."""
    melody = sorted(notes, key=lambda n: (n.start, -n.pitch))
    # One note per onset -- the top voice, which is what carries a melody.
    top: list[MidiNote] = []
    for note in melody:
        if not top or note.start - top[-1].start > 0.05:
            top.append(note)
    cell = top[:max_length]
    if len(cell) < 2:
        return {}

    return {
        "intervals": [b.pitch - a.pitch for a, b in zip(cell, cell[1:])],
        "rhythm": [round(b.start - a.start, 3) for a, b in zip(cell, cell[1:])],
        "range_semitones": max(n.pitch for n in cell) - min(n.pitch for n in cell),
        "direction": (
            "rising" if cell[-1].pitch > cell[0].pitch
            else "falling" if cell[-1].pitch < cell[0].pitch
            else "static"
        ),
    }


# ----------------------------------------------------------------------
# A learned reference, and the library of them
# ----------------------------------------------------------------------

# Which musical job a track in a MIDI file is doing, judged by register and
# behaviour rather than by whatever the track happened to be named.
def classify_part(notes: list[MidiNote]) -> str:
    if not notes:
        return "unknown"
    pitches = [n.pitch for n in notes]
    mean = statistics.fmean(pitches)
    lowest = min(pitches)

    # How often several notes sound together -- the mark of a chord part.
    onsets = Counter(round(n.start, 2) for n in notes)
    polyphony = statistics.fmean(onsets.values()) if onsets else 1.0

    # Drums first: a narrow set of low pitches with no harmony in them.
    if lowest <= 36 and mean < 45 and len(set(pitches)) <= 8:
        return "drums"
    # Notes sounding together are harmony, whatever register they sit in --
    # a low piano voicing is still a chord part, not a bassline.
    if polyphony >= 2.2:
        return "chords"
    if mean < 52:
        return "bass"
    if mean > 76:
        return "lead"
    return "melody"


@dataclass
class Reference:
    """Everything learned from one MIDI file."""

    name: str
    path: str
    tempo: float
    bars: float
    key_root: str
    key_scale: str
    key_confidence: float
    progression: list[int] = field(default_factory=list)
    qualities: list[str] = field(default_factory=list)
    chords: list[dict] = field(default_factory=list)
    parts: dict = field(default_factory=dict)
    voicing: dict = field(default_factory=dict)
    notes_total: int = 0

    def summary(self) -> str:
        degrees = "-".join(str(d) for d in self.progression) or "?"
        return (
            f"{self.name}: {self.key_root} {self.key_scale} "
            f"({self.key_confidence:.0%} sure), {degrees}, "
            f"{self.bars:.0f} bars @ {self.tempo:.0f}bpm"
        )


def learn(path: str | Path) -> Reference:
    """Extract everything useful from one MIDI file."""
    path = Path(path)
    notes, tempo = read_midi(path)
    if not notes:
        raise ValueError(f"{path.name} contains no notes")

    root, scale, confidence = detect_key(notes)
    end = max(n.start + n.duration for n in notes)
    chords = extract_chords(notes, root, scale)

    # Group the file's tracks by the job each is doing.
    by_track: dict[int, list[MidiNote]] = defaultdict(list)
    for note in notes:
        by_track[note.track].append(note)

    parts: dict[str, dict] = {}
    for track_notes in by_track.values():
        role = classify_part(track_notes)
        entry: dict = {
            "notes": len(track_notes),
            "range": [min(n.pitch for n in track_notes),
                      max(n.pitch for n in track_notes)],
            "rhythm": extract_rhythm(track_notes),
            "groove": asdict(extract_groove(track_notes, name=role)),
        }
        if role == "bass":
            entry["articulation"] = classify_bass(track_notes)
        if role in ("lead", "melody"):
            entry["motif"] = extract_motif(track_notes)
        # Several tracks can share a role; keep the busiest.
        if role not in parts or entry["notes"] > parts[role]["notes"]:
            parts[role] = entry

    spreads = [c.spread_semitones for c in chords] or [0]
    inversions = [c.inversion for c in chords] or [0]

    return Reference(
        name=path.stem,
        path=str(path),
        tempo=tempo,
        bars=round(end / BEATS_PER_BAR, 2),
        key_root=root,
        key_scale=scale,
        key_confidence=confidence,
        progression=[c.degree for c in chords if c.degree],
        qualities=[c.quality for c in chords],
        chords=[asdict(c) for c in chords],
        parts=parts,
        voicing={
            "mean_spread_semitones": round(statistics.fmean(spreads), 1),
            "inversion_share": round(
                sum(1 for i in inversions if i) / len(inversions), 2
            ),
            "mean_voices": round(
                statistics.fmean(len(c.pitches) for c in chords), 1
            ) if chords else 0.0,
        },
        notes_total=len(notes),
    )


class Library:
    """A folder of learned references, plus what they have in common."""

    def __init__(self, path: Path | None = None) -> None:
        from .sounds import CONFIG_DIR
        self.path = path or (CONFIG_DIR / "corpus.json")
        self.references: dict[str, Reference] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for name, data in (raw.get("references") or {}).items():
            try:
                self.references[name] = Reference(**data)
            except TypeError:
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"references": {n: asdict(r) for n, r in self.references.items()}},
            indent=2,
        ) + "\n")

    def add(self, reference: Reference) -> None:
        self.references[reference.name] = reference

    def learn_folder(self, folder: str | Path, limit: int | None = None) -> dict:
        """Learn every MIDI file in a folder."""
        folder = Path(folder)
        files = sorted(
            [p for p in folder.rglob("*") if p.suffix.lower() in (".mid", ".midi")]
        )
        if limit:
            files = files[:limit]

        learned, failed = [], []
        for file in files:
            try:
                reference = learn(file)
                self.add(reference)
                learned.append(reference.name)
            except Exception as exc:
                failed.append(f"{file.name}: {exc}")
        self.save()
        return {"learned": learned, "failed": failed, "total": len(self.references)}

    # -- what the corpus agrees on -----------------------------------

    def common_progressions(self, top: int = 12) -> list[dict]:
        """Which progressions recur, as degree sequences."""
        counter: Counter[tuple[int, ...]] = Counter()
        for reference in self.references.values():
            degrees = tuple(reference.progression)
            if len(degrees) >= 2:
                counter[degrees] += 1
        return [
            {
                "degrees": list(degrees),
                "as_text": "-".join(str(d) for d in degrees),
                "count": count,
                "share": round(count / max(1, len(self.references)), 3),
            }
            for degrees, count in counter.most_common(top)
        ]

    def common_movements(self, top: int = 12) -> list[dict]:
        """Which chord-to-chord moves recur. More useful than whole loops.

        Whole progressions rarely repeat across a varied corpus, but the moves
        inside them do -- and a move is what you need to generate a new one
        that still sounds like the references.
        """
        counter: Counter[tuple[int, int]] = Counter()
        for reference in self.references.values():
            degrees = reference.progression
            for a, b in zip(degrees, degrees[1:]):
                if a and b:
                    counter[(a, b)] += 1
        total = sum(counter.values()) or 1
        return [
            {"from": a, "to": b, "count": count,
             "share": round(count / total, 3)}
            for (a, b), count in counter.most_common(top)
        ]

    def common_qualities(self) -> dict:
        counter: Counter[str] = Counter()
        for reference in self.references.values():
            counter.update(reference.qualities)
        total = sum(counter.values()) or 1
        return {q: round(c / total, 3) for q, c in counter.most_common()}

    def summary(self) -> dict:
        keys = Counter(
            f"{r.key_root} {r.key_scale}" for r in self.references.values()
        )
        tempos = [r.tempo for r in self.references.values() if r.tempo]
        bass_styles = Counter(
            r.parts["bass"]["articulation"]["style"]
            for r in self.references.values()
            if "bass" in r.parts and "articulation" in r.parts["bass"]
        )
        return {
            "references": len(self.references),
            "keys": dict(keys.most_common(8)),
            "tempo_range": [min(tempos), max(tempos)] if tempos else [],
            "median_tempo": round(statistics.median(tempos), 1) if tempos else None,
            "bass_styles": dict(bass_styles),
            "chord_qualities": self.common_qualities(),
            "top_progressions": self.common_progressions(6),
            "top_movements": self.common_movements(8),
        }

    # -- generating from what was learned ----------------------------

    def transition_model(self) -> dict[int, list[tuple[int, float]]]:
        """Weighted chord-to-chord moves, as degree -> [(next, probability)].

        Whole progressions rarely repeat across a varied corpus; the moves
        inside them do. Walking this model produces a progression that is new
        but behaves like the references.
        """
        counts: dict[int, Counter[int]] = defaultdict(Counter)
        for reference in self.references.values():
            degrees = [d for d in reference.progression if d]
            for a, b in zip(degrees, degrees[1:]):
                counts[a][b] += 1

        model: dict[int, list[tuple[int, float]]] = {}
        for degree, followers in counts.items():
            total = sum(followers.values())
            model[degree] = [(n, c / total) for n, c in followers.most_common()]
        return model

    def suggest_progression(
        self,
        length: int = 4,
        start: int = 1,
        seed: int | None = None,
        avoid_repeats: bool = True,
        cadence: bool = True,
    ) -> dict:
        """Walk the learned transitions to propose a new progression.

        `avoid_repeats` suppresses a degree following itself -- corpora are full
        of held chords, and reproducing that faithfully gives a progression that
        does not move. `cadence` steers the last chord home.
        """
        import random

        rng = random.Random(seed)
        model = self.transition_model()
        if not model:
            raise ValueError("nothing learned yet -- run learn_references first")

        degrees = [start]
        for position in range(1, length):
            options = model.get(degrees[-1]) or model.get(1) or [(1, 1.0)]
            if avoid_repeats and len(options) > 1:
                filtered = [(d, p) for d, p in options if d != degrees[-1]]
                options = filtered or options

            last = position == length - 1
            if cadence and last:
                # Land on a chord that resolves: the tonic or its relative.
                homely = [(d, p) for d, p in options if d in (1, 6, 4, 5)]
                options = homely or options

            population = [d for d, _ in options]
            weights = [p for _, p in options]
            degrees.append(rng.choices(population, weights=weights, k=1)[0])

        qualities = Counter(
            q for r in self.references.values() for q in r.qualities
        )
        extension = "seventh" if sum(
            c for q, c in qualities.items() if "7" in q or "9" in q
        ) > sum(qualities.values()) * 0.25 else "triad"

        return {
            "degrees": degrees,
            "as_text": "-".join(str(d) for d in degrees),
            "suggested_extension": extension,
            "drawn_from": len(self.references),
            "voicing": self.voicing_profile(),
        }

    def voicing_profile(self) -> dict:
        """How the corpus voices its chords -- spread, density, inversions."""
        spreads = [
            r.voicing.get("mean_spread_semitones", 0)
            for r in self.references.values() if r.voicing
        ]
        voices = [
            r.voicing.get("mean_voices", 3)
            for r in self.references.values() if r.voicing
        ]
        inversions = [
            r.voicing.get("inversion_share", 0)
            for r in self.references.values() if r.voicing
        ]
        if not spreads:
            return {}
        spread = statistics.fmean(spreads)
        return {
            "mean_spread_semitones": round(spread, 1),
            "mean_voices": round(statistics.fmean(voices), 1),
            "inversion_share": round(statistics.fmean(inversions), 2),
            # A spread beyond an octave means the chords are voiced open, with
            # the root well below the upper structure.
            "style": "open" if spread > 12 else "close",
        }

    def groove_for(self, role: str) -> Groove | None:
        """Average the feel of every learned part playing this role."""
        grooves = [
            r.parts[role]["groove"]
            for r in self.references.values()
            if role in r.parts and r.parts[role].get("groove")
        ]
        if not grooves:
            return None

        def mean_at(field_name: str, index: int) -> float:
            values = [g[field_name][index] for g in grooves if g.get(field_name)]
            return statistics.fmean(values) if values else 0.0

        return Groove(
            name=f"learned_{role}",
            swing=round(statistics.fmean(g["swing"] for g in grooves), 3),
            push=round(statistics.fmean(g["push"] for g in grooves), 4),
            accents=tuple(int(round(mean_at("accents", i))) for i in range(16)),
            timing=tuple(round(mean_at("timing", i), 4) for i in range(16)),
            jitter=round(statistics.fmean(g["jitter"] for g in grooves), 3),
        )

    def bass_profile(self) -> dict:
        """The bass articulation the corpus favours."""
        articulations = [
            r.parts["bass"]["articulation"]
            for r in self.references.values()
            if "bass" in r.parts and "articulation" in r.parts["bass"]
        ]
        if not articulations:
            return {}
        styles = Counter(a["style"] for a in articulations)
        return {
            "dominant_style": styles.most_common(1)[0][0],
            "styles": dict(styles),
            "mean_onsets_per_bar": round(
                statistics.fmean(a["onsets_per_bar"] for a in articulations), 2
            ),
            "mean_legato": round(
                statistics.fmean(a["legato"] for a in articulations), 2
            ),
        }
