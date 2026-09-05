"""An in-memory stand-in for the remote script.

Implements the same command surface against plain dicts, so the tool layer, the
agent loop and the arrangement logic can all be exercised without Ableton
running. Anything the real script accepts, this should accept too.
"""

from __future__ import annotations

from typing import Any

from ableton_ai.bridge import AbletonError

BEATS_PER_BAR = 4.0


class FakeBridge:
    """Duck-types AbletonBridge."""

    def __init__(self) -> None:
        self.tempo = 120.0
        self.tracks: list[dict[str, Any]] = []
        self.arrangement: dict[int, list[dict[str, Any]]] = {}
        self.locators: list[dict[str, Any]] = []
        self.returns: list[dict[str, Any]] = [
            {"name": "A Reverb", "is_midi": False, "clips": {}, "devices": [],
             "params": {}, "volume": 0.85, "panning": 0.0, "muted": False,
             "soloed": False},
            {"name": "B Delay", "is_midi": False, "clips": {}, "devices": [],
             "params": {}, "volume": 0.85, "panning": 0.0, "muted": False,
             "soloed": False},
        ]
        self.envelopes: dict[tuple, list[dict]] = {}
        self.scale: dict[str, Any] = {}
        self.view = "session"
        self.playing = False
        self.recording = False
        self.session_record = False
        self.metronome = False
        self.calls: list[tuple[str, dict]] = []
        self.host, self.port = "fake", 0

    # -- plumbing -----------------------------------------------------

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def call(self, command: str, **params: Any) -> dict[str, Any]:
        self.calls.append((command, params))
        handler = getattr(self, f"_{command}", None)
        if handler is None:
            raise AbletonError(f"unknown command: {command}")
        try:
            return handler(**params)
        except AbletonError:
            raise
        except Exception as exc:
            # The real remote script catches handler exceptions and returns
            # them as error responses, which the bridge raises as
            # AbletonError. A fake that lets KeyError escape is testing an
            # error path the real system does not have.
            raise AbletonError(f"{command}: {exc}") from exc

    # -- helpers ------------------------------------------------------

    def _track(self, track_index: int) -> dict[str, Any]:
        # -1 is the master, -2 and below are return tracks -- as in the real
        # remote's _track_at. Model them so master-chain operations work.
        if track_index == -1:
            if not hasattr(self, "_master"):
                self._master = {"name": "Master", "is_midi": False,
                                "clips": {}, "devices": [], "params": {},
                                "volume": 0.85, "panning": 0.0,
                                "muted": False, "soloed": False}
            return self._master
        if track_index <= -2:
            ri = -track_index - 2
            if ri < len(self.returns):
                return self.returns[ri]
            raise AbletonError(f"return track {ri} out of range")
        if track_index < 0 or track_index >= len(self.tracks):
            raise AbletonError(f"track_index {track_index} out of range")
        return self.tracks[track_index]

    def _clip(self, track_index: int, clip_index: int) -> dict[str, Any]:
        clip = self._track(track_index)["clips"].get(clip_index)
        if clip is None:
            raise AbletonError(f"no clip at track {track_index} slot {clip_index}")
        return clip

    def _new_track(self, kind: str, index: int, name: str | None, color: int | None):
        track = {
            "name": name or f"{kind} {len(self.tracks)}",
            "kind": kind,
            "color": color,
            "clips": {},
            "muted": False, "solo": False, "volume": 0.85, "panning": 0.0,
            "devices": [],
            "sends": [0.0 for _ in self.returns],
        }
        self.tracks.append(track)
        return {"track_index": len(self.tracks) - 1, "name": track["name"]}

    # -- commands -----------------------------------------------------

    def _ping(self) -> dict:
        return {"protocol": 1, "live_version": "12.0", "tempo": self.tempo,
                "commands": []}

    def _get_song(self, include_notes: bool = False) -> dict:
        tracks = []
        for i, t in enumerate(self.tracks):
            clips = []
            for slot, clip in sorted(t["clips"].items()):
                entry = {
                    "slot": slot, "name": clip["name"],
                    "length_beats": clip["length"],
                    "length_bars": clip["length"] / BEATS_PER_BAR,
                    "is_midi": t["kind"] == "midi", "looping": True,
                }
                if include_notes:
                    entry["notes"] = clip["notes"]
                clips.append(entry)
            tracks.append({
                "index": i, "name": t["name"], "is_midi": t["kind"] == "midi",
                "muted": t["muted"], "soloed": t["solo"], "armed": False,
                "volume": t["volume"], "panning": t["panning"],
                "devices": t["devices"], "clips": clips,
                "is_grouped": False, "is_foldable": False,
                "arrangement_clip_count": len(self.arrangement.get(i, [])),
            })
        return {
            "tempo": self.tempo, "signature": "4/4", "is_playing": self.playing,
            "is_recording": self.recording, "session_record": self.session_record,
            "metronome": self.metronome,
            "current_time_beats": 0.0, "scene_count": 8,
            "track_count": len(self.tracks), "return_track_count": 2,
            "tracks": tracks,
        }

    def _get_track(self, track_index: int) -> dict:
        t = self._track(track_index)
        return {"index": track_index, "name": t["name"], "is_midi": t["kind"] == "midi",
                "muted": t["muted"], "soloed": t["solo"], "volume": t["volume"],
                "panning": t["panning"], "devices": [], "clip_slots": []}

    def _get_clip(self, track_index: int, clip_index: int) -> dict:
        clip = self._clip(track_index, clip_index)
        return {
            "track_index": track_index, "clip_index": clip_index,
            "name": clip["name"], "length_beats": clip["length"],
            "length_bars": clip["length"] / BEATS_PER_BAR,
            "is_midi": True, "looping": True, "loop_start": 0.0,
            "loop_end": clip["length"], "notes": clip["notes"],
        }

    def _set_tempo(self, tempo: float) -> dict:
        self.tempo = float(tempo)
        return {"tempo": self.tempo}

    def _create_midi_track(self, index: int = -1, name=None, color=None) -> dict:
        return self._new_track("midi", index, name, color)

    def _create_audio_track(self, index: int = -1, name=None, color=None) -> dict:
        return self._new_track("audio", index, name, color)

    def _delete_track(self, track_index: int) -> dict:
        self._track(track_index)
        self.tracks.pop(track_index)
        return {"deleted": track_index, "track_count": len(self.tracks)}

    def _duplicate_track(self, index: int = 0, name=None, color=None) -> dict:
        """Live's track duplicate: a full copy below the source, clips and all.

        Inserting a track shifts every later index, so the arrangement lanes
        and clip envelopes keyed by track index shift with it -- otherwise a
        copied track would inherit the wrong lane and the split would be a
        no-op that still looked applied.
        """
        import copy
        if index < 0 or index >= len(self.tracks):
            raise AbletonError(f"track index {index} out of range")
        new_index = index + 1
        dup = copy.deepcopy(self.tracks[index])
        self.tracks.insert(new_index, dup)
        self.arrangement = {
            (k + 1 if k >= new_index else k): v
            for k, v in self.arrangement.items()
        }
        self.arrangement[new_index] = copy.deepcopy(
            self.arrangement.get(index, []))
        self.envelopes = {
            ((k[0] + 1,) + k[1:] if k[0] >= new_index else k): v
            for k, v in self.envelopes.items()
        }
        if name:
            dup["name"] = name
        if color is not None:
            dup["color"] = color
        return {"source_index": index, "track_index": new_index,
                "name": dup["name"]}

    def _set_track_name(self, track_index: int, name: str) -> dict:
        self._track(track_index)["name"] = name
        return {"name": name}

    def _set_track_color(self, track_index: int, color: int) -> dict:
        self._track(track_index)["color"] = color
        return {"color": color}

    def _set_track_mixer(self, track_index: int, **kw) -> dict:
        t = self._track(track_index)
        t.update({k: v for k, v in kw.items() if k in ("volume", "panning")})
        if "mute" in kw:
            t["muted"] = kw["mute"]
        if "solo" in kw:
            t["solo"] = kw["solo"]
        return {"volume": t["volume"], "panning": t["panning"],
                "mute": t["muted"], "solo": t["solo"]}

    def _create_clip(self, track_index: int, clip_index: int, length_beats: float,
                     notes=None, name=None, overwrite: bool = True) -> dict:
        track = self._track(track_index)
        track["clips"][clip_index] = {
            "name": name or f"clip {clip_index}",
            "length": float(length_beats),
            "notes": [dict(n) for n in (notes or [])],
        }
        return {"track_index": track_index, "clip_index": clip_index,
                "length_beats": float(length_beats),
                "notes_written": len(notes or [])}

    def _delete_clip(self, track_index: int, clip_index: int) -> dict:
        self._track(track_index)["clips"].pop(clip_index, None)
        return {"deleted": True}

    def _set_clip_name(self, track_index: int, clip_index: int, name: str) -> dict:
        self._clip(track_index, clip_index)["name"] = name
        return {"name": name}

    def _set_clip_color(self, track_index, clip_index, color) -> dict:
        return {"color": color}

    def _replace_notes(self, track_index: int, clip_index: int, notes) -> dict:
        self._clip(track_index, clip_index)["notes"] = [dict(n) for n in notes]
        return {"notes_written": len(notes)}

    def _add_notes(self, track_index: int, clip_index: int, notes) -> dict:
        self._clip(track_index, clip_index)["notes"].extend(dict(n) for n in notes)
        return {"notes_added": len(notes)}

    def _duplicate_clip_to_arrangement(self, track_index: int, clip_index: int,
                                       start_bar: float, repeats: int = 1) -> dict:
        clip = self._clip(track_index, clip_index)
        bars = clip["length"] / BEATS_PER_BAR
        lane = self.arrangement.setdefault(track_index, [])
        for r in range(repeats):
            start = start_bar + r * bars
            lane.append({"name": clip["name"], "start_bars": start,
                         "length_bars": bars,
                         "start_beats": start * BEATS_PER_BAR,
                         "end_beats": (start + bars) * BEATS_PER_BAR})
        return {"placed_at_beats": [], "end_bars": start_bar + repeats * bars}

    def _duplicate_arrangement_clip(self, track_index: int, source_index: int = 0,
                                    placements=None, start_bar: float = 0.0,
                                    repeats: int = 1) -> dict:
        lane = self.arrangement.setdefault(track_index, [])
        if not lane:
            raise KeyError(f"track {track_index} has nothing on the timeline")
        ordered = sorted(lane, key=lambda c: c["start_bars"])
        source = ordered[source_index]
        bars = source["length_bars"]
        specs = placements or [{"start_bar": start_bar, "repeats": repeats}]
        placed = 0
        for spec in specs:
            begin = float(spec.get("start_bar", 0.0))
            for r in range(max(1, int(spec.get("repeats", 1)))):
                at = begin + r * bars
                # Live replaces whatever an incoming clip overlaps.
                lane[:] = [c for c in lane
                           if c["start_bars"] + c["length_bars"] <= at
                           or c["start_bars"] >= at + bars]
                lane.append({"name": source["name"], "start_bars": at,
                             "length_bars": bars,
                             "start_beats": at * BEATS_PER_BAR,
                             "end_beats": (at + bars) * BEATS_PER_BAR})
                placed += 1
        return {"source": {"name": source["name"],
                           "start_bar": source["start_bars"],
                           "length_bars": bars},
                "placed": placed}

    def _get_arrangement(self) -> dict:
        tracks, end = [], 0.0
        for index, clips in sorted(self.arrangement.items()):
            if not clips:
                continue
            ordered = sorted(clips, key=lambda c: c["start_bars"])
            tracks.append({"index": index, "name": self.tracks[index]["name"],
                           "clips": ordered})
            end = max(end, max(c["start_bars"] + c["length_bars"] for c in ordered))
        return {"tempo": self.tempo, "end_beats": end * BEATS_PER_BAR,
                "end_bars": end,
                "duration_seconds": round(end * BEATS_PER_BAR / self.tempo * 60, 2),
                "tracks": tracks}

    def _clear_arrangement(self, track_indices=None) -> dict:
        # An explicitly empty list clears nothing; only an absent one is "all".
        targets = (list(self.arrangement) if track_indices is None
                   else list(track_indices))
        removed = 0
        for index in targets:
            removed += len(self.arrangement.get(index, []))
            self.arrangement[index] = []
        return {"removed": removed}

    def _set_arrangement_loop(self, start_bar, length_bars, enabled) -> dict:
        return {"loop_start": start_bar * BEATS_PER_BAR,
                "loop_length": length_bars * BEATS_PER_BAR, "loop": enabled}

    def _set_locators(self, markers, clear_existing: bool = True) -> dict:
        if clear_existing:
            self.locators = []
        self.locators.extend(dict(m) for m in markers)
        return {"locators": self.locators}

    def _get_locators(self) -> dict:
        return {"locators": self.locators}

    # -- devices, mixer, automation -----------------------------------

    # A stock-synth parameter set, so recipe matching can be exercised.
    STOCK_PARAMS = [
        "Device On", "Osc 1 On", "Osc 1 Transp", "Osc 1 Detune", "Osc 1 Pan",
        "Osc 2 On", "Osc 2 Transp", "Osc 2 Detune", "Osc 2 Pan",
        "Flt 1 Freq", "Flt 1 Res", "Amp Attack", "Amp Decay", "Amp Sustain",
        "Amp Release",
    ]

    # Faithful to Live's EQ Eight: Frequency is 0..1 (log Hz), Type is 0..7,
    # Gain is -15..15. Modelling frequency as real Hz is what let the
    # normalised-parameter bug (a high-pass clamped to 22kHz) hide for so long.
    EQ_PARAMS = {
        "Device On": (0.0, 1.0),
        "1 Filter On A": (0.0, 1.0), "1 Frequency A": (0.0, 1.0),
        "1 Filter Type A": (0.0, 7.0), "1 Gain A": (-15.0, 15.0),
        "1 Resonance A": (0.0, 1.0),
        "8 Filter On A": (0.0, 1.0), "8 Frequency A": (0.0, 1.0),
        "8 Filter Type A": (0.0, 7.0), "8 Gain A": (-15.0, 15.0),
        "8 Resonance A": (0.0, 1.0), "Output Gain": (-15.0, 15.0),
    }

    # Faithful to Live 12's Utility: Bass Mono is an on/off toggle plus a
    # Bass Freq control in real Hz (50..500), NOT a normalised 0..1. Modelling
    # it as generic 0..1 stock params is what let low-end-mono look applied
    # while writing to a parameter that did not exist.
    UTILITY_PARAMS = {
        "Device On": (0.0, 1.0, 1.0),
        "Gain": (-35.0, 35.0, 0.0),
        "Width": (0.0, 400.0, 100.0),
        "Mono": (0.0, 1.0, 0.0),
        "Bass Mono": (0.0, 1.0, 0.0),
        "Bass Freq": (50.0, 500.0, 120.0),
    }

    # Faithful to Live's Limiter: Gain is the input drive in real dB and
    # Ceiling the output cap in real dB -- neither is a normalised 0..1. Master
    # loudness is made by raising Gain, so a fake that hid Gain would let the
    # "mix for loudness" fix look applied while doing nothing.
    LIMITER_PARAMS = {
        "Device On": (0.0, 1.0, 1.0),
        "Gain": (-12.0, 12.0, 0.0),
        "Ceiling": (-70.0, 0.0, -0.3),
        "Release": (0.1, 1.0, 0.5),
        "Auto": (0.0, 1.0, 1.0),
    }

    def _device_params(self, track_index: int, device_index: int) -> list[dict]:
        track = self._track(track_index)
        if device_index >= len(track["devices"]):
            raise AbletonError(f"device_index {device_index} out of range")
        store = track.setdefault("params", {}).setdefault(device_index, {})
        device = str(track["devices"][device_index]).lower()
        if "eq" in device:
            return [
                {"name": n, "value": store.get(n, 0.5), "min": lo, "max": hi}
                for n, (lo, hi) in self.EQ_PARAMS.items()
            ]
        if "limiter" in device:
            return [
                {"name": n, "value": store.get(n, default), "min": lo, "max": hi}
                for n, (lo, hi, default) in self.LIMITER_PARAMS.items()
            ]
        if "utility" in device:
            return [
                {"name": n, "value": store.get(n, default), "min": lo, "max": hi}
                for n, (lo, hi, default) in self.UTILITY_PARAMS.items()
            ]
        return [
            {"name": n, "value": store.get(n, 0.5), "min": 0.0, "max": 1.0}
            for n in self.STOCK_PARAMS
        ]

    def _resolve_param_name(self, track_index, device_index, parameter) -> str:
        names = [p["name"] for p in self._device_params(track_index, device_index)]
        if isinstance(parameter, int) or str(parameter).isdigit():
            index = int(parameter)
            if index >= len(names):
                raise AbletonError("parameter index out of range")
            return names[index]
        needle = str(parameter).lower()
        for n in names:
            if n.lower() == needle:
                return n
        for n in names:
            if needle in n.lower():
                return n
        raise AbletonError(f"no parameter matching '{parameter}'")

    def _set_device_parameter(self, track_index: int, value: float,
                              device_index: int = 0, parameter: Any = 0,
                              target: str = "device", send_index: int = 0,
                              normalised: bool = True) -> dict:
        track = self._track(track_index)
        if target == "volume":
            track["volume"] = float(value)
            return {"name": "Volume", "value": track["volume"], "min": 0.0, "max": 1.0}
        if target in ("panning", "pan"):
            track["panning"] = float(value) * 2 - 1
            return {"name": "Pan", "value": track["panning"], "min": -1.0, "max": 1.0}
        if target == "send":
            return self._set_send(track_index, send_index, value)
        name = self._resolve_param_name(track_index, device_index, parameter)
        params = {p["name"]: p for p in
                  self._device_params(track_index, device_index)}
        lo, hi = params[name]["min"], params[name]["max"]
        # Match the remote script: normalised writes map 0..1 onto the real
        # range, and every write clamps into [min, max]. A tool that writes a
        # raw 22000 into a 0..1 frequency must land pinned at the top here too.
        if normalised:
            value = lo + (hi - lo) * max(0.0, min(1.0, float(value)))
        value = max(lo, min(hi, float(value)))
        track.setdefault("params", {}).setdefault(device_index, {})[name] = value
        return {"name": name, "value": value, "min": lo, "max": hi}

    def _back_to_arrangement(self, stop_clips: bool = True) -> dict:
        self.back_to_arrangement_calls = getattr(
            self, "back_to_arrangement_calls", 0) + 1
        return {"back_to_arrangement": True}

    def _get_meters(self) -> dict:
        return {
            "is_playing": self.playing,
            "tracks": [
                {"index": i, "name": t["name"],
                 "level": 0.5 if self.playing else 0.0,
                 "left": 0.5 if self.playing else 0.0,
                 "right": 0.5 if self.playing else 0.0,
                 "muted": t["muted"]}
                for i, t in enumerate(self.tracks)
            ],
            "master": {"level": 0.6 if self.playing else 0.0,
                       "left": 0.6 if self.playing else 0.0,
                       "right": 0.6 if self.playing else 0.0},
        }

    def _get_mixer(self) -> dict:
        return {
            "tracks": [
                {"index": i, "name": t["name"], "volume": t["volume"],
                 "panning": t["panning"], "mute": t["muted"], "solo": t["solo"],
                 "sends": [{"index": j, "to": self.returns[j]["name"], "value": v}
                           for j, v in enumerate(t.get("sends", []))]}
                for i, t in enumerate(self.tracks)
            ],
            "returns": [{"index": i, "name": r["name"], "volume": 0.85}
                        for i, r in enumerate(self.returns)],
            "master_volume": 0.85,
        }

    def _set_send(self, track_index: int, send_index: int = 0,
                  value: float = 0.0) -> dict:
        track = self._track(track_index)
        sends = track.setdefault("sends", [0.0 for _ in self.returns])
        if send_index >= len(sends):
            raise AbletonError("send_index out of range")
        sends[send_index] = float(value)
        return {"send_index": send_index, "value": float(value)}

    def _create_return_track(self, name: str | None = None) -> dict:
        self.returns.append({"name": name or f"Return {len(self.returns)}"})
        for t in self.tracks:
            t.setdefault("sends", []).append(0.0)
        return {"return_index": len(self.returns) - 1,
                "name": self.returns[-1]["name"]}

    def _set_song_scale(self, root_note: int | None = None,
                        scale_name: str | None = None) -> dict:
        if root_note is not None:
            self.scale["root_note"] = int(root_note) % 12
        if scale_name is not None:
            self.scale["scale_name"] = scale_name
        if not self.scale:
            raise AbletonError("nothing to set")
        return dict(self.scale)

    def _set_clip_envelope(self, track_index: int, clip_index: int, points,
                           target: str = "device", parameter: Any = 0,
                           device_index: int = 0, send_index: int = 0,
                           resolution: float = 0.125, normalised: bool = True,
                           clear_first: bool = True, max_steps: int = 3000) -> dict:
        self._clip(track_index, clip_index)
        if len(points) < 2:
            raise AbletonError("need at least two points to draw an envelope")
        if target == "device":
            name = self._resolve_param_name(track_index, device_index, parameter)
        else:
            name = target
        ordered = sorted(points, key=lambda p: float(p["time"]))
        steps = 0
        for a, b in zip(ordered, ordered[1:]):
            span = float(b["time"]) - float(a["time"])
            if span > 0:
                steps += max(1, int(round(span / resolution)))
        steps = min(steps, max_steps)
        key = (track_index, clip_index, name)
        if clear_first:
            self.envelopes.pop(key, None)
        self.envelopes[key] = list(ordered)
        return {"parameter": name, "steps": steps,
                "from": ordered[0]["time"], "to": ordered[-1]["time"]}

    def _clear_clip_envelope(self, track_index: int, clip_index: int,
                             all: bool = False, target: str = "device",
                             parameter: Any = 0, device_index: int = 0) -> dict:
        self._clip(track_index, clip_index)
        if all:
            for k in [k for k in self.envelopes if k[:2] == (track_index, clip_index)]:
                self.envelopes.pop(k)
            return {"cleared": "all"}
        name = self._resolve_param_name(track_index, device_index, parameter)
        self.envelopes.pop((track_index, clip_index, name), None)
        return {"cleared": name}

    def _search_browser(self, query: str, limit: int = 25,
                        roots=None, max_depth: int = 4) -> dict:
        catalogue = [
            ("Serum 2", "Plugins/VST3/Xfer Records/Serum 2"),
            ("Massive", "Plugins/VST3/Native Instruments/Massive"),
            ("Wavetable", "Instruments/Wavetable"),
            ("Operator", "Instruments/Operator"),
            ("Drum Rack", "Instruments/Drum Rack"),
        ]
        needle = query.lower()
        hits = [{"name": n, "path": p, "uri": f"query:{p}",
                 "is_loadable": True, "is_folder": False}
                for n, p in catalogue if needle in n.lower()]
        return {"query": query, "scanned": len(catalogue), "results": hits[:limit]}

    def _fire_clip(self, track_index, clip_index) -> dict:
        self.playing = True
        return {"fired": True}

    def _stop_clip(self, track_index, clip_index) -> dict:
        return {"stopped": True}

    def _fire_scene(self, scene_index) -> dict:
        return {"fired": scene_index}

    def _create_scene(self, index=-1, name=None) -> dict:
        return {"scene_index": 0}

    def _start_playback(self, start_bar: float | None = None) -> dict:
        self.playing = True
        return {"is_playing": True}

    def _stop_playback(self, disarm_record: bool = True) -> dict:
        self.playing = False
        if disarm_record:
            self.recording = False
            self.session_record = False
        return {"is_playing": False, "is_recording": self.recording}

    def _set_record(self, on: bool = True, mode: str = "arrangement",
                    start: bool = True, start_bar: float | None = None) -> dict:
        if str(mode).lower().startswith("s"):
            self.session_record = bool(on)
        else:
            self.recording = bool(on)
        if on and start:
            self.playing = True
        return {"is_playing": self.playing, "is_recording": self.recording,
                "session_record": self.session_record}

    def _set_metronome(self, on: bool = True) -> dict:
        self.metronome = bool(on)
        return {"metronome": self.metronome}

    def _set_view(self, view: str) -> dict:
        self.view = view
        return {"view": view}

    # A believable slice of the browser, so preference validation and preset
    # picking exercise their real logic instead of matching one hardcoded hit.
    BROWSER: dict[str, list[str]] = {
        "": ["Instruments/", "Drums/", "Sounds/", "Audio Effects/", "Plugins/"],
        "Instruments": ["Operator", "Wavetable", "Drum Rack"],
        "Drums": ["909 Core Kit.adg", "808 Core Kit.adg"],
        "Sounds": ["Bass/", "Pad/", "Synth Lead/", "Synth Keys/"],
        "Sounds/Bass": ["Deep Sub Bass", "Analog Bass"],
        "Sounds/Pad": ["Warm Analog Pad", "Evolving Pad"],
        "Sounds/Synth Lead": ["Bright Saw Lead", "Soft Lead"],
        "Sounds/Synth Keys": ["Warm Keys", "Pluck Keys"],
        "Audio Effects": ["EQ Eight", "Compressor", "Glue Compressor",
                          "Reverb", "Delay", "Saturator", "Limiter",
                          "Auto Filter", "Utility", "Spectrum"],
        "Plugins": ["VST3/"],
        "Plugins/VST3": ["Xfer Records/"],
        "Plugins/VST3/Xfer Records": ["Serum 2"],
    }

    def _browse(self, path: str = "", limit: int = 100) -> dict:
        entries = self.BROWSER.get(path.strip("/"))
        if entries is None:
            raise KeyError(f"no browser item at {path!r}")
        return {"path": path, "items": [
            {"name": e.rstrip("/"), "is_folder": e.endswith("/"),
             "is_loadable": not e.endswith("/"),
             "uri": None if e.endswith("/") else f"query:{e}"}
            for e in entries
        ]}

    def _load_device(self, track_index: int, path=None, uri=None) -> dict:
        # Live raises on a path that does not exist; a fake that loads
        # anything hides every bad-preference bug there is.
        if path and not uri:
            parent, _, leaf = str(path).rpartition("/")
            known = self.BROWSER.get(parent.strip("/"), [])
            if leaf not in [k.rstrip("/") for k in known]:
                raise AbletonError(f"no browser item at {path!r}")
        self._track(track_index)["devices"].append(path or uri or "device")
        return {"track_index": track_index, "loaded": path or uri}

    def _delete_device(self, track_index: int, device_index: int) -> dict:
        track = self._track(track_index)
        devs = track["devices"]
        if device_index < 0 or device_index >= len(devs):
            raise AbletonError(f"device_index {device_index} out of range")
        name = devs.pop(device_index)
        track.setdefault("params", {}).pop(device_index, None)
        return {"track_index": track_index, "deleted": name,
                "remaining": list(devs)}

    def _get_devices(self, track_index: int, max_parameters: int = 512) -> dict:
        return {
            "devices": [
                {"index": i, "name": d, "class": d,
                 "parameters": self._device_params(track_index, i)[:max_parameters]}
                for i, d in enumerate(self._track(track_index)["devices"])
            ]
        }
