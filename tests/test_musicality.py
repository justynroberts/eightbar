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


def test_drums_mark_the_phrase_once_and_subtly():
    """One small gesture at the end of the eight-bar phrase -- and no more.

    The first version stacked an open hat, a clap stutter, ghost notes and
    extra perc every four bars. The user's correction is the specification:
    "some small variation at the end of bar 8 is enough. Doesn't need big
    rolls, be more subtle." Dance drums earn their hypnosis by NOT varying.
    """
    from ableton_ai import generators

    notes = generators.generate_drums(pattern="tech_house", bars=8, seed=1)
    bars: dict[int, list] = {}
    for note in notes:
        bars.setdefault(int(note["start"] // 4), []).append(
            (round(note["start"] % 4, 2), note["pitch"])
        )
    signatures = [tuple(sorted(v)) for _, v in sorted(bars.items())]

    assert len(set(signatures[:7])) == 1, "bars 1-7 must be identical"
    assert signatures[7] != signatures[0], "bar 8 must mark the phrase"
    # Subtle: the marked bar differs by a couple of notes, not a fill.
    delta = len(set(signatures[7]) ^ set(signatures[0]))
    assert delta <= 4, f"bar 8 differs by {delta} notes -- that is a roll"


# ------------------------------------------------------------- composition

def test_designed_progressions_follow_their_arc():
    """A progression is a shape drawn in tension, not a list of numbers."""
    from ableton_ai import composing

    cadence = composing.design_progression("minor", 4, "cadence", seed=1)
    curve = composing.tension_curve(cadence, "minor")
    assert cadence[-1] == 1, "a cadence arc must come home"
    assert curve[-2] == max(curve), "strain must peak just before the resolution"

    rise = composing.design_progression("minor", 4, "rise", seed=1)
    assert rise[-1] == 5, "a build ends on the dominant; the drop resolves it"

    calm = composing.design_progression("minor", 4, "calm", seed=1)
    assert max(composing.tension_curve(calm, "minor")) < 0.45, calm


def test_harmonic_plan_treats_sections_differently():
    """The breakdown's harmony is not the drop's harmony played quieter."""
    from ableton_ai import composing

    sections = [{"name": n, "start_bar": i * 16, "bars": 16} for i, n in
                enumerate(["intro", "build", "drop", "breakdown", "build",
                           "drop", "outro"])]
    plan = composing.harmonic_plan(sections, [1, 6, 3, 7], key="D", scale="minor")
    by_name = {}
    for entry in plan:
        by_name.setdefault(entry["name"], []).append(entry)

    assert by_name["intro"][0]["degrees"] == [1], "intros pedal the tonic"
    assert by_name["build"][0]["degrees"][-1] == 5, "builds end on V"
    assert by_name["breakdown"][0]["degrees"] != [1, 6, 3, 7], "breakdowns reharmonise"
    assert by_name["drop"][0]["degrees"] == [1, 6, 3, 7]
    assert by_name["drop"][-1]["treatment"] == "lift", "the final drop lifts"
    assert by_name["drop"][-1]["key"] == "E", "a lift is a whole tone up from D"
    assert by_name["outro"][0]["degrees"] == [4, 1], "outros settle plagally"


def test_theme_parts_share_their_dna():
    """One motif, five parts -- the bass must carry the motif's rhythm."""
    from ableton_ai import composing, theory

    chords = theory.build_progression("D", "minor", [1, 6, 3, 7], octave=3)
    theme = composing.compose_theme("D", "minor", chords, bars_per_chord=2.0,
                                    seed=5)
    assert set(theme) == {"lead", "hook", "arp", "bass", "counter"}
    for role, notes in theme.items():
        assert notes, f"{role} came out empty"

    # The bass's onsets inside a bar are the motif's rhythm: the same set of
    # positions the lead states in its first bar.
    def positions(notes, bar=0):
        return {round(n["start"] % 4, 2) for n in notes
                if bar * 4 <= n["start"] < (bar + 1) * 4}

    assert positions(theme["bass"]) == positions(theme["lead"]), (
        "the bass no longer carries the motif's rhythm"
    )

    # The hook is a fragment: strictly sparser than the lead, and higher.
    assert len(theme["hook"]) < len(theme["lead"])
    assert min(n["pitch"] for n in theme["hook"]) > max(
        n["pitch"] for n in theme["counter"]
    ) - 12


def test_parallel_perfects_are_detected_and_absent():
    """The counterpoint detector catches planted fifths and passes the theme."""
    from ableton_ai import composing, theory

    a = [{"pitch": 60, "start": 0.0, "duration": 1, "velocity": 90},
         {"pitch": 62, "start": 1.0, "duration": 1, "velocity": 90}]
    b = [{"pitch": 53, "start": 0.0, "duration": 1, "velocity": 90},
         {"pitch": 55, "start": 1.0, "duration": 1, "velocity": 90}]
    assert len(composing.parallel_perfects(a, b)) == 1

    # Contrary motion is fine.
    c = [{"pitch": 53, "start": 0.0, "duration": 1, "velocity": 90},
         {"pitch": 51, "start": 1.0, "duration": 1, "velocity": 90}]
    assert composing.parallel_perfects(a, c) == []

    chords = theory.build_progression("D", "minor", [1, 6, 3, 7], octave=3)
    theme = composing.compose_theme("D", "minor", chords, bars_per_chord=2.0,
                                    seed=5)
    hits = composing.parallel_perfects(theme["bass"], theme["lead"])
    assert len(hits) <= 1, hits


@pytest.fixture
def box():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from fake_live import FakeBridge

    from ableton_ai.tools import Toolbox

    return Toolbox(FakeBridge())


def test_compose_theme_tool_writes_related_parts(box):
    """The tool lands each part on the track named for its role, creating none."""
    for name in ("Lead", "Hook", "Arp", "Bass", "Melody"):
        box.call("create_track", {"name": name, "role": name.lower()})
    before = len(box.bridge.tracks)

    result = box.call("compose_theme", {
        "key": "D", "scale": "minor", "degrees": [1, 6, 3, 7],
        "bars": 8, "seed": 3,
    })
    assert len(box.bridge.tracks) == before, "compose_theme created a track"
    assert len(result["written"]) == 5, result
    for entry in result["written"]:
        clip = box.bridge.tracks[entry["track_index"]]["clips"][0]
        assert clip["notes"], entry


def test_sections_can_name_their_own_clip(box):
    """The breakdown's clip is chosen by name, and stays out of the ladder.

    First version cached the section slot by appending it to the variation
    ladder, so every full-energy drop picked the breakdown's clip as "the
    biggest variation".
    """
    box.call("create_track", {"name": "Lead", "role": "lead"})
    for slot in (0, 1, 2, 3, 6):
        box.bridge.call(
            "create_clip", track_index=0, clip_index=slot, length_beats=32.0,
            notes=[{"pitch": 70, "start": 0.0, "duration": 1, "velocity": 90}],
            name=f"slot{slot}",
        )
    result = box.call("arrange_to_timeline", {
        "sections": [
            {"name": "drop", "start_bar": 0, "bars": 16,
             "roles": ["lead"], "energy": 1.0},
            {"name": "breakdown", "start_bar": 16, "bars": 16,
             "roles": ["lead"], "energy": 0.3},
        ],
        "tracks": [{"track_index": 0, "role": "lead",
                    "clip_indices": [0, 1, 2, 3],
                    "clip_by_section": {"breakdown": 6}}],
        "clear_first": False,
    })
    chosen = {d["section"]: d["clip_index"] for d in result["detail"]}
    assert chosen["breakdown"] == 6
    assert chosen["drop"] in (0, 1, 2, 3), chosen


def test_build_track_treats_breakdown_harmony(box):
    """The breakdown gets reharmonised chords and the augmented theme, not a
    quieter copy of the drop."""
    box.call("build_track", {"genre": "trance", "key": "A",
                             "duration_seconds": 320, "seed": 2})
    tracks = {t["name"]: i for i, t in enumerate(box.bridge.tracks)}
    chord_names = {c["name"] for c in
                   box.bridge.arrangement.get(tracks["Chords"], [])}
    assert any("reharmonised" in n for n in chord_names), chord_names

    lead_lane = box.bridge.arrangement.get(tracks["Lead"], [])
    names = {c["name"] for c in lead_lane}
    assert any("augmented" in n for n in names), names
    # The drops must still carry the actual theme, not the breakdown clip.
    assert any("Theme lead" in n for n in names), names


def test_appoggiaturas_land_on_chord_changes_and_resolve():
    """The expressive dissonance: lean on the new chord's strong beat, then
    resolve down by step. At tension 0 there must be none."""
    from ableton_ai import melody, theory

    chords = theory.build_progression("D", "minor", [1, 6, 3, 7], octave=4)

    def leans(notes):
        found = 0
        ordered = sorted(notes, key=lambda n: n["start"])
        for index, note in enumerate(ordered):
            if note["start"] % 8.0 != 0.0 or note["start"] == 0.0:
                continue
            chord = chords[int(note["start"] // 8) % 4]
            if note["pitch"] % 12 in {p % 12 for p in chord.pitches}:
                continue
            found += 1
            # It must resolve: the next note steps down into a chord tone.
            following = ordered[index + 1:]
            assert following, "an appoggiatura with nothing after it"
            nxt = following[0]
            assert nxt["pitch"] < note["pitch"], "the lean must resolve down"
            assert nxt["pitch"] % 12 in {p % 12 for p in chord.pitches}
        return found

    tense = sum(leans(melody.write("D", "minor", chords, bars=8,
                                   tension=0.7, seed=s)) for s in range(6))
    assert tense >= 2, "high tension never produced an appoggiatura"

    flat = sum(leans(melody.write("D", "minor", chords, bars=8,
                                  tension=0.0, seed=s)) for s in range(6))
    assert flat == 0, "tension 0 must mean no dissonance on the downbeat"


def test_learned_motif_becomes_a_workable_cell():
    """A corpus-extracted motif develops exactly like a written one."""
    from ableton_ai import composing, motif, theory

    learned = {
        "intervals": [3, -1, -2, 2],           # semitones, as corpus stores it
        "rhythm": [0.5, 0.5, 1.0, 0.5],        # gaps between onsets
        "range_semitones": 5, "direction": "rising",
    }
    cell = motif.cell_from_learned(learned)
    assert len(cell.degrees) == len(cell.rhythm) == len(cell.durations)
    assert cell.rhythm[0] == 0.0
    assert max(cell.rhythm) < 4.0, "the cell must fit inside a bar"

    chords = theory.build_progression("A", "minor", [1, 6, 4, 5], octave=3)
    theme = composing.compose_theme("A", "minor", chords, bars_per_chord=2.0,
                                    seed=1, cell=cell)
    assert all(theme[role] for role in ("lead", "hook", "arp", "bass", "counter"))

    def positions(notes, bar=0):
        return {round(n["start"] % 4, 2) for n in notes
                if bar * 4 <= n["start"] < (bar + 1) * 4}

    assert positions(theme["bass"]) == positions(theme["lead"]), (
        "the learned cell's rhythm did not reach both parts"
    )


def test_melodic_peak_lands_on_the_harmonic_peak():
    """The climax note belongs over the climax chord.

    Harmony and melody carry tension separately; a composer releases them
    together. The melody's single highest note must fall within the span of
    the progression's highest-tension chord.
    """
    from ableton_ai import melody, theory
    from ableton_ai.composing import chord_tension

    chords = theory.build_progression("D", "minor", [1, 6, 4, 5], octave=4)
    tensions = [chord_tension(c.degree, c.quality, "minor") for c in chords]
    peak_chord = max(range(4), key=lambda i: (tensions[i], i))

    hits = 0
    for seed in range(5):
        notes = melody.write("D", "minor", chords, bars=8, tension=0.3,
                             seed=seed)
        top = max(notes, key=lambda n: n["pitch"])
        if int(top["start"] // 8) == peak_chord:
            hits += 1
    assert hits >= 4, f"peak aligned with the tensest chord only {hits}/5 times"


def test_counterpoint_repair_breaks_parallels_minimally():
    """Planted parallel fifths are dissolved; the bass is never touched."""
    from ableton_ai import composing

    bass = [{"pitch": p, "start": float(i), "duration": 1, "velocity": 90}
            for i, p in enumerate((50, 52, 53, 55))]
    top = [{"pitch": p + 7, "start": float(i), "duration": 1, "velocity": 90}
           for i, p in enumerate((50, 52, 53, 55))]

    repaired, fixed = composing.repair_counterpoint(bass, top, "C", "major")
    assert fixed >= 3
    assert composing.parallel_perfects(bass, repaired) == []
    # Minimal motion: no repaired note moved more than a third.
    for before, after in zip(top, repaired):
        assert abs(after["pitch"] - before["pitch"]) <= 4

    # And the composed theme ships clean against its own bass.
    from ableton_ai import theory
    chords = theory.build_progression("A", "minor", [1, 6, 4, 5], octave=3)
    for seed in range(4):
        theme = composing.compose_theme("A", "minor", chords,
                                        bars_per_chord=2.0, seed=seed)
        for part in ("lead", "counter", "hook"):
            hits = composing.parallel_perfects(theme["bass"], theme[part])
            assert not hits, f"seed {seed} {part}: {hits}"


def test_corpus_styles_cluster_and_filter(tmp_path):
    """Thirty house references and five DnB ones are two tastes, not one."""
    from ableton_ai import corpus as c

    library = c.Library(path=tmp_path / "corpus.json")
    for name, tempo, prog in (("h1", 122, [1, 6, 4, 5]), ("h2", 124, [1, 6, 4, 5]),
                              ("h3", 121, [1, 4, 6, 5])):
        library.add(c.Reference(
            name=name, path=f"/{name}.mid", tempo=tempo, bars=8.0,
            key_root="A", key_scale="minor", key_confidence=0.9,
            progression=prog,
            parts={"bass": {"notes": 16, "articulation":
                            {"style": "rolling", "onsets_per_bar": 4,
                             "legato": 0.4}}},
        ))
    library.add(c.Reference(
        name="dnb1", path="/dnb1.mid", tempo=174, bars=8.0,
        key_root="F", key_scale="minor", key_confidence=0.9,
        progression=[1, 2, 1, 7],
        parts={"bass": {"notes": 32, "articulation":
                        {"style": "reese", "onsets_per_bar": 8,
                         "legato": 0.8}}},
    ))

    styles = library.cluster_styles()
    assert len(styles) == 2, styles
    biggest = next(iter(styles.values()))
    assert biggest["count"] == 3 and biggest["bass_style"] == "rolling"

    # A style-filtered walk only uses that cohort's transitions: the DnB
    # cluster's 2 -> 1 move must never appear in the house walk.
    model = library.transition_model(style="rolling")
    assert 2 not in model, model
    assert 6 in model

    # A loose word matches; nonsense refuses with the list.
    assert library._in_style("fast")[0].name == "dnb1"
    try:
        library._in_style("polka")
        raise AssertionError("unknown style should refuse")
    except ValueError as exc:
        assert "polka" in str(exc)


# --------------------------------------------------- what hit melodies do

def test_hooks_repeat_literally_over_the_moving_harmony():
    """The same pitches every statement -- re-anchoring per chord is what
    made generated hooks forgettable."""
    from ableton_ai import hooks, theory

    chords = theory.build_progression("A", "minor", [1, 6, 4, 5], octave=3)
    for pattern in hooks.HOOK_PATTERNS:
        notes = sorted(hooks.render_hook("A", "minor", chords, bars=8,
                                         pattern=pattern, seed=2),
                       key=lambda n: n["start"])

        def statement(k):
            lo, hi = k * 4 - 0.6, (k + 1) * 4 - 0.6
            return [n["pitch"] for n in notes if lo <= n["start"] < hi]

        assert statement(2) == statement(3), pattern
        # Singable: the whole line inside a tenth.
        span = max(n["pitch"] for n in notes) - min(n["pitch"] for n in notes)
        assert span <= 16, f"{pattern} spans {span} semitones"
        # The cadence bend: the final statement's last note is a tone of
        # the closing chord. (It may equal the repeated statement -- a hook
        # already ending on a chord tone needs no bend, and gets none.)
        closing = {p % 12 for p in chords[-1].pitches}
        last_note = max(notes, key=lambda n: n["start"])
        assert last_note["pitch"] % 12 in closing, pattern


def test_hooks_anticipate_the_downbeat():
    """Pop phrasing: later downbeats are hit an eighth early."""
    from ableton_ai import hooks, theory

    chords = theory.build_progression("A", "minor", [1, 6, 4, 5], octave=3)
    notes = hooks.render_hook("A", "minor", chords, bars=8,
                              pattern="falling_fifth", seed=1)
    pushed = [n for n in notes if n["start"] % 4 == 3.5]
    on_grid_downbeats = [n for n in notes if n["start"] % 4 == 0 and n["start"] > 0]
    assert pushed, "no anticipations at all"
    assert not on_grid_downbeats, "later downbeats should be pushed early"


def test_gap_fill_turns_back_after_a_leap():
    from ableton_ai import hooks

    # An octave leap followed by another rise: the third note must turn back.
    filled = hooks.gap_fill([60, 72, 76], "C", "major")
    assert filled[2] < 72, filled
    # A leap already filled stepwise is left alone.
    assert hooks.gap_fill([60, 72, 71], "C", "major") == [60, 72, 71]


def test_hook_styles_pick_suitable_patterns():
    from ableton_ai import hooks

    for style, expect_any in (("anthem", {"falling_fifth", "leap_and_fill"}),
                              ("melodic_techno", {"drone_fifth",
                                                  "descending_sigh",
                                                  "climb_and_fall"})):
        names = set(hooks.catalog(style))
        assert names & expect_any, (style, names)
    # Nonsense falls back to everything rather than nothing.
    assert len(hooks.catalog("polka")) == len(hooks.HOOK_PATTERNS)


def test_breakdowns_and_climaxes_can_modulate():
    """"Modulate more": the breakdown can go to the relative major, and the
    final drop can take any named move, not only the tone-up lift."""
    from ableton_ai import composing

    sections = [{"name": n} for n in
                ("intro", "build", "drop", "breakdown", "build", "drop")]
    plan = composing.harmonic_plan(sections, [1, 6, 3, 7], key="D",
                                   scale="minor", breakdown="relative",
                                   climax="semitone_lift")
    by = {}
    for entry in plan:
        by.setdefault(entry["name"], []).append(entry)

    breakdown = by["breakdown"][0]
    assert breakdown["key"] == "F" and breakdown["scale"] == "major", breakdown
    assert "relative" in breakdown["treatment"]

    final = by["drop"][-1]
    assert final["key"] == "D#", final
    assert final["treatment"] == "semitone_lift"

    # And every named modulation resolves to a real key.
    for how in composing.MODULATIONS:
        key, scale = composing.modulate("A", "minor", how)
        assert key in __import__("ableton_ai.theory", fromlist=["x"]).NOTE_NAMES


# ------------------------------------------------- ears, pocket, references

def test_taste_weights_the_draw_without_closing_it(tmp_path):
    """Three wins pick more often; the loser stays reachable."""
    from ableton_ai.taste import Taste

    store = Taste(path=tmp_path / "taste.json")
    for _ in range(6):
        store.record("hook_pattern", "penta_loop", "house")
    picks = {store.choose("hook_pattern", ["penta_loop", "falling_fifth"],
                          "house", seed=s) for s in range(60)}
    counts = [store.choose("hook_pattern", ["penta_loop", "falling_fifth"],
                           "house", seed=s) for s in range(200)]
    assert counts.count("penta_loop") > counts.count("falling_fifth") * 2
    assert "falling_fifth" in picks, "taste must narrow the draw, not close it"

    # Context-specific wins outrank general ones; forgetting works.
    store.record("hook_pattern", "falling_fifth", "any")
    assert store.weights("hook_pattern", "house")["penta_loop"] == 12
    store.forget("hook_pattern", "penta_loop", "house")
    assert "penta_loop" not in store.weights("hook_pattern", "house")


def test_audition_lines_up_named_candidates(box, tmp_path):
    from ableton_ai import taste as taste_mod

    box._taste_store = taste_mod.Taste(path=tmp_path / "taste.json")
    box.call("create_track", {"name": "Hook", "role": "hook"})
    result = box.call("audition_hooks", {
        "track_index": 0, "key": "A", "scale": "minor",
        "degrees": [1, 6, 4, 5], "count": 3, "seed": 1,
    })
    assert len(result["auditions"]) == 3
    clips = box.bridge.tracks[0]["clips"]
    assert {0, 1, 2} <= set(clips)
    assert clips[0]["name"].startswith("A: ")
    patterns = {a["pattern"] for a in result["auditions"]}
    assert len(patterns) == 3, "candidates must differ"


def test_pocket_sits_roles_against_the_grid():
    """The bass sits behind the kick; the hats push. Bar one stays anchored."""
    from ableton_ai import perform

    notes = [{"pitch": 40, "start": float(b), "duration": 0.5, "velocity": 100}
             for b in range(8)]
    bass = perform.pocket(notes, "bass")
    hats = perform.pocket(notes, "hat")
    kick = perform.pocket(notes, "kick")

    assert bass[0]["start"] == 0.0, "the first downbeat is the anchor"
    assert all(b["start"] > k["start"] for b, k in zip(bass[1:], kick[1:]))
    assert all(h["start"] < k["start"] for h, k in zip(hats[1:], kick[1:]))
    # Felt, not heard: within 0.03 beats.
    assert all(abs(b["start"] - n["start"]) <= 0.03
               for b, n in zip(bass, notes))


def test_critique_can_measure_against_the_references(tmp_path):
    """A part wildly denser than everything the user fed in gets flagged."""
    from ableton_ai import corpus as c
    from ableton_ai import critique

    library = c.Library(path=tmp_path / "corpus.json")
    library.add(c.Reference(
        name="r1", path="/r1.mid", tempo=124, bars=8.0,
        key_root="A", key_scale="minor", key_confidence=0.9,
        progression=[1, 6, 4, 5],
        parts={"bass": {"notes": 16, "range": [36, 48]}},
    ))

    dense = critique.Part("Bass", "bass", [
        {"pitch": 40, "start": i * 0.25, "duration": 0.2, "velocity": 100}
        for i in range(64)
    ], 8)
    findings = critique.against_references([dense], library)
    assert findings and "unlike your references" in findings[0].problem

    matching = critique.Part("Bass", "bass", [
        {"pitch": 40, "start": i * 0.5, "duration": 0.4, "velocity": 100}
        for i in range(16)
    ], 8)
    assert critique.against_references([matching], library) == []


def test_modern_rhythms_are_in_every_vocabulary(box):
    """Tresillo, dembow, two-step, amapiano: the current decade, requestable."""
    from ableton_ai import generators, hooks, motif

    assert motif.RHYTHM_CELLS["tresillo"] == (0, 3, 6, 8, 11, 14)
    for kit in ("reggaeton", "afrobeats", "amapiano", "two_step"):
        assert kit in generators.DRUM_PATTERNS, kit
    assert generators.normalise_pattern("dembow") == "reggaeton"
    assert "tresillo_fall" in hooks.HOOK_PATTERNS

    box.call("create_track", {"name": "Drums", "role": "drums"})
    result = box.call("create_drum_clip", {
        "track_index": 0, "clip_index": 0, "pattern": "amapiano", "bars": 2,
    })
    assert result.get("summary")

    box.call("create_track", {"name": "Hook", "role": "hook"})
    result = box.call("create_hook_clip", {
        "track_index": 1, "clip_index": 0, "key": "A", "scale": "minor",
        "degrees": [1, 6, 4, 5], "pattern": "tresillo_fall", "bars": 4,
    })
    assert result["pattern"] == "tresillo_fall"


# ----------------------------------------------------------- the chord bank

def _bank_fixture(tmp_path):
    """A miniature references folder shaped exactly like the real one."""
    import shutil

    from ableton_ai import chordbank

    real = chordbank.REFERENCES_DIR
    root = tmp_path / "references"
    (root / "pop style").mkdir(parents=True)
    if real.is_dir():
        source = sorted(real.glob("*.mid"))[0]
        for name in ("A - i VI III VII - Nostalgic Hopeful.mid",
                     "A - i iv v - Dark Mysterious.mid",
                     "D - i VI III VII - Nostalgic Hopeful.mid"):
            shutil.copy(source, root / name)
            shutil.copy(source, root / "pop style" / name)
    return chordbank.ChordBank(root=root)


def test_chord_bank_indexes_the_filename_labels(tmp_path):
    bank = _bank_fixture(tmp_path)
    if not bank.entries:
        pytest.skip("no real reference MIDI available to copy")

    assert bank.summary()["progressions"] == 3
    assert set(bank.moods()) == {"nostalgic", "hopeful", "dark", "mysterious"}

    found = bank.find(mood="nostalgic")
    assert len(found) == 2
    assert all("nostalgic" in e.moods for e in found)
    assert found[0].degrees == [1, 6, 3, 7]

    with pytest.raises(ValueError, match="polka"):
        bank.find(mood="polka")


def test_chord_bank_loads_real_voicings_transposed(tmp_path):
    bank = _bank_fixture(tmp_path)
    if not bank.entries:
        pytest.skip("no real reference MIDI available to copy")

    entry = bank.find(mood="nostalgic", key="A")[0]
    home = bank.load_notes(entry, key="A", bars=8)
    up = bank.load_notes(entry, key="C", bars=8)
    assert home and len(home) == len(up)
    # Minimal movement: C is +3 from A, never +9 down nor -9.
    assert up[0]["pitch"] - home[0]["pitch"] == 3
    # Tiled to the asked-for length.
    assert max(n["start"] for n in home) >= 16.0

    comped = bank.load_notes(entry, key="A", bars=8, style="pop")
    assert comped
    with pytest.raises(ValueError, match="soul"):
        bank.load_notes(entry, key="A", bars=8, style="soul")


def test_real_reference_library_if_present():
    """The actual folder: 696 progressions, four comped styles, sane moods."""
    from ableton_ai import chordbank

    bank = chordbank.ChordBank()
    if not bank.entries:
        pytest.skip("references/ not present")
    s = bank.summary()
    assert s["progressions"] > 600
    assert set(s["styles"]) >= {"pop", "soul"}
    assert s["moods"].get("nostalgic", 0) > 100

    e = bank.pick(mood="dark", key="D", seed=1)
    notes = bank.load_notes(e, key="D", bars=8)
    assert len(notes) >= 16


def test_reharmonisation_stays_in_key():
    """A reharmonised breakdown must not introduce out-of-key notes.

    The clip the user heard as dischordant was a stale one from before the
    diatonic-extension fix (an F# in A minor); the current path must never
    reproduce it. Every substituted degree is diatonic, and its chord tones --
    at every extension -- stay in the scale.
    """
    from ableton_ai import composing, theory, voicings

    for key, scale, main in (("A", "minor", [1, 6, 3, 7]),
                             ("E", "minor", [1, 6, 3, 7]),
                             ("C", "minor", [1, 4, 5, 6]),
                             ("D", "dorian", [1, 4, 1, 5])):
        scale_pcs = {p % 12 for p in theory.scale_pitches(key, scale, octaves=1)}
        rehar = composing._reharmonise(main, scale)
        assert all(1 <= d <= 7 for d in rehar), rehar
        for degree in rehar:
            for ext in ("triad", "seventh", "ninth"):
                pitches = voicings.extend(
                    theory.build_chord(key, scale, degree), ext,
                    key=key, scale=scale,
                )
                off = {p % 12 for p in pitches} - scale_pcs
                assert not off, (
                    f"{key} {scale} reharmonised degree {degree} {ext}: "
                    f"out-of-key {[_n(p) for p in off]}"
                )


def _n(pc):
    return ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][pc % 12]


def test_reharmonised_melody_stays_consonant():
    """Relative substitution keeps the old melody consonant over new chords.

    The whole point of reharmonising is 'familiar melody over changed harmony';
    if the old chord tones clashed with the new chords it would defeat itself.
    """
    from ableton_ai import composing, theory

    main = [1, 6, 3, 7]
    rehar = composing._reharmonise(main, "minor")
    old = [theory.build_chord("A", "minor", d) for d in main]
    new = [theory.build_chord("A", "minor", d, extension="seventh") for d in rehar]

    clashes = 0
    for o, n in zip(old, new):
        old_tones = {p % 12 for p in o.pitches}
        new_tones = {p % 12 for p in n.pitches}
        for mt in old_tones:
            if mt in new_tones:
                continue
            if any((mt - nt) % 12 in (1, 11) for nt in new_tones):
                clashes += 1
    assert clashes == 0, f"{clashes} semitone clashes in the reharmonisation"


def test_build_track_is_coherent_one_key_no_collisions():
    """A whole build sits in one key with registers that do not collide.

    The "sounds a mess" was a set of stale clips from many builds in
    different keys, plus chords voiced down into the bass and leads pushed an
    octave too high. A fresh build must be coherent by construction.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from fake_live import FakeBridge

    from ableton_ai import theory
    from ableton_ai.tools import Toolbox

    box = Toolbox(FakeBridge())
    box.call("build_track", {"genre": "trance", "key": "E",
                            "duration_seconds": 240, "seed": 7})

    scale_pcs = {p % 12 for p in theory.scale_pitches("E", "minor", octaves=1)}
    tracks = {t["name"]: t for t in box.bridge.tracks}

    ranges = {}
    for name in ("Bass", "Chords", "Lead", "Hook", "Melody"):
        track = tracks.get(name)
        if not track or not track["clips"]:
            continue
        notes = track["clips"][min(track["clips"])]["notes"]
        if not notes:
            continue
        off = {n["pitch"] % 12 for n in notes} - scale_pcs
        assert not off, f"{name} went out of E minor: {sorted(off)}"
        ranges[name] = (min(n["pitch"] for n in notes),
                        max(n["pitch"] for n in notes))

    # Chords sit above the bass, not down in its register.
    if "Bass" in ranges and "Chords" in ranges:
        assert ranges["Chords"][0] >= ranges["Bass"][0] + 5, (
            f"chords {ranges['Chords']} collide with bass {ranges['Bass']}"
        )
    # The lead is in a musical register, not shrill.
    if "Lead" in ranges:
        assert ranges["Lead"][1] <= 96, f"lead tops out at {ranges['Lead'][1]}"
