"""The tool layer and a full track build, against the simulated Live."""

from __future__ import annotations

import pytest
from pathlib import Path
from fake_live import FakeBridge

from ableton_ai.schemas import render_for_text_protocol, tool_schemas
from ableton_ai.tools import ToolError, Toolbox


@pytest.fixture
def box() -> Toolbox:
    return Toolbox(FakeBridge())


# ------------------------------------------------------------- schemas

def test_every_tool_has_a_schema_and_a_description():
    schemas = tool_schemas()
    names = {s["name"] for s in schemas}
    exposed = {a[len("tool_"):] for a in dir(Toolbox) if a.startswith("tool_")}
    assert names == exposed
    for schema in schemas:
        assert schema["description"] != "No description."
        assert schema["input_schema"]["type"] == "object"


def test_schema_types_are_resolved_not_stringified():
    """PEP 563 turns hints into strings; the generator must resolve them."""
    schema = next(s for s in tool_schemas() if s["name"] == "create_chord_clip")
    props = schema["input_schema"]["properties"]
    assert props["track_index"]["type"] == "integer"
    assert props["bars"]["type"] == "number"
    assert props["smooth_voicing"]["type"] == "boolean"
    assert props["key"]["type"] == "string"
    assert schema["input_schema"]["required"] == ["track_index"]


def test_text_protocol_renders_every_tool():
    text = render_for_text_protocol(tool_schemas())
    for schema in tool_schemas():
        assert schema["name"] in text


# --------------------------------------------------------------- tools

def test_unknown_tool_is_an_error(box):
    with pytest.raises(ToolError, match="no such tool"):
        box.call("teleport", {})


def test_bad_arguments_surface_as_tool_errors(box):
    with pytest.raises(ToolError, match="unknown argument"):
        box.call("create_drum_clip", {"nonsense": 1})


def test_live_errors_become_tool_errors(box):
    with pytest.raises(ToolError):
        box.call("create_drum_clip", {"track_index": 99})


def test_chord_clip_reports_the_chords_it_chose(box):
    box.call("create_track", {"name": "Chords", "role": "chords"})
    result = box.call("create_chord_clip", {
        "track_index": 0, "key": "C", "scale": "minor",
        "degrees": "1-6-4-5", "bars": 8,
    })
    assert result["chords"][0].startswith("Cm")
    assert len(result["chords"]) == 4
    assert result["notes_written"] > 0


def test_named_progression_is_accepted(box):
    box.call("create_track", {"name": "Chords"})
    result = box.call("create_chord_clip",
                      {"track_index": 0, "degrees": "andalusian"})
    assert len(result["chords"]) == 4


def test_placeholder_set_creates_audio_tracks(box):
    result = box.call("create_placeholder_set", {"roles": ["vocal", "vocal", "fx"]})
    assert result["count"] == 3
    assert [t["kind"] for t in box.bridge.tracks] == ["audio"] * 3
    assert box.bridge.tracks[0]["name"] == "Vocal 1"


def test_variation_set_fills_consecutive_slots(box):
    box.call("create_track", {"name": "Drums", "role": "drums"})
    box.call("create_drum_clip", {"track_index": 0, "pattern": "house", "bars": 4})
    result = box.call("create_variation_set",
                      {"track_index": 0, "clip_index": 0, "count": 4, "start_slot": 0})
    assert result["clip_indices"] == [0, 1, 2, 3]
    slots = box.bridge.tracks[0]["clips"]
    signatures = {len(slots[i]["notes"]) for i in range(4)}
    assert len(signatures) > 1, "variations should differ in density"


def test_variation_of_an_empty_clip_is_a_clear_error(box):
    box.call("create_track", {"name": "Empty"})
    box.call("write_clip_notes", {"track_index": 0, "clip_index": 0, "notes": []})
    with pytest.raises(ToolError, match="no MIDI notes"):
        box.call("create_clip_variation", {"track_index": 0, "clip_index": 0})


def test_transpose_moves_every_note(box):
    box.call("create_track", {"name": "Bass"})
    box.call("create_bass_clip", {"track_index": 0, "bars": 4})
    before = [n["pitch"] for n in box.bridge.tracks[0]["clips"][0]["notes"]]
    box.call("transpose_clip",
             {"track_index": 0, "clip_index": 0, "semitones": 5})
    after = [n["pitch"] for n in box.bridge.tracks[0]["clips"][0]["notes"]]
    assert after == [p + 5 for p in before]


def test_write_clip_notes_rejects_a_note_without_a_pitch(box):
    box.call("create_track", {"name": "X"})
    with pytest.raises(ToolError, match="pitch"):
        box.call("write_clip_notes",
                 {"track_index": 0, "notes": [{"start": 0.0}]})


# --------------------------------------------------- full arrangement

def _build_track(box: Toolbox, seconds: int = 360, tempo: float = 128) -> dict:
    """Build a complete EDM track the way the agent is meant to."""
    box.call("set_tempo", {"tempo": tempo})

    layout = [
        ("Kick", "kick", "drum", {"pattern": "four_on_floor"}),
        ("Drums", "drums", "drum", {"pattern": "tech_house"}),
        ("Bass", "bass", "bass", {}),
        ("Chords", "chords", "chord", {}),
        ("Hook", "hook", "hook", {}),
        ("Riser", "riser", "riser", {}),
        ("Impact", "impact", "impact", {}),
    ]
    tracks = []
    for index, (name, role, kind, extra) in enumerate(layout):
        box.call("create_track", {"name": name, "role": role})
        if kind == "drum":
            box.call("create_drum_clip",
                     {"track_index": index, "bars": 4, **extra})
        elif kind == "bass":
            box.call("create_bass_clip",
                     {"track_index": index, "key": "C", "scale": "minor", "bars": 8})
        elif kind == "chord":
            box.call("create_chord_clip",
                     {"track_index": index, "key": "C", "scale": "minor", "bars": 8})
        elif kind == "hook":
            box.call("create_hook_clip",
                     {"track_index": index, "key": "C", "scale": "minor", "bars": 8})
        elif kind == "riser":
            box.call("create_riser_clip",
                     {"track_index": index, "key": "C", "scale": "minor", "bars": 8})
        elif kind == "impact":
            box.call("create_impact_clip", {"track_index": index, "bars": 1})

        entry = {"track_index": index, "clip_index": 0, "role": role}
        # Give the looped parts a ladder of variations to draw from.
        if role in ("kick", "drums", "bass", "chords"):
            made = box.call("create_variation_set",
                            {"track_index": index, "clip_index": 0,
                             "count": 4, "start_slot": 0})
            entry = {"track_index": index, "role": role,
                     "clip_indices": made["clip_indices"]}
        tracks.append(entry)

    box.call("create_placeholder_set", {"roles": ["vocal", "fx"]})

    plan = box.call("plan_arrangement",
                    {"target_seconds": seconds, "tempo": tempo, "template": "edm"})
    result = box.call("arrange_to_timeline",
                      {"sections": plan["sections"], "tracks": tracks})
    return {"plan": plan, "result": result}


def test_full_six_minute_edm_build(box):
    built = _build_track(box, seconds=360, tempo=128)
    plan, result = built["plan"], built["result"]

    assert plan["duration"] == "6:00"
    assert plan["total_bars"] == 192
    assert result["placements"] > 0
    # The timeline must actually reach ~6 minutes.
    assert 330 <= result["duration_seconds"] <= 390


def test_arrangement_places_impacts_once_per_drop(box):
    built = _build_track(box)
    impact_lane = box.bridge.arrangement[6]
    drops = [s for s in built["plan"]["sections"] if s["kind"] == "drop"]
    assert len(impact_lane) == len(drops)
    for clip, section in zip(sorted(impact_lane, key=lambda c: c["start_bars"]), drops):
        assert clip["start_bars"] == section["start_bar"]


def test_risers_finish_exactly_on_the_drop(box):
    built = _build_track(box)
    riser_lane = sorted(box.bridge.arrangement[5], key=lambda c: c["start_bars"])
    builds = [s for s in built["plan"]["sections"] if s["kind"] == "build"]
    assert len(riser_lane) == len(builds)
    for clip, section in zip(riser_lane, builds):
        end = clip["start_bars"] + clip["length_bars"]
        assert end == section["end_bar"], "a riser must land on the section boundary"


def test_energy_picks_bigger_variations_for_drops(box):
    """Quiet sections should draw earlier (stripped) variations than drops do."""
    built = _build_track(box)
    detail = built["result"]["detail"]
    by_kind = {s["name"]: s["kind"] for s in built["plan"]["sections"]}
    intro = [d["clip_index"] for d in detail
             if by_kind.get(d["section"]) == "intro" and d["role"] == "drums"]
    drop = [d["clip_index"] for d in detail
            if by_kind.get(d["section"]) == "drop" and d["role"] == "drums"]
    assert intro and drop
    assert min(intro) < max(drop)


def test_arrangement_writes_locators_for_every_section(box):
    built = _build_track(box)
    assert len(box.bridge.locators) == len(built["plan"]["sections"])
    assert box.bridge.locators[0]["start_bar"] == 0
    assert box.bridge.view == "arrangement"


def test_rearranging_clears_the_previous_pass(box):
    """Arranging twice must replace the timeline, not stack a second copy on it."""
    _build_track(box)
    plan = box.call("plan_arrangement",
                    {"target_seconds": 360, "tempo": 128, "template": "edm"})
    tracks = [{"track_index": 0, "clip_index": 0, "role": "kick"}]

    box.call("arrange_to_timeline",
             {"sections": plan["sections"], "tracks": tracks, "clear_first": True})
    after_first = len(box.bridge.arrangement[0])

    box.call("arrange_to_timeline",
             {"sections": plan["sections"], "tracks": tracks, "clear_first": True})
    assert len(box.bridge.arrangement[0]) == after_first

    # And without clearing, the second pass really does stack.
    box.call("arrange_to_timeline",
             {"sections": plan["sections"], "tracks": tracks, "clear_first": False})
    assert len(box.bridge.arrangement[0]) == after_first * 2


def test_arrange_without_sections_is_a_clear_error(box):
    with pytest.raises(ToolError, match="plan_arrangement"):
        box.call("arrange_to_timeline", {"sections": [], "tracks": []})


# ------------------------------------------- regressions from the live run

def test_every_generated_role_gets_used_in_edm_templates():
    """A track built for a role must not sit silent through the whole song.

    Regression: `big_room` listed `sub` but never `bass` in its drops, so a
    Bass track played in exactly one section out of nine.
    """
    from ableton_ai import arrangement

    generated = {"kick", "drums", "bass", "chords", "hook", "riser", "impact"}
    for name in ("house", "big_room", "progressive_house", "future_bass",
                 "trance", "techno", "melodic_techno"):
        used = {r for s in arrangement.plan(360, 128, name) for r in s.roles}
        for role in generated & used:
            sections = [s for s in arrangement.plan(360, 128, name)
                        if role in s.roles]
            bars = sum(s.bars for s in sections)
            assert bars >= 16, f"{name}: role {role!r} only plays {bars} bars"


def test_bass_plays_through_the_drops(box):
    """The specific shape of the live-run bug: bass idle during drops."""
    from ableton_ai import arrangement

    for name in ("house", "big_room", "trance"):
        for section in arrangement.plan(360, 128, name):
            if section.kind == "drop":
                assert "bass" in section.roles or "sub" in section.roles, \
                    f"{name}: {section.name} at bar {section.start_bar} has no low end"


def test_a_missing_role_falls_back_to_a_neighbour(box):
    """A set with Bass but no Sub must still get bass through sub-only sections."""
    box.call("create_track", {"name": "Bass", "role": "bass"})
    box.call("create_bass_clip", {"track_index": 0, "bars": 4})

    sections = [{"name": "drop", "kind": "drop", "start_bar": 0, "bars": 16,
                 "energy": 1.0, "roles": ["sub"]}]
    result = box.call("arrange_to_timeline", {
        "sections": sections,
        "tracks": [{"track_index": 0, "clip_index": 0, "role": "bass"}],
    })
    assert result["placements"] == 1, "bass should stand in for the missing sub"


def test_a_role_with_no_fallback_is_simply_skipped(box):
    box.call("create_track", {"name": "Drums", "role": "drums"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})
    sections = [{"name": "breakdown", "kind": "breakdown", "start_bar": 0,
                 "bars": 16, "energy": 0.3, "roles": ["vocal"]}]
    result = box.call("arrange_to_timeline", {
        "sections": sections,
        "tracks": [{"track_index": 0, "clip_index": 0, "role": "drums"}],
    })
    assert result["placements"] == 0


# ------------------------------------------------------ sound preferences

def test_sound_preferences_round_trip(tmp_path):
    from ableton_ai.sounds import SoundPreferences

    prefs = SoundPreferences(tmp_path / "sounds.json")
    assert prefs.for_role("bass") == "Instruments/Operator"   # stock default

    prefs.set_role("bass", "Plugins/VST3/Xfer Records/Serum 2")
    prefs.add_favourite("Plugins/VST3/Xfer Records/Serum 2")
    assert prefs.for_role("bass") == "Plugins/VST3/Xfer Records/Serum 2"

    # Survives a reload from disk -- the point of the feature.
    reloaded = SoundPreferences(tmp_path / "sounds.json")
    assert reloaded.for_role("bass") == "Plugins/VST3/Xfer Records/Serum 2"
    assert "Serum 2" in reloaded.describe()

    reloaded.clear_role("bass")
    assert reloaded.for_role("bass") == "Instruments/Operator"


def test_corrupt_preferences_file_does_not_crash(tmp_path):
    path = tmp_path / "sounds.json"
    path.write_text("{ not json at all")
    from ableton_ai.sounds import SoundPreferences

    prefs = SoundPreferences(path)
    assert prefs.for_role("bass") == "Instruments/Operator"
    assert prefs.favourites() == []


def test_set_sound_preference_rejects_an_unknown_role(box, tmp_path):
    from ableton_ai.sounds import SoundPreferences

    box.sounds = SoundPreferences(tmp_path / "s.json")
    with pytest.raises(ToolError, match="is not a known value"):
        box.call("set_sound_preference", {"role": "kazoo", "path": "x"})


def test_load_sound_uses_the_saved_role_preference(box, tmp_path):
    from ableton_ai.sounds import SoundPreferences

    box.sounds = SoundPreferences(tmp_path / "s.json")
    box.call("create_track", {"name": "Bass", "role": "bass"})
    box.call("set_sound_preference",
             {"role": "bass", "path": "Plugins/VST3/Xfer Records/Serum 2"})

    result = box.call("load_sound", {"track_index": 0, "role": "bass"})
    assert result["path"] == "Plugins/VST3/Xfer Records/Serum 2"
    assert result["resolved_from"] == "role:bass"
    assert box.bridge.tracks[0]["devices"]


def test_load_sound_needs_something_to_go_on(box):
    box.call("create_track", {"name": "X"})
    with pytest.raises(ToolError, match="needs one of"):
        box.call("load_sound", {"track_index": 0})


# --------------------------------------------------------------- mixing

def test_gain_staging_puts_the_kick_on_top(box):
    """Everything should sit under the kick, which is the reference."""
    for name, role in (("Kick", "kick"), ("Bass", "bass"),
                       ("Pad", "pad"), ("Hook", "hook")):
        box.call("create_track", {"name": name, "role": role})

    result = box.call("mix_levels", {})
    by_role = {a["role"]: a["gain_db"] for a in result["applied"]}

    assert by_role["kick"] == 0.0
    assert by_role["bass"] < by_role["kick"]
    assert by_role["pad"] < by_role["hook"] < by_role["kick"]
    # And the faders actually moved in Live.
    volumes = [t["volume"] for t in box.bridge.tracks]
    assert volumes[0] > volumes[3] > volumes[2]


def test_frequency_separation_spares_the_low_end(box):
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_track", {"name": "Pad", "role": "pad"})
    box.call("create_track", {"name": "Chords", "role": "chords"})

    result = box.call("frequency_separation", {})
    filtered = {f["role"] for f in result["filtered"]}
    spared = {s["role"] for s in result["skipped"]}

    assert "kick" in spared, "the kick must keep its low end"
    assert {"pad", "chords"} <= filtered


def test_add_eq_loads_one_and_sets_the_filter(box):
    box.call("create_track", {"name": "Pad", "role": "pad"})
    result = box.call("add_eq", {"track_index": 0, "high_pass_hz": 250})

    assert "EQ" in result["device"]
    assert result["not_found"] == []
    # Frequency is set as a 0..1 position (250Hz -> ~0.4), and band 1 is put
    # into a low-cut (high-pass) mode, not left as the default bell.
    from ableton_ai import mixing
    want = round(mixing.hz_to_normalised(250), 3)
    assert any("1 Frequency" in x and str(want) in x for x in result["set"]), \
        result["set"]
    assert any("1 Filter Type" in x for x in result["set"])


def test_meters_say_so_when_the_transport_is_stopped(box):
    box.call("create_track", {"name": "Kick", "role": "kick"})
    stopped = box.call("read_meters", {})
    assert "warning" in stopped and "stopped" in stopped["warning"]

    box.call("transport", {"action": "play"})
    playing = box.call("read_meters", {})
    assert "warning" not in playing
    assert playing["tracks"][0]["level"] > 0


def test_db_conversion_round_trips():
    from ableton_ai import mixing

    assert mixing.db_to_live(0.0) == pytest.approx(mixing.UNITY)
    for db in (-12.0, -6.0, -3.0, 0.0):
        assert mixing.live_to_db(mixing.db_to_live(db)) == pytest.approx(db, abs=0.01)
    # Clamped at the ends rather than wrapping.
    assert mixing.db_to_live(-999) == 0.0
    assert mixing.db_to_live(999) == 1.0


# ------------------------------- arrangement input validation (regressions)

def test_a_null_in_the_input_names_the_offending_field(box):
    """Regression: nulls raised a bare TypeError reported as "bad arguments"."""
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})
    tracks = [{"track_index": 0, "clip_index": 0, "role": "kick"}]
    section = {"name": "drop", "start_bar": 0, "bars": 16,
               "energy": 1.0, "roles": ["kick"]}

    with pytest.raises(ToolError, match="drop has no bars"):
        box.call("arrange_to_timeline",
                 {"sections": [{**section, "bars": None}], "tracks": tracks})

    with pytest.raises(ToolError, match="no start_bar"):
        box.call("arrange_to_timeline",
                 {"sections": [{**section, "start_bar": None}], "tracks": tracks})

    with pytest.raises(ToolError, match="no track_index"):
        box.call("arrange_to_timeline",
                 {"sections": [section],
                  "tracks": [{"track_index": None, "role": "kick"}]})


def test_a_non_object_section_is_reported_clearly(box):
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})
    with pytest.raises(ToolError, match="not an object"):
        box.call("arrange_to_timeline", {
            "sections": [10],
            "tracks": [{"track_index": 0, "clip_index": 0, "role": "kick"}],
        })


def test_null_roles_means_silence_not_a_crash(box):
    """A section with nothing playing is legitimate, not an error."""
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})
    result = box.call("arrange_to_timeline", {
        "sections": [{"name": "silence", "start_bar": 0, "bars": 8,
                      "energy": 0.0, "roles": None}],
        "tracks": [{"track_index": 0, "clip_index": 0, "role": "kick"}],
    })
    assert result["placements"] == 0


def test_placeholder_tracks_are_skipped_not_fatal(box):
    """Vocals and FX have no clip yet -- that must not stop the arrangement."""
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})
    box.call("create_placeholder_set", {"roles": ["vocal", "fx"]})

    plan = box.call("plan_arrangement",
                    {"target_seconds": 240, "tempo": 128, "template": "edm"})
    result = box.call("arrange_to_timeline", {
        "sections": plan["sections"],
        "tracks": [
            {"track_index": 0, "clip_index": 0, "role": "kick"},
            {"track_index": 1, "clip_index": None, "role": "vocal"},
            {"track_index": 2, "clip_index": 0, "role": "fx"},
        ],
    })
    assert result["placements"] > 0, "the kick should still have been placed"
    skipped = {s["role"] for s in result["skipped_tracks"]}
    assert skipped == {"vocal", "fx"}
    assert "note" in result


def test_an_internal_error_is_not_reported_as_bad_arguments(box):
    """The handler used to relabel any internal TypeError as a signature fault."""
    with pytest.raises(ToolError) as caught:
        box.call("create_drum_clip", {"track_index": 0, "pattern": "nope"})
    assert "bad arguments" not in str(caught.value)

    # A genuine signature mistake still says so.
    with pytest.raises(ToolError, match="unknown argument.*Accepts"):
        box.call("create_drum_clip", {"not_a_parameter": 1})


# ------------------------------------------------------ remembered rules

def test_rules_are_remembered_and_reach_the_prompt(tmp_path):
    from ableton_ai.sounds import SoundPreferences

    prefs = SoundPreferences(tmp_path / "preferences.json")
    assert prefs.rules() == []

    prefs.remember("Always use Serum for bass")
    prefs.remember("Start tracks at 138bpm")
    assert len(prefs.rules()) == 2

    # Saying the same thing twice must not accumulate duplicates.
    prefs.remember("always use serum for bass")
    assert len(prefs.rules()) == 2

    reloaded = SoundPreferences(tmp_path / "preferences.json")
    described = reloaded.describe()
    assert "Standing instructions" in described
    assert "Serum for bass" in described


def test_forgetting_removes_only_the_matching_rule(tmp_path):
    from ableton_ai.sounds import SoundPreferences

    prefs = SoundPreferences(tmp_path / "preferences.json")
    prefs.remember("Always use Serum for bass")
    prefs.remember("Start tracks at 138bpm")

    remaining = prefs.forget("serum")
    assert remaining == ["Start tracks at 138bpm"]
    assert SoundPreferences(tmp_path / "preferences.json").rules() == remaining


def test_an_explicit_path_never_inherits_the_real_user_config(tmp_path):
    """Regression: the legacy-file fallback leaked ~/.config into every store."""
    from ableton_ai.sounds import SoundPreferences

    prefs = SoundPreferences(tmp_path / "preferences.json")
    assert prefs.rules() == []
    assert prefs.favourites() == []
    assert prefs.for_role("bass") == "Instruments/Operator"   # the stock default


def test_remember_and_recall_through_the_tools(box, tmp_path):
    from ableton_ai.sounds import SoundPreferences

    box.sounds = SoundPreferences(tmp_path / "preferences.json")
    box.call("remember", {"rule": "Always add a riser before the drop"})
    recalled = box.call("recall", {})
    assert recalled["rules"] == ["Always add a riser before the drop"]

    box.call("forget", {"about": "riser"})
    assert box.call("recall", {})["rules"] == []


# ------------------------------------------- schema constrains the model

def test_closed_vocabularies_are_enumerated_in_the_schema():
    """The variation enum matches exactly what the code accepts.

    "extended" was once the plausible-but-nonexistent word the model reached
    for; it is now a real recipe -- the safe diatonic default -- so the guard
    is that the enum and the code agree, whatever the set is."""
    from ableton_ai import harmony

    schemas = {s["name"]: s for s in tool_schemas()}
    variation = schemas["create_varied_chords"]["input_schema"]["properties"]["variation"]
    assert set(variation["enum"]) == set(harmony.RECIPES)
    assert "extended" in variation["enum"], "the diatonic default must be offered"

    # Every enum must match what the code actually accepts.
    scale = schemas["create_varied_chords"]["input_schema"]["properties"]["scale"]
    assert "minor" in scale["enum"] and "dorian" in scale["enum"]


def test_object_shaped_parameters_describe_their_fields():
    """Regression: sections was a bare {"type": "object"}, so the model
    invented a shape and left out `bars`."""
    schemas = {s["name"]: s for s in tool_schemas()}
    props = schemas["arrange_to_timeline"]["input_schema"]["properties"]

    section = props["sections"]["items"]
    assert set(section["required"]) == {"start_bar", "bars", "roles"}

    track = props["tracks"]["items"]
    assert set(track["required"]) == {"track_index", "role"}
    assert "kick" in track["properties"]["role"]["enum"]


def test_every_enum_value_is_actually_accepted(box):
    """An enum that offers a value the tool rejects is worse than none."""
    from ableton_ai import basslines, harmony, leads, theory, voicings

    for name, module_vocab in (
        ("variation", harmony.RECIPES),
        ("voicing", {**voicings.STYLES, **voicings.ALIASES}),
    ):
        schemas = {s["name"]: s for s in tool_schemas()}
        allowed = schemas["create_varied_chords"]["input_schema"]["properties"][name]["enum"]
        for value in allowed:
            assert value in module_vocab, f"{name}={value} is offered but unknown"

    for style in leads.STYLES:
        leads.resolve(style)
    for style in basslines.STYLES:
        basslines.resolve(style)


def test_a_section_can_give_end_bar_instead_of_bars(box):
    """Regression: a hand-built section with boundaries but no `bars` failed."""
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})

    result = box.call("arrange_to_timeline", {
        "sections": [{"name": "intro", "start_bar": 0, "end_bar": 16,
                      "roles": ["kick"]}],
        "tracks": [{"track_index": 0, "clip_index": 0, "role": "kick"}],
    })
    assert result["placements"] == 1
    assert result["end_bars"] == 16


def test_a_section_with_no_length_at_all_says_so(box):
    box.call("create_track", {"name": "Kick", "role": "kick"})
    box.call("create_drum_clip", {"track_index": 0, "bars": 4})
    with pytest.raises(ToolError, match="needs either `bars` or an `end_bar`"):
        box.call("arrange_to_timeline", {
            "sections": [{"name": "intro", "start_bar": 0, "roles": ["kick"]}],
            "tracks": [{"track_index": 0, "clip_index": 0, "role": "kick"}],
        })


def test_extension_accepts_chord_quality_names(box):
    """Regression: extension="minor9" was refused. It is the natural way to
    say it, and the ladder/quality distinction is an internal detail."""
    from ableton_ai import voicings

    for said, meant in (("minor9", "ninth"), ("maj7", "seventh"),
                        ("11", "eleventh"), ("plain", "triad")):
        assert voicings.normalise_extension(said) == meant

    box.call("create_track", {"name": "Chords", "role": "chords"})
    result = box.call("create_varied_chords",
                      {"track_index": 0, "key": "G", "extension": "minor9"})
    assert result["notes_written"] > 0


def test_role_accepts_the_obvious_synonyms(box, tmp_path):
    """Regression: role="melody" was refused, while corpus.classify_part was
    itself emitting that word."""
    from ableton_ai.arrangement import normalise_role
    from ableton_ai.sounds import SoundPreferences

    for said, meant in (("melody", "lead"), ("keys", "chords"),
                        ("hats", "drums"), ("vox", "vocal")):
        assert normalise_role(said) == meant
    assert normalise_role("kazoo") is None

    box.sounds = SoundPreferences(tmp_path / "p.json")
    saved = box.call("set_sound_preference",
                     {"role": "melody", "path": "Instruments/Wavetable"})
    assert saved["role"] == "lead", "the synonym should be stored canonically"


def test_samples_on_the_timeline_get_arranged(box):
    """A dropped-in sample has no session clip, and used to be left behind.

    Its only copy lives on the arrangement, so the ordinary duplicate path had
    nothing to place and skipped the track -- which is how a user's own loops
    ended up sitting on the timeline once while every generated part was spread
    across the whole track.
    """
    live = box.bridge
    live.call("create_audio_track", name="TORI_vocal_phrase")
    audio = len(live.tracks) - 1
    live.arrangement[audio] = [
        {"name": "TORI_vocal_phrase", "start_bars": 0.0, "length_bars": 4.0,
         "start_beats": 0.0, "end_beats": 16.0}
    ]

    result = box.call("arrange_to_timeline", {
        "sections": [
            {"name": "intro", "start_bar": 0, "bars": 16, "roles": ["vocal"]},
            {"name": "drop", "start_bar": 16, "bars": 16, "roles": ["vocal"]},
        ],
        "tracks": [{"track_index": audio, "role": "vocal"}],
        "clear_first": True,
    })

    assert not result.get("skipped_tracks"), result.get("skipped_tracks")
    lane = live.arrangement[audio]
    assert len(lane) == 8, f"expected the 4-bar sample eight times, got {len(lane)}"
    assert max(c["start_bars"] + c["length_bars"] for c in lane) == 32.0


def test_timeline_source_survives_clear_first(box):
    """clear_first must not wipe the only copy of the sample it is about to place."""
    live = box.bridge
    live.call("create_audio_track", name="hat_loop")
    audio = len(live.tracks) - 1
    live.arrangement[audio] = [
        {"name": "hat_loop", "start_bars": 8.0, "length_bars": 2.0,
         "start_beats": 32.0, "end_beats": 40.0}
    ]

    box.call("arrange_to_timeline", {
        "sections": [{"name": "drop", "start_bar": 0, "bars": 8,
                      "roles": ["drums"]}],
        "tracks": [{"track_index": audio, "role": "drums"}],
        "clear_first": True,
    })

    assert live.arrangement[audio], "the sample was cleared before it could be placed"


def test_empty_clear_list_clears_nothing(box):
    """An empty track list must not be read as "every track".

    arrange_to_timeline filters timeline-sourced tracks out of the clear, and
    that filter can legitimately empty the list. Falling back to "all" there
    would wipe the whole arrangement on the way to placing one sample.
    """
    live = box.bridge
    live.call("create_midi_track", name="Keep")
    live.arrangement[0] = [
        {"name": "Keep", "start_bars": 0.0, "length_bars": 4.0,
         "start_beats": 0.0, "end_beats": 16.0}
    ]

    live.call("clear_arrangement", track_indices=[])
    assert live.arrangement[0], "an empty list wiped the timeline"

    live.call("clear_arrangement")
    assert not live.arrangement[0], "an absent list should still clear everything"


def test_bare_track_index_does_not_crash_mix_levels(box):
    """tracks=[5] is a list of ints, not of {track_index, role} dicts.

    Subscripting the int raised a TypeError from inside the tool, which the
    caller saw as an unhelpful "bad arguments". A bare index just carries no
    role override.
    """
    box.call("create_track", {"name": "Bass", "role": "bass"})
    result = box.call("mix_levels", {"tracks": [0], "apply_pan": True})
    assert result["applied"]


def test_musical_synonyms_are_accepted_as_mutations(box):
    """A stab is a real request; it should not come back as an unknown word."""
    from ableton_ai import variations

    notes = [{"pitch": 60, "start": i * 0.5, "duration": 0.5, "velocity": 100}
             for i in range(8)]
    for word in ("stab", "stabby", "sparse", "build", "chorus", "swing", "ghost"):
        assert variations.apply(notes, [word]), word

    schema = {t["name"]: t for t in __import__(
        "ableton_ai.schemas", fromlist=["x"]).tool_schemas()}
    allowed = schema["create_clip_variation"]["input_schema"]["properties"][
        "mutations"]["items"]["enum"]
    assert "stab" in allowed and "staccato" in allowed


def _place(box, track_index, clip_index, notes, bars):
    """Put raw notes in a slot, straight through the bridge."""
    box.bridge.call("create_clip", track_index=track_index, clip_index=clip_index,
                    length_beats=bars * 4.0, notes=notes)


def _chords(pitches_per_bar):
    return [{"pitch": p, "start": bar * 4.0, "duration": 4.0, "velocity": 90}
            for bar, pitches in enumerate(pitches_per_bar) for p in pitches]


def test_analyse_set_reads_the_key_off_the_chords_not_the_filler(box):
    """A dense arp must not outvote the chords the set was written around.

    The key vote was weighted by note count, so five copies of a 128-note
    sixteenth arp buried an eight-chord progression and the whole set was
    generated a fifth away from the material already in it.
    """
    box.call("create_track", {"name": "Chords", "role": "chords"})
    box.call("create_track", {"name": "Arp", "role": "hook"})

    # Dm - Gm - Am - Dm, whole notes: unambiguous D minor.
    _place(box, 0, 0, _chords([[62, 65, 69], [67, 70, 74], [69, 72, 76],
                               [62, 65, 69]]), bars=4)

    # A busy sixteenth arp on a B-flat major shape: many notes, little weight.
    _place(box, 1, 0, [{"pitch": [70, 74, 77][i % 3], "start": i * 0.25,
                        "duration": 0.25, "velocity": 90} for i in range(64)],
           bars=4)

    result = box.call("analyse_set", {})
    assert result["key"] == "D" and result["scale"] == "minor", result["summary"]
    assert result["progression_from"]["track_index"] == 0, result["progression_from"]


def test_analyse_clip_reports_degrees(box):
    box.call("create_track", {"name": "Chords", "role": "chords"})
    _place(box, 0, 0, _chords([[62, 65, 69], [67, 70, 74]]), bars=2)
    result = box.call("analyse_clip", {"track_index": 0, "clip_index": 0})
    assert result["key"] == "D"
    assert result["part"] == "chords"
    assert result["degrees"], result


def test_reference_clip_keeps_the_chord_quality(box):
    """A degree number is not the chord. Em7 must not come back as E diminished.

    The second degree of D minor is diminished, so rebuilding harmony from
    degrees turned a clip's Em7 into Edim and every part generated against it
    disagreed with what was actually playing.
    """
    box.call("create_track", {"name": "Chords", "role": "chords"})
    box.call("create_track", {"name": "Bass", "role": "bass"})

    # Dm9 then Em7 -- the Em7's B natural is what a degree lookup destroys.
    _place(box, 0, 0, _chords([[62, 65, 69, 72, 76], [64, 67, 71, 74]]), bars=2)

    chords, bars_per_chord = box._reference_progression(0, 0, bars=2)
    assert len(chords) == 2, chords
    assert bars_per_chord == 1.0
    second = {p % 12 for p in chords[1].pitches}
    assert 11 in second, f"lost the B natural: {sorted(second)}"

    # And a bassline generated from it lands on those roots.
    result = box.call("create_bass_clip", {
        "track_index": 1, "clip_index": 0, "bars": 2, "reference_track": 0,
    })
    assert result.get("summary"), result


def test_reference_clip_sets_the_harmonic_rhythm(box):
    """Two-bar chords must not be read as one chord per bar."""
    box.call("create_track", {"name": "Chords", "role": "chords"})
    notes = [{"pitch": p, "start": bar * 4.0, "duration": 8.0, "velocity": 90}
             for bar, pitches in ((0, [62, 65, 69]), (2, [67, 70, 74]))
             for p in pitches]
    _place(box, 0, 0, notes, bars=4)

    _chords_out, bars_per_chord = box._reference_progression(0, 0, bars=4)
    assert bars_per_chord == 2.0, bars_per_chord


def test_snapshot_round_trips_a_set(box, tmp_path, monkeypatch):
    """A snapshot has to be able to put the notes back, or it is not a backup."""
    monkeypatch.setenv("ABLETON_AI_SNAPSHOTS", str(tmp_path))
    box.call("create_track", {"name": "Chords", "role": "chords"})
    _place(box, 0, 0, _chords([[62, 65, 69], [67, 70, 74]]), bars=2)

    snap = box.call("snapshot_set", {"label": "test"})
    assert snap["midi_clips"] == 1, snap

    box.bridge.tracks[0]["clips"].clear()
    assert not box.bridge.tracks[0]["clips"]

    result = box.call("restore_snapshot", {"path": snap["path"]})
    assert result["clips_restored"] == 1, result
    assert len(box.bridge.tracks[0]["clips"][0]["notes"]) == 6


def test_destructive_tools_snapshot_first(box, tmp_path, monkeypatch):
    """Clearing the arrangement must leave a way back."""
    monkeypatch.setenv("ABLETON_AI_SNAPSHOTS", str(tmp_path))
    box.call("create_track", {"name": "Chords", "role": "chords"})
    _place(box, 0, 0, _chords([[62, 65, 69]]), bars=1)

    result = box.call("clear_arrangement", {})
    assert result.get("snapshot"), "no snapshot was taken before clearing"
    assert Path(result["snapshot"]).exists()


def _tiny_corpus(tmp_path, monkeypatch):
    """A corpus library with two references, on disk, isolated from the user's."""
    from ableton_ai import corpus as c

    library = c.Library(path=tmp_path / "corpus.json")
    for name, degrees, style in (("a", [1, 6, 4, 5], "rolling"),
                                 ("b", [1, 6, 4, 5], "rolling")):
        library.add(c.Reference(
            name=name, path=f"/{name}.mid", tempo=126.0, bars=8.0,
            key_root="A", key_scale="minor", key_confidence=0.9,
            progression=degrees, qualities=["minor", "major"],
            chords=[], notes_total=64,
            parts={"bass": {"notes": 32, "range": [36, 48], "rhythm": "x" * 16,
                            "groove": {"swing": 0.08, "push": -0.01,
                                       "accents": [0] * 16,
                                       "timing": [0.0] * 16, "jitter": 0.01},
                            "articulation": {"style": style, "onsets_per_bar": 4.0,
                                             "legato": 0.4}}},
            voicing={"mean_spread_semitones": 14.0, "mean_voices": 4.0,
                     "inversion_share": 0.3},
        ))
    library.save()
    return library


def test_generators_can_take_the_progression_from_the_corpus(box, tmp_path,
                                                             monkeypatch):
    """Learning was write-only: nothing generated ever changed because of it."""
    library = _tiny_corpus(tmp_path, monkeypatch)
    box._corpus_library = library

    box.call("create_track", {"name": "Chords", "role": "chords"})
    result = box.call("create_chord_clip", {
        "track_index": 0, "clip_index": 0, "key": "A", "scale": "minor",
        "degrees": "learned", "bars": 8,
    })
    assert result.get("summary")

    walked = box.call("suggest_progression", {"length": 4, "seed": 1})
    assert walked["degrees"], walked
    assert walked["learned_from"] == 2


def test_corpus_profile_reports_what_was_learned(box, tmp_path, monkeypatch):
    box._corpus_library = _tiny_corpus(tmp_path, monkeypatch)
    profile = box.call("corpus_profile", {})
    assert profile["references"] == 2
    assert profile["bass"]["dominant_style"] == "rolling"
    assert profile["voicing"]["style"] == "open"


def test_learned_bass_style_resolves(box, tmp_path, monkeypatch):
    box._corpus_library = _tiny_corpus(tmp_path, monkeypatch)
    assert box._learned_bass_style("learned") == "rolling"
    assert box._learned_bass_style("offbeat") == "offbeat"


def test_learned_asks_for_a_corpus_before_using_one(box):
    """With nothing learned, say so rather than silently falling back."""
    box.call("create_track", {"name": "Chords", "role": "chords"})
    with pytest.raises(ToolError, match="learn_references"):
        box.call("create_chord_clip", {
            "track_index": 0, "clip_index": 0, "degrees": "learned",
        })


def test_near_miss_values_are_corrected_and_reported(box):
    """A one-character slip should not cost a round trip.

    The correction is reported rather than applied silently: substituting a word
    that merely sounds similar would quietly make different music.
    """
    box.call("create_track", {"name": "Drums", "role": "drums"})
    result = box.call("create_drum_clip", {
        "track_index": 0, "clip_index": 0, "pattern": "tech_hous", "bars": 1,
    })
    assert result.get("corrected") == ["pattern='tech_hous' -> 'tech_house'"]


def test_unknown_values_suggest_instead_of_listing_everything(box):
    """Twenty-five names is the least useful possible reply."""
    box.call("create_track", {"name": "Drums", "role": "drums"})
    with pytest.raises(ToolError) as caught:
        box.call("create_drum_clip", {
            "track_index": 0, "clip_index": 0, "pattern": "wubwubwub",
        })
    message = str(caught.value)
    assert "wubwubwub" in message
    assert len(message) < 200, f"still dumping the whole table: {message}"


def test_part_level_drum_patterns_exist(box):
    """"Offbeat hats" is a normal request; the table only held whole kits."""
    box.call("create_track", {"name": "Hats", "role": "drums"})
    for pattern in ("offbeat_hats", "offbeat", "hats", "16ths", "garage"):
        result = box.call("create_drum_clip", {
            "track_index": 0, "clip_index": 0, "pattern": pattern, "bars": 1,
        })
        assert result.get("summary"), pattern


def test_an_unknown_tool_suggests_a_real_one(box):
    with pytest.raises(ToolError, match="Did you mean"):
        box.call("create_drums_clip", {})


def test_arrange_existing_creates_nothing(box):
    """"Arrange this" means arrange this. It kept coming back with new tracks."""
    for name, notes in (("Kick", [{"pitch": 36, "start": 0.0, "duration": 0.5,
                                   "velocity": 110}]),
                        ("Bass", [{"pitch": 40, "start": 0.0, "duration": 1.0,
                                   "velocity": 100}]),
                        ("Chords", _chords([[60, 64, 67]]))):
        box.call("create_track", {"name": name, "role": name.lower()})
    for index in range(3):
        _place(box, index, 0, [{"pitch": 60, "start": 0.0, "duration": 1.0,
                                "velocity": 100}], bars=4)

    before = len(box.bridge.tracks)
    result = box.call("arrange_existing", {"target_seconds": 120})

    assert len(box.bridge.tracks) == before, "arrange_existing created a track"
    assert result["created_nothing"] is True
    assert len(result["arranged"]) == 3
    assert result["placements"] > 0


def test_arrange_existing_picks_a_form_that_suits_the_set(box):
    """A set with no drums is not a house track."""
    for name in ("Strings", "Piano", "Choir"):
        box.call("create_track", {"name": name, "role": name.lower()})
    for index in range(3):
        _place(box, index, 0, _chords([[60, 64, 67]]), bars=4)

    result = box.call("arrange_existing", {"target_seconds": 180})
    assert result["template"] == "cinematic", result["template"]
    assert not any(p["section"] == "drop" for p in result["detail"]), result["detail"]


def test_arrange_existing_says_what_it_left_alone(box):
    """An empty track is reported, not filled in."""
    box.call("create_track", {"name": "Chords", "role": "chords"})
    box.call("create_track", {"name": "Lead", "role": "lead"})
    _place(box, 0, 0, _chords([[60, 64, 67]]), bars=4)

    result = box.call("arrange_existing", {"target_seconds": 120})
    assert [i["track"] for i in result["ignored"]] == ["Lead"]
    assert len(box.bridge.tracks) == 2


def test_arrange_existing_keeps_unclear_named_tracks(box):
    """A track with material but an unclear name plays, it is not dropped.

    Dropping "Main Midi" / "Offbeat" for having unreadable names is what left
    a verse with only hi-hats. Content-bearing tracks are arranged as a core
    part; only a set with nothing to place is refused.
    """
    box.call("create_track", {"name": "Main Midi"})
    _place(box, 0, 0, [{"pitch": 60, "start": 0.0, "duration": 1.0,
                        "velocity": 100}], bars=4)
    result = box.call("arrange_existing", {})
    assert result["placements"] > 0, "the unclear track was dropped"

    empty = Toolbox(FakeBridge())
    empty.call("create_track", {"name": "Nothing"})
    with pytest.raises(ToolError, match="nothing in this set"):
        empty.call("arrange_existing", {})


def test_tool_enum_tables_have_no_duplicate_keys():
    """A duplicate key in a dict literal is legal Python and a silent loss.

    TOOL_ENUMS carried "create_hook_clip" twice; the second literal swallowed
    the first and the hook patterns vanished from the schema, so the model
    was told its own vocabulary was invalid.
    """
    import ast
    import inspect

    from ableton_ai import schemas

    tree = ast.parse(inspect.getsource(schemas))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        duplicates = {k for k in keys if keys.count(k) > 1}
        assert not duplicates, f"duplicate keys in a schemas dict: {duplicates}"


def test_soundcheck_finds_and_fixes_silent_tracks(box):
    """A track with notes and no instrument is broken, whatever the notes say."""
    box.call("create_track", {"name": "Lead", "role": "lead"})
    _place(box, 0, 0, [{"pitch": 72, "start": 0.0, "duration": 1.0,
                        "velocity": 100}], bars=4)
    assert box.bridge.tracks[0]["devices"] == []

    report = box.call("soundcheck", {"fix": True})
    assert report["fixed"], report
    assert box.bridge.tracks[0]["devices"], "no instrument was loaded"

    # A track that already sounds is left alone.
    clean = box.call("soundcheck", {"fix": True})
    assert not clean["fixed"] and not clean["silent"], clean


def test_soundcheck_treats_effects_only_as_silent(box):
    box.call("create_track", {"name": "Melody", "role": "melody"})
    _place(box, 0, 0, [{"pitch": 72, "start": 0.0, "duration": 1.0,
                        "velocity": 100}], bars=4)
    box.bridge.tracks[0]["devices"] = ["EQ Eight", "Compressor"]

    report = box.call("soundcheck", {"fix": False})
    assert report["silent"], report
    assert "no instrument" in report["silent"][0]["problems"][0]


def test_sound_preferences_reject_junk_paths(box):
    """"x/y" must never reach the config again."""
    with pytest.raises(ToolError, match="not a loadable browser item"):
        box.call("set_sound_preference", {"role": "lead", "path": "x/y"})
    assert box.sounds.for_role("lead") != "x/y"


def test_bare_plugin_preference_warns(box):
    box.bridge.browser_items = getattr(box.bridge, "browser_items", None)
    # The fake browser will not know this path; patch _browser_item to accept.
    box._browser_item = lambda path: {"name": path.rsplit("/", 1)[-1],
                                      "uri": "u", "is_loadable": True}
    result = box.call("set_sound_preference", {
        "role": "bass", "path": "Plugins/VST3/Xfer Records/Serum 2",
    })
    assert "init patch" in result.get("warning", ""), result


def test_failed_role_preference_falls_back_not_silent(box):
    """The preference fails; the track still gets an instrument, loudly."""
    box._browser_item = lambda path: {"name": "junk", "uri": "u",
                                      "is_loadable": True}
    box.call("create_track", {"name": "Lead", "role": "lead"})
    box.sounds.set_role("lead", "Nonsense/Not A Device")

    result = box.call("load_sound", {"track_index": 0, "role": "lead"})
    assert "failed to load" in result.get("warning", ""), result
    assert box.bridge.tracks[0]["devices"], "fell back to nothing"


def test_build_track_twice_does_not_double_the_band(box):
    """A rebuild reuses its own tracks; it does not create a second band."""
    box.call("build_track", {"genre": "trance", "key": "A",
                             "duration_seconds": 240, "seed": 1})
    count = len(box.bridge.tracks)
    box.call("build_track", {"genre": "trance", "key": "A",
                             "duration_seconds": 240, "seed": 2})
    assert len(box.bridge.tracks) == count, (
        f"{count} tracks became {len(box.bridge.tracks)}"
    )


def test_enum_defaults_are_in_their_own_enum():
    """A tool whose default value its own schema rejects is broken on arrival.

    create_chord_clip defaulted rhythm="pad" while its enum listed the bass
    rhythm patterns, so the very first call with no rhythm was rejected.
    """
    import inspect

    from ableton_ai.tools import Toolbox

    schemas = {s["name"]: s for s in tool_schemas()}
    for name, schema in schemas.items():
        method = getattr(Toolbox, f"tool_{name}", None)
        if method is None:
            continue
        sig = inspect.signature(method)
        props = schema["input_schema"]["properties"]
        for param, spec in props.items():
            enum = spec.get("enum")
            if not enum or param not in sig.parameters:
                continue
            default = sig.parameters[param].default
            if default is inspect.Parameter.empty or default is None:
                continue
            assert default in enum, (
                f"{name}.{param} defaults to {default!r}, "
                f"not in its enum {enum}"
            )


def test_compression_only_where_it_belongs(box):
    """Best practice: the rhythm section is compressed, melodic parts are not.

    A compressor on every lead, pad and hook was the "too much weird
    compression" -- those want EQ and sidechain, not gain reduction.
    """
    from ableton_ai import mixing

    for role in ("kick", "drums", "bass", "sub", "vocal"):
        assert mixing.wants_compression(role), role
    for role in ("lead", "hook", "pad", "chords", "arp", "melody", "strings"):
        assert not mixing.wants_compression(role), role

    box.call("create_track", {"name": "Lead", "role": "lead"})
    result = box.call("add_compression", {"track_index": 0})
    assert result.get("skipped"), "a lead should not be compressed by default"

    box.call("create_track", {"name": "Bass", "role": "bass"})
    result = box.call("add_compression", {"track_index": 1})
    assert not result.get("skipped"), "the bass should be compressed"


def test_compressor_values_are_gentle_and_normalised():
    """Threshold/ratio/attack/release are 0..1 -- passing a raw ratio clamps
    to infinity:1, which crushed every track. Every value must be in range and
    in the gentle third."""
    from ableton_ai import mixing

    for name, setting in mixing.COMPRESSION.items():
        for field in ("threshold", "ratio", "attack", "release"):
            value = getattr(setting, field)
            assert 0.0 <= value <= 1.0, f"{name}.{field}={value} out of 0..1"
        # No brick walls: ratio well under maximum.
        assert setting.ratio <= 0.5, f"{name} ratio {setting.ratio} too high"


def test_processing_plan_high_passes_and_sidechains_by_role():
    """The engineer's rules: high-pass non-low roles, duck the sustained ones."""
    from ableton_ai import processing

    roles = {0: "kick", 1: "bass", 2: "pad", 3: "lead", 4: "hook", 5: "sub"}
    plan = processing.plan(roles)

    # The kick and sub are not high-passed away; everyone else is.
    hp = {e["role"]: e["high_pass_hz"] for e in plan["eq"]}
    assert hp["kick"] <= 40 and hp["sub"] == 0
    assert hp["lead"] >= 150 and hp["hook"] >= 150 and hp["pad"] >= 150

    # Sustained roles duck; the kick, lead and hook never do.
    ducked = {e["role"] for e in plan["sidechain"]}
    assert {"bass", "sub", "pad"} <= ducked
    assert "kick" not in ducked and "lead" not in ducked and "hook" not in ducked

    # Only the rhythm section is compressed.
    compressed = {e["role"] for e in plan["compress"]}
    assert compressed == {"kick", "bass", "sub"}


def test_timeout_retries_reads_but_not_writes():
    """A transient Live-busy timeout is recovered for reads, surfaced for writes.

    Live's main thread blocks momentarily (a dialog, a loading plugin). A
    read is safe to retry; a write may have half-applied and must not be
    repeated. And the socket is reset either way, so a late response can never
    corrupt the next command -- which is why a timeout used to be followed by
    a second, unrelated failure.
    """
    from ableton_ai.bridge import AbletonBridge, AbletonError

    class Flaky(AbletonBridge):
        def __init__(self):
            super().__init__()
            self.attempts = {}
            self.closed = 0

        def _call_locked(self, command, params):
            self.attempts[command] = self.attempts.get(command, 0) + 1
            if self.attempts[command] == 1:
                raise AbletonError(f"{command}: Ableton did not respond within 30.0s")
            return {"ok": True}

        def _close_locked(self):
            self.closed += 1

    b = Flaky()
    assert b.call("ping") == {"ok": True}
    assert b.attempts["ping"] == 2, "a read should retry once"
    assert b.closed >= 1, "the socket must be reset before retry"

    import pytest

    with pytest.raises(AbletonError, match="may have partly applied|was busy"):
        b.call("create_clip", track_index=0)
    assert b.attempts["create_clip"] == 1, "a write must not be retried"


def test_parameter_name_aliases_are_repaired(box):
    """The model reaches for plausible parameter names; repair them like values.

    'build_track(genre=trance, key=E, scale=minor)' failed with a bare
    "unexpected keyword argument" that hid which argument and what the real
    names were. Common synonyms are now renamed, and a genuinely unknown one
    names the accepted parameters.
    """
    # scale is a real build_track parameter -- passes untouched.
    r = box.call("build_track", {"genre": "trance", "key": "E", "scale": "minor",
                                "duration_seconds": 120})
    assert "scale" not in (r.get("corrected") or [])

    # bpm/mode/length are synonyms -> tempo/scale/duration_seconds.
    r = box.call("build_track", {"genre": "trance", "key": "E", "bpm": 138,
                                "mode": "minor", "length": 120})
    corrected = set(r.get("corrected") or [])
    assert "bpm -> tempo" in corrected
    assert "mode -> scale" in corrected
    assert "length -> duration_seconds" in corrected

    # An alias never overwrites an explicit real value.
    with pytest.raises(ToolError, match="unknown argument"):
        box.call("build_track", {"genre": "trance", "key": "E", "tonic": "C"})

    # A genuinely unknown keyword lists the accepted parameters.
    with pytest.raises(ToolError, match="Accepts:.*genre"):
        box.call("build_track", {"genre": "trance", "wibble": 1})


def test_character_is_a_free_hint_not_an_enum(box):
    """"low strings" is a sensible request; character must not be rejected.

    It was enum-locked, so 'low' failed with a nonsense near-match
    ('analog'). It is a free descriptor that biases the search -- an unknown
    word simply does not bias, it never fails.
    """
    from ableton_ai import schemas

    props = {t["name"]: t for t in schemas.tool_schemas()}["pick_sound"][
        "input_schema"]["properties"]
    assert "enum" not in props.get("character", {}), (
        "character must not be a closed enum"
    )


def test_compose_theme_tolerates_bare_track_entries(box):
    """tracks may be dicts, bare indices, or role-name strings -- never a crash.

    A bare list of ints raised "'int' object has no attribute 'get'"; a list
    of role names raised the same for 'str'.
    """
    for name in ("Lead", "Hook", "Bass", "Melody"):
        box.call("create_track", {"name": name, "role": name.lower()})

    # bare indices
    r = box.call("compose_theme", {"key": "E", "scale": "minor",
                                  "degrees": "1-3-7-5", "tracks": [0, 1, 2]})
    assert r["written"], r
    # role-name strings
    r = box.call("compose_theme", {"key": "E", "scale": "minor",
                                  "degrees": [1, 3, 7, 5],
                                  "tracks": ["lead", "hook"]})
    assert r["written"], r
    # dicts still work
    r = box.call("compose_theme", {"key": "E", "scale": "minor",
                                  "degrees": "1-3-7-5",
                                  "tracks": [{"track_index": 0, "role": "lead"}]})
    assert r["written"], r


def test_arrange_existing_handles_timeline_only_material():
    """Every Session slot empty, material only on the arrangement -- it must
    still arrange, and clear_first must not destroy the only copy.

    This was the exact case that found nothing: arrange_existing places
    Session clips, and a set whose parts live solely on the timeline has no
    Session clip to place. The timeline-source fallback repeats that material
    instead.
    """
    box = Toolbox(FakeBridge())
    b = box.bridge
    for name, role in (("Kick", "kick"), ("Bass", "bass"),
                       ("Chords", "chords"), ("Lead", "lead")):
        box.call("create_track", {"name": name, "role": role})
    for i in range(4):
        b.arrangement[i] = [{"name": f"c{i}", "start_bars": 0.0,
                             "length_bars": 8.0, "start_beats": 0.0,
                             "end_beats": 32.0}]
    for t in b.tracks:               # every session slot empty
        t["clips"] = {}

    result = box.call("arrange_existing", {"target_seconds": 120,
                                          "template": "techno"})
    assert len(result["arranged"]) == 4, result
    # Each track's single timeline clip was spread across the sections.
    for i in range(4):
        assert len(b.arrangement.get(i, [])) > 1, f"track {i} not spread"


def test_arrange_existing_mixed_session_and_timeline():
    """Session-only, timeline-only and both, side by side, all arrange."""
    box = Toolbox(FakeBridge())
    b = box.bridge
    for name, role in (("Kick", "kick"), ("Bass", "bass"), ("Chords", "chords")):
        box.call("create_track", {"name": name, "role": role})
    b.call("create_clip", track_index=0, clip_index=0, length_beats=16.0,
           notes=[{"pitch": 36, "start": 0, "duration": 1, "velocity": 110}],
           name="kick")
    b.arrangement[1] = [{"name": "bass", "start_bars": 0.0, "length_bars": 8.0,
                         "start_beats": 0.0, "end_beats": 32.0}]   # timeline-only
    b.call("create_clip", track_index=2, clip_index=0, length_beats=32.0,
           notes=[{"pitch": 60, "start": 0, "duration": 4, "velocity": 90}],
           name="chords")

    result = box.call("arrange_existing", {"target_seconds": 90,
                                          "template": "techno"})
    assert {a["role"] for a in result["arranged"]} == {"kick", "bass", "chords"}
    assert len(b.arrangement.get(1, [])) > 1, "timeline-only bass not spread"


def test_arrange_infers_role_from_content_no_rename_needed():
    """An unclear track name must not force a rename -- the notes say enough.

    "Offbeat", "Main Midi", "DragPluck" carry no role in the name, but a
    bassline is a bassline by its register and a chord part by its polyphony.
    arrange_existing classifies from content and arranges every track, without
    telling the user to rename anything.
    """
    box = Toolbox(FakeBridge())

    # A low monophonic part named nothing useful -> classified as bass.
    box.call("create_track", {"name": "Offbeat"})
    box.bridge.call("create_clip", track_index=0, clip_index=0,
                    length_beats=8.0, name="Offbeat", notes=[
                        {"pitch": 38, "start": i * 0.5, "duration": 0.4,
                         "velocity": 100} for i in range(8)])
    # A clear kick, by name.
    box.call("create_track", {"name": "Kick"})
    box.bridge.call("create_clip", track_index=1, clip_index=0,
                    length_beats=4.0, name="k", notes=[
                        {"pitch": 36, "start": 0.0, "duration": 0.5,
                         "velocity": 110}])

    result = box.call("arrange_existing", {"target_seconds": 90})
    roles = {a["role"] for a in result["arranged"]}
    assert "bass" in roles, f"Offbeat not inferred as bass: {roles}"
    assert "kick" in roles
    # No 'rename' anywhere in the result.
    assert "rename" not in str(result).lower()


def test_master_chain_includes_a_spectrum(box):
    """The master gets a Spectrum for the user to watch during mix/master."""
    from ableton_ai import mixing
    assert any("Spectrum" in dev for dev, _why in mixing.MASTER_CHAIN)

    box.call("add_spectrum", {"track_index": -1})
    devices = [d["name"] for d in
               box.bridge.call("get_devices", track_index=-1)["devices"]]
    assert any("Spectrum" in d for d in devices)
    # Idempotent -- a second call does not stack a second analyser.
    r = box.call("add_spectrum", {"track_index": -1})
    assert r.get("already_present")


def test_hot_band_flags_only_a_real_peak():
    """The spectral EQ dips a band only when it stands well above the rest."""
    from ableton_ai.tools import Toolbox
    flat = {"sub": -20, "bass": -19, "mid": -21, "high": -20}
    peaky = {"sub": -20, "bass": -8, "mid": -22, "high": -21}
    assert Toolbox._hot_band(flat) is None
    assert Toolbox._hot_band(peaky) == "bass"


def test_master_chain_is_idempotent(box):
    """Running mix/master twice must not stack a second EQ/glue/limiter.

    A doubled master chain over-processes the mix -- and a stale sidecar
    re-running the buggy EQ was how a high-pass got pinned at 22kHz and
    silenced the pad, kick and leads.
    """
    box.call("add_master_chain", {})
    first = [d["name"] for d in box.bridge.call("get_devices", track_index=-1)["devices"]]
    box.call("add_master_chain", {})
    second = [d["name"] for d in box.bridge.call("get_devices", track_index=-1)["devices"]]
    assert first == second, f"master chain grew: {first} -> {second}"
    assert sum("EQ Eight" in d for d in first) == 1
    assert sum("Limiter" in d for d in first) == 1


def test_delete_device_and_clean_chain(box):
    """delete_device removes one; clean_device_chain strips duplicates."""
    box.call("create_track", {"name": "Master test", "role": "chords"})
    # stack a doubled chain
    for path in ("Audio Effects/EQ Eight", "Audio Effects/Compressor",
                 "Audio Effects/EQ Eight", "Audio Effects/Compressor"):
        box.bridge.call("load_device", track_index=0, path=path)
    before = box.bridge.call("get_devices", track_index=0)["devices"]
    assert len(before) == 4

    r = box.call("clean_device_chain", {"track_index": 0})
    after = [d["name"] for d in box.bridge.call("get_devices", track_index=0)["devices"]]
    assert len(after) == 2, after
    assert len(r["removed"]) == 2

    # direct delete by index
    box.call("delete_device", {"track_index": 0, "device_index": 0})
    assert len(box.bridge.call("get_devices", track_index=0)["devices"]) == 1


def test_mix_and_master_applies_the_ten_practices(box):
    """"mix/master" runs one flow that applies the practices in order."""
    from ableton_ai import mixing
    for role in ("Kick", "Bass", "Pad", "Chords", "Lead", "Hats"):
        box.bridge.call("create_midi_track", name=role)

    r = box.call("mix_and_master", {"translation_check": False})
    # the published checklist is exactly ten, in order
    assert r["practices"] == [n for n, _why in mixing.MIX_MASTER_PRACTICES]
    assert len(r["practices"]) == 10
    # every step that does not need real-time audio capture applies here
    ok = {s["practice"] for s in r["steps"] if s["ok"]}
    assert {"gain staging", "balance and pan", "low-end mono",
            "master bus"} <= ok


def test_low_end_mono_turns_bass_mono_on_at_the_right_hz(box):
    """The sub is summed to mono with a real-Hz frequency, not a 0..1 guess."""
    box.bridge.call("create_midi_track", name="Bass")
    r = box.call("low_end_mono", {"below_hz": 100})
    assert r["tracks"] == ["Bass"]

    devices = box.bridge.call("get_devices", track_index=0)["devices"]
    util = next(d for d in devices if "utility" in d["name"].lower())
    params = {p["name"]: p["value"] for p in util["parameters"]}
    assert params["Bass Mono"] == 1.0            # toggle on
    assert params["Bass Freq"] == 100.0          # written as real Hz, clamped in range


def test_loudness_gain_drives_up_and_caps():
    from ableton_ai import mixing
    # 6 LU under target -> add 6 dB of drive
    assert mixing.loudness_gain(-15.0, -9.0, 0.0) == 6.0
    # already loud enough -> no negative gain (never turns it down here)
    assert mixing.loudness_gain(-7.0, -9.0, 0.0) == 0.0
    # cannot ask for more than the cap
    assert mixing.loudness_gain(-40.0, -9.0, 0.0, max_gain_db=12.0) == 12.0


def test_master_loudness_gains_into_the_limiter(box, monkeypatch):
    """Given a too-quiet master, it raises the limiter's input Gain."""
    pytest.importorskip("pyloudnorm")
    box.bridge.call("load_device", track_index=-1, path="Audio Effects/Limiter")

    # stub the real-time capture + analysis: report a quiet master once,
    # then on-target after the drive is applied.
    from ableton_ai import analysis as ana
    calls = {"n": 0}
    monkeypatch.setattr(box, "tool_capture_audio",
                        lambda **k: {"file_path": "/tmp/fake.wav"})

    class R:
        def __init__(self, lufs): self.lufs = lufs
        def to_dict(self):
            return {"lufs": self.lufs, "true_peak_db": -1.0}

    def fake_analyse(path, max_seconds=16):
        calls["n"] += 1
        return R(-15.0) if calls["n"] == 1 else R(-9.2)
    monkeypatch.setattr(ana, "analyse", fake_analyse)

    r = box.tool_master_loudness(target_lufs=-9.0, rounds=3)
    assert r["limiter_gain_db"] > 0, r
    # the Gain param on the master limiter actually moved
    devs = box.bridge.call("get_devices", track_index=-1)["devices"]
    lim = next(d for d in devs if "limiter" in d["name"].lower())
    gain = next(p["value"] for p in lim["parameters"] if p["name"] == "Gain")
    assert gain > 0
    assert r["final_lufs"] == -9.2


def test_pad_and_pulse_are_separate_roles(box):
    """The big pad and the pulse pad are distinct: names, roles, and pump."""
    from ableton_ai import arrangement, processing
    # aliases resolve the words producers use
    assert arrangement.normalise_role("pulse pad") == "pulse"
    assert arrangement.normalise_role("big pad") == "pad"
    assert arrangement.normalise_role("Pulse") == "pulse"
    # the sustained pad barely ducks; the pulse carries the pump
    assert processing.sidechain_depth("pulse") > processing.sidechain_depth("pad")
    assert processing.sidechain_depth("pad") <= 0.2


def test_build_track_writes_a_pad_and_a_pulse(box):
    """build_track creates both harmonic layers, not one pad doing both jobs."""
    box.tool_build_track(genre="trance", key="A", duration_seconds=120,
                         seed=1, mix=False, master=False, placeholders=False)
    names = {t["name"] for t in box.bridge.call("get_song")["tracks"]}
    assert "Pad" in names and "Pulse" in names
    # and the mix ducks the pulse deeper than the pad
    r = box.tool_process_mix()
    depths = {s.split(" duck ")[0]: float(s.split(" duck ")[1])
              for s in r["sidechain"]}
    assert depths.get("pulse", 0) > depths.get("pad", 1)


def test_duplicate_track_copies_below_with_its_arrangement(box):
    """duplicate_track makes a full copy directly below, arrangement and all."""
    box.bridge.call("create_midi_track", name="PulsePad")
    box.bridge.call("create_midi_track", name="Lead")
    # give PulsePad some arrangement clips
    box.bridge.arrangement[0] = [{"start": 0.0, "name": "a"},
                                 {"start": 16.0, "name": "b"}]

    r = box.tool_duplicate_track(track_index=0, name="Pad", role="pad")
    assert r["source_index"] == 0
    assert r["track_index"] == 1
    assert r["name"] == "Pad"

    names = [t["name"] for t in box.bridge.call("get_song")["tracks"]]
    assert names == ["PulsePad", "Pad", "Lead"]        # copy sits below source
    # the arrangement lane came with it, and Lead's lane shifted to index 2
    assert len(box.bridge.arrangement.get(1, [])) == 2
    assert box.bridge.arrangement.get(1) is not box.bridge.arrangement.get(0)


def test_transport_record_arms_and_rolls(box):
    """record arms arrangement recording and starts playback; stop disarms."""
    r = box.tool_transport(action="record")
    assert r["is_playing"] is True and r["is_recording"] is True
    song = box.bridge.call("get_song")
    assert song["is_recording"] is True

    r = box.tool_transport(action="stop")
    assert r["is_playing"] is False and r["is_recording"] is False

    # session record punches into slots instead of the timeline
    r = box.tool_transport(action="record", record_mode="session")
    assert box.bridge.session_record is True

    # metronome toggles
    box.tool_transport(action="metronome", metronome=True)
    assert box.bridge.call("get_song")["metronome"] is True


def test_intro_builds_up_elements_enter_progressively(box):
    """In the intro the foundation enters before the harmony -- it builds."""
    for role in ("Kick", "Drums", "Bass", "Chords", "Pad"):
        box.bridge.call("create_midi_track", name=role)
        idx = box.bridge.call("get_song")["tracks"][-1]["index"]
        if role in ("Kick", "Drums"):
            box.call("create_drum_clip", {"track_index": idx, "bars": 4})
        else:
            box.call("create_chord_clip",
                     {"track_index": idx, "bars": 4, "key": "A",
                      "scale": "minor"})
    r = box.tool_arrange_existing(target_seconds=120, template="house",
                                  clear_first=True)

    def first_bar(role):
        bars = [p["start_bar"] for p in r["detail"] if p["role"] == role]
        return min(bars) if bars else None

    # the kick opens; the harmony arrives later -- a build, not a block start
    assert first_bar("kick") == 0
    later = [first_bar(x) for x in ("chords", "pad") if first_bar(x) is not None]
    assert later and min(later) > 0, r["detail"][:8]


def test_pop_song_builds_subtly_no_drum_fills():
    """A pop song lifts into each chorus but gets no dance drum fills."""
    from ableton_ai import arrangement
    secs = arrangement.plan(target_seconds=180, tempo=120, template="song")
    assert not arrangement.is_dance_form(secs)
    lifts = arrangement.dropout_before_lifts(secs)
    assert lifts and all("subtle" in d["why"] for d in lifts)
    assert arrangement.phrase_marks(secs) == []      # no fills every 8 bars


def test_recipe_opts_into_sub_and_strings_layers(box):
    """A recipe with sub/strings gets those tracks; one without does not."""
    box.tool_build_track(genre="trance", key="D", duration_seconds=120,
                         seed=1, mix=False, master=False, placeholders=False)
    names = {t["name"] for t in box.bridge.call("get_song")["tracks"]}
    assert {"Sub", "Strings"} <= names, names

    from fake_live import FakeBridge
    from ableton_ai.tools import Toolbox
    other = Toolbox(FakeBridge())
    other.tool_build_track(genre="house", key="A", duration_seconds=120,
                           seed=1, mix=False, master=False, placeholders=False)
    n2 = {t["name"] for t in other.bridge.call("get_song")["tracks"]}
    assert "Sub" not in n2 and "Strings" not in n2, n2


def test_gain_to_targets_trims_kick_to_its_dbfs_anchor(box, monkeypatch):
    """The kick is measured and trimmed toward its absolute dBFS target."""
    pytest.importorskip("pyloudnorm")
    box.bridge.call("create_midi_track", name="Kick")
    box.bridge.call("create_midi_track", name="Sub")
    box.bridge.call("set_track_mixer", track_index=0, volume=0.85)

    from ableton_ai import analysis as ana
    monkeypatch.setattr(box, "tool_capture_audio",
                        lambda **k: {"file_path": "/tmp/x.wav"})

    class R:
        def __init__(self, **kw): self.kw = kw
        def to_dict(self): return self.kw
    # kick measures -6 dBFS peak (4 dB too hot vs -12); sub -14 rms (too hot vs -20)
    seq = iter([R(peak_db=-6.0, rms_db=-9.0, true_peak_db=-6.0),
                R(peak_db=-11.0, rms_db=-14.0, true_peak_db=-11.0)])
    monkeypatch.setattr(ana, "analyse", lambda p, max_seconds=8: next(seq))

    before = box.bridge.call("get_track", track_index=0)["volume"]
    r = box.tool_gain_to_targets()
    after = box.bridge.call("get_track", track_index=0)["volume"]
    assert after < before, r                       # kick was too hot -> trimmed down
    kick = next(a for a in r["anchored"] if a["role"] == "kick")
    assert kick["target_dbfs"] == -12.0 and kick["trim_db"] < 0


def test_gain_to_targets_trims_kick_to_its_dbfs_anchor(box, monkeypatch):
    """The kick is measured and trimmed toward its absolute dBFS target."""
    pytest.importorskip("pyloudnorm")
    box.bridge.call("create_midi_track", name="Kick")
    box.bridge.call("create_midi_track", name="Sub")
    box.bridge.call("set_track_mixer", track_index=0, volume=0.85)

    from ableton_ai import analysis as ana
    monkeypatch.setattr(box, "tool_capture_audio",
                        lambda **k: {"file_path": "/tmp/x.wav"})

    class R:
        def __init__(self, **kw): self.kw = kw
        def to_dict(self): return self.kw
    seq = iter([R(peak_db=-6.0, rms_db=-9.0, true_peak_db=-6.0),
                R(peak_db=-11.0, rms_db=-14.0, true_peak_db=-11.0)])
    monkeypatch.setattr(ana, "analyse", lambda p, max_seconds=8: next(seq))

    before = box.bridge.call("get_track", track_index=0)["volume"]
    r = box.tool_gain_to_targets()
    after = box.bridge.call("get_track", track_index=0)["volume"]
    assert after < before, r
    kick = next(a for a in r["anchored"] if a["role"] == "kick")
    assert kick["target_dbfs"] == -12.0 and kick["trim_db"] < 0


def test_trance_progression_library_is_named_and_diatonic(box):
    """The named trance shapes resolve, and start on the degree that names them."""
    from ableton_ai import theory
    for name, first in (("workhorse_1564", 1), ("minor_axis", 1),
                        ("lydian_lift", 6), ("children", 6),
                        ("dorian_lift", 4), ("mixolydian_lift", 7),
                        ("dark_turnaround", 1), ("step_down", 1)):
        assert name in theory.PROGRESSIONS, name
        assert theory.PROGRESSIONS[name][0] == first, name
    # and one builds real chords through the tool, in key
    r = box.call("create_chord_clip",
                 {"track_index": box.bridge.call("create_midi_track",
                                                 name="C")["track_index"],
                  "key": "D", "scale": "minor", "degrees": "lydian_lift",
                  "bars": 6})
    assert r["chords"], r


def test_build_picks_a_random_progression_from_the_genre_pool():
    """Unspecified progression is drawn from the genre's pool; a seed repeats it."""
    from fake_live import FakeBridge
    from ableton_ai.tools import Toolbox

    def build(seed, **kw):
        return Toolbox(FakeBridge()).tool_build_track(
            genre="trance", key="D", duration_seconds=90, seed=seed,
            mix=False, master=False, placeholders=False, **kw)

    picks = {build(s)["progression"] for s in range(8)}
    assert len(picks) > 1, picks                      # it actually varies
    pool = set(Toolbox(FakeBridge()).BUILD_RECIPES["trance"]["progressions"])
    assert picks <= pool, picks                       # only from the pool
    assert build(3)["progression"] == build(3)["progression"]   # seed repeats
    # a named progression overrides the pool
    assert build(1, progression="axis")["progression"] == "axis"


def test_pedal_tone_holds_one_note_across_the_progression(box):
    """pedal='tonic' adds one full-length held tonic under the chords."""
    from ableton_ai import theory
    ti = box.bridge.call("create_midi_track", name="Chords")["track_index"]
    box.tool_create_chord_clip(track_index=ti, key="D", scale="minor",
                               degrees="lydian_lift", bars=6, pedal="tonic")
    notes = box.bridge.call("get_clip", track_index=ti, clip_index=0)["notes"]
    tonic = theory.degree_to_pitch("D", "minor", 1, octave=2)
    held = [n for n in notes if n["pitch"] == tonic and n["duration"] >= 6 * 4 - 0.5]
    assert held, notes
    # no pedal by default
    box.tool_create_chord_clip(track_index=ti, clip_index=1, key="D",
                               scale="minor", degrees="lydian_lift", bars=6)
    n2 = box.bridge.call("get_clip", track_index=ti, clip_index=1)["notes"]
    assert not [n for n in n2 if n["duration"] >= 6 * 4 - 0.5]


def test_new_progressions_present(box):
    from ableton_ai import theory
    for name in ("uplifting_sweet", "euphoric_neutral", "bittersweet",
                 "melancholic"):
        assert name in theory.PROGRESSIONS, name
    assert theory.PROGRESSIONS["bittersweet"][:2] == (6, 6)   # the long 6


def test_build_from_scratch_clears_existing_tracks(box):
    """from_scratch deletes the existing tracks and builds on a clean set."""
    for n in ("1 MIDI", "2 Audio", "OldPad"):
        box.bridge.call("create_midi_track", name=n)
    before = {t["name"] for t in box.bridge.call("get_song")["tracks"]}

    r = box.tool_build_track(genre="house", key="A", duration_seconds=90,
                             seed=1, mix=False, master=False,
                             placeholders=False, from_scratch=True)
    after = {t["name"] for t in box.bridge.call("get_song")["tracks"]}
    assert r["cleared_tracks"] == 3
    assert not (before & after), after          # none of the junk survived
    assert {"Kick", "Bass", "Chords"} <= after  # and the build is there

    # default (no from_scratch) builds ALONGSIDE what exists
    box2 = Toolbox(FakeBridge())
    box2.bridge.call("create_midi_track", name="KeepMe")
    box2.tool_build_track(genre="house", key="A", duration_seconds=90, seed=1,
                          mix=False, master=False, placeholders=False)
    assert "KeepMe" in {t["name"] for t in box2.bridge.call("get_song")["tracks"]}
