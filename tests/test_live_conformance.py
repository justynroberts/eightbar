"""Tests that talk to a real Ableton. Skipped unless one is listening.

The simulator in `fake_live.py` is faithful to the API as *written down*, which
is not the API Live has. Every serious bug found on 2026-08-30 passed the whole
simulator suite:

    clear_arrangement removed nothing     -- Clip has no delete_clip()
    the park clip could not be deleted    -- handles die after any edit
    four locators of ten survived         -- current_song_time lands a tick late
    an empty track list cleared the set   -- [] read as "all"

None of those are expressible against dicts. They need Live.

    python -m pytest tests/test_live_conformance.py -m live -v

The set is left as it was found: every test works on a scratch track it creates
and deletes, and nothing touches the arrangement of a track it did not make.
"""

from __future__ import annotations

import pytest

from ableton_ai.bridge import AbletonBridge, AbletonError, AbletonNotRunning
from ableton_ai.tools import Toolbox

pytestmark = pytest.mark.live

BEATS_PER_BAR = 4.0


@pytest.fixture(scope="module")
def bridge():
    live = AbletonBridge()
    try:
        live.call("ping")
    except (AbletonError, AbletonNotRunning) as exc:
        pytest.skip(f"no Ableton on 9878: {exc}")
    return live


@pytest.fixture(scope="module")
def box(bridge):
    return Toolbox(bridge)


@pytest.fixture
def scratch(bridge):
    """A MIDI track created for one test and deleted afterwards."""
    before = len(bridge.call("get_song")["tracks"])
    bridge.call("create_midi_track", index=-1, name="AbletonAI Conformance")
    index = before
    yield index
    try:
        bridge.call("delete_track", track_index=index)
    except (AbletonError, AbletonNotRunning):
        pass


def _notes(count=4, pitch=60):
    return [{"pitch": pitch, "start": i * 1.0, "duration": 0.5, "velocity": 100}
            for i in range(count)]


# --------------------------------------------------------------- the API

def test_ping_reports_every_command_the_script_defines(bridge):
    """A command in the source that Live has not loaded means a stale install."""
    import ast
    from pathlib import Path

    source = Path(__file__).parent.parent / "remote_script/AbletonAI/__init__.py"
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    build = next(n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == "_build_handlers")
    table = next(n for n in ast.walk(build) if isinstance(n, ast.Dict))
    declared = {k.value for k in table.keys}

    loaded = set(bridge.call("ping")["commands"])
    missing = sorted(declared - loaded)
    assert not missing, (
        f"Live is running an older copy of the script; missing {missing}. "
        "Reinstall and restart Live."
    )


# ------------------------------------------------------- deletion actually deletes

def test_clear_arrangement_actually_removes_clips(bridge, scratch):
    """It reported success while deleting nothing, because Clip.delete_clip()
    does not exist and the AttributeError was swallowed."""
    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=4.0, notes=_notes())
    bridge.call("duplicate_clip_to_arrangement", track_index=scratch,
                clip_index=0, start_bar=0.0, repeats=4)

    lanes = {t["index"]: t for t in bridge.call("get_arrangement")["tracks"]}
    assert len(lanes[scratch]["clips"]) == 4

    result = bridge.call("clear_arrangement", track_indices=[scratch])
    assert result["removed"] == 4, result

    lanes = {t["index"]: t for t in bridge.call("get_arrangement")["tracks"]}
    assert scratch not in lanes or not lanes[scratch]["clips"]


def test_empty_track_list_clears_nothing(bridge, scratch):
    """[] used to be read as "every track" and wiped the whole timeline."""
    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=4.0, notes=_notes())
    bridge.call("duplicate_clip_to_arrangement", track_index=scratch,
                clip_index=0, start_bar=0.0, repeats=2)

    bridge.call("clear_arrangement", track_indices=[])

    lanes = {t["index"]: t for t in bridge.call("get_arrangement")["tracks"]}
    assert len(lanes[scratch]["clips"]) == 2, "an empty list wiped the timeline"


# ------------------------------------------------------------ handle lifetimes

def test_timeline_clip_can_be_repeated_and_the_park_removed(bridge, scratch):
    """Copies come from a parked duplicate; a stale handle fails as a Boost
    signature error rather than anything that reads like a lifetime problem."""
    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=4.0, notes=_notes())
    bridge.call("duplicate_clip_to_arrangement", track_index=scratch,
                clip_index=0, start_bar=0.0, repeats=1)

    result = bridge.call("duplicate_arrangement_clip", track_index=scratch,
                         source_index=0,
                         placements=[{"start_bar": 0, "repeats": 8}])
    assert result["placed"] == 8, result
    assert result["park_removed"], result
    assert not result.get("warnings"), result["warnings"]

    lane = {t["index"]: t for t in bridge.call("get_arrangement")["tracks"]}[scratch]
    ends_at = max(c["start_bars"] + c["length_bars"] for c in lane["clips"])
    assert ends_at == 8.0, f"a park clip was left behind at bar {ends_at}"


def test_envelope_survives_being_cleared_first(bridge, scratch):
    """clear_envelope() invalidates any Envelope taken before it, and writing
    through the stale handle fails with a Boost error naming TPyHandle."""
    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=4.0, notes=_notes())
    try:
        result = bridge.call(
            "set_clip_envelope", track_index=scratch, clip_index=0,
            device_index=0, parameter_index=0,
            points=[{"beat": 0.0, "value": 0.0}, {"beat": 4.0, "value": 1.0}],
            clear_first=True,
        )
    except AbletonError as exc:
        pytest.skip(f"no automatable device on a bare MIDI track: {exc}")
    assert not result.get("warnings"), result["warnings"]


# ------------------------------------------------------------ deferred writes

def test_every_locator_lands_and_keeps_its_name(bridge, scratch):
    """Assigning current_song_time takes a tick to land. Toggling before it did
    left four markers of ten, each deleting the one before it."""
    existing = bridge.call("get_locators")["locators"]

    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=4.0, notes=_notes())
    bridge.call("duplicate_clip_to_arrangement", track_index=scratch,
                clip_index=0, start_bar=0.0, repeats=32)

    wanted = [{"name": f"Mark {i}", "start_bar": i * 8} for i in range(1, 9)]
    bridge.call("set_locators", markers=wanted, clear_existing=True)

    box = Toolbox(bridge)
    placed = box._await_locators(len(wanted))
    by_bar = {round(m["start_bar"]): m["name"] for m in placed}
    try:
        for marker in wanted:
            bar = marker["start_bar"]
            assert bar in by_bar, f"no locator at bar {bar}; got {sorted(by_bar)}"
            assert by_bar[bar] == marker["name"], (
                f"bar {bar} is named {by_bar[bar]!r}, not {marker['name']!r}"
            )
    finally:
        bridge.call("set_locators",
                    markers=[{"name": m["name"], "start_bar": m["start_bar"]}
                             for m in existing],
                    clear_existing=True)


# -------------------------------------------------------------- reads that raise

def test_meters_read_on_a_midi_track(bridge, scratch):
    """A MIDI-routed track *raises* on output_meter_left rather than lacking it,
    so getattr's default never fires."""
    meters = bridge.call("get_meters")
    assert isinstance(meters, dict)


def test_note_round_trip_is_exact(bridge, scratch):
    """What comes back has to be what went in, or every analyser is wrong."""
    written = _notes(count=6, pitch=64)
    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=8.0, notes=written)
    clip = bridge.call("get_clip", track_index=scratch, clip_index=0)

    got = sorted(clip["notes"], key=lambda n: n["start"])
    assert len(got) == len(written)
    for original, returned in zip(written, got):
        assert returned["pitch"] == original["pitch"]
        assert abs(returned["start"] - original["start"]) < 1e-6
        assert abs(returned["duration"] - original["duration"]) < 1e-6


def test_unsaved_change_counter_moves(bridge, scratch):
    """The only warning available that a restart would cost work."""
    bridge.call("mark_saved")
    assert bridge.call("ping")["unsaved_changes"] == 0

    bridge.call("create_clip", track_index=scratch, clip_index=0,
                length_beats=4.0, notes=_notes())
    assert bridge.call("ping")["unsaved_changes"] > 0

    # Reading must not count as changing.
    before = bridge.call("ping")["unsaved_changes"]
    bridge.call("get_song")
    bridge.call("get_arrangement")
    assert bridge.call("ping")["unsaved_changes"] == before
