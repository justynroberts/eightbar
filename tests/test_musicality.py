"""Harmony variation, bass articulation, leads and learning from MIDI."""

from __future__ import annotations

import pytest

from ableton_ai import basslines, corpus, generators, harmony, leads, theory


# ------------------------------------------------------------- harmony

@pytest.mark.parametrize("recipe", sorted(harmony.RECIPES))
def test_every_variation_preserves_the_loop_length(recipe):
    """A variation must not lengthen the loop -- it substitutes, not appends."""
    steps = harmony.vary([1, 6, 4, 5], "minor", recipe, seed=1)
    assert harmony.total_bars(steps) == pytest.approx(4.0)
    assert all(s.bars > 0 for s in steps)


@pytest.mark.parametrize("recipe", sorted(harmony.RECIPES))
def test_every_variation_builds_real_chords(recipe):
    steps = harmony.vary([1, 6, 4, 5], "minor", recipe, seed=2)
    chords, durations = harmony.build("C", "minor", steps)
    assert len(chords) == len(durations) == len(steps)
    for chord in chords:
        assert all(0 <= p <= 127 for p in chord.pitches)


def test_borrowing_in_minor_gives_a_major_five():
    """The borrowed major V is the whole point -- it pulls home."""
    steps = harmony.borrow(harmony.as_steps([1, 4, 5, 1]), "minor", count=4, seed=0)
    five = next(s for s in steps if s.degree == 5)
    assert five.quality == "major"
    chords, _ = harmony.build("C", "minor", steps)
    assert chords[2].name == "G"          # G major, not Gm


def test_secondary_dominant_lands_before_its_target():
    steps = harmony.secondary_dominant(harmony.as_steps([1, 6, 4, 5]), 1, seed=1)
    inserted = [i for i, s in enumerate(steps) if s.label.startswith("V/")]
    assert inserted, "no secondary dominant was inserted"
    index = inserted[0]
    target = int(steps[index].label.split("/")[1])
    assert steps[index + 1].degree == target
    assert steps[index].quality == "dominant7"


def test_turnaround_splits_the_final_bar():
    steps = harmony.half_bar_turnaround(harmony.as_steps([1, 6, 4, 5]), seed=1)
    assert steps[-1].label == "turnaround"
    assert steps[-1].bars == 0.5 and steps[-2].bars == 0.5


def test_unknown_variation_is_rejected_with_help():
    with pytest.raises(ValueError, match="unknown variation"):
        harmony.vary([1, 6, 4, 5], "minor", "nonsense")


# ------------------------------------------------------------ basslines

@pytest.mark.parametrize("style", sorted(basslines.STYLES))
def test_every_bass_style_generates_valid_notes(style):
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    notes = basslines.generate(chords, style, seed=3)
    assert notes
    for note in notes:
        assert 0 <= note["pitch"] <= 127
        assert 1 <= note["velocity"] <= 127
        assert note["duration"] > 0


def test_rolling_bass_keeps_out_of_the_kick():
    """The rolling feel is entirely about sitting in the gaps."""
    from ableton_ai.groove import kick_onsets

    drums = generators.generate_drums("four_on_floor", 4, instruments=["kick"])
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    notes = basslines.generate(chords, "rolling", against_drums=drums, seed=1)

    onsets = {round(o, 2) for o in kick_onsets(drums)}
    assert not [n for n in notes if round(float(n["start"]), 2) in onsets]


def test_octave_style_keeps_both_octaves():
    """Regression: avoiding the kick used to delete every root note."""
    drums = generators.generate_drums("four_on_floor", 4, instruments=["kick"])
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    notes = basslines.generate(chords, "octave", against_drums=drums, seed=1)
    pitches = {int(n["pitch"]) for n in notes}
    roots = {p for p in pitches if p < 48}
    assert roots and any(p >= 48 for p in pitches), "lost an octave"


def test_walking_bass_uses_more_than_the_root():
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    walking = basslines.generate(chords, "walking", seed=1)
    rolling = basslines.generate(chords, "rolling", seed=1)
    assert len({n["pitch"] for n in walking}) > len({n["pitch"] for n in rolling}) - 1


def test_styles_actually_differ_from_each_other():
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    shapes = {
        style: tuple(round(float(n["start"]), 3) for n in
                     basslines.generate(chords, style, seed=1))
        for style in ("rolling", "offbeat", "stab", "sustained")
    }
    assert len(set(shapes.values())) == len(shapes)


def test_unknown_bass_style_is_rejected_with_help():
    with pytest.raises(ValueError, match="unknown bass style"):
        basslines.generate([], "wobblewobble")


# ---------------------------------------------------------------- leads

@pytest.mark.parametrize("style", sorted(leads.STYLES))
def test_every_lead_style_generates_valid_notes(style):
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    notes = leads.generate(chords, "C", "minor", style, bars_per_chord=2, seed=1)
    assert notes
    for note in notes:
        assert 0 <= note["pitch"] <= 127
        assert 1 <= note["velocity"] <= 127


def test_a_soaring_lead_actually_climbs():
    """The arc across the phrase is the point; a flat lead is a failed lead."""
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    notes = leads.generate(chords, "C", "minor", "soaring", bars_per_chord=2, seed=1)

    first_quarter = notes[: len(notes) // 4]
    last_quarter = notes[-len(notes) // 4:]
    start_pitch = sum(n["pitch"] for n in first_quarter) / len(first_quarter)
    end_pitch = sum(n["pitch"] for n in last_quarter) / len(last_quarter)

    assert end_pitch > start_pitch + 6, "the line does not climb"
    assert notes[-1]["pitch"] == max(n["pitch"] for n in notes), "no peak at the end"
    # And it gets louder as it rises.
    assert last_quarter[-1]["velocity"] > first_quarter[0]["velocity"]


def test_a_soaring_lead_is_continuous_sixteenths():
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    notes = leads.generate(chords, "C", "minor", "soaring", bars_per_chord=2, seed=1)
    gaps = [
        round(float(b["start"]) - float(a["start"]), 3)
        for a, b in zip(notes, notes[1:])
    ]
    assert all(g == pytest.approx(0.25) for g in gaps)


def test_lead_notes_agree_with_the_harmony():
    """Strong beats take chord tones; the notes between them may pass.

    This previously asserted that *every* note was a chord tone, which is a
    worse line -- three tones inside an octave is too sparse a ladder for the
    contour to climb, and the result barely moved. The correct property is
    that the line stays in key and lands on the harmony where it matters.
    """
    chords = theory.build_progression("C", "minor", [1])
    notes = leads.generate(chords, "C", "minor", "soaring", bars_per_chord=2, seed=1)

    in_key = {p % 12 for p in theory.scale_pitches("C", "minor", octaves=1)}
    assert {int(n["pitch"]) % 12 for n in notes} <= in_key

    chord_tones = {p % 12 for p in chords[0].pitches}
    downbeats = [n for n in notes if float(n["start"]) % 1.0 == 0]
    assert downbeats
    landed = sum(1 for n in downbeats if int(n["pitch"]) % 12 in chord_tones)
    assert landed / len(downbeats) > 0.8, "the line ignores the chord underneath"


def test_a_lead_stays_inside_one_register():
    """Regression: a lead spanning two and a half octaves collides with every
    other top line, whatever register it is nominally placed in."""
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    for style in ("soaring", "pluck", "rolling"):
        notes = leads.generate(chords, "C", "minor", style, bars_per_chord=2,
                               octave=4, seed=1)
        span = max(n["pitch"] for n in notes) - min(n["pitch"] for n in notes)
        assert span <= 19, f"{style} spans {span} semitones"


def test_top_lines_do_not_all_share_one_octave():
    """Regression: hook, lead and melody were generated in the same register
    and played simultaneously, which cancels all three out."""
    from ableton_ai import generators, melody as melody_mod

    chords = theory.build_progression("C", "minor", [6, 4, 5, 1])
    hook = generators.generate_hook(chords, bars_per_chord=2, octave=5,
                                    root="C", scale="minor", seed=1)
    lead = leads.generate(chords, "C", "minor", "soaring", bars_per_chord=2,
                          octave=4, seed=1)
    line = melody_mod.write("C", "minor", chords, bars=8, octave=4, seed=1)

    def centre(notes):
        return sum(n["pitch"] for n in notes) / len(notes)

    # The hook owns the top; the others sit meaningfully below it.
    assert centre(hook) > centre(lead) + 5
    assert centre(hook) > centre(line) + 5


# --------------------------------------------------------------- corpus

@pytest.fixture
def sample_midi(tmp_path):
    """A two-bar i-VI progression written out as a real MIDI file."""
    import mido

    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    for offset, pitches in ((0, (48, 51, 55)), (0, (44, 48, 51))):
        for i, pitch in enumerate(pitches):
            track.append(mido.Message("note_on", note=pitch, velocity=90,
                                      time=offset if i == 0 else 0))
        for i, pitch in enumerate(pitches):
            track.append(mido.Message("note_off", note=pitch, velocity=0,
                                      time=1920 if i == 0 else 0))
    path = tmp_path / "sample.mid"
    midi.save(str(path))
    return path


def test_reading_midi_gives_notes_in_beats(sample_midi):
    notes, tempo = corpus.read_midi(sample_midi)
    assert len(notes) == 6
    assert tempo == pytest.approx(120.0)
    assert notes[0].start == 0.0
    assert notes[0].duration == pytest.approx(4.0)


def test_key_detection_finds_c_minor(sample_midi):
    notes, _ = corpus.read_midi(sample_midi)
    root, scale, confidence = corpus.detect_key(notes)
    assert (root, scale) == ("C", "minor")
    assert confidence > 0.5


def test_chord_identification_names_a_minor_triad():
    identified = corpus.identify_chord([48, 51, 55])
    assert identified is not None
    root, quality, fit = identified
    assert theory.NOTE_NAMES[root] == "C"
    assert quality == "minor"
    assert fit == 1.0


def test_extracted_rhythm_is_a_sixteen_step_string():
    notes = [corpus.MidiNote(36, t * 0.25, 0.2, 100, 0) for t in (0, 4, 8, 12)]
    assert corpus.extract_rhythm(notes) == "x...x...x...x..."


def test_bass_classification_separates_rolling_from_sustained():
    rolling = [corpus.MidiNote(36, i * 0.25, 0.12, 100, 0) for i in range(16)]
    sustained = [corpus.MidiNote(36, 0.0, 8.0, 100, 0)]
    assert corpus.classify_bass(rolling)["style"] == "rolling"
    assert corpus.classify_bass(sustained)["style"] == "sustained"


def test_learning_a_file_produces_a_usable_reference(sample_midi):
    reference = corpus.learn(sample_midi)
    assert reference.key_root == "C"
    assert reference.progression
    assert reference.notes_total == 6
    assert "chords" in reference.parts


def test_library_round_trips_and_suggests(tmp_path, sample_midi):
    library = corpus.Library(tmp_path / "corpus.json")
    library.add(corpus.learn(sample_midi))
    library.save()

    reloaded = corpus.Library(tmp_path / "corpus.json")
    assert len(reloaded.references) == 1

    # One reference gives a thin model, but it must still produce something.
    suggestion = reloaded.suggest_progression(length=4, seed=1)
    assert len(suggestion["degrees"]) == 4
    assert all(1 <= d <= 7 for d in suggestion["degrees"])


def test_suggesting_without_a_corpus_is_a_clear_error(tmp_path):
    library = corpus.Library(tmp_path / "empty.json")
    with pytest.raises(ValueError, match="nothing learned"):
        library.suggest_progression()


# --------------------------------------------------------------- melody

def _melody(seed=1, bars=8):
    from ableton_ai import melody as m
    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    return m.write("C", "minor", chords, bars=bars, octave=5, seed=seed)


def test_melody_stays_in_key():
    allowed = {p % 12 for p in theory.scale_pitches("C", "minor", octaves=1)}
    for seed in range(1, 6):
        assert {int(n["pitch"]) % 12 for n in _melody(seed)} <= allowed


def test_melody_resolves_rather_than_ending_on_its_peak():
    """A phrase whose last note is also its highest has nowhere to go."""
    for seed in range(1, 8):
        notes = _melody(seed)
        highest = max(int(n["pitch"]) for n in notes)
        assert int(notes[-1]["pitch"]) <= highest


def test_melody_peaks_before_the_end():
    from statistics import median

    positions = []
    for seed in range(1, 9):
        notes = _melody(seed)
        peak = max(notes, key=lambda n: n["pitch"])
        positions.append(float(peak["start"]) / (8 * 4))
    # The climax belongs in the second half but not at the very end.
    assert 0.2 < median(positions) < 0.85


def test_melody_moves_rather_than_repeating():
    """A stuck note reads as a sequencer fault, not as a melody."""
    from itertools import groupby

    for seed in range(1, 8):
        pitches = [int(n["pitch"]) for n in _melody(seed)]
        longest = max(len(list(g)) for _, g in groupby(pitches))
        assert longest <= 6, f"seed {seed} repeats one note {longest} times"
        assert len(set(pitches)) >= 4, "too few distinct pitches to be a melody"


def test_melody_breathes():
    """Rests are structural, not decoration."""
    notes = _melody(2)
    gaps = [
        float(b["start"]) - (float(a["start"]) + float(a["duration"]))
        for a, b in zip(notes, notes[1:])
    ]
    assert any(g > 0.2 for g in gaps), "no rests anywhere in the phrase"


def test_melody_stays_in_a_singable_range():
    for seed in range(1, 6):
        pitches = [int(n["pitch"]) for n in _melody(seed)]
        assert max(pitches) - min(pitches) <= 24, "wider than two octaves"


def test_tension_setting_changes_the_line():
    from ableton_ai import melody as m

    chords = theory.build_progression("C", "minor", [1, 6, 4, 5])
    safe = m.write("C", "minor", chords, bars=8, tension=0.0, seed=4)
    edgy = m.write("C", "minor", chords, bars=8, tension=0.6, seed=4)
    assert [n["pitch"] for n in safe] != [n["pitch"] for n in edgy]


def _generated_ensemble(crowded: bool = False):
    """A set of parts, generated the way the tools generate them.

    `crowded` adds a second and third top line on purpose. Lead, melody, hook
    and arp all sounding at once is genuinely crowded music, and the critique
    is supposed to say so -- see the test that asserts it still does.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from fake_live import FakeBridge

    from ableton_ai import critique
    from ableton_ai.tools import Toolbox

    box = Toolbox(FakeBridge())
    # Seeded: an unseeded ensemble scores differently on every run, which makes
    # a quality guard a coin toss rather than a regression test.
    key = dict(key="A", scale="minor", degrees=[1, 6, 4, 5], bars=8, seed=7)
    spec = [
        ("Chords", "chords", "create_varied_chords",
         dict(extension="ninth", voicing="spread", octave=3, **key)),
        ("Bass", "bass", "create_styled_bass", dict(style="rolling", octave=1, **key)),
        ("Lead", "lead", "create_lead_clip", dict(octave=5, **key)),
        ("Pad", "pad", "create_chord_clip", dict(extension="seventh", octave=3, **key)),
        ("Drums", "drums", "create_drum_clip", dict(pattern="tech_house", bars=8)),
    ]
    if crowded:
        spec += [
            ("Melody", "melody", "create_melody_clip", dict(octave=4, **key)),
            ("Hook", "hook", "create_hook_clip", dict(octave=5, **key)),
            ("Arp", "arp", "create_arpeggio_clip",
             dict(octave=4, octaves=2, rate="1/16", **key)),
        ]
    parts = []
    for index, (name, role, tool, kwargs) in enumerate(spec):
        box.call("create_track", {"name": name, "role": role})
        box.call(tool, {"track_index": index, "clip_index": 0, **kwargs})
        parts.append(critique.Part(
            name, role, box.bridge.tracks[index]["clips"][0]["notes"], 8
        ))
    return parts


def test_generated_music_has_no_serious_faults():
    """The regression guard for musical quality.

    Measured before the performance layer existed: 3 serious and 6 moderate
    faults, scoring 22/100. Flat velocity on four of eight parts, a lead
    filling all 128 sixteenths, the same drum bar eight times, and three top
    lines inside one octave.
    """
    from ableton_ai import critique

    result = critique.critique(_generated_ensemble())
    assert result["counts"]["high"] == 0, [
        f["problem"] + " -- " + f["measured"]
        for f in result["findings"] if f["severity"] == "high"
    ]
    assert result["score"] >= 85, result["summary"]


def test_every_part_is_played_not_typed():
    """Flat velocity is not a quiet dynamic, it is the absence of one."""
    import statistics

    for part in _generated_ensemble():
        velocities = [n["velocity"] for n in part.notes]
        assert statistics.pstdev(velocities) > 3.0, (
            f"{part.name} has velocity sd "
            f"{statistics.pstdev(velocities):.1f}"
        )


def test_dense_lines_are_given_rests():
    """A line that fills every sixteenth has no phrases in it."""
    for part in _generated_ensemble():
        if part.role in ("pad", "drums", "chords", "bass"):
            continue
        onsets = {round(n["start"] * 4) for n in part.notes}
        assert len(onsets) / (part.bars * 16) < 0.8, (
            f"{part.name} occupies {len(onsets) / (part.bars * 16):.0%} of slots"
        )


def test_top_lines_land_in_their_own_registers():
    """Lead, melody, hook and arp used to be written around the same octave.

    They are checked against their bands rather than against each other: a
    two-octave arpeggio necessarily shares register with something, and that is
    the arpeggio doing its job.
    """
    import statistics

    from ableton_ai import perform

    for part in _generated_ensemble(crowded=True):
        band = perform.REGISTER_BANDS.get(part.role)
        if band is None:
            continue
        low, high = band
        centre = statistics.fmean(part.pitches)
        assert low - 6 <= centre <= high + 6, (
            f"{part.role} is centred at {centre:.0f}, outside its band {band}"
        )


def test_crowding_is_still_reported():
    """The measurement has to keep failing the arrangements that deserve it.

    Four top lines at once is crowded music. If tightening the register bands
    ever silences this, the bands have been tuned to beat the metric rather
    than to separate the parts.
    """
    from ableton_ai import critique

    crowded = critique.critique(_generated_ensemble(crowded=True))
    spread = critique.critique(_generated_ensemble())
    assert crowded["score"] < spread["score"], (
        f"crowded {crowded['score']} vs spread {spread['score']}"
    )


def test_drums_mark_the_phrase():
    """Eight identical bars is the loudest thing wrong with a programmed loop."""
    from ableton_ai import generators

    notes = generators.generate_drums(pattern="tech_house", bars=8, seed=1)
    bars: dict[int, list] = {}
    for note in notes:
        bars.setdefault(int(note["start"] // 4), []).append(
            (round(note["start"] % 4, 2), note["pitch"])
        )
    distinct = len({tuple(sorted(v)) for v in bars.values()})
    assert distinct >= 4, f"only {distinct} distinct bars in 8"
