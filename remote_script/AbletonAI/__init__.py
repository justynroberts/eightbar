# AbletonAI Remote Script
#
# A JSON-over-TCP control surface for Ableton Live 12.
#
# Runs inside Live's embedded Python. Only the standard library and Live's own
# modules are available here -- no pip packages, no f-strings older Lives choke
# on, and every call into the Live Object Model must happen on the main thread.
#
# Framing: newline-delimited JSON. One request per line, one response per line.
#   -> {"id": 1, "type": "get_song", "params": {}}
#   <- {"id": 1, "status": "success", "result": {...}}
from __future__ import absolute_import, print_function, unicode_literals

import json
import socket
import threading
import time
import traceback

try:
    import Queue as queue
except ImportError:
    import queue

from _Framework.ControlSurface import ControlSurface

import Live

# Commands that only read. Everything else counts as a change to the set, which
# is how the app knows there is unsaved work worth protecting.
READ_ONLY_COMMANDS = frozenset([
    "ping", "get_song", "get_track", "get_clip", "get_devices", "get_mixer",
    "get_meters", "get_arrangement", "get_locators", "get_input_routings",
    "browse", "search_browser", "probe_automation", "mark_saved",
])

DEFAULT_PORT = 9878
HOST = "127.0.0.1"

# Live measures clip time in beats. One bar of 4/4 is 4 beats.
BEATS_PER_BAR = 4.0

PROTOCOL_VERSION = 1


def create_instance(c_instance):
    return AbletonAI(c_instance)


class AbletonAI(ControlSurface):
    """Exposes the Live Object Model over a line-delimited JSON socket."""

    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self.log_message("AbletonAI initializing...")

        self._server = None
        self._server_thread = None
        self._client_threads = []
        self._running = False

        self._handlers = self._build_handlers()

        self._start_server()
        self.show_message("AbletonAI: listening on port " + str(DEFAULT_PORT))
        self.log_message("AbletonAI ready with " + str(len(self._handlers)) + " commands")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def disconnect(self):
        self.log_message("AbletonAI disconnecting...")
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(1.0)
        ControlSurface.disconnect(self)

    def _start_server(self):
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind((HOST, DEFAULT_PORT))
            self._server.listen(5)
            self._running = True
            self._server_thread = threading.Thread(target=self._accept_loop)
            self._server_thread.daemon = True
            self._server_thread.start()
        except Exception as e:
            self.log_message("AbletonAI server failed to start: " + str(e))
            self.show_message("AbletonAI: port " + str(DEFAULT_PORT) + " unavailable")

    def _accept_loop(self):
        self._server.settimeout(1.0)
        while self._running:
            try:
                client, _address = self._server.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    time.sleep(0.5)
                continue
            t = threading.Thread(target=self._client_loop, args=(client,))
            t.daemon = True
            t.start()
            self._client_threads = [x for x in self._client_threads if x.is_alive()]
            self._client_threads.append(t)

    def _client_loop(self, client):
        buffer = ""
        try:
            while self._running:
                data = client.recv(65536)
                if not data:
                    break
                buffer += data.decode("utf-8")
                # Newline framing: everything before the last \n is complete.
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    response = self._dispatch_line(line)
                    client.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except Exception as e:
            self.log_message("AbletonAI client error: " + str(e))
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _dispatch_line(self, line):
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            command = request.get("type", "")
            params = request.get("params") or {}
        except ValueError as e:
            return {"id": None, "status": "error", "message": "bad JSON: " + str(e)}

        handler = self._handlers.get(command)
        if handler is None:
            return {
                "id": request_id,
                "status": "error",
                "message": "unknown command: " + command,
            }

        # Live exposes no "document modified" flag, so the next best thing is to
        # count what we changed. Every restart today cost unsaved work that
        # nothing could see was at risk.
        if command not in READ_ONLY_COMMANDS:
            self._mutations = getattr(self, "_mutations", 0) + 1
            self._last_mutation = command

        result_queue = queue.Queue()

        def run_on_main_thread():
            # Anything a handler chose to survive gets collected here rather
            # than vanishing. A swallowed exception is how clear_arrangement
            # reported success while deleting nothing at all, for months.
            self._warnings = []
            try:
                result = handler(params)
                if self._warnings and isinstance(result, dict):
                    result["warnings"] = self._warnings
                    result["warning_count"] = len(self._warnings)
                result_queue.put({"status": "success", "result": result})
            except Exception as e:
                self.log_message("AbletonAI " + command + " failed: " + str(e))
                self.log_message(traceback.format_exc())
                result_queue.put({"status": "error", "message": str(e)})

        # Every LOM touch -- read or write -- belongs on Live's main thread.
        try:
            self.schedule_message(0, run_on_main_thread)
        except AssertionError:
            run_on_main_thread()

        try:
            outcome = result_queue.get(timeout=30.0)
        except queue.Empty:
            return {
                "id": request_id,
                "status": "error",
                "message": "timed out waiting for Live's main thread",
            }

        outcome["id"] = request_id
        return outcome

    # ------------------------------------------------------------------
    # Command table
    # ------------------------------------------------------------------

    def _build_handlers(self):
        return {
            "ping": self._ping,
            "get_song": self._get_song,
            "get_track": self._get_track,
            "get_clip": self._get_clip,
            "set_tempo": self._set_tempo,
            "create_midi_track": self._create_midi_track,
            "create_audio_track": self._create_audio_track,
            "duplicate_track": self._duplicate_track,
            "set_locators": self._set_locators,
            "get_locators": self._get_locators,
            "mark_saved": self._mark_saved,
            "delete_track": self._delete_track,
            "set_track_name": self._set_track_name,
            "set_track_color": self._set_track_color,
            "set_track_mixer": self._set_track_mixer,
            "create_clip": self._create_clip,
            "delete_clip": self._delete_clip,
            "set_clip_name": self._set_clip_name,
            "set_clip_color": self._set_clip_color,
            "replace_notes": self._replace_notes,
            "add_notes": self._add_notes,
            "duplicate_clip_to_arrangement": self._duplicate_clip_to_arrangement,
            "duplicate_arrangement_clip": self._duplicate_arrangement_clip,
            "get_arrangement": self._get_arrangement,
            "clear_arrangement": self._clear_arrangement,
            "set_arrangement_loop": self._set_arrangement_loop,
            "fire_clip": self._fire_clip,
            "stop_clip": self._stop_clip,
            "fire_scene": self._fire_scene,
            "create_scene": self._create_scene,
            "start_playback": self._start_playback,
            "back_to_arrangement": self._back_to_arrangement,
            "stop_playback": self._stop_playback,
            "set_record": self._set_record,
            "set_metronome": self._set_metronome,
            "set_view": self._set_view,
            "browse": self._browse,
            "search_browser": self._search_browser,
            "load_device": self._load_device,
            "delete_device": self._delete_device,
            "get_devices": self._get_devices,
            "set_device_parameter": self._set_device_parameter,
            "get_mixer": self._get_mixer,
            "get_meters": self._get_meters,
            "get_input_routings": self._get_input_routings,
            "set_input_routing": self._set_input_routing,
            "set_arm": self._set_arm,
            "record_clip": self._record_clip,
            "set_send": self._set_send,
            "create_return_track": self._create_return_track,
            "set_song_scale": self._set_song_scale,
            "set_clip_envelope": self._set_clip_envelope,
            "probe_automation": self._probe_automation,
            "clear_clip_envelope": self._clear_clip_envelope,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _track_at(self, index):
        # -1 addresses the master track, -2 and below address return tracks,
        # so every mixer destination is reachable through one index space.
        if index == -1:
            return self.song().master_track
        if index < -1:
            returns = self.song().return_tracks
            slot = -index - 2
            if slot >= len(returns):
                raise IndexError("return track " + str(slot) + " out of range")
            return returns[slot]

        tracks = self.song().tracks
        if index >= len(tracks):
            raise IndexError(
                "track_index " + str(index) + " out of range (0.."
                + str(len(tracks) - 1) + "; -1 is master, -2 and below are returns)"
            )
        return tracks[index]

    def _slot_at(self, track_index, clip_index):
        track = self._track_at(track_index)
        slots = track.clip_slots
        if clip_index < 0 or clip_index >= len(slots):
            raise IndexError(
                "clip_index " + str(clip_index) + " out of range (0.." + str(len(slots) - 1) + ")"
            )
        return track, slots[clip_index]

    def _clip_at(self, track_index, clip_index):
        _track, slot = self._slot_at(track_index, clip_index)
        if not slot.has_clip:
            raise ValueError(
                "no clip at track " + str(track_index) + " slot " + str(clip_index)
            )
        return slot.clip

    @staticmethod
    def _note_dicts(clip):
        """Read notes out of a clip, preferring the Live 11+ typed API."""
        notes = []
        if hasattr(clip, "get_notes_extended"):
            # (from_pitch, pitch_span, from_time, time_span) -- covers the full range.
            for n in clip.get_notes_extended(0, 128, 0.0, clip.length):
                notes.append(
                    {
                        "pitch": n.pitch,
                        "start": n.start_time,
                        "duration": n.duration,
                        "velocity": n.velocity,
                        "mute": bool(n.mute),
                    }
                )
        else:
            for pitch, start, duration, velocity, mute in clip.get_notes(0, 0, clip.length, 128):
                notes.append(
                    {
                        "pitch": pitch,
                        "start": start,
                        "duration": duration,
                        "velocity": velocity,
                        "mute": bool(mute),
                    }
                )
        notes.sort(key=lambda n: (n["start"], n["pitch"]))
        return notes

    @staticmethod
    def _write_notes(clip, notes, replace):
        """Write notes into a clip. `replace` wipes the clip's existing notes first."""
        if hasattr(clip, "add_new_notes"):
            if replace:
                clip.remove_notes_extended(0, 128, 0.0, clip.length)
            specs = []
            for n in notes:
                specs.append(
                    Live.Clip.MidiNoteSpecification(
                        pitch=int(n.get("pitch", 60)),
                        start_time=float(n.get("start", 0.0)),
                        duration=float(n.get("duration", 0.25)),
                        velocity=float(n.get("velocity", 100)),
                        mute=bool(n.get("mute", False)),
                    )
                )
            if specs:
                clip.add_new_notes(tuple(specs))
        else:
            # Legacy path: set_notes always replaces, so merge by hand when adding.
            existing = [] if replace else [
                (n["pitch"], n["start"], n["duration"], n["velocity"], n["mute"])
                for n in AbletonAI._note_dicts(clip)
            ]
            for n in notes:
                existing.append(
                    (
                        int(n.get("pitch", 60)),
                        float(n.get("start", 0.0)),
                        float(n.get("duration", 0.25)),
                        int(n.get("velocity", 100)),
                        bool(n.get("mute", False)),
                    )
                )
            clip.set_notes(tuple(existing))
        return len(notes)

    # ------------------------------------------------------------------
    # Read commands
    # ------------------------------------------------------------------

    def _mark_saved(self, params):
        """Zero the change counter. The user tells us; Live will not."""
        was = getattr(self, "_mutations", 0)
        self._mutations = 0
        return {"cleared": was}

    def _warn(self, where, exc=None):
        """Record a failure the handler decided to continue past.

        Continuing is often right -- a missing meter, an envelope that was not
        there to clear. Continuing *silently* is not: the caller cannot tell a
        no-op from a success, and neither can a test.
        """
        note = str(where) if exc is None else str(where) + ": " + repr(exc)[:160]
        try:
            self._warnings.append(note)
        except AttributeError:
            self._warnings = [note]
        self.log_message("AbletonAI warning -- " + note)

    def _ping(self, params):
        song = self.song()
        return {
            "unsaved_changes": getattr(self, "_mutations", 0),
            "last_change": getattr(self, "_last_mutation", None),
            "protocol": PROTOCOL_VERSION,
            "live_version": ".".join(
                str(x) for x in [
                    getattr(self.application(), "get_major_version", lambda: 0)(),
                    getattr(self.application(), "get_minor_version", lambda: 0)(),
                ]
            ),
            "tempo": song.tempo,
            "commands": sorted(self._handlers.keys()),
        }

    def _get_song(self, params):
        song = self.song()
        include_notes = bool(params.get("include_notes", False))

        tracks = []
        for i, track in enumerate(song.tracks):
            clips = []
            for j, slot in enumerate(track.clip_slots):
                if not slot.has_clip:
                    continue
                clip = slot.clip
                entry = {
                    "slot": j,
                    "name": clip.name,
                    "length_beats": clip.length,
                    "length_bars": round(clip.length / BEATS_PER_BAR, 3),
                    "is_midi": clip.is_midi_clip,
                    "looping": bool(clip.looping),
                }
                if include_notes and clip.is_midi_clip:
                    entry["notes"] = self._note_dicts(clip)
                clips.append(entry)

            tracks.append(
                {
                    "index": i,
                    "name": track.name,
                    "is_midi": track.has_midi_input,
                    "is_grouped": bool(track.is_grouped),
                    "is_foldable": bool(track.is_foldable),
                    "muted": bool(track.mute),
                    "soloed": bool(track.solo),
                    "armed": bool(track.can_be_armed and track.arm),
                    "volume": track.mixer_device.volume.value,
                    "panning": track.mixer_device.panning.value,
                    "devices": [d.name for d in track.devices],
                    "clips": clips,
                    "arrangement_clip_count": len(getattr(track, "arrangement_clips", [])),
                }
            )

        return {
            "tempo": song.tempo,
            "signature": str(song.signature_numerator) + "/" + str(song.signature_denominator),
            "is_playing": bool(song.is_playing),
            "is_recording": bool(getattr(song, "record_mode", 0)),
            "session_record": bool(getattr(song, "session_record", False)),
            "metronome": bool(getattr(song, "metronome", False)),
            "current_time_beats": song.current_song_time,
            "scene_count": len(song.scenes),
            "track_count": len(song.tracks),
            "return_track_count": len(song.return_tracks),
            "tracks": tracks,
        }

    def _get_track(self, params):
        index = int(params.get("track_index", 0))
        track = self._track_at(index)
        return {
            "index": index,
            "name": track.name,
            "is_midi": track.has_midi_input,
            "muted": bool(track.mute),
            "soloed": bool(track.solo),
            "volume": track.mixer_device.volume.value,
            "panning": track.mixer_device.panning.value,
            "devices": [{"name": d.name, "class": d.class_name} for d in track.devices],
            "clip_slots": [
                {"slot": j, "has_clip": bool(s.has_clip),
                 "name": s.clip.name if s.has_clip else None}
                for j, s in enumerate(track.clip_slots)
            ],
        }

    def _get_clip(self, params):
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        clip = self._clip_at(track_index, clip_index)
        result = {
            "track_index": track_index,
            "clip_index": clip_index,
            "name": clip.name,
            "length_beats": clip.length,
            "length_bars": round(clip.length / BEATS_PER_BAR, 3),
            "is_midi": clip.is_midi_clip,
            "looping": bool(clip.looping),
            "loop_start": clip.loop_start,
            "loop_end": clip.loop_end,
        }
        if clip.is_midi_clip:
            result["notes"] = self._note_dicts(clip)
        else:
            result["file_path"] = getattr(clip, "file_path", None)
        return result

    def _get_devices(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        devices = []
        for i, d in enumerate(track.devices):
            devices.append(
                {
                    "index": i,
                    "name": d.name,
                    "class": d.class_name,
                    "parameters": [
                        {"name": p.name, "value": p.value,
                         "min": p.min, "max": p.max}
                        for p in d.parameters
                    ][: int(params.get("max_parameters", 512))],
                }
            )
        return {"devices": devices}

    # ------------------------------------------------------------------
    # Devices, mixer and automation
    # ------------------------------------------------------------------

    def _resolve_parameter(self, track, spec):
        """Resolve an automation target to a live DeviceParameter.

        spec is one of:
          {"target": "volume"} / {"target": "panning"}
          {"target": "send", "send_index": 0}
          {"target": "device", "device_index": 0, "parameter": "Frequency" | 3}
        """
        target = (spec.get("target") or "device").lower()
        mixer = track.mixer_device

        if target == "volume":
            return mixer.volume
        if target in ("panning", "pan"):
            return mixer.panning
        if target == "send":
            index = int(spec.get("send_index", 0))
            if index >= len(mixer.sends):
                raise IndexError(
                    "send_index " + str(index) + " out of range ("
                    + str(len(mixer.sends)) + " sends)"
                )
            return mixer.sends[index]

        device_index = int(spec.get("device_index", 0))
        if device_index >= len(track.devices):
            raise IndexError("device_index " + str(device_index) + " out of range")
        device = track.devices[device_index]

        wanted = spec.get("parameter", 0)
        if isinstance(wanted, int) or (
            isinstance(wanted, str) and wanted.isdigit()
        ):
            index = int(wanted)
            if index >= len(device.parameters):
                raise IndexError("parameter index " + str(index) + " out of range")
            return device.parameters[index]

        needle = str(wanted).strip().lower()
        for parameter in device.parameters:
            if parameter.name.lower() == needle:
                return parameter
        for parameter in device.parameters:
            if needle in parameter.name.lower():
                return parameter
        raise ValueError(
            "no parameter matching '" + str(wanted) + "' on " + device.name
            + "; available: "
            + ", ".join(p.name for p in device.parameters[:25])
        )

    def _set_device_parameter(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        parameter = self._resolve_parameter(track, params)
        value = float(params.get("value", 0.0))
        # Callers can pass 0..1 and have it mapped onto the real range.
        if params.get("normalised", False):
            value = parameter.min + (parameter.max - parameter.min) * max(
                0.0, min(1.0, value)
            )
        parameter.value = max(parameter.min, min(parameter.max, value))
        return {
            "name": parameter.name,
            "value": parameter.value,
            "min": parameter.min,
            "max": parameter.max,
        }

    def _get_mixer(self, params):
        song = self.song()
        returns = [
            {"index": i, "name": t.name, "volume": t.mixer_device.volume.value}
            for i, t in enumerate(song.return_tracks)
        ]
        tracks = []
        for i, track in enumerate(song.tracks):
            mixer = track.mixer_device
            tracks.append({
                "index": i,
                "name": track.name,
                "volume": mixer.volume.value,
                "panning": mixer.panning.value,
                "mute": bool(track.mute),
                "solo": bool(track.solo),
                "sends": [
                    {"index": j, "to": returns[j]["name"] if j < len(returns) else "?",
                     "value": s.value}
                    for j, s in enumerate(mixer.sends)
                ],
            })
        return {
            "tracks": tracks,
            "returns": returns,
            "master_volume": song.master_track.mixer_device.volume.value,
        }

    def _get_meters(self, params):
        """Read each track's output meter.

        Only meaningful while Live is playing -- a stopped transport reads
        zero everywhere, which is not the same as a quiet mix.
        """
        song = self.song()
        def meter(obj, name):
            # A track routed to MIDI out raises on these rather than simply
            # not having them, so getattr's default never fires.
            try:
                return float(getattr(obj, name))
            except Exception:
                return None

        tracks = []
        for i, track in enumerate(song.tracks):
            level = meter(track, "output_meter_level")
            tracks.append({
                "index": i,
                "name": track.name,
                "level": level,
                "left": meter(track, "output_meter_left"),
                "right": meter(track, "output_meter_right"),
                "audio": level is not None,
                "muted": bool(track.mute),
            })
        master = song.master_track
        return {
            "is_playing": bool(song.is_playing),
            "tracks": tracks,
            "master": {
                "level": meter(master, "output_meter_level"),
                "left": meter(master, "output_meter_left"),
                "right": meter(master, "output_meter_right"),
            },
        }

    def _get_input_routings(self, params):
        """What a track can be fed from. "Resampling" is the useful one here."""
        track = self._track_at(int(params.get("track_index", 0)))
        types = getattr(track, "available_input_routing_types", []) or []
        current = getattr(track, "input_routing_type", None)
        return {
            "available": [getattr(t, "display_name", str(t)) for t in types],
            "current": getattr(current, "display_name", None),
            "can_be_armed": bool(getattr(track, "can_be_armed", False)),
        }

    def _set_input_routing(self, params):
        """Point a track's input at a source by display name."""
        track = self._track_at(int(params.get("track_index", 0)))
        wanted = str(params.get("name", "Resampling")).lower()
        for routing in getattr(track, "available_input_routing_types", []) or []:
            if getattr(routing, "display_name", "").lower() == wanted:
                track.input_routing_type = routing
                return {"routing": routing.display_name}
        available = [
            getattr(t, "display_name", "?")
            for t in getattr(track, "available_input_routing_types", []) or []
        ]
        raise ValueError(
            "no input routing named '" + str(params.get("name")) + "'; "
            "available: " + ", ".join(available)
        )

    def _set_arm(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        if not getattr(track, "can_be_armed", False):
            raise RuntimeError("this track cannot be armed")
        track.arm = bool(params.get("armed", True))
        # Monitoring must be off, or resampling records the input twice.
        if params.get("monitor_off", True):
            try:
                track.current_monitoring_state = 2  # Off
            except Exception as exc:
                self._warn("current_monitoring_state", exc)
        return {"armed": bool(track.arm)}

    def _record_clip(self, params):
        """Fire a clip slot in record, or stop it.

        Recording length is controlled by the caller: start, wait, then stop.
        Blocking here would freeze Live's main thread.
        """
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        track, slot = self._slot_at(track_index, clip_index)
        song = self.song()

        if params.get("stop", False):
            song.stop_playing()
            slot.stop()
            track.arm = False
            clip = slot.clip if slot.has_clip else None
            return {
                "recorded": bool(clip),
                "file_path": getattr(clip, "file_path", None) if clip else None,
                "length_beats": clip.length if clip else 0.0,
            }

        if slot.has_clip:
            slot.delete_clip()
        # Deliberately not resetting current_song_time: in Session view that
        # stops whatever clips are already playing, so the capture records
        # silence. Fire the record slot and let the transport carry on.
        slot.fire()          # an armed, empty slot fires into record
        if not song.is_playing:
            song.start_playing()
        return {"recording": True, "track_index": track_index,
                "clip_index": clip_index}

    def _set_send(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        index = int(params.get("send_index", 0))
        sends = track.mixer_device.sends
        if index >= len(sends):
            raise IndexError("send_index out of range (" + str(len(sends)) + " sends)")
        send = sends[index]
        value = float(params.get("value", 0.0))
        send.value = max(send.min, min(send.max, value))
        return {"send_index": index, "value": send.value}

    def _create_return_track(self, params):
        song = self.song()
        song.create_return_track()
        index = len(song.return_tracks) - 1
        name = params.get("name")
        if name:
            song.return_tracks[index].name = name
        return {"return_index": index, "name": song.return_tracks[index].name}

    def _set_song_scale(self, params):
        """Live 12 exposes the session's key, which its own MIDI tools follow."""
        song = self.song()
        result = {}
        if "root_note" in params and hasattr(song, "root_note"):
            song.root_note = int(params["root_note"]) % 12
            result["root_note"] = song.root_note
        if "scale_name" in params and hasattr(song, "scale_name"):
            song.scale_name = str(params["scale_name"])
            result["scale_name"] = song.scale_name
        if not result:
            raise RuntimeError("this Live version does not expose song scale")
        return result

    # -- clip automation ----------------------------------------------

    def _set_clip_envelope(self, params):
        """Write an automation envelope into a clip.

        The Live API only offers insert_step (a flat segment), so a ramp is
        approximated by many short steps. `resolution` is the step length in
        beats -- smaller is smoother and slower.
        """
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        track = self._track_at(track_index)
        clip = self._clip_at(track_index, clip_index)

        parameter = self._resolve_parameter(track, params)
        points = params.get("points") or []
        if len(points) < 2:
            raise ValueError("need at least two points to draw an envelope")

        # Order matters: clear_envelope() destroys the underlying automation,
        # which leaves any Envelope handle taken beforehand pointing at freed
        # memory. Writing through that stale handle fails with a Boost type
        # error about TPyHandle<AAutomation> rather than anything informative,
        # so the clear has to happen *before* the envelope is acquired.
        if params.get("clear_first", True):
            try:
                clip.clear_envelope(parameter)
            except Exception as exc:
                self._warn("clear_envelope", exc)

        # Live exposes two ways in, and which one yields a *writable* envelope
        # varies. Prefer creating one outright; fall back to reading.
        envelope = None
        if hasattr(clip, "create_automation_envelope"):
            try:
                envelope = clip.create_automation_envelope(parameter)
            except Exception:
                envelope = None
        if envelope is None:
            envelope = clip.automation_envelope(parameter)
        if envelope is None:
            raise RuntimeError(
                "could not create an automation envelope for " + parameter.name
                + " (this parameter may not be automatable)"
            )
        if not hasattr(envelope, "insert_step"):
            raise RuntimeError(
                "envelope for " + parameter.name + " is a "
                + type(envelope).__name__ + " with no insert_step; it offers: "
                + ", ".join(sorted(
                    a for a in dir(envelope) if not a.startswith("_")
                ))[:400]
            )

        resolution = float(params.get("resolution", 0.125))
        span = parameter.max - parameter.min

        def to_real(v):
            v = float(v)
            if params.get("normalised", True):
                return parameter.min + span * max(0.0, min(1.0, v))
            return max(parameter.min, min(parameter.max, v))

        ordered = sorted(points, key=lambda p: float(p.get("time", 0.0)))
        written = 0
        budget = int(params.get("max_steps", 3000))
        for a, b in zip(ordered, ordered[1:]):
            t0, t1 = float(a.get("time", 0.0)), float(b.get("time", 0.0))
            v0, v1 = to_real(a.get("value", 0.0)), to_real(b.get("value", 0.0))
            if t1 <= t0:
                continue
            steps = max(1, int(round((t1 - t0) / resolution)))
            steps = min(steps, budget - written)
            if steps <= 0:
                break
            length = (t1 - t0) / steps
            for i in range(steps):
                # Linear interpolation, held flat across each short step.
                value = v0 + (v1 - v0) * (i / float(steps))
                try:
                    envelope.insert_step(
                        float(t0 + i * length), float(length), float(value)
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "insert_step failed on " + parameter.name
                        + " (envelope is a " + type(envelope).__name__ + "): "
                        + str(exc)[:300]
                    )
                written += 1

        return {
            "parameter": parameter.name,
            "steps": written,
            "from": ordered[0].get("time"),
            "to": ordered[-1].get("time"),
        }

    def _probe_automation(self, params):
        """Report exactly what Live offers for writing clip automation.

        The envelope object handed back does not accept insert_step, so rather
        than guess at the API across restarts this reports the real surface:
        which objects exist, what methods they carry, and what each write
        attempt actually raises.
        """
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        track = self._track_at(track_index)
        clip = self._clip_at(track_index, clip_index)
        parameter = self._resolve_parameter(track, params)

        def surface(obj):
            if obj is None:
                return None
            return {
                "type": type(obj).__name__,
                "methods": sorted(
                    a for a in dir(obj) if not a.startswith("_")
                ),
            }

        report = {
            "parameter": parameter.name,
            "parameter_type": type(parameter).__name__,
            "clip_methods": sorted(
                a for a in dir(clip) if "envelope" in a.lower() or "automat" in a.lower()
            ),
            "attempts": [],
        }

        for label, getter in (
            ("create_automation_envelope",
             lambda: clip.create_automation_envelope(parameter)),
            ("automation_envelope",
             lambda: clip.automation_envelope(parameter)),
        ):
            entry = {"source": label}
            try:
                envelope = getter()
                entry["envelope"] = surface(envelope)
                if envelope is not None and hasattr(envelope, "insert_step"):
                    try:
                        envelope.insert_step(0.0, 1.0, float(parameter.value))
                        entry["insert_step"] = "OK"
                    except Exception as exc:
                        entry["insert_step"] = str(exc)[:300]
            except Exception as exc:
                entry["error"] = str(exc)[:300]
            report["attempts"].append(entry)

        return report

    def _clear_clip_envelope(self, params):
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        track = self._track_at(track_index)
        clip = self._clip_at(track_index, clip_index)
        if params.get("all", False):
            clip.clear_all_envelopes()
            return {"cleared": "all"}
        parameter = self._resolve_parameter(track, params)
        clip.clear_envelope(parameter)
        return {"cleared": parameter.name}

    def _get_arrangement(self, params):
        song = self.song()
        tracks = []
        for i, track in enumerate(song.tracks):
            clips = []
            for clip in getattr(track, "arrangement_clips", []):
                clips.append(
                    {
                        "name": clip.name,
                        "start_beats": clip.start_time,
                        "end_beats": clip.end_time,
                        "start_bars": round(clip.start_time / BEATS_PER_BAR, 3),
                        "length_bars": round(
                            (clip.end_time - clip.start_time) / BEATS_PER_BAR, 3
                        ),
                    }
                )
            clips.sort(key=lambda c: c["start_beats"])
            if clips:
                tracks.append({"index": i, "name": track.name, "clips": clips})
        end = 0.0
        for t in tracks:
            for c in t["clips"]:
                end = max(end, c["end_beats"])
        return {
            "tempo": song.tempo,
            "end_beats": end,
            "end_bars": round(end / BEATS_PER_BAR, 3),
            "duration_seconds": round(end / song.tempo * 60.0, 2) if song.tempo else 0,
            "tracks": tracks,
        }

    # ------------------------------------------------------------------
    # Write commands -- session
    # ------------------------------------------------------------------

    def _set_tempo(self, params):
        tempo = float(params.get("tempo", 120.0))
        self.song().tempo = max(20.0, min(999.0, tempo))
        return {"tempo": self.song().tempo}

    def _create_midi_track(self, params):
        song = self.song()
        index = int(params.get("index", -1))
        song.create_midi_track(index)
        new_index = len(song.tracks) - 1 if index == -1 else index
        name = params.get("name")
        if name:
            song.tracks[new_index].name = name
        color = params.get("color")
        if color is not None:
            song.tracks[new_index].color = int(color)
        return {"track_index": new_index, "name": song.tracks[new_index].name}

    def _create_audio_track(self, params):
        """Audio tracks are where vocal and FX stems get dropped in later."""
        song = self.song()
        index = int(params.get("index", -1))
        song.create_audio_track(index)
        new_index = len(song.tracks) - 1 if index == -1 else index
        name = params.get("name")
        if name:
            song.tracks[new_index].name = name
        color = params.get("color")
        if color is not None:
            song.tracks[new_index].color = int(color)
        return {"track_index": new_index, "name": song.tracks[new_index].name}

    def _duplicate_track(self, params):
        """Duplicate a whole track -- clips, devices, arrangement, automation.

        Live's own Cmd-D. The copy lands directly below the source, at
        index + 1. This is the only faithful way to split a track that already
        has arrangement clips: the socket cannot copy those across tracks by
        hand, and duplicate_track preserves every clip, device and automation
        envelope exactly.
        """
        song = self.song()
        index = int(params.get("index", 0))
        if index < 0 or index >= len(song.tracks):
            raise IndexError("track index out of range (" + str(len(song.tracks)) + ")")
        song.duplicate_track(index)
        new_index = index + 1
        name = params.get("name")
        if name:
            song.tracks[new_index].name = name
        color = params.get("color")
        if color is not None:
            song.tracks[new_index].color = int(color)
        return {"source_index": index,
                "track_index": new_index,
                "name": song.tracks[new_index].name}

    def _set_locators(self, params):
        """Drop named arrangement markers so section boundaries are visible.

        Live only exposes "toggle a cue at the playhead", and assigning
        ``current_song_time`` does not take effect until a later tick. A single
        pass therefore toggled markers at whatever position the playhead was
        still at: with one tick of slack, every other marker deleted the one
        before it and four of ten survived.

        So nothing here assumes. Each step confirms the playhead actually
        arrived before toggling, and confirms the cue exists before naming it,
        retrying until it does. That makes the command asynchronous -- callers
        poll ``get_locators``.
        """
        song = self.song()
        markers = params.get("markers") or []

        self._locator_resume = bool(song.is_playing)
        if self._locator_resume:
            song.stop_playing()
        self._locator_restore = song.current_song_time

        queue = []
        if params.get("clear_existing", True):
            for cue in list(song.cue_points):
                queue.append({"do": "delete", "beat": cue.time,
                              "phase": "move", "tries": 0})
        for marker in markers:
            queue.append({
                "do": "create",
                "beat": float(marker.get("start_bar", 0.0)) * BEATS_PER_BAR,
                "name": marker.get("name"),
                "phase": "move",
                "tries": 0,
            })

        self._locator_queue = queue
        self._locator_done = []
        if queue:
            self.schedule_message(1, self._locator_step)
        return {"scheduled": len(queue), "pending": True}

    def _locator_step(self):
        """One tick of the locator queue. Every phase verifies before moving on."""
        song = self.song()
        queue = getattr(self, "_locator_queue", None)
        if not queue:
            try:
                song.current_song_time = getattr(self, "_locator_restore", 0.0)
                if getattr(self, "_locator_resume", False):
                    song.start_playing()
            except Exception:
                pass
            return

        step = queue[0]
        beat = step["beat"]
        step["tries"] += 1
        if step["tries"] > 40:          # Never spin forever on one marker.
            self._warn("gave up placing a locator at bar %.1f"
                       % (beat / BEATS_PER_BAR))
            queue.pop(0)
            self.schedule_message(1, self._locator_step)
            return

        def cue_at():
            for cue in song.cue_points:
                if abs(cue.time - beat) < 0.25:
                    return cue
            return None

        try:
            if step["phase"] == "move":
                # Confirm the playhead arrived. Toggling before it does is what
                # made markers land on each other.
                if abs(song.current_song_time - beat) > 1e-6:
                    song.current_song_time = beat
                else:
                    step["phase"] = "toggle"
                    step["tries"] = 0

            elif step["phase"] == "toggle":
                here = cue_at()
                if step["do"] == "delete":
                    if here is None:
                        queue.pop(0)
                    else:
                        song.set_or_delete_cue()
                else:
                    if here is None:
                        song.set_or_delete_cue()
                    else:
                        step["phase"] = "name"
                        step["tries"] = 0

            elif step["phase"] == "name":
                here = cue_at()
                if here is not None:
                    if step.get("name"):
                        try:
                            here.name = step["name"]
                        except Exception:
                            pass
                    self._locator_done.append({
                        "name": here.name,
                        "start_bar": here.time / BEATS_PER_BAR,
                    })
                queue.pop(0)
        except Exception as exc:
            self._warn("locator step at bar %.1f" % (beat / BEATS_PER_BAR), exc)
            queue.pop(0)

        self.schedule_message(1, self._locator_step)

    def _get_locators(self, params):
        song = self.song()
        return {
            "locators": sorted(
                [
                    {"name": c.name, "start_bar": c.time / BEATS_PER_BAR, "beats": c.time}
                    for c in song.cue_points
                ],
                key=lambda x: x["beats"],
            )
        }

    def _delete_track(self, params):
        index = int(params.get("track_index", 0))
        self._track_at(index)
        self.song().delete_track(index)
        return {"deleted": index, "track_count": len(self.song().tracks)}

    def _set_track_name(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        track.name = params.get("name", track.name)
        return {"name": track.name}

    def _set_track_color(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        track.color = int(params.get("color", 0))
        return {"color": track.color}

    def _set_track_mixer(self, params):
        track = self._track_at(int(params.get("track_index", 0)))
        mixer = track.mixer_device
        if "volume" in params:
            mixer.volume.value = max(0.0, min(1.0, float(params["volume"])))
        if "panning" in params:
            mixer.panning.value = max(-1.0, min(1.0, float(params["panning"])))
        if "mute" in params:
            track.mute = bool(params["mute"])
        if "solo" in params:
            track.solo = bool(params["solo"])
        return {
            "volume": mixer.volume.value,
            "panning": mixer.panning.value,
            "mute": bool(track.mute),
            "solo": bool(track.solo),
        }

    def _create_clip(self, params):
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        length = float(params.get("length_beats", params.get("length", 16.0)))
        _track, slot = self._slot_at(track_index, clip_index)
        if slot.has_clip:
            if params.get("overwrite", True):
                slot.delete_clip()
            else:
                raise ValueError("slot already has a clip; pass overwrite=true to replace")
        slot.create_clip(length)
        clip = slot.clip
        if params.get("name"):
            clip.name = params["name"]
        notes = params.get("notes")
        written = 0
        if notes:
            written = self._write_notes(clip, notes, replace=True)
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "length_beats": clip.length,
            "notes_written": written,
        }

    def _delete_clip(self, params):
        _track, slot = self._slot_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        if slot.has_clip:
            slot.delete_clip()
        return {"deleted": True}

    def _set_clip_name(self, params):
        clip = self._clip_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        clip.name = params.get("name", clip.name)
        return {"name": clip.name}

    def _set_clip_color(self, params):
        clip = self._clip_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        clip.color = int(params.get("color", 0))
        return {"color": clip.color}

    def _replace_notes(self, params):
        clip = self._clip_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        count = self._write_notes(clip, params.get("notes", []), replace=True)
        return {"notes_written": count}

    def _add_notes(self, params):
        clip = self._clip_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        count = self._write_notes(clip, params.get("notes", []), replace=False)
        return {"notes_added": count}

    # ------------------------------------------------------------------
    # Write commands -- arrangement
    # ------------------------------------------------------------------

    def _duplicate_clip_to_arrangement(self, params):
        """Place a session clip onto the arrangement timeline, optionally repeated."""
        track_index = int(params.get("track_index", 0))
        clip_index = int(params.get("clip_index", 0))
        track = self._track_at(track_index)
        clip = self._clip_at(track_index, clip_index)

        start_bar = float(params.get("start_bar", 0.0))
        repeats = max(1, int(params.get("repeats", 1)))
        start = start_bar * BEATS_PER_BAR
        step = clip.length

        if not hasattr(track, "duplicate_clip_to_arrangement"):
            raise RuntimeError(
                "this Live version has no Track.duplicate_clip_to_arrangement"
            )

        placed = []
        for r in range(repeats):
            at = start + r * step
            track.duplicate_clip_to_arrangement(clip, at)
            placed.append(at)
        return {
            "placed_at_beats": placed,
            "end_beats": start + repeats * step,
            "end_bars": round((start + repeats * step) / BEATS_PER_BAR, 3),
        }

    def _duplicate_arrangement_clip(self, params):
        """Repeat a clip that already sits on the timeline.

        A sample dropped straight into the arrangement has no session clip, so
        the ordinary duplicate path cannot place it -- which is why user samples
        ended up sitting on the timeline once while the generated parts were
        spread across every section.

        Copies are made from a *parked* duplicate rather than from the original.
        Live truncates whatever an incoming clip overlaps, so placing copies
        directly from the original risks trimming the source halfway through the
        run and turning the remaining copies into fragments. The park sits well
        past everything being placed, and is deleted at the end.
        """
        track_index = int(params.get("track_index", 0))
        track = self._track_at(track_index)
        clips = sorted(getattr(track, "arrangement_clips", []),
                       key=lambda c: c.start_time)
        if not clips:
            raise ValueError(
                "track " + str(track_index) + " has nothing on the timeline to repeat"
            )

        source_index = int(params.get("source_index", 0))
        if source_index >= len(clips):
            raise IndexError("source_index out of range (" + str(len(clips)) + ")")
        source = clips[source_index]

        # Read everything off the source *now*. A copy placed over its position
        # destroys the original, and any later attribute access on the dead
        # handle fails as a Boost signature mismatch rather than an AttributeError.
        source_name = source.name
        source_start = source.start_time
        length = source.end_time - source.start_time
        if length <= 0:
            raise ValueError("source clip has no length")

        placements = params.get("placements")
        if not placements:
            placements = [{"start_bar": params.get("start_bar", 0.0),
                           "repeats": params.get("repeats", 1)}]

        wanted = []
        for spec in placements:
            start = float(spec.get("start_bar", 0.0)) * BEATS_PER_BAR
            repeats = max(1, int(spec.get("repeats", 1)))
            for r in range(repeats):
                wanted.append(start + r * length)
        if not wanted:
            return {"placed": 0}

        # Park beyond everything, including the existing timeline.
        furthest = max([max(wanted) + length] + [c.end_time for c in clips])
        park_at = furthest + 128 * BEATS_PER_BAR
        def clip_at(beat):
            """A *fresh* handle for the clip starting at ``beat``.

            Every duplication invalidates handles taken before it, and Live
            reports a stale one as a Boost signature mismatch rather than a
            clean failure -- the same lifetime rule that bites clip envelopes.
            So the park is looked up again for each use, never cached.
            """
            for clip in getattr(track, "arrangement_clips", []):
                try:
                    if abs(clip.start_time - beat) < 1e-6:
                        return clip
                except Exception:
                    continue
            return None

        track.duplicate_clip_to_arrangement(source, park_at)
        placed = 0
        for at in sorted(wanted):
            park = clip_at(park_at)
            if park is None:
                break
            track.duplicate_clip_to_arrangement(park, at)
            placed += 1

        park_removed = False
        try:
            park = clip_at(park_at)
            if park is None:
                self._warn("park clip not found at %.3f" % park_at)
            else:
                track.delete_clip(park)
                park_removed = True
        except Exception as exc:
            self._warn("delete park clip", exc)

        return {
            "source": {"name": source_name,
                       "start_bar": source_start / BEATS_PER_BAR,
                       "length_bars": length / BEATS_PER_BAR},
            "placed": placed,
            "park_removed": park_removed,
            "placed_at_bars": [a / BEATS_PER_BAR for a in sorted(wanted)],
        }

    def _clear_arrangement(self, params):
        song = self.song()
        indices = params.get("track_indices")
        # An *explicitly empty* list means clear nothing. Treating it the same
        # as an absent one wiped the whole timeline, which is the opposite of
        # what a caller that filtered its list down to zero tracks wants.
        if indices is None:
            targets = list(song.tracks)
        else:
            targets = [self._track_at(int(i)) for i in indices]
        # Deletion belongs to the Track, not the Clip: Clip has no delete_clip,
        # so the old call raised an AttributeError that was swallowed and this
        # cleared nothing at all. Removing a clip also invalidates the rest of
        # the collection, so the list is re-read after every delete.
        removed = 0
        for track in targets:
            while True:
                clips = list(getattr(track, "arrangement_clips", []))
                if not clips:
                    break
                try:
                    track.delete_clip(clips[0])
                    removed += 1
                except Exception as exc:
                    self._warn("delete_clip on track", exc)
                    break
        return {"removed": removed}

    def _set_arrangement_loop(self, params):
        song = self.song()
        song.loop_start = float(params.get("start_bar", 0.0)) * BEATS_PER_BAR
        song.loop_length = float(params.get("length_bars", 8.0)) * BEATS_PER_BAR
        song.loop = bool(params.get("enabled", True))
        return {
            "loop_start": song.loop_start,
            "loop_length": song.loop_length,
            "loop": bool(song.loop),
        }

    # ------------------------------------------------------------------
    # Transport & view
    # ------------------------------------------------------------------

    def _fire_clip(self, params):
        _track, slot = self._slot_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        slot.fire()
        return {"fired": True}

    def _stop_clip(self, params):
        _track, slot = self._slot_at(
            int(params.get("track_index", 0)), int(params.get("clip_index", 0))
        )
        slot.stop()
        return {"stopped": True}

    def _fire_scene(self, params):
        song = self.song()
        index = int(params.get("scene_index", 0))
        if index < 0 or index >= len(song.scenes):
            raise IndexError("scene_index out of range")
        song.scenes[index].fire()
        return {"fired": index}

    def _create_scene(self, params):
        song = self.song()
        index = int(params.get("index", -1))
        song.create_scene(index)
        new_index = len(song.scenes) - 1 if index == -1 else index
        if params.get("name"):
            song.scenes[new_index].name = params["name"]
        return {"scene_index": new_index}

    def _back_to_arrangement(self, params):
        """Return every track to playing the arrangement.

        One fired scene takes the whole set into session mode, and from then
        on arrangement playback rolls silently under whatever clips loop --
        which is how a full arrangement metered as four looping session
        tracks and six silent ones. Live's own orange "Back to Arrangement"
        button is the fix, and this is that button.
        """
        song = self.song()
        if params.get("stop_clips", True):
            try:
                song.stop_all_clips()
            except Exception as exc:
                self._warn("stop_all_clips", exc)
        try:
            song.back_to_arrangement = 1
        except Exception:
            # Older API exposes it as a trigger rather than a property.
            try:
                song.trigger_back_to_arrangement()
            except Exception as exc:
                self._warn("back_to_arrangement", exc)
        return {"back_to_arrangement": True}

    def _start_playback(self, params):
        song = self.song()
        if "start_bar" in params:
            song.current_song_time = float(params["start_bar"]) * BEATS_PER_BAR
        song.start_playing()
        return {"is_playing": bool(song.is_playing)}

    def _stop_playback(self, params):
        song = self.song()
        song.stop_playing()
        # Stopping also disarms recording, so a later plain play does not
        # keep punching in -- the transport button behaves the same way.
        if params.get("disarm_record", True):
            try:
                song.record_mode = 0
                song.session_record = False
            except Exception as exc:
                self._warn("stop_disarm", exc)
        return {"is_playing": bool(song.is_playing),
                "is_recording": bool(getattr(song, "record_mode", 0))}

    def _set_record(self, params):
        """Arm recording and roll -- captures automation and notes.

        Arrangement record (the default) writes parameter moves and MIDI into
        the timeline, which is what "record the automation" means. Session
        record punches into clip slots instead. Setting record_mode is the LOM
        equivalent of pressing the transport's record button, so it must run on
        the main thread like every other write.
        """
        song = self.song()
        on = bool(params.get("on", True))
        session = str(params.get("mode", "arrangement")).lower().startswith("s")
        if session:
            song.session_record = on
        else:
            song.record_mode = 1 if on else 0
        if on and params.get("start", True) and not song.is_playing:
            if "start_bar" in params:
                song.current_song_time = float(params["start_bar"]) * BEATS_PER_BAR
            song.start_playing()
        return {"is_playing": bool(song.is_playing),
                "is_recording": bool(getattr(song, "record_mode", 0)),
                "session_record": bool(getattr(song, "session_record", False))}

    def _set_metronome(self, params):
        """Click on or off -- handy before a take."""
        song = self.song()
        song.metronome = bool(params.get("on", True))
        return {"metronome": bool(song.metronome)}

    def _set_view(self, params):
        view = self.application().view
        target = params.get("view", "session").lower()
        name = "Session" if target.startswith("s") else "Arranger"
        view.show_view(name)
        return {"view": name}

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def _browse(self, params):
        """Walk the Live browser one level at a time.

        `path` is a slash-separated trail of item names, e.g.
        "Instruments/Drums/Drum Rack". An empty path lists the top categories.
        """
        browser = self.application().browser
        path = (params.get("path") or "").strip("/")
        roots = {
            "Instruments": browser.instruments,
            "Drums": browser.drums,
            "Audio Effects": browser.audio_effects,
            "MIDI Effects": browser.midi_effects,
            "Sounds": browser.sounds,
            "Samples": browser.samples,
            "Plugins": browser.plugins,
            "User Library": browser.user_library,
        }

        if not path:
            return {
                "path": "",
                "items": [
                    {"name": k, "is_folder": True, "uri": None} for k in sorted(roots.keys())
                ],
            }

        parts = path.split("/")
        if parts[0] not in roots:
            raise ValueError("unknown browser root: " + parts[0])
        node = roots[parts[0]]
        for part in parts[1:]:
            match = None
            for child in node.children:
                if child.name == part:
                    match = child
                    break
            if match is None:
                raise ValueError("no browser item named '" + part + "' under " + path)
            node = match

        limit = int(params.get("limit", 200))
        items = []
        for child in list(node.children)[:limit]:
            items.append(
                {
                    "name": child.name,
                    "is_folder": bool(child.is_folder),
                    "is_loadable": bool(child.is_loadable),
                    "uri": getattr(child, "uri", None),
                }
            )
        return {"path": path, "items": items}

    def _search_browser(self, params):
        """Find loadable browser items by name, without knowing the path.

        Walks a bounded slice of the browser tree. Depth is capped because the
        full tree (every pack, every preset) is enormous and Live walks it
        lazily -- an unbounded search would stall the main thread.
        """
        query = (params.get("query") or "").strip().lower()
        if not query:
            raise ValueError("search_browser needs a query")
        limit = int(params.get("limit", 25))
        max_depth = int(params.get("max_depth", 4))

        browser = self.application().browser
        roots = {
            "Instruments": browser.instruments,
            "Drums": browser.drums,
            "Audio Effects": browser.audio_effects,
            "MIDI Effects": browser.midi_effects,
            "Sounds": browser.sounds,
            "Plugins": browser.plugins,
            "User Library": browser.user_library,
        }
        wanted = params.get("roots") or list(roots.keys())

        hits = []
        visited = [0]
        budget = [0]
        # Per-root budget. A single global cap gets exhausted inside
        # Instruments and never reaches Plugins, so a plugin search finds
        # nothing at all.
        per_root = int(params.get("budget_per_root", 6000))

        def walk(node, trail, depth):
            if len(hits) >= limit or depth > max_depth or budget[0] > per_root:
                return
            try:
                children = node.children
            except Exception:
                return
            for child in children:
                if len(hits) >= limit:
                    return
                visited[0] += 1
                budget[0] += 1
                try:
                    name = child.name
                except Exception:
                    continue
                path = trail + "/" + name
                if query in name.lower():
                    hits.append({
                        "name": name,
                        "path": path,
                        "uri": getattr(child, "uri", None),
                        "is_loadable": bool(getattr(child, "is_loadable", False)),
                        "is_folder": bool(getattr(child, "is_folder", False)),
                    })
                if getattr(child, "is_folder", False):
                    walk(child, path, depth + 1)

        for root_name in wanted:
            node = roots.get(root_name)
            if node is None:
                continue
            budget[0] = 0
            if query in root_name.lower():
                hits.append({"name": root_name, "path": root_name,
                             "uri": None, "is_loadable": False, "is_folder": True})
            walk(node, root_name, 1)

        # Loadable items first -- those are what you can actually put on a track.
        hits.sort(key=lambda h: (not h["is_loadable"], len(h["path"])))
        return {"query": query, "scanned": visited[0], "results": hits[:limit]}

    def _load_device(self, params):
        """Select a track, then load a browser item onto it."""
        track_index = int(params.get("track_index", 0))
        track = self._track_at(track_index)
        try:
            self.song().view.selected_track = track
        except Exception:
            # The master track cannot always be "selected"; loading still works.
            pass

        uri = params.get("uri")
        path = params.get("path")
        browser = self.application().browser

        item = None
        if uri:
            item = self._find_by_uri(browser, uri)
        elif path:
            listing = self._browse({"path": "/".join(path.split("/")[:-1]), "limit": 5000})
            leaf = path.split("/")[-1]
            for entry in listing["items"]:
                if entry["name"] == leaf and entry["uri"]:
                    item = self._find_by_uri(browser, entry["uri"])
                    break
        if item is None:
            raise ValueError("could not resolve browser item from uri/path")

        browser.load_item(item)
        return {"track_index": track_index, "loaded": item.name}

    def _delete_device(self, params):
        """Remove a device from a track's chain by its index.

        Live keeps devices in track.devices; Track.delete_device(index)
        removes one. Deleting shifts every later device down, so a caller
        removing several must work from the highest index down or re-read
        between calls -- like clearing arrangement clips.
        """
        track_index = int(params.get("track_index", 0))
        track = self._track_at(track_index)
        devices = list(getattr(track, "devices", []))
        index = int(params.get("device_index", -999))
        if index < 0 or index >= len(devices):
            raise IndexError(
                "device_index " + str(index) + " out of range (0.."
                + str(len(devices) - 1) + ")"
            )
        name = devices[index].name
        track.delete_device(index)
        return {"track_index": track_index, "deleted": name,
                "remaining": [d.name for d in getattr(track, "devices", [])]}

    def _find_by_uri(self, browser, uri, node=None, depth=0):
        if depth > 12:
            return None
        if node is None:
            for root in [
                browser.instruments, browser.drums, browser.audio_effects,
                browser.midi_effects, browser.sounds, browser.plugins,
                browser.user_library,
            ]:
                found = self._find_by_uri(browser, uri, root, depth + 1)
                if found:
                    return found
            return None
        if getattr(node, "uri", None) == uri:
            return node
        try:
            children = node.children
        except Exception:
            return None
        for child in children:
            found = self._find_by_uri(browser, uri, child, depth + 1)
            if found:
                return found
        return None
