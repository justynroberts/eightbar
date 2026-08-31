"""Composition: tension, cadence, and one idea shared across the parts.

The generator layer writes correct parts, and the perform layer makes them
sound played. What neither does is *compose*: the harmony has no shape beyond
a looping degree list, and the parts are strangers to each other -- the lead,
hook, arp and bass each invent their own material, which is why a generated
track sounds assembled rather than written.

Three ideas fix most of that, and each is standard practice, not invention:

**Tension.** Every chord carries a measurable amount of it -- a dominant
seventh strains towards the tonic, a first-inversion triad is softer than
root position, a diminished chord is all strain. A progression is not a list,
it is a shape drawn in tension: rise towards the cadence, release into the
downbeat. `design_progression` picks chords to follow such a shape.

**The cadence.** Sections end, and how they end is what makes the next one
land. A build that simply loops until the drop wastes the whole point of the
dominant; a build that ends on V makes the drop's I an arrival.
`harmonic_plan` gives every section of an arrangement its own harmonic
treatment: pedal intro, half-cadence build, reharmonised breakdown, lifted
final drop.

**The motif.** Real tracks are built from one short idea that every part
develops. `compose_theme` states a single cell and derives the whole
ensemble from it -- the lead develops it, the hook fragments it, the arp
runs it double-time, the counter-line answers it upside down, and the bass
plays its rhythm on the roots. Relatedness is the single biggest audible
difference between composed and assembled.
"""

from __future__ import annotations

import random
from typing import Any, Sequence

from . import motif, theory

BEATS_PER_BAR = 4.0
Note = dict[str, Any]

# ---------------------------------------------------------------- tension

# How much strain each chord quality carries, 0..1. Consonance ranks are old
# and settled: perfect consonances at the bottom, the tritone-bearing
# dominants and diminished chords at the top.
QUALITY_TENSION: dict[str, float] = {
    "major": 0.12, "minor": 0.18, "power": 0.10,
    "major6": 0.22, "minor6": 0.30, "sus2": 0.32, "sus4": 0.38,
    "major7": 0.30, "minor7": 0.34, "add9": 0.28,
    "dominant7": 0.62, "major9": 0.38, "minor9": 0.42, "dominant9": 0.66,
    "minor11": 0.48, "major11": 0.50, "dominant11": 0.68,
    "major13": 0.52, "minor13": 0.52, "dominant13": 0.70,
    "half_diminished7": 0.74, "diminished": 0.80, "diminished7": 0.85,
    "augmented": 0.78,
}

# How far each scale degree stands from home. The dominant and the leading
# tone want to move; the tonic is where they want to go. One table per mode
# family, because vi in major is restful where VI in minor is colour.
DEGREE_TENSION_MAJOR: dict[int, float] = {
    1: 0.05, 6: 0.25, 3: 0.35, 4: 0.42, 2: 0.48, 5: 0.70, 7: 0.85,
}
DEGREE_TENSION_MINOR: dict[int, float] = {
    1: 0.05, 3: 0.28, 6: 0.30, 7: 0.50, 4: 0.48, 2: 0.62, 5: 0.68,
}

# The named shapes a progression's tension can follow. Values are targets for
# each quarter of the progression; they are interpolated to any length.
ARCS: dict[str, tuple[float, ...]] = {
    # The workhorse: away from home, strain peaks at the penultimate chord,
    # cadence releases. This is what "a progression that resolves" means.
    "cadence": (0.15, 0.45, 0.70, 0.10),
    # Strain never resolves -- for builds, which hand their resolution to the
    # next section's downbeat.
    "rise": (0.15, 0.35, 0.55, 0.75),
    # Home, away, home: verses that need to sit still.
    "arch": (0.10, 0.50, 0.45, 0.15),
    # Gentle rocking, never far from home: intros, outros, ambient beds.
    "calm": (0.08, 0.25, 0.15, 0.25),
    # High strain throughout, released only at the loop point: dark rooms.
    "drive": (0.40, 0.60, 0.55, 0.70),
}


def _degree_table(scale: str) -> dict[int, float]:
    key = theory.normalise_scale(scale)
    if key in ("major", "ionian", "lydian", "mixolydian", "pentatonic_major"):
        return DEGREE_TENSION_MAJOR
    return DEGREE_TENSION_MINOR


def chord_tension(degree: int, quality: str, scale: str = "minor") -> float:
    """How much strain one chord carries in context, 0..1."""
    table = _degree_table(scale)
    functional = table.get(((degree - 1) % 7) + 1, 0.5)
    colour = QUALITY_TENSION.get(quality, 0.4)
    return round(0.6 * functional + 0.4 * colour, 3)


def tension_curve(
    degrees: Sequence[int], scale: str = "minor", qualities: Sequence[str] | None = None
) -> list[float]:
    """The tension of each chord in a progression, in order."""
    out = []
    for index, degree in enumerate(degrees):
        quality = (
            qualities[index]
            if qualities and index < len(qualities)
            else theory.triad_quality(scale, degree)
        )
        out.append(chord_tension(degree, quality, scale))
    return out


def _targets(arc: str, length: int) -> list[float]:
    """Interpolate a named arc to a progression length."""
    anchors = ARCS.get(arc, ARCS["cadence"])
    if length == 1:
        return [anchors[0]]
    out = []
    for index in range(length):
        position = index / (length - 1) * (len(anchors) - 1)
        low = int(position)
        high = min(low + 1, len(anchors) - 1)
        frac = position - low
        out.append(anchors[low] * (1 - frac) + anchors[high] * frac)
    return out


def design_progression(
    scale: str = "minor",
    length: int = 4,
    arc: str = "cadence",
    start: int = 1,
    seed: int | None = None,
) -> list[int]:
    """Choose degrees so the harmony's tension follows a shape.

    This is the difference between a progression and a list: the chords are
    picked for where the strain should be, not for which numbers look nice.
    A "cadence" arc lands its highest-tension chord second to last and comes
    home; a "rise" arc for a build never resolves at all, because the drop's
    downbeat is the resolution.
    """
    rng = random.Random(seed)
    table = _degree_table(scale)
    targets = _targets(arc, length)

    degrees = [start]
    for index in range(1, length):
        wanted = targets[index]
        # Candidates ranked by how close their tension sits to the target,
        # with repeats of the previous chord discouraged and a little noise so
        # two calls with different seeds differ.
        previous = degrees[-1]
        scored = []
        for degree, strain in table.items():
            cost = abs(strain - wanted)
            if degree == previous:
                cost += 0.35
            if index >= 2 and degree == degrees[-2] and degree == previous:
                cost += 0.5
            cost += rng.uniform(0.0, 0.12)
            scored.append((cost, degree))
        degrees.append(min(scored)[1])

    # A cadence arc must actually cadence: penultimate pulls, last resolves.
    if arc == "cadence" and length >= 3:
        pull = 5 if table is DEGREE_TENSION_MINOR else 5
        degrees[-2] = pull
        degrees[-1] = start
    if arc == "rise" and length >= 2:
        degrees[-1] = 5  # the half cadence: strain handed to the next section
    return degrees


# ----------------------------------------------------------- section harmony

# What each kind of section does to the track's main progression. This is the
# part of arranging that loop tools skip entirely: the harmony of a breakdown
# is not the harmony of the drop played quieter.
SECTION_TREATMENTS: dict[str, dict[str, Any]] = {
    "intro": {"mode": "pedal", "why": "hold the tonic; arrival needs somewhere to arrive from"},
    "outro": {"mode": "plagal", "why": "the amen cadence settles without drama"},
    "build": {"mode": "half_cadence", "why": "end on V so the drop's downbeat is a resolution"},
    "buildup": {"mode": "half_cadence", "why": "end on V so the drop's downbeat is a resolution"},
    "rise": {"mode": "half_cadence", "why": "end on V so the drop's downbeat is a resolution"},
    "drop": {"mode": "main", "why": "the progression the track is about"},
    "chorus": {"mode": "main", "why": "the progression the track is about"},
    "hit": {"mode": "main", "why": "the progression the track is about"},
    "climax": {"mode": "lift", "why": "the same music a step higher reads as bigger, not different"},
    "breakdown": {"mode": "reharmonise", "why": "familiar melody over changed harmony is the emotional pivot"},
    "break": {"mode": "reharmonise", "why": "familiar melody over changed harmony is the emotional pivot"},
    "bridge": {"mode": "reharmonise", "why": "familiar melody over changed harmony is the emotional pivot"},
    "verse": {"mode": "thin", "why": "fewer chords, more room; save the full progression for the chorus"},
    "groove": {"mode": "thin", "why": "fewer chords, more room; save the full progression for the chorus"},
}


def _reharmonise(degrees: list[int], scale: str) -> list[int]:
    """Substitute chords under the same melody notes.

    Relative substitution is the safe workhorse: each chord swaps for the
    diatonic chord a third away, which shares two of its three tones -- so a
    melody written over the original still fits, but the ground has moved.
    """
    table = _degree_table(scale)
    substitute = {1: 6, 6: 4, 4: 2, 2: 7, 5: 3, 3: 1, 7: 5}
    out = [substitute.get(((d - 1) % 7) + 1, d) for d in degrees]
    # Keep the final resolution honest.
    if degrees and degrees[-1] == 1:
        out[-1] = 1
    return out


def harmonic_plan(
    sections: Sequence[dict],
    degrees: Sequence[int],
    key: str = "A",
    scale: str = "minor",
) -> list[dict]:
    """Give every section of an arrangement its own harmonic treatment.

    One progression looping for six minutes is the loudest tell of generated
    music at the structural level. The plan keeps the *material* constant --
    it is all derived from the main degrees -- while the treatment changes:
    the intro pedals, builds end on the dominant, breakdowns reharmonise, and
    a final climax lifts the whole thing a tone.
    """
    main = list(degrees)
    out = []
    drops_seen = 0
    total_drops = sum(
        1 for s in sections
        if SECTION_TREATMENTS.get(str(s.get("name", "")).lower(), {}).get("mode")
        in ("main", "lift")
    )

    for section in sections:
        name = str(section.get("name", "")).lower()
        treatment = SECTION_TREATMENTS.get(name, {"mode": "main", "why": ""})
        mode = treatment["mode"]
        plan_key, plan_scale = key, scale
        plan_degrees = main

        if mode == "pedal":
            plan_degrees = [1]
        elif mode == "plagal":
            plan_degrees = [4, 1]
        elif mode == "half_cadence":
            plan_degrees = main[:-1] + [5] if len(main) > 1 else [5]
        elif mode == "thin":
            # Half the harmonic rhythm: every other chord, held twice as long.
            plan_degrees = main[::2] or main
        elif mode == "reharmonise":
            plan_degrees = _reharmonise(main, scale)
        elif mode == "main":
            drops_seen += 1
            # The last drop of the track lifts, if there are at least two.
            if total_drops >= 2 and drops_seen == total_drops:
                mode = "lift"
        if mode == "lift":
            plan_key = theory.NOTE_NAMES[
                (theory.note_to_pitch_class(key) + 2) % 12
            ]
            treatment = {
                "mode": "lift",
                "why": "the same music a tone higher reads as bigger, not different",
            }

        out.append({
            **{k: section[k] for k in ("name", "start_bar", "bars") if k in section},
            "treatment": mode if mode != "main" else treatment["mode"],
            "why": treatment["why"],
            "key": plan_key,
            "scale": plan_scale,
            "degrees": list(plan_degrees),
            "tension": tension_curve(plan_degrees, plan_scale),
        })
    return out


# ---------------------------------------------------------------- the theme

def _phrase_from_cell(
    cell: motif.Cell,
    key: str,
    scale: str,
    chords: list[theory.Chord],
    bars_per_chord: float,
    octave: int,
    seed: int | None,
    plan: list[str],
) -> list[Note]:
    """build_phrase, but from an explicit cell rather than shape/rhythm names.

    This is the guarantee that every part grows from the *same* motif: the
    cell is constructed once by the caller and passed in, so a learned cell
    from the corpus develops exactly like a written one.
    """
    notes: list[Note] = []
    for index, chord in enumerate(chords):
        at = index * bars_per_chord * BEATS_PER_BAR
        operation = plan[index % len(plan)]
        variant = motif.develop(
            cell, operation, seed=None if seed is None else seed + index
        )
        rendered = motif.render(
            variant, key, scale, octave=octave,
            anchor_degree=chord.degree, at=at,
            velocity=92, chord_tones=chord.pitches,
        )
        limit = at + bars_per_chord * BEATS_PER_BAR
        for note in rendered:
            if float(note["start"]) < limit:
                note["duration"] = min(
                    float(note["duration"]), limit - float(note["start"])
                )
                notes.append(note)
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    return notes


def _cell_rhythm_starts(cell: motif.Cell) -> list[float]:
    """Where the motif's notes fall inside its bar, in beats."""
    return [float(s) for s in cell.rhythm]


def compose_theme(
    key: str,
    scale: str,
    chords: list[theory.Chord],
    bars_per_chord: float = 1.0,
    octave: int = 4,
    seed: int | None = None,
    shape: str = "arch",
    rhythm: str = "syncopated",
    cell: motif.Cell | None = None,
) -> dict[str, list[Note]]:
    """One motif, five parts, all of them relatives.

    The lead states and develops the cell; the hook is its most memorable
    fragment, higher and sparser; the counter-line answers it upside down and
    fills its gaps; the arp runs it double-time as texture; the bass takes
    only its rhythm and plays the roots with it. Every part shares DNA with
    every other, which is what makes an ensemble sound written.

    Returns note lists by role; register and dynamics are the perform
    layer's job, so parts are rendered where the material naturally sits.
    """
    if cell is None:
        cell = motif.make_cell(shape, rhythm, seed=seed)
    total_bars = len(chords) * bars_per_chord

    # --- lead: the full statement-and-development treatment
    # The same shape/rhythm/seed as the cell above, so build_phrase constructs
    # the identical cell -- without this the lead grew from a different motif
    # and the "shared DNA" was a comment, not a fact.
    lead = _phrase_from_cell(
        cell, key, scale, chords, bars_per_chord, octave + 1, seed,
        plan=["repeat", "sequence_up", "repeat", "answer"],
    )

    # --- hook: the cell's strongest fragment, augmented so it breathes,
    # stated once per pair of chords rather than continuously
    fragment = motif.augment(motif.fragment(cell, keep=3), factor=2.0)
    hook: list[Note] = []
    for index in range(0, len(chords), 2):
        chord = chords[index]
        at = index * bars_per_chord * BEATS_PER_BAR
        hook.extend(motif.render(
            fragment, key, scale, octave=octave + 2,
            anchor_degree=chord.degree, at=at,
            chord_tones=chord.pitches, velocity=100,
        ))

    # --- counter-line: the answer, inverted, entering where the lead rests.
    # Offset by half the cell so the two lines interlock rather than double.
    inverted = motif.answer(motif.invert(cell))
    counter: list[Note] = []
    for index, chord in enumerate(chords):
        at = index * bars_per_chord * BEATS_PER_BAR + BEATS_PER_BAR / 2
        rendered = motif.render(
            inverted, key, scale, octave=octave,
            anchor_degree=chord.degree, at=at,
            chord_tones=chord.pitches, velocity=84,
        )
        limit = (index + 1) * bars_per_chord * BEATS_PER_BAR
        counter.extend(n for n in rendered if float(n["start"]) < limit)

    # --- arp: the cell diminished into perpetual motion, twice per chord
    quick = motif.diminish(cell, factor=0.5)
    arp: list[Note] = []
    for index, chord in enumerate(chords):
        base = index * bars_per_chord * BEATS_PER_BAR
        for half in range(int(bars_per_chord * 2)):
            arp.extend(motif.render(
                quick, key, scale, octave=octave + 1,
                anchor_degree=chord.degree,
                at=base + half * (BEATS_PER_BAR / 2),
                chord_tones=chord.pitches, velocity=88,
            ))

    # --- bass: the motif's rhythm, the chord's roots. Rhythmic DNA without
    # melodic wandering -- the bass's job is the root, in the motif's accent.
    starts = _cell_rhythm_starts(cell)
    bass: list[Note] = []
    for index, chord in enumerate(chords):
        base = index * bars_per_chord * BEATS_PER_BAR
        root = min(chord.pitches) - 24
        span = bars_per_chord * BEATS_PER_BAR
        for bar in range(int(bars_per_chord)):
            for position, start in enumerate(starts):
                at = base + bar * BEATS_PER_BAR + start
                if at >= base + span:
                    continue
                gap = (starts[position + 1] - start
                       if position + 1 < len(starts)
                       else BEATS_PER_BAR - start)
                # The last note of the bar walks a fifth, so the line moves.
                pitch = root + (7 if position == len(starts) - 1 and index % 2 else 0)
                bass.append({
                    "pitch": pitch, "start": round(at, 4),
                    "duration": round(max(0.1, gap * 0.85), 4),
                    "velocity": 104,
                })

    clip = lambda notes: [  # noqa: E731 -- trim anything past the loop end
        n for n in notes if float(n["start"]) < total_bars * BEATS_PER_BAR
    ]
    return {
        "lead": clip(lead),
        "hook": clip(hook),
        "counter": clip(counter),
        "arp": clip(arp),
        "bass": clip(bass),
    }


# ------------------------------------------------------------- counterpoint

def parallel_perfects(
    line_a: Sequence[Note], line_b: Sequence[Note]
) -> list[dict]:
    """Consecutive perfect intervals moving in the same direction.

    Parallel fifths and octaves fuse two lines into one thick line -- which is
    why four centuries of counterpoint teaching bans them between independent
    parts. Between a bass and a melody they are the most audible way for
    generated parts to accidentally stop being independent.
    """
    def onsets(notes: Sequence[Note]) -> dict[float, int]:
        # Highest pitch per onset: the sounding top of the line.
        out: dict[float, int] = {}
        for note in notes:
            at = round(float(note["start"]), 3)
            pitch = int(note["pitch"])
            if at not in out or pitch > out[at]:
                out[at] = pitch
        return out

    a, b = onsets(line_a), onsets(line_b)
    shared = sorted(set(a) & set(b))
    found = []
    for first, second in zip(shared, shared[1:]):
        interval_1 = abs(a[first] - b[first]) % 12
        interval_2 = abs(a[second] - b[second]) % 12
        move_a = a[second] - a[first]
        move_b = b[second] - b[first]
        if (
            interval_1 in (0, 7) and interval_1 == interval_2
            and move_a != 0 and move_b != 0
            and (move_a > 0) == (move_b > 0)
        ):
            found.append({
                "at_beat": first,
                "interval": "octave" if interval_1 == 0 else "fifth",
                "pitches": [a[first], b[first], a[second], b[second]],
            })
    return found
