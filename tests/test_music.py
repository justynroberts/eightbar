"""Music theory, generators, variations and arrangement maths.

None of this needs Ableton -- it is the part that must be right before any
socket is opened.
"""

from __future__ import annotations

import pytest

from ableton_ai import arrangement, generators, theory, variations


# --------------------------------------------------------------- theory

def test_note_names_round_trip():
    assert theory.note_to_pitch_class("C") == 0
    assert theory.note_to_pitch_class("Eb") == 3
    assert theory.note_to_pitch_class("F#") == 6
    assert theory.pitch_name(60) == "C3"


def test_c_minor_triad_is_c_eb_g():
    chord = theory.build_chord("C", "minor", 1, octave=3)
    assert [p % 12 for p in chord.pitches] == [0, 3, 7]
    assert chord.name == "Cm"


def test_dominant_seventh_on_the_fifth_degree():
    chord = theory.build_chord("C", "minor", 5, extension="seventh")
    assert chord.quality == "dominant7"


def test_every_progression_stays_in_key():
    """A generated progression must only use notes from the scale."""
    scale_classes = {p % 12 for p in theory.scale_pitches("C", "minor", octaves=1)}
    for chord in theory.build_progression("C", "minor", [1, 6, 4, 5]):
        assert {p % 12 for p in chord.pitches} <= scale_classes


def test_voice_leading_reduces_movement():
    raw = theory.build_progression("C", "minor", [1, 6, 4, 5], smooth=False)
    smooth = theory.build_progression("C", "minor", [1, 6, 4, 5], smooth=True)

    def travel(chords):
        return sum(
            abs(sum(a.pitches) / len(a.pitches) - sum(b.pitches) / len(b.pitches))
            for a, b in zip(chords, chords[1:])
        )

    assert travel(smooth) <= travel(raw)


@pytest.mark.parametrize(
    "spec,expected",
    [("1-6-4-5", [1, 6, 4, 5]), ("i-VI-IV-V", [1, 6, 4, 5]),
     ([1, 5], [1, 5]), ("1,4,5", [1, 4, 5])],
)
def test_degree_parsing(spec, expected):
    assert theory.parse_degrees(spec) == expected


def test_unknown_scale_is_rejected_with_help():
    with pytest.raises(ValueError, match="unknown scale"):
        theory.normalise_scale("wibble")


# ----------------------------------------------------------- generators

def test_drums_fill_the_requested_bars():
    notes = generators.generate_drums("house", bars=4)
    assert notes
    end = max(n["start"] + n["duration"] for n in notes)
    assert 12.0 < end <= 16.0


def test_every_drum_pattern_generates():
    for name in generators.DRUM_PATTERNS:
        assert generators.generate_drums(name, bars=2)


def test_notes_never_leave_midi_range():
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    parts = [
        generators.generate_chords(chords),
        generators.generate_bassline(chords),
        generators.generate_arpeggio(chords, octaves=3),
        generators.generate_melody("C", "minor", bars=4),
        generators.generate_hook(chords),
        generators.generate_riser("C", "minor", bars=4),
        generators.generate_buildup(bars=8),
    ]
    for notes in parts:
        for note in notes:
            assert 0 <= note["pitch"] <= 127
            assert 1 <= note["velocity"] <= 127
            assert note["start"] >= 0
            assert note["duration"] > 0


def test_bassline_sits_below_the_chords():
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    bass = generators.generate_bassline(chords, octave=1)
    chord_notes = generators.generate_chords(chords)
    assert max(n["pitch"] for n in bass) < min(n["pitch"] for n in chord_notes)


def test_buildup_accelerates_and_gets_louder():
    notes = generators.generate_buildup(bars=8, add_hats=False, instrument="snare")
    snares = [n for n in notes if n["pitch"] == generators.DRUM_MAP["snare"]]
    first_half = [n for n in snares if n["start"] < 16]
    second_half = [n for n in snares if n["start"] >= 16]
    assert len(second_half) > len(first_half)          # accelerating
    avg = lambda xs: sum(n["velocity"] for n in xs) / len(xs)
    assert avg(second_half) > avg(first_half)          # and louder


def test_seed_makes_generation_reproducible():
    a = generators.generate_drums("house", bars=4, humanise=0.5, seed=7)
    b = generators.generate_drums("house", bars=4, humanise=0.5, seed=7)
    c = generators.generate_drums("house", bars=4, humanise=0.5, seed=8)
    assert a == b
    assert a != c


# ----------------------------------------------------------- variations

def test_every_mutation_and_recipe_runs():
    source = generators.generate_drums("house", bars=4)
    for name in list(variations.MUTATIONS) + list(variations.RECIPES):
        result = variations.apply(source, [name], seed=1)
        assert result, f"{name} produced nothing"
        for note in result:
            assert 0 <= note["pitch"] <= 127
            assert 1 <= note["velocity"] <= 127


def test_variations_actually_differ_from_the_source():
    source = generators.generate_drums("house", bars=4)
    produced = variations.variation_set(source, count=5, seed=3)
    assert produced[0][0] == "original"
    assert all(notes != source for _label, notes in produced[1:])


def test_thin_never_empties_a_part():
    source = generators.generate_drums("minimal", bars=1)
    assert variations.thin(source, amount=1.0, seed=1)


def test_unknown_mutation_is_rejected_with_help():
    with pytest.raises(ValueError, match="unknown mutation"):
        variations.apply([{"pitch": 60, "start": 0, "duration": 1, "velocity": 100}],
                         ["nonsense"])


# ---------------------------------------------------------- arrangement

def test_six_minutes_at_128_is_192_bars():
    sections = arrangement.plan(target_seconds=360, tempo=128, template="house")
    summary = arrangement.summarise(sections, 128)
    assert summary["total_bars"] == 192
    assert summary["duration"] == "6:00"


@pytest.mark.parametrize("template", sorted(arrangement.TEMPLATES))
@pytest.mark.parametrize("seconds", [120, 240, 360, 480])
def test_plans_are_contiguous_and_on_phrase_boundaries(template, seconds):
    sections = arrangement.plan(seconds, 128, template)
    cursor = 0
    for section in sections:
        assert section.start_bar == cursor, "sections must not overlap or gap"
        assert section.bars % 8 == 0, "sections must land on 8-bar phrases"
        assert section.bars > 0
        cursor = section.end_bar


@pytest.mark.parametrize("seconds", [120, 240, 360, 600])
def test_plans_land_near_the_requested_duration(seconds):
    sections = arrangement.plan(seconds, 128, "big_room")
    actual = arrangement.summarise(sections, 128)["total_seconds"]
    # Phrase rounding means we can't hit it exactly, but we should be close.
    assert abs(actual - seconds) < seconds * 0.2


def test_edm_alias_resolves_and_has_drops():
    sections = arrangement.plan(360, 128, "edm")
    kinds = [s.kind for s in sections]
    assert "drop" in kinds and "build" in kinds and "breakdown" in kinds
    # Every build must be immediately followed by a drop -- that is the point.
    for a, b in zip(sections, sections[1:]):
        if a.kind == "build":
            assert b.kind == "drop", f"{a.name} is not followed by a drop"


def test_placeholder_roles_are_reserved_in_edm_templates():
    for name in ("house", "big_room", "future_bass", "trance"):
        roles = {r for s in arrangement.plan(360, 128, name) for r in s.roles}
        assert "vocal" in roles
        assert "riser" in roles and "impact" in roles
