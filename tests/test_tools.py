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
    with pytest.raises(ToolError, match="bad arguments"):
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
    assert any("1 Frequency" in s and "250" in s for s in result["set"])


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
    with pytest.raises(ToolError, match="bad arguments"):
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
    """Regression: the model passed variation="extended", which sounds
    plausible and does not exist. Prose in a description does not stop it;
    an enum does."""
    from ableton_ai import harmony

    schemas = {s["name"]: s for s in tool_schemas()}
    variation = schemas["create_varied_chords"]["input_schema"]["properties"]["variation"]
    assert set(variation["enum"]) == set(harmony.RECIPES)
    assert "extended" not in variation["enum"]

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
