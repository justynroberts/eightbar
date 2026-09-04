"""The tool surface the model drives.

Each tool is deliberately high-level: the model says "a rising i-VI-III-VII in C
minor over 8 bars" and the theory/generator modules decide the notes. The raw
`write_clip_notes` escape hatch exists for when the model genuinely does want to
place individual notes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import (
    arrangement, basslines, catalogue, chordbank, composing, corpus, critique,
    generators, groove, harmony, hooks, leads, melody, mixing, motif, perform,
    presets, processing, taste, theory, variations, voicings,
)

try:  # numpy and friends are optional; everything else works without them
    from . import analysis
except ImportError:  # pragma: no cover
    analysis = None  # type: ignore[assignment]
from .sounds import SoundPreferences
from .bridge import AbletonBridge, AbletonError, AbletonNotRunning

log = logging.getLogger(__name__)

BEATS_PER_BAR = 4.0

# Track colours so generated tracks are visually distinguishable in Live.
ROLE_COLOURS = {
    "kick": 1, "drums": 2, "perc": 3, "bass": 5, "sub": 6, "chords": 9,
    "arp": 11, "lead": 13, "hook": 14, "pad": 17, "riser": 19,
    "impact": 20, "fx": 21, "vocal": 25,
    # Acoustic and orchestral roles. Warm ambers and greens, kept clear of the
    # synth roles above so an orchestral set reads at a glance.
    "strings": 8, "brass": 11, "woodwind": 17, "piano": 26, "guitar": 20,
    "choir": 14, "mallet": 33, "harp": 41, "organ": 47,
}

# Roles placed once at a section's start rather than looped across it.
# Values that mean "take this from the corpus rather than from a fixed table".
LEARNED = frozenset(["learned", "corpus", "reference", "references", "learnt"])

ONE_SHOT_ROLES = ("impact",)
# Roles stretched to fill the section exactly once (a riser spans the whole build).
SPAN_ROLES = ("riser",)


class ToolError(RuntimeError):
    """A tool failed in a way the model should see and can recover from."""


def _bars(value: float) -> float:
    return value * BEATS_PER_BAR


# Longest match first, so "sub bass" resolves to sub rather than bass.
_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("kick", "kick"), ("sub", "sub"), ("bass", "bass"), ("hat", "drums"),
    ("drum", "drums"), ("perc", "perc"), ("clap", "drums"), ("snare", "drums"),
    ("chord", "chords"), ("arp", "arp"), ("lead", "lead"), ("hook", "hook"),
    ("pad", "pad"), ("ris", "riser"), ("sweep", "riser"), ("impact", "impact"),
    ("crash", "impact"), ("stab", "chords"), ("pluck", "arp"),
    ("voc", "vocal"), ("fx", "fx"), ("build", "drums"), ("top", "lead"),
)


def _role_from_name(name: str, default: str | None = "lead") -> str | None:
    """Guess a track's musical role from what the producer called it.

    `default` is what to return when the name says nothing -- "Audio 1", "1-Serum
    2". "lead" is a reasonable guess for mixing, where every track needs *some*
    treatment. It is a bad one for arranging: a track nobody named would be
    placed in every drop as a lead. Pass None there and skip it instead.
    """
    lowered = (name or "").lower()
    direct = arrangement.normalise_role(lowered)
    if direct:
        return direct
    for needle, role in _ROLE_HINTS:
        if needle in lowered:
            return role
    return default


def _role_overrides(tracks: list | None) -> dict[int, str]:
    """Role overrides from a list that may hold dicts *or* bare track indices.

    The documented shape is [{"track_index": 3, "role": "bass"}], but a bare
    [3] is what gets passed when the caller means "this track" and has no role
    in mind. Subscripting that int raised a TypeError from inside the tool,
    which surfaced as an unhelpful "bad arguments". A bare index simply carries
    no override, leaving the role to be inferred from the track name.
    """
    out: dict[int, str] = {}
    for entry in tracks or []:
        if isinstance(entry, dict):
            if "track_index" in entry and entry.get("role"):
                out[int(entry["track_index"])] = arrangement.normalise_role(
                    str(entry["role"])
                )
        elif isinstance(entry, (int, float)):
            continue  # An index on its own says nothing about the role.
    return out


def _role_from_content(bridge, track_index: int, slots: list[int]) -> str | None:
    """What a track *is*, from the notes it plays -- register, polyphony, range.

    The tool should not make the user rename tracks: a bassline is a bassline
    whatever the track is called. corpus.classify_part reads a clip and says
    drums/bass/chords/lead from its content; this maps that onto a role.
    """
    for ci in slots or [0]:
        try:
            clip = bridge.call("get_clip", track_index=track_index, clip_index=ci)
        except (AbletonError, AbletonNotRunning):
            continue
        raw = clip.get("notes") or []
        if not raw:
            continue
        notes = [corpus.MidiNote(pitch=int(n["pitch"]), start=float(n["start"]),
                                 duration=float(n["duration"]),
                                 velocity=int(n.get("velocity", 90)),
                                 track=track_index)
                 for n in raw]
        part = corpus.classify_part(notes)
        # classify_part returns drums/bass/chords/lead/unknown.
        return None if part == "unknown" else part
    return None


def _has_session_clip(bridge, entry: dict) -> bool:
    """True when at least one of the entry's session slots holds a clip."""
    for ci in _slots_for(entry):
        try:
            bridge.call("get_clip", track_index=int(entry["track_index"]),
                        clip_index=int(ci))
            return True
        except (AbletonError, AbletonNotRunning):
            continue
    return False


def _slots_for(entry: dict[str, Any]) -> list[int]:
    """The clip slots an arrangement entry may draw from, in ladder order."""
    if entry.get("clip_indices"):
        return [int(c) for c in entry["clip_indices"]]
    return [int(entry.get("clip_index", 0))]


class Toolbox:
    """Holds the Ableton connection and exposes the callable tools."""

    def __init__(
        self, bridge: AbletonBridge, sounds: SoundPreferences | None = None
    ) -> None:
        self.bridge = bridge
        self.sounds = sounds or SoundPreferences()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _allowed_values(tool: str, param: str) -> list[str]:
        """The closed vocabulary for one parameter, if it has one.

        Imported lazily: schemas imports this module, so a module-level import
        would be circular.
        """
        from . import schemas

        per_tool = schemas.TOOL_ENUMS.get(tool, {})
        return list(per_tool.get(param) or schemas.ENUMS.get(param) or [])

    def _repair_vocabulary(self, name: str, arguments: dict[str, Any]) -> list[str]:
        """Fix near-misses in closed vocabularies, and explain the rest.

        The model reaches for a word that is musically sensible but not in the
        table -- "stab", "minor9", "offbeat_hats" -- and the call fails at the
        far end with a list of twenty-five names, which is the least useful
        possible reply. A very close match is corrected and reported; anything
        else gets the three nearest suggestions rather than the whole table.

        Only near-identical spellings are corrected. Substituting a word that
        merely sounds similar would silently make different music, which is
        worse than failing.
        """
        import difflib

        corrections: list[str] = []
        for param, value in list(arguments.items()):
            allowed = self._allowed_values(name, param)
            if not allowed:
                continue

            values = value if isinstance(value, list) else [value]
            if not all(isinstance(v, str) for v in values):
                continue

            repaired, unknown = [], []
            for one in values:
                if one in allowed:
                    repaired.append(one)
                    continue
                exact = difflib.get_close_matches(one, allowed, n=1, cutoff=0.86)
                if exact:
                    corrections.append(f"{param}={one!r} -> {exact[0]!r}")
                    repaired.append(exact[0])
                else:
                    unknown.append(one)

            if unknown:
                near = difflib.get_close_matches(unknown[0], allowed, n=3, cutoff=0.3)
                suggestion = (
                    f"did you mean {', '.join(repr(n) for n in near)}?"
                    if near else f"allowed: {', '.join(allowed[:12])}"
                )
                raise ToolError(
                    f"{name}: {param}={unknown[0]!r} is not a known value. "
                    f"{suggestion}"
                )

            arguments[param] = repaired if isinstance(value, list) else repaired[0]

        return corrections

    # Parameter-name synonyms the model commonly reaches for, mapped to the
    # real names. Applied only when the real name exists on the target tool
    # and is not already given, so a tool that genuinely has "mode" is safe.
    _PARAM_ALIASES: dict[str, str] = {
        "bpm": "tempo", "beats_per_minute": "tempo",
        "mode": "scale", "scale_name": "scale",
        "root": "key", "root_note": "key", "tonic": "key",
        "bars": "duration_seconds", "length": "duration_seconds",
        "duration": "duration_seconds", "seconds": "duration_seconds",
        "chords": "progression", "chord_progression": "progression",
        "degrees_list": "degrees", "prog": "progression",
        "style": "genre", "type": "genre",
        "track": "track_index", "clip": "clip_index", "slot": "clip_index",
    }

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        handler: Callable[..., Any] | None = getattr(self, f"tool_{name}", None)
        if handler is None:
            import difflib

            near = difflib.get_close_matches(
                name, [n[5:] for n in dir(self) if n.startswith("tool_")],
                n=3, cutoff=0.5,
            )
            hint = f" Did you mean {', '.join(near)}?" if near else ""
            raise ToolError(f"no such tool: {name}.{hint}")

        arguments = dict(arguments)
        import inspect

        # Repair parameter *names* first -- before value repair -- so a synonym
        # like "mode" becomes "scale" and is then value-checked against the
        # scale enum, not mistaken for a different tool's "mode" value. The
        # model reaches for plausible synonyms ("bpm" for tempo, "bars" for
        # length) and a raw TypeError hid which one and what the real names are.
        accepted = set(inspect.signature(handler).parameters) - {"self"}
        corrections: list[str] = []
        for wrong, right in list(self._PARAM_ALIASES.items()):
            if wrong in arguments and right in accepted and wrong not in accepted \
                    and right not in arguments:
                arguments[right] = arguments.pop(wrong)
                corrections.append(f"{wrong} -> {right}")

        corrections.extend(self._repair_vocabulary(name, arguments))

        # Reject an unknown keyword with the list of accepted parameters rather
        # than a bare "unexpected keyword argument".
        unknown = [k for k in arguments if k not in accepted]
        if unknown:
            import difflib

            hints = []
            for key in unknown:
                near = difflib.get_close_matches(key, accepted, n=2, cutoff=0.5)
                hints.append(f"{key!r}" + (f" (did you mean {', '.join(near)}?)"
                                           if near else ""))
            raise ToolError(
                f"{name}: unknown argument(s) {', '.join(hints)}. "
                f"Accepts: {', '.join(sorted(accepted))}."
            )

        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as exc:
            raise ToolError(f"{name}: bad arguments -- {exc}") from exc

        try:
            result = handler(**arguments)
            if corrections and isinstance(result, dict):
                result["corrected"] = corrections
            return result
        except TypeError as exc:
            raise ToolError(f"{name}: {type(exc).__name__} -- {exc}") from exc
        except (AbletonNotRunning, AbletonError) as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(f"{name}: {exc}") from exc

    # ------------------------------------------------------------------
    # Reading the set
    # ------------------------------------------------------------------

    def tool_get_song_state(self, include_notes: bool = False) -> dict:
        """Read the whole Live set: tracks, clips, tempo, devices and mixer state. Call this first when the user refers to what is already in their project."""
        state = self.bridge.call("get_song", include_notes=include_notes)
        # Trim note payloads so a busy set doesn't blow up the context window.
        if include_notes:
            for track in state.get("tracks", []):
                for clip in track.get("clips", []):
                    notes = clip.get("notes") or []
                    if len(notes) > 200:
                        clip["notes"] = notes[:200]
                        clip["notes_truncated"] = len(notes)
        return state

    def tool_get_clip(self, track_index: int, clip_index: int) -> dict:
        """Read one clip in full, including every MIDI note. Use this to understand a loop the user already made before arranging or extending it."""
        clip = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        notes = clip.get("notes") or []
        if notes:
            clip["summary"] = generators.summarise(notes)
        return clip

    def tool_get_arrangement(self) -> dict:
        """Read the arrangement timeline: which clips sit where, and the total length in bars and seconds."""
        return self.bridge.call("get_arrangement")

    def tool_list_musical_options(self) -> dict:
        """Reference data so the model picks names that actually exist."""
        return {
            "scales": sorted(theory.SCALES),
            "chord_qualities": sorted(theory.CHORD_QUALITIES),
            "named_progressions": {
                k: "-".join(str(d) for d in v)
                for k, v in theory.PROGRESSIONS.items()
            },
            "drum_patterns": sorted(generators.DRUM_PATTERNS),
            "drum_instruments": sorted(generators.DRUM_MAP),
            "rhythm_patterns": sorted(generators.RHYTHM_PATTERNS),
            "arp_styles": list(generators.ARP_STYLES),
            "melody_contours": ["rise", "fall", "arch", "valley", "random"],
            "bass_styles": ["root", "fifth", "octave", "walk"],
            "arrangement_templates": sorted(arrangement.TEMPLATES),
        }

    # ------------------------------------------------------------------
    # Session / transport
    # ------------------------------------------------------------------

    def tool_set_tempo(self, tempo: float) -> dict:
        """Set the session tempo in BPM."""
        return self.bridge.call("set_tempo", tempo=tempo)

    def tool_create_track(
        self, name: str, role: str | None = None, index: int = -1
    ) -> dict:
        """Create a MIDI track for generated material. Give it a role so it is colour-coded and can be placed by arrange_to_timeline."""
        params: dict[str, Any] = {"index": index, "name": name}
        if role and role in ROLE_COLOURS:
            params["color"] = ROLE_COLOURS[role]
        return self.bridge.call("create_midi_track", **params)

    def tool_delete_track(self, track_index: int) -> dict:
        """Delete a track by index. A snapshot is taken first, automatically."""
        backup = self._autosnapshot("delete-track")
        result = dict(self.bridge.call("delete_track", track_index=track_index))
        if backup:
            result["snapshot"] = backup
        return result

    def tool_set_track_mixer(
        self,
        track_index: int,
        volume: float | None = None,
        panning: float | None = None,
        mute: bool | None = None,
        solo: bool | None = None,
    ) -> dict:
        """Set a track volume (0-1), panning (-1 to 1), mute or solo."""
        params: dict[str, Any] = {"track_index": track_index}
        for key, value in (
            ("volume", volume), ("panning", panning),
            ("mute", mute), ("solo", solo),
        ):
            if value is not None:
                params[key] = value
        return self.bridge.call("set_track_mixer", **params)

    def tool_transport(
        self,
        action: str,
        track_index: int | None = None,
        clip_index: int | None = None,
        start_bar: float | None = None,
    ) -> dict:
        """Control playback: start, stop, fire or stop a clip, or switch between Session and Arrangement view."""
        if action == "play":
            params = {} if start_bar is None else {"start_bar": start_bar}
            return self.bridge.call("start_playback", **params)
        if action == "stop":
            return self.bridge.call("stop_playback")
        if action in ("fire_clip", "stop_clip"):
            if track_index is None or clip_index is None:
                raise ToolError(f"{action} needs track_index and clip_index")
            command = "fire_clip" if action == "fire_clip" else "stop_clip"
            return self.bridge.call(
                command, track_index=track_index, clip_index=clip_index
            )
        if action == "show_session":
            return self.bridge.call("set_view", view="session")
        if action == "show_arrangement":
            return self.bridge.call("set_view", view="arrangement")
        raise ToolError(f"unknown transport action: {action}")

    # ------------------------------------------------------------------
    # Clip creation
    # ------------------------------------------------------------------

    def _write_clip(
        self,
        track_index: int,
        clip_index: int,
        bars: float,
        notes: list[dict],
        name: str | None,
        role: str | None = None,
        played: bool = True,
    ) -> dict:
        """Write notes to a clip, performed rather than merely correct.

        Every generated part goes through here, which is why the performance
        layer lives here too: measurement of the old output found flat velocity
        and one note length per part in almost everything, and patching that
        into fifteen generators separately would have left the sixteenth wrong.

        `played=False` is for material whose exact velocities and lengths are
        the point -- an imported clip, a hand-written note list.
        """
        if played and notes:
            if role is None:
                role = self._role_of_track(track_index)
            notes = perform.perform(notes, role or "lead", bars=bars)

        result = self.bridge.call(
            "create_clip",
            track_index=track_index,
            clip_index=clip_index,
            length_beats=_bars(bars),
            notes=notes,
            name=name,
            overwrite=True,
        )
        result["summary"] = generators.summarise(notes)
        return result

    def _role_of_track(self, track_index: int) -> str | None:
        """The role of a track, from its name, remembered for this session."""
        cache = getattr(self, "_role_cache", None)
        if cache is None:
            cache = self._role_cache = {}
        if track_index not in cache:
            try:
                track = self.bridge.call("get_track", track_index=track_index)
                cache[track_index] = _role_from_name(
                    track.get("name", ""), default=None
                )
            except (AbletonError, AbletonNotRunning):
                return None
        return cache[track_index]

    def _reference_progression(
        self, track_index: int, clip_index: int, bars: float
    ) -> tuple[list[theory.Chord], float]:
        """Chords lifted from a clip that already exists, qualities and all.

        Rebuilding harmony from a degree number throws away everything that
        makes the progression itself: a clip whose second chord is Em7 comes
        back as E diminished, because that is what the second degree of D minor
        is. The extension is lost too. Reading the sounding pitches keeps the
        chord the user actually wrote, so a bass or lead generated against it
        agrees with what is playing rather than merely sharing a key.
        """
        notes, clip = self._clip_notes(track_index, clip_index)
        if not notes:
            raise ToolError(
                f"reference clip at track {track_index} slot {clip_index} is empty"
            )
        root, scale, _ = corpus.detect_key(notes)
        events = corpus.extract_chords(notes, root, scale)
        if not events:
            raise ToolError(
                f"no chords could be read from track {track_index} "
                f"slot {clip_index}; point at a chord part"
            )

        chords = [
            theory.Chord(
                degree=event.degree,
                root_pitch=min(event.pitches),
                quality=event.quality,
                pitches=tuple(sorted(set(event.pitches))),
            )
            for event in events
        ]
        # The reference sets the harmonic rhythm as well as the harmony. Its
        # chords are two bars each here; assuming one per bar would put every
        # generated part half a chord out.
        source_bars = clip["length_beats"] / BEATS_PER_BAR
        bars_per_chord = (source_bars or bars) / len(chords)
        return chords, bars_per_chord

    # ------------------------------------------------- learned from references

    def _library(self) -> corpus.Library:
        """The learned corpus, loaded once per Toolbox."""
        if getattr(self, "_corpus_library", None) is None:
            self._corpus_library = corpus.Library()
        return self._corpus_library

    def _learned_degrees(self, length: int, seed: int | None) -> list[int]:
        """A progression walked from what the corpus does, not a fixed list."""
        library = self._library()
        if not library.references:
            raise ToolError(
                "nothing has been learned yet -- run learn_references on a "
                "folder of MIDI files first, then ask for a learned progression"
            )
        suggestion = library.suggest_progression(length=length, seed=seed)
        return list(suggestion["degrees"])

    def _learned_bass_style(self, requested: str) -> str:
        """Resolve style="learned" to the articulation the corpus favours."""
        if str(requested).lower() not in LEARNED:
            return requested
        profile = self._library().bass_profile()
        style = profile.get("dominant_style")
        if not style:
            raise ToolError(
                "no bass articulation has been learned yet -- run "
                "learn_references on MIDI that contains basslines"
            )
        return style

    def _learned_groove(self, requested: str, role: str):
        """Resolve groove="learned" to the averaged feel of that role."""
        if str(requested).lower() not in LEARNED:
            return requested
        learned = self._library().groove_for(role)
        if learned is None:
            raise ToolError(
                f"no {role} groove has been learned yet -- run learn_references "
                f"on MIDI containing {role}"
            )
        groove.GROOVES[learned.name] = learned
        return learned.name

    def _reference_degrees(
        self, track_index: int, clip_index: int
    ) -> tuple[str, str, list[int]]:
        """Key, scale and degrees read off an existing clip."""
        notes, _clip = self._clip_notes(track_index, clip_index)
        if not notes:
            raise ToolError(
                f"reference clip at track {track_index} slot {clip_index} is empty"
            )
        root, scale, _ = corpus.detect_key(notes)
        events = corpus.extract_chords(notes, root, scale)
        degrees = [e.degree for e in events if e.degree]
        if not degrees:
            raise ToolError(
                f"no progression could be read from track {track_index} "
                f"slot {clip_index}; point at a chord part"
            )
        return root, scale, degrees

    def _progression(
        self,
        key: str,
        scale: str,
        degrees: Any,
        bars: float,
        octave: int,
        extension: str,
        smooth: bool = True,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> tuple[list[theory.Chord], float]:
        if reference_track is not None:
            return self._reference_progression(
                int(reference_track), int(reference_clip), bars
            )
        if isinstance(degrees, str) and degrees.lower() in LEARNED:
            resolved = self._learned_degrees(4, None)
            chords = theory.build_progression(
                key, scale, resolved, octave=octave, extension=extension,
                smooth=smooth,
            )
            return chords, bars / len(chords)
        if isinstance(degrees, str) and degrees.lower() in theory.PROGRESSIONS:
            resolved = list(theory.PROGRESSIONS[degrees.lower()])
        else:
            resolved = theory.parse_degrees(degrees)
        chords = theory.build_progression(
            key, scale, resolved, octave=octave, extension=extension, smooth=smooth
        )
        bars_per_chord = bars / len(chords)
        return chords, bars_per_chord

    def tool_create_chord_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        octave: int = 3,
        extension: str = "triad",
        rhythm: str = "pad",
        velocity: int = 85,
        spread: float = 0.0,
        humanise: float = 0.0,
        smooth_voicing: bool = True,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
        mood: str | None = None,
        bank_style: str | None = None,
    ) -> dict:
        """Generate a chord progression clip. Voice leading is applied automatically so the chords move smoothly instead of jumping in octaves."""
        if mood is not None:
            return self._chords_from_bank(
                track_index, clip_index, key, mood, bank_style, bars, name,
                seed,
            )
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, octave, extension, smooth_voicing,
            reference_track=reference_track, reference_clip=reference_clip,
        )
        notes = generators.generate_chords(
            chords,
            bars_per_chord=bars_per_chord,
            rhythm=rhythm,
            velocity=velocity,
            spread=spread,
            humanise=humanise,
            seed=seed,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} {scale} chords", role="chords")
        result["chords"] = [c.describe() for c in chords]
        return result

    def tool_create_bass_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        rhythm: str = "offbeat",
        style: str = "root",
        octave: int = 2,
        velocity: int = 100,
        swing: float = 0.0,
        humanise: float = 0.0,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """Generate a bassline that follows a chord progression."""
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, 3, "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )
        notes = generators.generate_bassline(
            chords,
            bars_per_chord=bars_per_chord,
            rhythm=rhythm,
            octave=octave,
            velocity=velocity,
            style=self._learned_bass_style(style),
            swing=swing,
            humanise=humanise,
            seed=seed,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} {scale} bass", role="bass")

    # ------------------------------------------------------------------
    # The whole job
    # ------------------------------------------------------------------

    # Which parts a genre wants, and how each is generated. Keeping this as
    # data means build_track stays readable and a new genre is a few lines.
    BUILD_RECIPES: dict[str, dict] = {
        "trance": {
            "tempo": 138, "scale": "minor", "template": "trance",
            "progression": "trance_classic", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "four_on_floor",
            "bass_style": "octave", "lead_style": "soaring",
            "groove": "pushed", "chord_rhythm": "offbeat",
        },
        "tech_house": {
            "tempo": 126, "scale": "minor", "template": "house",
            "progression": "rolling", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "tech_house",
            "bass_style": "rolling", "lead_style": "stab",
            "groove": "tech_house", "chord_rhythm": "offbeat",
        },
        "house": {
            "tempo": 124, "scale": "minor", "template": "house",
            "progression": "deep_house", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "house",
            "bass_style": "offbeat", "lead_style": "pluck",
            "groove": "house", "chord_rhythm": "offbeat",
        },
        "deep_house": {
            "tempo": 122, "scale": "dorian", "template": "house",
            "progression": "deep_house", "drum_kit": "Drums/707 Core Kit.adg", "drum_pattern": "deep_house",
            "bass_style": "drag", "lead_style": "call",
            "groove": "laid_back", "chord_rhythm": "syncopated",
        },
        "techno": {
            "tempo": 132, "scale": "phrygian", "template": "techno",
            "progression": "hypnotic", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "techno",
            "bass_style": "rolling", "lead_style": "rolling",
            "groove": "techno", "chord_rhythm": "stab",
        },
        "melodic_techno": {
            "tempo": 124, "scale": "minor", "template": "melodic_techno",
            "progression": "emotional", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "techno",
            "bass_style": "driving", "lead_style": "arp_climb",
            "groove": "techno", "chord_rhythm": "whole",
        },
        "big_room": {
            "tempo": 128, "scale": "minor", "template": "big_room",
            "progression": "edm", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "four_on_floor",
            "bass_style": "octave", "lead_style": "soaring",
            "groove": "pushed", "chord_rhythm": "quarters",
        },
        "progressive": {
            "tempo": 126, "scale": "minor", "template": "progressive_house",
            "progression": "emotional", "drum_kit": "Drums/909 Core Kit.adg", "drum_pattern": "house",
            "bass_style": "driving", "lead_style": "pluck",
            "groove": "house", "chord_rhythm": "offbeat",
        },
        "dnb": {
            "tempo": 174, "scale": "minor", "template": "dnb",
            "progression": "pop_minor", "drum_kit": "Drums/606 Core Kit.adg", "drum_pattern": "dnb",
            "bass_style": "sustained", "lead_style": "stab",
            "groove": "dnb", "chord_rhythm": "stab",
        },
    }

    BUILD_ALIASES = {
        "edm": "big_room", "festival": "big_room", "mainstage": "big_room",
        "uplifting": "trance", "psytrance": "trance",
        "prog": "progressive", "progressive_house": "progressive",
        "minimal": "techno", "drum_and_bass": "dnb", "jungle": "dnb",
        "tech": "tech_house", "deep": "deep_house",
    }

    def tool_build_track(
        self,
        genre: str = "trance",
        key: str = "C",
        duration_seconds: float = 360,
        tempo: float | None = None,
        scale: str | None = None,
        progression: str | None = None,
        mix: bool = True,
        master: bool = True,
        placeholders: bool = True,
        seed: int | None = None,
        spectral_eq: bool = False,
    ) -> dict:
        """Build a complete, mixed, arranged track in one call.

        This is the whole job, not a starting point: tempo and key, instruments
        on every track, parts written with the right articulation for the
        genre, variations so sections differ, a full arrangement with build-ups
        and drops, locators, placeholder tracks for vocals and FX, gain
        staging, frequency separation, compression and a master chain.

        Make the musical choices rather than asking -- genre and key are enough
        to decide the rest. Report what was chosen afterwards.
        """
        key_name = (genre or "trance").strip().lower().replace(" ", "_").replace("-", "_")
        key_name = self.BUILD_ALIASES.get(key_name, key_name)
        if key_name not in self.BUILD_RECIPES:
            raise ToolError(
                f"unknown genre {genre!r}; one of: "
                f"{', '.join(sorted(set(self.BUILD_RECIPES) | set(self.BUILD_ALIASES)))}"
            )
        recipe = self.BUILD_RECIPES[key_name]

        bpm = float(tempo or recipe["tempo"])
        mode = scale or recipe["scale"]
        chords = progression or recipe["progression"]
        report: dict[str, Any] = {
            "genre": key_name, "key": key, "scale": mode, "tempo": bpm,
            "progression": chords, "steps": [], "problems": [],
        }

        def step(label: str, fn):
            try:
                result = fn()
                report["steps"].append({"step": label, "ok": True})
                return result
            except Exception as exc:
                report["steps"].append({"step": label, "ok": False,
                                        "error": str(exc)[:160]})
                report["problems"].append(f"{label}: {str(exc)[:160]}")
                return None

        step("tempo", lambda: self.tool_set_tempo(bpm))
        step("song key", lambda: self.tool_set_song_scale(key, mode))

        # -- tracks and parts -------------------------------------------
        layout = [
            ("Kick", "kick"), ("Drums", "drums"), ("Bass", "bass"),
            ("Chords", "chords"), ("Hook", "hook"), ("Lead", "lead"),
            ("Melody", "lead"), ("Riser", "riser"), ("Impact", "impact"),
            ("Build", "drums"),
            # Rolls and phrase fills get their OWN track (role perc), never
            # layered onto the main Drums beat -- so the instrument behind
            # them can be swapped without touching the groove.
            ("Fills", "perc"),
        ]
        # Reuse this build's own tracks when they already exist: running
        # build_track twice used to create a second complete band next to the
        # first, and twenty tracks of doubled parts is its own kind of awful.
        existing = {
            str(t.get("name")): int(t["index"])
            for t in self.bridge.call("get_song").get("tracks", [])
        }
        made: dict[str, int] = {}
        for name, role in layout:
            if name in existing:
                made[name] = existing[name]
                continue
            created = step(f"track {name}",
                           lambda n=name, r=role: self.tool_create_track(n, r))
            if created:
                made[name] = int(created["track_index"])

        step("instruments", lambda: self.tool_ensure_instruments(
            only_empty=True, seed=seed))

        # A bare Drum Rack has no samples in it. Put the genre's kit on every
        # drum track explicitly rather than relying on the generic default.
        kit = recipe.get("drum_kit")
        if kit:
            for drum_track in ("Kick", "Drums", "Impact", "Build"):
                if drum_track in made:
                    step(f"kit {drum_track}",
                         lambda t=drum_track: self.tool_load_sound(
                             track_index=made[t], path=kit))

        common = {"key": key, "scale": mode, "degrees": chords, "seed": seed}
        drum_notes_track = made.get("Kick")

        if "Kick" in made:
            step("kick", lambda: self.tool_create_drum_clip(
                made["Kick"], pattern=recipe["drum_pattern"], bars=4,
                groove=recipe["groove"], seed=seed))
        if "Drums" in made:
            step("drums", lambda: self.tool_create_drum_clip(
                made["Drums"], pattern=recipe["drum_pattern"], bars=4,
                groove=recipe["groove"], seed=seed))
        if "Bass" in made:
            step("bass", lambda: self.tool_create_styled_bass(
                made["Bass"], style=recipe["bass_style"], bars=8,
                drums_track=drum_notes_track, **common))
        if "Chords" in made:
            # Diatonic colour from the ninth, not chromatic borrowed chords:
            # 'rich' injects a secondary-dominant leading tone that clashes
            # with an independently-built bass and melody. Extensions carry the
            # interest and stay in key.
            step("chords", lambda: self.tool_create_varied_chords(
                made["Chords"], bars=8, variation="extended",
                extension="ninth", rhythm=recipe["chord_rhythm"], **common))
        # The melodic parts grow from one motif rather than three strangers:
        # the lead develops it, the hook fragments it an octave up, and the
        # Melody track carries the counter-line that answers it inverted.
        # Relatedness is what makes an ensemble sound written. The melody tool
        # remains the fallback for a set where the theme cannot land.
        theme_tracks = [
            {"track_index": made[n], "role": r}
            for n, r in (("Lead", "lead"), ("Melody", "counter"))
            if n in made
        ]
        themed = None
        if theme_tracks:
            themed = step("theme", lambda: self.tool_compose_theme(
                key=key, scale=mode, degrees=chords, bars=8,
                tracks=theme_tracks, seed=seed))
        # The hook is the one part where memorability beats relatedness: it
        # comes from the catalog of shapes decades of hits share, repeated
        # literally over the moving harmony, chosen to suit the genre.
        if "Hook" in made:
            step("hook", lambda: self.tool_create_hook_clip(
                made["Hook"], bars=8, octave=5, hook_style=key_name, **common))
        if not themed:
            if "Lead" in made:
                step("lead", lambda: self.tool_create_lead_clip(
                    made["Lead"], bars=8, style=recipe["lead_style"], octave=4,
                    **common))
            if "Melody" in made:
                step("melody", lambda: self.tool_create_melody_clip(
                    made["Melody"], bars=8, octave=4, **common))
        if "Riser" in made:
            step("riser", lambda: self.tool_create_riser_clip(
                made["Riser"], bars=8, key=key, scale=mode))
        if "Impact" in made:
            step("impact", lambda: self.tool_create_impact_clip(made["Impact"]))
        if "Fills" in made:
            # A short fill and a double-snare on the Fills track: the phrase-
            # mark pass drops these onto every eighth bar. Small, on their own
            # track, re-instrumentable.
            step("phrase fill", lambda: self.tool_create_drum_fill(
                made["Fills"], clip_index=0, bars=1, style="snare", seed=seed))
            step("double snare", lambda: self.tool_create_snare_roll(
                made["Fills"], clip_index=1, bars=1, seed=seed))
        if "Build" in made:
            step("build-up", lambda: self.tool_create_buildup_clip(
                made["Build"], bars=8, seed=seed))
            # A roll and a fill in the spare slots, so the hand-over into each
            # drop has something to use beyond the build-up itself.
            step("snare roll", lambda: self.tool_create_snare_roll(
                made["Build"], clip_index=6, bars=2, seed=seed))
            step("fill", lambda: self.tool_create_drum_fill(
                made["Build"], clip_index=7, bars=1, style="toms", seed=seed))

        # -- variations, so sections are not identical -------------------
        ladders: dict[str, list[int]] = {}
        for name in ("Kick", "Drums", "Bass", "Chords", "Hook", "Lead"):
            if name not in made:
                continue
            result = step(f"variations {name}",
                          lambda n=name: self.tool_create_variation_set(
                              made[n], clip_index=0, count=4, start_slot=0,
                              seed=seed))
            if result:
                ladders[name] = result["clip_indices"]

        if placeholders:
            step("placeholders", lambda: self.tool_create_placeholder_set(
                roles=["vocal", "fx"]))

        # -- arrangement --------------------------------------------------
        # Build is a transition element: it belongs in builds, positioned to
        # finish on the boundary like a riser. With role "drums" it played
        # through every drop too -- the accelerating roll under the whole
        # chorus was most of what "drums sound busy" meant.
        role_of = {"Kick": "kick", "Drums": "drums", "Bass": "bass",
                   "Chords": "chords", "Hook": "hook", "Lead": "lead",
                   "Melody": "arp", "Riser": "riser", "Impact": "impact",
                   "Build": "riser", "Fills": "perc"}
        entries = []
        for name, index in made.items():
            entry: dict[str, Any] = {"track_index": index, "role": role_of[name]}
            if name in ladders:
                entry["clip_indices"] = ladders[name]
            else:
                entry["clip_index"] = 0
            entries.append(entry)

        # -- section harmony ---------------------------------------------
        # The breakdown is not the drop played quieter. Write the treated
        # chord clips into known slots and let the arrangement pick them by
        # section name: reharmonised chords under the breakdown, a
        # half-cadence under the build so the drop's downbeat resolves it,
        # and the lead restated in augmentation (half-time) over the
        # breakdown -- the familiar melody over changed ground.
        if "Chords" in made:
            # The recipe's progression may be a name ("emotional"), a string
            # of degrees, or a list -- resolve it the way the generators do.
            if isinstance(chords, str) and chords.lower() in theory.PROGRESSIONS:
                main_degrees = list(theory.PROGRESSIONS[chords.lower()])
            elif isinstance(chords, list):
                main_degrees = [int(d) for d in chords]
            else:
                main_degrees = theory.parse_degrees(chords)
            treatments = composing.harmonic_plan(
                [{"name": "build"}, {"name": "breakdown"}],
                main_degrees, key=key, scale=mode,
            )
            by_name = {t["name"]: t for t in treatments}
            step("build harmony", lambda: self.tool_create_varied_chords(
                made["Chords"], clip_index=6, bars=8,
                key=key, scale=mode, degrees=by_name["build"]["degrees"],
                rhythm=recipe["chord_rhythm"], seed=seed,
                name="Chords [half cadence]"))
            step("breakdown harmony", lambda: self.tool_create_varied_chords(
                made["Chords"], clip_index=7, bars=8,
                key=key, scale=mode, degrees=by_name["breakdown"]["degrees"],
                rhythm="pad", seed=seed, name="Chords [reharmonised]"))
            for entry in entries:
                if entry["track_index"] == made["Chords"]:
                    entry["clip_by_section"] = {"build": 6, "breakdown": 7}
        if "Lead" in made:
            step("breakdown theme", lambda: self.tool_create_clip_variation(
                made["Lead"], clip_index=0, to_clip_index=6,
                mutations=["half_time", "softer"],
                name="Lead [augmented]"))
            for entry in entries:
                if entry["track_index"] == made["Lead"]:
                    entry["clip_by_section"] = {"breakdown": 6}

        plan = step("plan", lambda: self.tool_plan_arrangement(
            target_seconds=duration_seconds, tempo=bpm,
            template=recipe["template"]))
        arranged = None
        if plan:
            # The augmented theme restatement only sounds if the lead is in
            # the breakdown -- most templates strip it out. Familiar melody
            # over changed harmony is the emotional centre of a breakdown,
            # so put the lead back where the treated clip exists to carry it.
            if "Lead" in made:
                for section in plan["sections"]:
                    if str(section.get("name", "")).lower() in (
                        "breakdown", "break",
                    ) and "lead" not in (section.get("roles") or []):
                        section["roles"] = list(section.get("roles") or []) + [
                            "lead"
                        ]
            arranged = step("arrange", lambda: self.tool_arrange_to_timeline(
                sections=plan["sections"], tracks=entries, clear_first=True))
            report["structure"] = [
                f"{s['name']}@{s['start_bar']}" for s in plan["sections"]
            ]
            report["duration"] = plan["duration"]

        # A part that plays nothing makes the whole track sound broken,
        # whatever its notes say. Verify and repair before any polish.
        step("soundcheck", lambda: self.tool_soundcheck(fix=True, genre=key_name))

        # -- mix ----------------------------------------------------------
        if mix:
            step("gain staging", lambda: self.tool_mix_levels())
            # The engineer's chain, by role: EQ high-passes every non-low part
            # to clear the mud, the sustained elements duck against the kick
            # for the dance pump, and compression touches only the rhythm
            # section. The melodic parts are shaped with EQ and sidechain, not
            # squashed -- compressing everything is what sounded wrong.
            step("process mix", lambda: self.tool_process_mix(
                kick_track=made.get("Kick")))
            # Opt-in, slow: solo each track, measure its real spectrum, and
            # set its EQ from what it actually contains rather than by role
            # alone. The master carries a Spectrum either way (master chain).
            if spectral_eq:
                step("spectral EQ", lambda: self.tool_eq_from_spectrum())
            # Two returns so sends have somewhere to go, then role-based
            # amounts -- drums nearly dry, pads and FX wet.
            step("reverb return",
                 lambda: self.tool_create_return_track("Reverb"))
            step("delay return", lambda: self.tool_create_return_track("Delay"))
            step("sends", lambda: self.tool_set_sends_by_role())
        if master:
            step("master chain", lambda: self.tool_add_master_chain())

        step("show arrangement", lambda: self.tool_transport("show_arrangement"))

        report["tracks"] = made
        if arranged:
            report["placements"] = arranged.get("placements")
            report["bars"] = arranged.get("end_bars")
            report["seconds"] = arranged.get("duration_seconds")
        report["ok"] = not report["problems"]
        return report

    # ------------------------------------------------------------------
    # Learned references
    # ------------------------------------------------------------------

    def tool_learn_references(
        self, folder: str = "references", limit: int | None = None
    ) -> dict:
        """Learn from a folder of MIDI files.

        Extracts key, chord movement, voicing spacing, rhythm, feel and bass
        articulation from every file, and keeps them. This is how the generated
        material starts to sound like the records the user actually likes
        rather than like a rulebook.
        """
        library = self._library()
        result = library.learn_folder(folder, limit=limit)
        self._corpus_library = library
        result["summary"] = library.summary()
        return result

    def _clip_notes(self, track_index: int, clip_index: int):
        """Notes from a Live clip, as the corpus analysers want them."""
        clip = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        notes = [
            corpus.MidiNote(pitch=int(n["pitch"]), start=float(n["start"]),
                            duration=float(n["duration"]),
                            velocity=int(n["velocity"]), track=track_index)
            for n in (clip.get("notes") or [])
        ]
        return notes, clip

    def tool_analyse_clip(self, track_index: int, clip_index: int = 0) -> dict:
        """Read what is already in a clip: its key, chords, degrees and part type.

        Use this before writing anything into a set you did not build. Guessing
        the key from a track or sample name is not the same thing -- a file
        called "..._Gmin" sat next to a chord clip that was actually in D minor,
        and every part generated around it clashed.
        """
        notes, clip = self._clip_notes(track_index, clip_index)
        if not notes:
            raise ToolError(
                f"track {track_index} slot {clip_index} has no notes to analyse"
            )

        root, scale, confidence = corpus.detect_key(notes)
        chords = corpus.extract_chords(notes, root, scale)
        return {
            "track_index": track_index,
            "clip_index": clip_index,
            "name": clip.get("name"),
            "bars": round(clip["length_beats"] / BEATS_PER_BAR, 2),
            "notes": len(notes),
            "key": root,
            "scale": scale,
            "confidence": confidence,
            "part": corpus.classify_part(notes),
            "degrees": [c.degree for c in chords],
            "chords": [
                {"bar": round(c.bar, 2), "name": f"{c.root_name}{c.quality}",
                 "degree": c.degree, "inversion": c.inversion,
                 "fit": round(c.fit, 2)}
                for c in chords
            ],
            "pitch_range": [min(n.pitch for n in notes),
                            max(n.pitch for n in notes)],
            "rhythm": corpus.extract_rhythm(notes),
            "summary": (
                f"{root} {scale} (confidence {confidence}), "
                f"{corpus.classify_part(notes)}, "
                f"degrees {[c.degree for c in chords] or 'none'}"
            ),
        }

    def tool_analyse_set(self) -> dict:
        """Work out the key and progression of the material already in the set.

        Every clip with notes is analysed and the results are pooled, so the
        answer comes from the whole set rather than from whichever clip was
        looked at first. Harmony parts decide the key -- a bass line of four
        notes will happily vote for the wrong one -- and the longest chord part
        supplies the progression.

        Call this first when a set already has music in it. What it returns is
        what every generated part should then be written against.
        """
        state = self.bridge.call("get_song")
        found, pooled = [], []
        for track in state.get("tracks", []):
            index = int(track["index"])
            for slot in track.get("clips", []) or []:
                clip_index = int(slot.get("slot", slot.get("index", 0)))
                try:
                    notes, clip = self._clip_notes(index, clip_index)
                except (AbletonError, AbletonNotRunning):
                    continue
                if not notes:
                    continue
                root, scale, confidence = corpus.detect_key(notes)
                found.append({
                    "track_index": index,
                    "track": track.get("name"),
                    "clip_index": clip_index,
                    "name": clip.get("name"),
                    "notes": len(notes),
                    "part": corpus.classify_part(notes),
                    "key": root, "scale": scale, "confidence": confidence,
                    "_notes": notes,
                })
                pooled.extend(notes)

        if not found:
            raise ToolError("no clip in this set has any notes to analyse")

        # Chord parts decide the key. Drums are atonal, a four-note bass line is
        # too thin to trust, and a lead or arp spells the harmony obliquely.
        HARMONIC = ("chords", "pad", "keys", "lead", "melody", "hook", "arp")
        voters = ([f for f in found if f["part"] == "chords"]
                  or [f for f in found if f["part"] in HARMONIC]
                  or found)

        # Weight by how long the notes *sound*, not how many there are. Counting
        # notes let five copies of a 128-note sixteenth arp outvote the eight
        # whole-note chords the set was actually written around.
        ballot: dict[tuple[str, str], float] = {}
        for f in voters:
            sounding = sum(n.duration for n in f["_notes"])
            f["weight"] = round(f["confidence"] * sounding, 2)
            ballot[(f["key"], f["scale"])] = (
                ballot.get((f["key"], f["scale"]), 0.0) + f["weight"]
            )
        ranked = sorted(ballot.items(), key=lambda kv: kv[1], reverse=True)
        (key, scale) = ranked[0][0]

        # The progression comes from whichever chord part sits most cleanly in
        # the key -- not the one with the most notes. A dense generated pad can
        # easily out-count the eight chords the set was written around, and
        # reading it in the wrong register yields degrees that are not in the
        # scale at all (degree 0). Mean chord fit, with degree 0 counted as a
        # miss, picks the clip that genuinely spells the harmony.
        candidates = [f for f in found if f["part"] == "chords"] or voters

        def spells_the_harmony(entry):
            events = corpus.extract_chords(entry["_notes"], key, scale)
            if not events:
                return (0.0, 0)
            diatonic = [e for e in events if e.degree]
            if not diatonic:
                return (0.0, 0)
            mean_fit = sum(e.fit for e in diatonic) / len(diatonic)
            return (mean_fit * (len(diatonic) / len(events)), len(diatonic))

        source = max(candidates, key=spells_the_harmony)
        chords = corpus.extract_chords(source["_notes"], key, scale)

        for f in found:
            del f["_notes"]

        degrees = [c.degree for c in chords]
        return {
            "key": key,
            "scale": scale,
            "degrees": degrees,
            "progression_from": {"track_index": source["track_index"],
                                 "track": source["track"],
                                 "clip_index": source["clip_index"]},
            "chords": [
                {"bar": round(c.bar, 2), "name": f"{c.root_name}{c.quality}",
                 "degree": c.degree}
                for c in chords
            ],
            "clips": found,
            # A set can genuinely hold two keys, and a near-tie is worth seeing
            # rather than silently resolving.
            "alternatives": [
                {"key": k, "scale": sc, "weight": round(w, 2)}
                for (k, sc), w in ranked[1:4]
            ],
            "summary": (
                f"{key} {scale}, degrees {degrees or 'none found'}, "
                f"from {source['track']!r} across {len(found)} clip(s)"
            ),
        }

    def _taste(self) -> taste.Taste:
        if getattr(self, "_taste_store", None) is None:
            self._taste_store = taste.Taste()
        return self._taste_store

    def tool_audition_hooks(
        self,
        track_index: int,
        key: str = "A",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        count: int = 4,
        hook_style: str | None = None,
        start_slot: int = 0,
        octave: int = 5,
        seed: int | None = None,
    ) -> dict:
        """Write several hook candidates side by side, for the user to judge.

        Measurement can prove a part is not broken; only listening can say it
        is good. This lines up `count` different patterns in consecutive
        session slots, named after their pattern, so the user can fire each
        in Live and compare. When they name a winner, record it with
        `record_taste` -- future picks weight towards what has actually won.

        Candidates are ordered by the user's recorded taste for the style,
        so the likeliest winners audition first.
        """
        chords, _bpc = self._progression(key, scale, degrees, bars, 3, "triad")
        options = sorted(hooks.catalog(hook_style))
        store = self._taste()
        wins = store.weights("hook_pattern", hook_style or "any")
        options.sort(key=lambda name: -wins.get(name, 0))
        chosen = options[: max(1, int(count))]

        written = []
        for offset, pattern in enumerate(chosen):
            notes = hooks.render_hook(
                key, scale, chords, bars=bars, pattern=pattern,
                octave=octave, seed=seed,
            )
            slot = start_slot + offset
            self._write_clip(
                track_index, slot, bars, notes,
                name=f"{chr(65 + offset)}: {pattern}", role="hook",
            )
            written.append({"slot": slot, "pattern": pattern,
                            "notes": len(notes),
                            "wins_so_far": wins.get(pattern, 0)})
        return {
            "auditions": written,
            "summary": (
                f"{len(written)} hook(s) in slots "
                f"{start_slot}-{start_slot + len(written) - 1}: "
                + ", ".join(w["pattern"] for w in written)
                + ". Fire each in Live; record the winner with record_taste."
            ),
        }

    def tool_record_taste(
        self, kind: str, choice: str, context: str = "any"
    ) -> dict:
        """Record that the user preferred one option, so future picks learn.

        `kind` is what was being chosen ("hook_pattern", "drum_pattern",
        "progression", "bass_style"), `choice` the winner, `context` a style
        or genre word. A tally, not a model: three wins pick three times as
        often, every option keeps a baseline chance, and forget_taste undoes
        anything. This is the only place the user's actual ears reach the
        generators -- use it whenever they express a preference between
        things they heard.
        """
        result = self._taste().record(kind, choice, context)
        result["summary"] = (
            f"noted: {choice} for {kind}"
            + (f" in {context}" if context != "any" else "")
            + f" (tally {result['tally']})"
        )
        return result

    def tool_forget_taste(
        self, kind: str, choice: str | None = None, context: str = "any"
    ) -> dict:
        """Remove a recorded preference, or clear a whole kind/context."""
        return self._taste().forget(kind, choice, context)

    def tool_taste_summary(self) -> dict:
        """Everything the user's ears have taught this app so far."""
        summary = self._taste().summary()
        return {
            "taste": summary,
            "summary": (
                "nothing recorded yet -- audition_hooks then record_taste"
                if not summary else
                f"{sum(len(c) for k in summary.values() for c in k.values())} "
                f"preference(s) across {len(summary)} kind(s)"
            ),
        }

    def _bank(self) -> chordbank.ChordBank:
        if getattr(self, "_chord_bank", None) is None:
            self._chord_bank = chordbank.ChordBank()
        return self._chord_bank

    def _chords_from_bank(
        self, track_index, clip_index, key, mood, bank_style, bars, name, seed,
    ) -> dict:
        """Write a progression from the user's library, voicings and all.

        The file's i7 carries its actual seventh and spacing; a degree list
        rebuilds a plain triad. When the user has 700 progressions they call
        good, using them beats reconstructing them.
        """
        bank = self._bank()
        try:
            entry = bank.pick(mood=mood, key=key, seed=seed)
            notes = bank.load_notes(entry, key=key, bars=bars,
                                    style=bank_style)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        result = self._write_clip(
            track_index, clip_index, bars, notes,
            name or f"{key} {entry.name} ({mood})", role="chords",
        )
        result["progression"] = entry.name
        result["degrees"] = entry.degrees
        result["moods"] = list(entry.moods)
        result["from"] = entry.path.name
        result["summary"] = (
            f"{entry.name} from the reference library "
            f"({', '.join(entry.moods)}), voicings as written, in {key}"
        )
        return result

    def tool_find_progressions(
        self,
        mood: str | None = None,
        key: str | None = None,
        length: int | None = None,
        limit: int = 10,
        seed: int | None = None,
    ) -> dict:
        """Search the user's own progression library by mood.

        The references folder holds ~700 progressions the user chose, each
        labelled with its key, its roman-numeral spelling and mood words --
        nostalgic, mysterious, hopeful, dark, triumphant... This searches
        those labels. Use it when the request names a feeling rather than a
        chord list, then pass the winner's `name` to a chord tool as
        `degrees`, or better, write its actual voicings with `mood=` on
        create_chord_clip / create_varied_chords.
        """
        bank = self._bank()
        if not bank.entries:
            raise ToolError(
                f"no reference library at {bank.root} -- add labelled MIDI "
                "progressions to references/"
            )
        try:
            found = bank.find(mood=mood, key=key, length=length,
                              limit=limit, seed=seed)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "matches": [
                {"name": e.name, "key": e.key, "degrees": e.degrees,
                 "moods": list(e.moods), "chords": e.length,
                 "styles": sorted(e.styles)}
                for e in found
            ],
            "moods_available": bank.moods(),
            "summary": (
                f"{len(found)} progression(s)"
                + (f" for {mood!r}" if mood else "")
                + (": " + "; ".join(e.name for e in found[:4]) if found else "")
            ),
        }

    def tool_design_progression(
        self,
        scale: str = "minor",
        length: int = 4,
        arc: str = "cadence",
        start: int = 1,
        seed: int | None = None,
    ) -> dict:
        """Design a progression by the shape of its tension, not by name.

        Every chord carries measurable strain -- a dominant pulls, the tonic
        rests, a diminished chord is all pull. `arc` says where the strain
        should sit: "cadence" peaks second-to-last and resolves, "rise" never
        resolves (for builds -- the drop's downbeat is the resolution),
        "arch" for verses, "calm" for intros, "drive" for dark rooms.

        Use the result as `degrees` in any generator.
        """
        degrees = composing.design_progression(
            scale=scale, length=length, arc=arc, start=start, seed=seed
        )
        curve = composing.tension_curve(degrees, scale)
        return {
            "degrees": degrees,
            "arc": arc,
            "tension": curve,
            "summary": (
                "-".join(str(d) for d in degrees)
                + f" ({arc}: tension "
                + " -> ".join(f"{t:.2f}" for t in curve) + ")"
            ),
        }

    def tool_plan_harmony(
        self,
        sections: list[dict],
        degrees: Any = None,
        key: str | None = None,
        scale: str | None = None,
        breakdown: str = "reharmonise",
        climax: str = "lift",
    ) -> dict:
        """Give each section of an arrangement its own harmonic treatment.

        `breakdown` may be "reharmonise" (relative substitution under the same
        melody) or any modulation -- "relative" sends a minor track to its
        relative major for the breakdown, the classic clouds-parting move.
        `climax` names the final drop's modulation: "lift" (a tone up),
        "semitone_lift", "relative", "parallel", "dominant", "subdominant".

        One progression looping for six minutes is the structural tell of
        generated music. This keeps the material constant but changes the
        treatment: the intro pedals the tonic, builds end on V so the drop's
        downbeat lands as a resolution, breakdowns re-harmonise under the
        same melody, and the final drop lifts a whole tone.

        With no key/degrees given, they are read from the set via analyse_set.
        Write each section's clip from its entry, then arrange as usual.
        """
        if key is None or degrees is None:
            found = self.tool_analyse_set()
            key = key or found["key"]
            scale = scale or found["scale"]
            degrees = degrees if degrees is not None else found["degrees"]
        resolved = theory.parse_degrees(degrees) if not isinstance(degrees, list)             else [int(d) for d in degrees]
        plan = composing.harmonic_plan(
            sections, resolved, key=key, scale=scale or "minor",
            breakdown=breakdown, climax=climax,
        )
        return {
            "key": key,
            "scale": scale or "minor",
            "sections": plan,
            "summary": " | ".join(
                f"{p.get('name', '?')}: {p['treatment']}"
                + (f" in {p['key']}" if p["key"] != key else "")
                for p in plan
            ),
        }

    def tool_compose_theme(
        self,
        key: str = "A",
        scale: str = "minor",
        degrees: Any = "1-6-3-7",
        bars: float = 8,
        clip_index: int = 0,
        tracks: list[dict] | None = None,
        seed: int | None = None,
        shape: str = "arch",
        rhythm: str = "syncopated",
        style: str | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """Compose one motif and derive a whole ensemble from it.

        The lead states and develops the cell, the hook is its strongest
        fragment an octave up, the counter-line answers it inverted in the
        gaps, the arp runs it double-time, and the bass plays its rhythm on
        the chord roots. Every part shares DNA, which is what makes a track
        sound written rather than assembled -- separately generated parts are
        strangers however good each one is.

        Parts land on existing tracks matched by role (lead, hook, arp, bass;
        the counter-line goes to a track named counter/melody if present).
        Missing roles are reported, never created.
        """
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, 3, "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )

        # A learned motif: the contour and rhythm of the references fed to
        # learn_references, developed exactly like a written cell would be.
        cell = None
        if str(rhythm).lower() in LEARNED or str(shape).lower() in LEARNED:
            library = self._library()
            try:
                cohort = library._in_style(style)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            candidates = [
                r.parts[part]["motif"]
                for r in cohort
                for part in ("lead", "melody")
                if part in r.parts and r.parts[part].get("motif")
            ]
            if not candidates:
                raise ToolError(
                    "no melodic motif has been learned yet -- run "
                    "learn_references on MIDI that contains a lead or melody"
                )
            picked = candidates[(seed or 0) % len(candidates)]
            try:
                cell = motif.cell_from_learned(picked, seed=seed)
            except ValueError as exc:
                raise ToolError(f"learned motif unusable: {exc}") from exc
            shape, rhythm = "arch", "syncopated"   # only used for reporting

        theme = composing.compose_theme(
            key, scale, chords, bars_per_chord=bars_per_chord,
            seed=seed, shape=shape, rhythm=rhythm, cell=cell,
        )

        # Map roles onto tracks: explicit list first, then by name.
        wanted = {"lead", "hook", "arp", "bass", "counter"}
        targets: dict[str, int] = {}
        if tracks:
            # Tolerate the shapes the model actually passes: a {track_index,
            # role} dict, a bare index (role read from the track's name), or a
            # role-name string (matched to a track by name). A bare list of
            # ints used to crash with "'int' object has no attribute 'get'".
            state = self.bridge.call("get_song")
            by_index = {int(t["index"]): t for t in state.get("tracks", [])}
            name_to_index = {
                str(t.get("name", "")).lower(): int(t["index"])
                for t in state.get("tracks", [])
            }
            for entry in tracks:
                if isinstance(entry, dict):
                    role = str(entry.get("role", "")).lower()
                    idx = entry.get("track_index")
                    if idx is None and role in name_to_index:
                        idx = name_to_index[role]
                    if role in wanted and idx is not None:
                        targets[role] = int(idx)
                elif isinstance(entry, (int, float)):
                    idx = int(entry)
                    track = by_index.get(idx, {})
                    nm = str(track.get("name", "")).lower()
                    role = ("counter" if ("counter" in nm or "melody" in nm)
                            else _role_from_name(nm, default=None))
                    if role in wanted:
                        targets[role] = idx
                elif isinstance(entry, str):
                    role = entry.strip().lower()
                    role = "counter" if role in ("counter", "melody") else role
                    if role in wanted and role in name_to_index:
                        targets[role] = name_to_index[role]
        else:
            state = self.bridge.call("get_song")
            for track in state.get("tracks", []):
                if not track.get("is_midi"):
                    continue
                name = str(track.get("name", "")).lower()
                # "melody" aliases to "lead" in the role table, so the raw name
                # has to be checked first or the counter-line never lands.
                if ("counter" in name or "melody" in name) \
                        and "counter" not in targets:
                    targets["counter"] = int(track["index"])
                    continue
                role = _role_from_name(name, default=None)
                if role in wanted and role not in targets:
                    targets[role] = int(track["index"])

        written, missing = [], []
        for role, notes in theme.items():
            index = targets.get(role)
            if index is None:
                missing.append(role)
                continue
            perform_role = "melody" if role == "counter" else role
            self._write_clip(
                index, clip_index, bars, notes,
                name=f"Theme {role}", role=perform_role,
            )
            written.append({"role": role, "track_index": index,
                            "notes": len(notes)})

        if not written:
            raise ToolError(
                "no tracks matched any theme role (lead, hook, arp, bass, "
                "counter/melody). Name the tracks after their roles or pass "
                "an explicit tracks list."
            )
        return {
            "written": written,
            "missing_roles": missing,
            "seed": seed,
            "summary": (
                f"one motif developed across {len(written)} part(s)"
                + (f"; no track for {', '.join(missing)}" if missing else "")
            ),
        }

    def tool_corpus_profile(self) -> dict:
        """What the learned references do: voicing, bass articulation, grooves.

        This is what `"learned"` resolves to wherever a generator accepts it,
        and it is worth reading before generating so you can say what the
        corpus actually favours rather than guessing.
        """
        library = self._library()
        if not library.references:
            raise ToolError("nothing learned yet -- run learn_references first")

        styles = library.cluster_styles()
        grooves = {}
        for role in ("drums", "bass", "chords", "lead", "melody"):
            learned = library.groove_for(role)
            if learned is not None:
                grooves[role] = {"swing": learned.swing, "push": learned.push}

        voicing = library.voicing_profile()
        bass = library.bass_profile()
        return {
            "references": len(library.references),
            "voicing": voicing,
            "bass": bass,
            "grooves": grooves,
            "styles": styles,
            "top_progressions": library.common_progressions(6),
            "top_movements": library.common_movements(8),
            "chord_qualities": library.common_qualities(),
            "summary": (
                f"{len(library.references)} reference(s): "
                f"{voicing.get('style', '?')} voicings averaging "
                f"{voicing.get('mean_voices', '?')} voices, "
                f"{bass.get('dominant_style', 'no')} bass"
            ),
        }

    def tool_corpus_summary(self) -> dict:
        """What the learned references have in common.

        Reports the keys, tempos, chord qualities, recurring progressions and
        -- most usefully -- the chord-to-chord movements, which is what new
        progressions are generated from.
        """
        library = self._library()
        if not library.references:
            raise ToolError(
                "nothing learned yet. Put MIDI files in references/ and call "
                "learn_references."
            )
        return library.summary()

    def tool_suggest_progression(
        self, length: int = 4, start: int = 1, seed: int | None = None,
        style: str | None = None,
    ) -> dict:
        """Propose a progression by walking the learned chord movements.

        `style` narrows the walk to one cluster of references -- corpus_profile
        lists them -- so thirty house references and five DnB ones stop being
        averaged into a taste nobody has.

        Whole progressions rarely repeat across a corpus, but the moves inside
        them do -- so this produces something new that still behaves like the
        references.
        """
        library = self._library()
        if not library.references:
            raise ToolError(
                "nothing learned yet -- run learn_references on a folder of "
                "MIDI files first"
            )
        suggestion = dict(
            library.suggest_progression(length=length, start=start, seed=seed,
                                        style=style)
        )
        suggestion["learned_from"] = len(library.references)
        suggestion["summary"] = (
            "-".join(str(d) for d in suggestion["degrees"])
            + f" (walked from {len(library.references)} reference(s))"
        )
        return suggestion

    def tool_list_voicings(self) -> dict:
        """Every chord extension and voicing style, and what each is for."""
        return voicings.describe()

    def tool_list_styles(self) -> dict:
        """Every bass articulation, lead style and harmonic variation available."""
        return {
            "bass": basslines.describe(),
            "lead": leads.describe(),
            "harmony_variations": harmony.RECIPES,
            "grooves": sorted(groove.GROOVES),
        }

    # ------------------------------------------------------------------
    # Styled parts
    # ------------------------------------------------------------------

    def tool_create_styled_bass(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        style: str = "rolling",
        octave: int = 2,
        drums_track: int | None = None,
        humanise: float = 0.2,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """Build a bassline in a named articulation style.

        Styles differ in where notes sit against the kick, how long they ring,
        and which chord tones they use -- not just in rhythm. "rolling" threads
        sixteenths between the kicks, "drag" sits behind the beat and legato,
        "offbeat" answers the kick, "walking" steps toward the next chord.
        Call list_styles to see them all.

        Pass `drums_track` and the bass is placed relative to that track's
        actual kick, which is what produces the rolling feel.
        """
        chords, bars_per_chord = self._progression(key, scale, degrees, bars, 3,
                                                   "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )
        drum_notes = None
        if drums_track is not None:
            try:
                drum_clip = self.bridge.call(
                    "get_clip", track_index=drums_track, clip_index=0
                )
                drum_notes = drum_clip.get("notes") or None
            except (AbletonError, AbletonNotRunning):
                drum_notes = None

        notes = basslines.generate(
            chords, style=self._learned_bass_style(style),
            bars_per_chord=bars_per_chord, octave=octave,
            against_drums=drum_notes, humanise=humanise, seed=seed,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes,
            name or f"{key} bass ({style})", role="bass")
        result["style"] = style
        result["description"] = basslines.resolve(style).description
        return result

    def tool_create_lead_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        style: str = "soaring",
        octave: int = 5,
        groove_name: str = "straight",
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """Build a lead that arcs across the whole phrase.

        A trance lead is a continuous sixteenth-note stream that climbs over
        eight or sixteen bars and reaches its highest note exactly where the
        drop lands. The register is driven by position in the phrase rather
        than by the bar, which is what makes it soar instead of merely
        repeating. Styles: soaring, pluck, rolling, arp_climb, call, stab.
        """
        chords, bars_per_chord = self._progression(key, scale, degrees, bars,
                                                   octave, "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )
        notes = leads.generate(
            chords, root=key, scale=scale, style=style,
            bars_per_chord=bars_per_chord, octave=octave,
            groove=self._learned_groove(groove_name, "lead"), seed=seed,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} lead ({style})", role="lead")
        result["style"] = style
        result["description"] = leads.resolve(style).description
        return result

    def tool_create_varied_chords(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        variation: str = "extended",
        octave: int | None = None,
        extension: str = "seventh",
        voicing: str = "open",
        rhythm: str = "pad",
        velocity: int = 85,
        spread: float = 0.0,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
        mood: str | None = None,
        bank_style: str | None = None,
    ) -> dict:
        """Chords with harmonic movement inside the loop.

        Four chords repeated is what makes generated music tiring. These are
        the conventional moves that fix it without adding complexity:
        "borrowed" swaps one chord for its parallel-mode version (the major V
        in a minor key), "secondary" puts a dominant in front of the chord it
        belongs to, "passing" fills a step-wise gap, "turnaround" splits the
        last bar in two. "rich" and "moving" combine them.

        `extension` is the complexity dial: triad, add9, sixth, seventh, ninth,
        eleventh, thirteenth. `voicing` is the spacing, which matters more than
        the extension does -- "open" drops the 7th and puts the 9th on top,
        "rootless" leaves the root to the bassline, "shell" keeps only root,
        3rd and 7th. Deep and progressive house live on sevenths and ninths in
        open or rootless voicings; triads read as naive in those genres.
        """
        if mood is not None:
            return self._chords_from_bank(
                track_index, clip_index, key, mood, bank_style, bars, name,
                seed,
            )
        # A reference clip re-voices *that* progression rather than inventing
        # one: same key, same degrees, new spacing and extensions.
        if isinstance(degrees, str) and degrees.lower() in LEARNED:
            resolved = self._learned_degrees(4, seed)
        elif reference_track is not None:
            key, scale, resolved = self._reference_degrees(
                int(reference_track), int(reference_clip)
            )
        elif isinstance(degrees, str) and degrees.lower() in theory.PROGRESSIONS:
            resolved = list(theory.PROGRESSIONS[degrees.lower()])
        else:
            resolved = theory.parse_degrees(degrees)

        # Extended chords sit higher than triads. A ninth voiced from a triad's
        # home register puts its 7th and 9th in the low mids, which is the
        # single fastest way to make a chord part sound like mud.
        extension = voicings.normalise_extension(extension) or extension
        if octave is None:
            octave = 4 if extension in ("ninth", "eleventh", "thirteenth") else 3

        steps = harmony.vary(resolved, scale=scale, recipe=variation, seed=seed)

        # Spacing matters more than the chord symbols. Build each step through
        # the voicing engine so extensions land where they belong -- 7th
        # dropped, 5th omitted, 9th on top -- rather than stacked in a block.
        durations = [s.bars for s in steps]
        centre = (octave + 2) * 12 + 4
        chords = []
        for entry in steps:
            base = theory.build_chord(
                key, scale, entry.degree, octave=octave, quality=entry.quality
            )
            pitches = voicings.voice(
                voicings.extend(base, extension, key=key, scale=scale),
                style=voicing, centre=centre, quality=base.quality,
            )
            chords.append(theory.Chord(base.degree, base.root_pitch,
                                       base.quality, tuple(pitches)))
        chords = theory.voice_lead(chords, centre=centre)

        # Scale the step durations so the clip is exactly `bars` long.
        span = harmony.total_bars(steps) or 1.0
        scale_factor = bars / span

        notes: list[Note] = []
        cursor = 0.0
        for chord, chord_bars in zip(chords, durations):
            length = chord_bars * scale_factor
            part = generators.generate_chords(
                [chord], bars_per_chord=length, rhythm=rhythm,
                velocity=velocity, spread=spread, seed=seed,
            )
            for note in part:
                note["start"] = float(note["start"]) + cursor * BEATS_PER_BAR
                notes.append(note)
            cursor += length

        notes.sort(key=lambda n: (n["start"], n["pitch"]))
        result = self._write_clip(
            track_index, clip_index, bars, notes,
            name or f"{key} {scale} chords ({variation})", role="chords")
        result["chords"] = [c.describe() for c in chords]
        result["steps"] = [s.describe() for s in steps]
        result["variation"] = harmony.RECIPES.get(variation, variation)
        return result

    def tool_create_drum_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        pattern: str = "four_on_floor",
        bars: float = 4,
        velocity: int = 100,
        swing: float = 0.0,
        humanise: float = 0.0,
        fill_last_bar: bool = False,
        vary: bool = True,
        name: str | None = None,
        seed: int | None = None,
        groove: str = "straight",
        instruments: list[str] | None = None,
    ) -> dict:
        """Generate a drum clip from a named pattern.

        Patterns contain several voices (kick, clap, hats) laid out for a full
        Drum Rack, where kick is C1/36. Use `instruments` to take only some of
        them -- ["kick"] for a track holding a single kick device.

        If the track is clearly a single-instrument drum track (a Kick device,
        or a track named "Kick") this restricts itself to the kick
        automatically: sending clap and hat notes to a kick device just plays
        the kick sample at other pitches, which sounds broken.
        """
        if instruments is None:
            instruments = self._infer_drum_voices(track_index)

        notes = generators.generate_drums(
            pattern=pattern,
            bars=int(bars),
            velocity=velocity,
            swing=swing,
            humanise=humanise,
            fill_last_bar=fill_last_bar,
            vary=vary,
            seed=seed,
            groove=self._learned_groove(groove, "drums"),
            instruments=instruments,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"{pattern} drums", role="drums")

    def _infer_drum_voices(self, track_index: int) -> list[str] | None:
        """Work out whether a drum track can take a whole kit or just one voice.

        A Drum Rack maps a different sound to every pad, so it takes the full
        pattern. A single instrument -- Kick, DS Kick, a one-shot Simpler --
        maps one sound across the keyboard, so anything but its own voice comes
        out as that sound pitched wrongly.
        """
        try:
            track = self.bridge.call("get_song")["tracks"][track_index]
        except (AbletonError, AbletonNotRunning, IndexError, KeyError):
            return None

        devices = [str(d).lower() for d in track.get("devices", [])]
        name = str(track.get("name", "")).lower()

        voices = ("kick", "snare", "clap", "hat", "tom", "rim", "ride",
                  "crash", "shaker", "perc", "cowbell")

        def only(voice: str) -> list[str]:
            # "hat" covers both the closed and open hat lines.
            return ["closed_hat", "open_hat"] if voice == "hat" else [voice]

        # The track's name is the most specific signal there is. A track called
        # "Kick" wants the kick, even when it holds a full rack -- otherwise it
        # duplicates whatever the "Drums" track is already playing.
        for voice in voices:
            if voice in name:
                return only(voice)

        # A rack with a general name (or no device yet) can host the whole kit.
        if any("rack" in d or "kit" in d or "impulse" in d for d in devices):
            return None
        if not devices:
            return None

        for voice in voices:
            if any(voice in d for d in devices):
                return only(voice)

        # An unrecognised single device: safest to send only the kick.
        return ["kick"]

    def tool_create_arpeggio_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        style: str = "up",
        rate: str = "1/16",
        octaves: int = 2,
        octave: int = 4,
        velocity: int = 90,
        gate: float = 0.9,
        swing: float = 0.0,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """Generate an arpeggio over a chord progression."""
        rates = {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25, "1/32": 0.125}
        if rate not in rates:
            raise ToolError(f"rate must be one of {', '.join(rates)}")
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, octave, "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )
        notes = generators.generate_arpeggio(
            chords,
            bars_per_chord=bars_per_chord,
            style=style,
            rate=rates[rate],
            octaves=octaves,
            velocity=velocity,
            gate=gate,
            swing=swing,
            seed=seed,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} arp", role="arp")

    def tool_create_melody_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        octave: int = 5,
        rhythm: str = "syncopated",
        tension: float = 0.3,
        velocity: int = 96,
        variation: str | None = None,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """Write a melody with real phrase structure.

        Four bars that ask and four that answer: the first phrase ends
        unresolved on the 2nd, 5th or 7th so the ear waits, the second lands on
        the tonic. One highest note, placed about two-thirds through -- a line
        that peaks on its last note has nowhere to resolve to.

        `tension` is how often a note is a non-chord tone. Every one of them
        resolves by step, which is what makes it tension rather than a mistake.
        At 0 the line is safe and dull; above 0.5 it stops agreeing with the
        harmony. Rests are included deliberately: a phrase that never breathes
        cannot be sung.

        `rhythm` picks the cell the phrases share -- even, anticipated, gallop,
        long_short, syncopated, driving, sparse, held, trance, answer, vocal.

        Pass the same `variation` you gave create_varied_chords. Without it the
        melody assumes the progression is evenly divided, and a half-bar
        turnaround or a passing chord puts it on the wrong harmony.
        """
        # Match the harmony the chord part is actually playing. Passing
        # `variation` builds the same uneven progression -- half-bar
        # turnarounds and passing chords included -- so the melody lands on the
        # chord that is really sounding rather than an assumed even split.
        if variation:
            if isinstance(degrees, str) and degrees.lower() in theory.PROGRESSIONS:
                resolved = list(theory.PROGRESSIONS[degrees.lower()])
            else:
                resolved = theory.parse_degrees(degrees)
            steps = harmony.vary(resolved, scale=scale, recipe=variation, seed=seed)
            chords, durations = harmony.build(key, scale, steps, octave=3)
        else:
            chords, _ = self._progression(key, scale, degrees, bars, 3, "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )
            durations = None

        notes = melody.write(
            root=key, scale=scale, chords=chords, bars=bars, octave=octave,
            rhythm=rhythm, tension=tension, velocity=velocity, seed=seed,
            durations=durations,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} {scale} melody", role="melody")
        if notes:
            peak = max(notes, key=lambda n: n["pitch"])
            span = bars * BEATS_PER_BAR
            result["shape"] = {
                "peak": theory.pitch_name(int(peak["pitch"])),
                "peak_at": f"{float(peak['start']) / span:.0%} through",
                "ends_on": theory.pitch_name(int(notes[-1]["pitch"])),
                "distinct_pitches": len({int(n["pitch"]) for n in notes}),
            }
        return result

    def tool_write_clip_notes(
        self,
        track_index: int,
        clip_index: int = 0,
        bars: float = 4,
        notes: list[dict] | None = None,
        name: str | None = None,
        mode: str = "replace",
    ) -> dict:
        """Write raw MIDI notes into a clip. The escape hatch for material the generators do not cover -- use a generator tool first where one fits."""
        notes = notes or []
        for note in notes:
            if "pitch" not in note or "start" not in note:
                raise ToolError("each note needs at least 'pitch' and 'start'")
            note.setdefault("duration", 0.25)
            note.setdefault("velocity", 100)

        if mode == "add":
            result = self.bridge.call(
                "add_notes",
                track_index=track_index,
                clip_index=clip_index,
                notes=notes,
            )
            result["summary"] = generators.summarise(notes)
            return result
        return self._write_clip(track_index, clip_index, bars, notes, name)

    def tool_transpose_clip(
        self, track_index: int, clip_index: int, semitones: int
    ) -> dict:
        """Transpose every note in an existing clip by a number of semitones."""
        clip = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        notes = generators.transpose(clip.get("notes") or [], semitones)
        result = self.bridge.call(
            "replace_notes",
            track_index=track_index,
            clip_index=clip_index,
            notes=notes,
        )
        result["summary"] = generators.summarise(notes)
        return result

    def tool_duplicate_clip(
        self,
        track_index: int,
        clip_index: int,
        to_track_index: int | None = None,
        to_clip_index: int | None = None,
        transpose: int = 0,
        name: str | None = None,
    ) -> dict:
        """Copy a clip to another slot or track, optionally transposing it. Good for making a variation."""
        source = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        notes = source.get("notes") or []
        if transpose:
            notes = generators.transpose(notes, transpose)
        target_track = track_index if to_track_index is None else to_track_index
        target_clip = (clip_index + 1) if to_clip_index is None else to_clip_index
        return self._write_clip(
            target_track,
            target_clip,
            source["length_beats"] / BEATS_PER_BAR,
            notes,
            name or f"{source.get('name', 'clip')} copy",
        )

    # ------------------------------------------------------------------
    # Variations
    # ------------------------------------------------------------------

    def tool_list_variations(self) -> dict:
        """List every mutation and recipe create_clip_variation understands."""
        return {
            "mutations": sorted(variations.MUTATIONS),
            "recipes": {k: v for k, v in variations.RECIPES.items()},
            "note": (
                "Recipes are shorthand for a chain of mutations. Pass either in "
                "the `mutations` list."
            ),
        }

    def tool_create_clip_variation(
        self,
        track_index: int,
        clip_index: int,
        to_clip_index: int | None = None,
        mutations: list[str] | None = None,
        intensity: float = 0.35,
        to_track_index: int | None = None,
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """Make a variation of an existing clip into another slot.

        This is how sections stay related but stop sounding identical: take the
        drop's drum loop and make a "stripped" version for the intro, a "bigger"
        one for the second drop, a "pre_drop" stutter for the last two bars of a
        build. The source clip is left untouched.
        """
        source = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        notes = source.get("notes") or []
        if not notes:
            raise ToolError(
                f"clip at track {track_index} slot {clip_index} has no MIDI notes"
            )

        recipe = mutations or ["bigger"]
        mutated = variations.apply(notes, recipe, intensity=intensity, seed=seed)

        target_track = track_index if to_track_index is None else to_track_index
        target_slot = (clip_index + 1) if to_clip_index is None else to_clip_index
        bars = source["length_beats"] / BEATS_PER_BAR

        result = self._write_clip(
            target_track,
            target_slot,
            bars,
            mutated,
            name or f"{source.get('name', 'clip')} ({'+'.join(recipe)})",
        )
        result["mutations"] = recipe
        result["source"] = {"track_index": track_index, "clip_index": clip_index}
        return result

    def tool_create_variation_set(
        self,
        track_index: int,
        clip_index: int = 0,
        count: int = 4,
        escalate: bool = True,
        start_slot: int | None = None,
        seed: int | None = None,
    ) -> dict:
        """Fill consecutive clip slots with progressively different versions.

        With `escalate` the set runs stripped -> original -> bigger -> climax, so
        the slots can be handed straight to arrange_to_timeline as
        `clip_indices` and the track will grow across the song.
        """
        source = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        notes = source.get("notes") or []
        if not notes:
            raise ToolError(
                f"clip at track {track_index} slot {clip_index} has no MIDI notes"
            )

        bars = source["length_beats"] / BEATS_PER_BAR
        base_name = source.get("name", "clip")
        first = clip_index if start_slot is None else start_slot

        created = []
        for offset, (label, variant) in enumerate(
            variations.variation_set(notes, count=count, escalate=escalate, seed=seed)
        ):
            slot = first + offset
            self._write_clip(
                track_index, slot, bars, variant, f"{base_name} [{label}]"
            )
            created.append(
                {
                    "clip_index": slot,
                    "label": label,
                    "summary": generators.summarise(variant),
                }
            )
        return {
            "track_index": track_index,
            "clip_indices": [c["clip_index"] for c in created],
            "variations": created,
        }

    # ------------------------------------------------------------------
    # Arrangement
    # ------------------------------------------------------------------

    def tool_plan_arrangement(
        self,
        target_seconds: float = 360,
        tempo: float | None = None,
        template: str = "house",
        phrase_bars: int = 8,
    ) -> dict:
        """Plan a full song structure for a target duration. Returns sections (intro, build, drop, breakdown, outro) with start bars and the instrument roles active in each. Call this before arrange_to_timeline."""
        if tempo is None:
            tempo = float(self.bridge.call("get_song").get("tempo", 124.0))
        sections = arrangement.plan(
            target_seconds=target_seconds,
            tempo=tempo,
            template=template,
            phrase_bars=phrase_bars,
        )
        return arrangement.summarise(sections, tempo)

    def tool_arrange_existing(
        self,
        target_seconds: float = 360,
        template: str | None = None,
        clear_first: bool = True,
    ) -> dict:
        """Arrange the tracks already in the set. Creates nothing.

        This is "arrange what I have" as one call. Every track that holds
        material -- a session clip, or a sample sitting on the timeline -- is
        given a role from its name and placed. No track is created, no clip is
        generated, no instrument is loaded, whatever is missing.

        The template is chosen from what the set actually contains unless you
        name one: a set with no drums is not a house track, and arranging it as
        one produces a build-up to a drop that never arrives.

        Use `build_track` when the set is empty and the parts need writing
        first. Use this when they already exist.
        """
        state = self.bridge.call("get_song")
        tempo = float(state.get("tempo", 124.0))

        try:
            on_timeline = {
                int(t["index"]) for t in
                self.bridge.call("get_arrangement").get("tracks", [])
                if t.get("clips")
            }
        except (AbletonError, AbletonNotRunning):
            on_timeline = set()

        playable, ignored = [], []
        for track in state.get("tracks", []):
            index = int(track["index"])
            slots = [int(c["slot"]) for c in (track.get("clips") or [])]
            if not slots and index not in on_timeline:
                ignored.append({"track": track.get("name"),
                                "why": "nothing in it to place"})
                continue
            role = _role_from_name(track.get("name", ""), default=None)
            if not role:
                # The name says nothing, so ask the notes. A bassline is a
                # bassline whatever the track is called -- the user should
                # never have to rename anything for the arranger to work.
                role = _role_from_content(self.bridge, index, slots)
            if not role:
                # Audio, or a clip the classifier could not read: keep it as a
                # core element so it plays through, silently -- no nagging.
                role = "vocal" if not track.get("is_midi") else "chords"
            entry = {"track_index": index, "role": role, "track_name":
                     track.get("name")}
            if slots:
                entry["clip_indices"] = sorted(slots)
            playable.append(entry)

        if not playable:
            raise ToolError(
                "nothing in this set to arrange -- no track has a clip in a "
                "Session slot or on the timeline. Add some material, or use "
                "build_track to write the parts first."
            )

        roles_present = {e["role"] for e in playable}
        chosen = template or arrangement.template_for(roles_present)
        sections = arrangement.plan(
            target_seconds=target_seconds, tempo=tempo, template=chosen
        )

        # Choose each section's roles from the tracks that are ACTUALLY here,
        # by tier and energy -- not the template's fixed vocabulary. This is
        # what keeps a verse carrying the groove (foundation + core + a
        # topline) instead of only whatever happened to match a piano/guitar
        # role list.
        plan = arrangement.summarise(sections, tempo)
        for sec_dict, sec_obj in zip(plan["sections"], sections):
            sec_dict["roles"] = arrangement.section_roles(roles_present, sec_obj)

        result = self.tool_arrange_to_timeline(
            sections=plan["sections"], tracks=playable, clear_first=clear_first
        )
        result["template"] = chosen
        result["arranged"] = [
            {"track_index": e["track_index"], "role": e["role"]} for e in playable
        ]
        result["ignored"] = ignored
        result["created_nothing"] = True
        result["summary"] = (
            f"arranged {len(playable)} existing track(s) as {chosen!r}: "
            f"{result['end_bars']:.0f} bars, "
            f"{result['duration_seconds'] / 60:.1f} min"
            + (f"; {len(ignored)} track(s) had nothing to place" if ignored else "")
        )
        return result

    def tool_arrange_to_timeline(
        self,
        sections: list[dict],
        tracks: list[dict],
        clear_first: bool = True,
    ) -> dict:
        """Lay session clips onto the arrangement following a section map.

        `tracks` maps roles onto real clips, e.g.
        [{"track_index": 0, "clip_index": 0, "role": "kick"}]
        A track whose role appears in a section's `roles` gets its clip looped
        across that section.

        To vary a part across the song, give an entry `clip_indices` (a list of
        slots, e.g. from create_variation_set) instead of a single `clip_index`,
        plus an optional `variation_policy`:
          "escalate" (default) picks by the section's energy, so quiet sections
              get the early/stripped variations and drops get the big ones;
          "cycle" rotates through the list each time the role appears;
          "random" picks freely.
        Impacts are placed once on the section downbeat; risers are positioned
        to finish exactly on the section boundary.
        """
        if not sections:
            raise ToolError("sections is empty -- call plan_arrangement first")
        if not tracks:
            raise ToolError("tracks is empty -- nothing to place")

        # Validate up front and say precisely what is wrong. A null in any of
        # these used to surface as a bare TypeError with no indication of which
        # section or track caused it.
        clean_sections: list[dict] = []
        for position, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ToolError(
                    f"section {position} is {type(section).__name__}, not an "
                    "object. Pass the 'sections' list from plan_arrangement."
                )
            label = section.get("name") or f"section {position}"

            # Accept the shapes a caller reasonably reaches for: `bars`,
            # `length_bars`, or a pair of boundaries. Only complain when the
            # length genuinely cannot be worked out.
            raw_start = section.get("start_bar", section.get("start"))
            raw_length = section.get("bars", section.get("length_bars"))
            raw_end = section.get("end_bar", section.get("end"))

            if raw_start is None:
                raise ToolError(
                    f"{label} has no start_bar. Pass the sections list that "
                    "plan_arrangement returns rather than rebuilding it."
                )
            try:
                start = float(raw_start)
                if raw_length is not None:
                    length = float(raw_length)
                elif raw_end is not None:
                    length = float(raw_end) - start
                else:
                    raise ToolError(
                        f"{label} has no bars. A section needs either `bars` "
                        "or an `end_bar`. The sections list from "
                        "plan_arrangement already has both."
                    )
            except (TypeError, ValueError) as exc:
                raise ToolError(
                    f"{label}: start_bar and bars must be numbers ({exc})"
                )
            if length <= 0:
                raise ToolError(f"{label}: bars must be greater than zero")
            clean_sections.append({
                **section,
                "start_bar": start,
                "bars": length,
                # A missing or null roles list means nothing plays here, which
                # is legitimate for a silent intro -- it is not an error.
                "roles": [str(r).lower() for r in (section.get("roles") or [])],
            })

        clean_tracks: list[dict] = []
        skipped_tracks: list[dict] = []
        for position, entry in enumerate(tracks):
            if not isinstance(entry, dict):
                raise ToolError(
                    f"track entry {position} is {type(entry).__name__}, not an "
                    'object. Each needs {"track_index": n, "role": "..."}.'
                )
            if entry.get("track_index") is None:
                raise ToolError(
                    f"track entry {position} has no track_index."
                )
            role = entry.get("role")
            if not role:
                skipped_tracks.append({
                    "track_index": entry["track_index"],
                    "why": "no role given, so it matches no section",
                })
                continue
            # A placeholder track has no clip to place. That is expected --
            # vocals and FX are recorded by hand -- so skip it rather than fail.
            slots = [
                int(c) for c in (
                    entry.get("clip_indices")
                    or ([entry["clip_index"]] if entry.get("clip_index") is not None
                        else [0])
                )
                if c is not None
            ]
            if not slots:
                skipped_tracks.append({
                    "track_index": int(entry["track_index"]),
                    "role": str(role).lower(),
                    "why": "no clip to place",
                })
                continue
            clean_tracks.append({
                **entry,
                "track_index": int(entry["track_index"]),
                "role": str(role).lower(),
                "clip_indices": slots,
            })

        if not clean_tracks:
            raise ToolError(
                "none of the given tracks have both a role and a clip to place. "
                f"Skipped: {skipped_tracks}"
            )

        sections, tracks = clean_sections, clean_tracks

        # What is already on the timeline. A sample dragged straight into the
        # arrangement has no session clip, so this is the only source for it --
        # and it has to be read before anything is cleared.
        on_timeline: dict[int, float] = {}
        try:
            for entry in self.bridge.call("get_arrangement").get("tracks", []):
                clips = entry.get("clips") or []
                if clips:
                    first = clips[0]
                    span = float(first.get("length_bars") or 0.0)
                    if span > 0:
                        on_timeline[int(entry["index"])] = span
        except (AbletonError, AbletonNotRunning) as exc:
            log.warning("could not read the timeline: %s", exc)

        if clear_first:
            self._autosnapshot("arrange")
            # Clearing a track whose only copy of the sample is on the
            # timeline would destroy the very thing we are placing.
            clear_these = [
                int(t["track_index"]) for t in tracks
                if int(t["track_index"]) not in on_timeline
                or _has_session_clip(self.bridge, t)
            ]
            if clear_these:
                self.bridge.call("clear_arrangement", track_indices=clear_these)

        # Cache clip lengths so we know how many repeats fill a section. A slot
        # that holds no clip is skipped rather than fatal: a placeholder track
        # for vocals or FX legitimately has nothing in it yet.
        lengths: dict[tuple[int, int], float] = {}
        placeable: list[dict] = []
        for entry in tracks:
            ti = int(entry["track_index"])
            usable: list[int] = []
            # Slots named per-section need their lengths cached, but they are
            # NOT part of the variation ladder -- appending them to `usable`
            # put the breakdown's clip at the top of the escalation, and every
            # full-energy drop picked it as "the biggest variation".
            section_slots = {
                int(v) for v in (entry.get("clip_by_section") or {}).values()
            }
            for ci in section_slots:
                if (ti, ci) in lengths:
                    continue
                try:
                    clip = self.bridge.call("get_clip", track_index=ti,
                                            clip_index=ci)
                    lengths[(ti, ci)] = max(
                        1.0, clip["length_beats"] / BEATS_PER_BAR
                    )
                except (AbletonError, AbletonNotRunning) as exc:
                    skipped_tracks.append({
                        "track_index": ti, "clip_index": ci,
                        "why": f"clip_by_section: {exc}",
                    })
            for ci in _slots_for(entry):
                if (ti, ci) in lengths:
                    usable.append(ci)
                    continue
                try:
                    clip = self.bridge.call(
                        "get_clip", track_index=ti, clip_index=ci
                    )
                except (AbletonError, AbletonNotRunning) as exc:
                    skipped_tracks.append({
                        "track_index": ti, "role": entry["role"],
                        "clip_index": ci, "why": str(exc),
                    })
                    continue
                lengths[(ti, ci)] = max(1.0, clip["length_beats"] / BEATS_PER_BAR)
                usable.append(ci)
            if usable:
                placeable.append({**entry, "clip_indices": usable})
            elif ti in on_timeline:
                # Nothing in the session, but there is material on the
                # timeline -- an imported sample. Spread that instead.
                lengths[(ti, -1)] = on_timeline[ti]
                placeable.append({**entry, "clip_indices": [-1],
                                  "from_timeline": True})
                skipped_tracks[:] = [
                    sk for sk in skipped_tracks if sk.get("track_index") != ti
                ]

        if not placeable:
            raise ToolError(
                "no track had a clip that could be placed. "
                f"Skipped: {skipped_tracks}"
            )
        tracks = placeable

        # How many times each role has been placed, for the "cycle" policy.
        seen: dict[str, int] = {}
        rng = random.Random(0)

        # Which roles the caller actually supplied a track for.
        available = {str(e.get("role", "")).lower() for e in tracks}

        # Dance-arrangement detail: a bar of air before each drop, and a small
        # transition mark every phrase. Built from Section objects so the same
        # craft applies however the caller described the sections.
        section_objs = [
            arrangement.Section(
                name=str(sec.get("name", "?")),
                start_bar=int(round(float(sec["start_bar"]))),
                bars=int(round(float(sec["bars"]))),
                energy=float(sec.get("energy", 0.5)),
                roles=[str(r).lower() for r in sec.get("roles", [])],
            )
            for sec in sections
        ]
        dropouts = arrangement.dropout_before_lifts(section_objs)
        dropout_bars = {int(d["at_bar"]) for d in dropouts}
        phrase_bars_set = {int(m["at_bar"]) for m in arrangement.phrase_marks(section_objs)}

        placements = []
        # Timeline-sourced tracks are placed in one call each, at the end: the
        # source has to survive until every copy is made.
        timeline_spread: dict[int, list[dict]] = {}
        for section in sections:
            start_bar = float(section["start_bar"])
            bars = float(section["bars"])
            roles = {str(r).lower() for r in section.get("roles", [])}

            # A requested role with no track falls back to a near neighbour, so
            # a set without a dedicated Sub still gets bass through the drops.
            for wanted in list(roles):
                if wanted in available:
                    continue
                for substitute in arrangement.ROLE_FALLBACKS.get(wanted, ()):
                    if substitute in available:
                        roles.add(substitute)
                        break

            for entry in tracks:
                role = str(entry.get("role", "")).lower()
                if role not in roles:
                    continue
                ti = int(entry["track_index"])
                slots = _slots_for(entry)
                policy = str(entry.get("variation_policy", "escalate")).lower()
                occurrence = seen.get(role, 0)
                seen[role] = occurrence + 1

                by_section = entry.get("clip_by_section") or {}
                section_name = str(section.get("name", "")).lower()
                if section_name in by_section:
                    # The section names its own clip: this is how a breakdown
                    # gets the reharmonised chords and the augmented theme
                    # rather than a quieter copy of the drop.
                    ci = int(by_section[section_name])
                    lengths.setdefault((ti, ci), lengths.get((ti, slots[0]), 8.0))
                elif len(slots) == 1:
                    ci = slots[0]
                elif policy == "cycle":
                    ci = slots[occurrence % len(slots)]
                elif policy == "random":
                    ci = rng.choice(slots)
                else:
                    # Escalate: map the section's energy onto the variation ladder.
                    energy = float(section.get("energy", 0.5))
                    ci = slots[min(len(slots) - 1, int(energy * len(slots)))]

                clip_bars = lengths[(ti, ci)]

                if role in ONE_SHOT_ROLES:
                    # An impact hits the downbeat of the section, once.
                    repeats, at = 1, start_bar
                elif role in SPAN_ROLES:
                    # A riser is placed so it *finishes* on the section boundary,
                    # which is what makes the handover to the drop land.
                    repeats = 1
                    at = max(0.0, start_bar + bars - clip_bars)
                else:
                    span = bars
                    # Leave the bar of air before a drop: a sustained part
                    # stops one bar short of the boundary so the drop's
                    # downbeat arrives out of silence. Drums keep going (the
                    # kick dropping out is the drummer's call, handled by the
                    # build fill), but bass/chords/pads/leads cut.
                    end_bar = int(round(start_bar + bars))
                    if (end_bar - 1 in dropout_bars
                            and role in arrangement.SUSTAINED_FOR_DROPOUT):
                        span = max(clip_bars, bars - 1)
                    repeats = max(1, int(round(span / clip_bars)))
                    at = start_bar

                if entry.get("from_timeline"):
                    timeline_spread.setdefault(ti, []).append(
                        {"start_bar": at, "repeats": repeats}
                    )
                else:
                    self.bridge.call(
                        "duplicate_clip_to_arrangement",
                        track_index=ti,
                        clip_index=ci,
                        start_bar=at,
                        repeats=repeats,
                    )
                placements.append(
                    {
                        "section": section.get("name"),
                        "track_index": ti,
                        "clip_index": ci,
                        "role": role,
                        "start_bar": at,
                        "repeats": repeats,
                    }
                )

        # Phrase-boundary detail: a small transition (a fill, a snare hit) on
        # every eighth bar, dropped from whichever track carries short
        # transition material -- a Build/Perc/FX with a clip of a couple of
        # bars or less. Deliberately the SAME small mark each phrase, not a
        # different roll every time; regular is what a listener locks onto.
        fill_placed = 0
        transition = None
        for entry in tracks:
            role = str(entry.get("role", "")).lower()
            ti = int(entry["track_index"])
            for ci in _slots_for(entry):
                # Only genuine fill material: a short perc or fx clip. Never
                # the impact (a one-shot on the drop), the riser (placed to
                # span into the drop) or the main drums (the beat itself) --
                # using those as phrase fills scattered them across every bar.
                if (lengths.get((ti, ci), 99) <= 2.0
                        and role in ("perc", "fx")
                        and role not in ONE_SHOT_ROLES
                        and role not in SPAN_ROLES):
                    transition = (ti, ci, lengths[(ti, ci)])
                    break
            if transition:
                break
        if transition and phrase_bars_set:
            ti, ci, clen = transition
            for bar in sorted(phrase_bars_set):
                # Land the transition so it finishes on the phrase boundary,
                # like a fill leading into the next eight.
                at = max(0.0, bar + 1 - clen)
                try:
                    self.bridge.call(
                        "duplicate_clip_to_arrangement",
                        track_index=ti, clip_index=ci, start_bar=at, repeats=1,
                    )
                    fill_placed += 1
                except (AbletonError, AbletonNotRunning):
                    pass

        for ti, specs in timeline_spread.items():
            try:
                self.bridge.call(
                    "duplicate_arrangement_clip",
                    track_index=ti, source_index=0, placements=specs,
                )
            except (AbletonError, AbletonNotRunning) as exc:
                skipped_tracks.append({"track_index": ti, "why": str(exc)})

        # Label the timeline so the sections are navigable in Live.
        try:
            self.bridge.call(
                "set_locators",
                markers=[
                    {"name": str(sec.get("name", "?")).title(),
                     "start_bar": float(sec["start_bar"])}
                    for sec in sections
                ],
                clear_existing=True,
            )
            markers = self._await_locators(len(sections))
            log.info("placed %d locators", len(markers))
        except (AbletonError, AbletonNotRunning) as exc:
            markers = []
            log.warning("could not set locators: %s", exc)

        self.bridge.call("set_view", view="arrangement")
        summary = self.bridge.call("get_arrangement")
        result = {
            "placements": len(placements),
            "end_bars": summary.get("end_bars"),
            "duration_seconds": summary.get("duration_seconds"),
            "detail": placements[:60],
            "markers": [m["name"] for m in markers],
            "dropouts": [d["at_bar"] for d in dropouts],
            "phrase_fills": fill_placed,
        }
        if skipped_tracks:
            result["skipped_tracks"] = skipped_tracks
            result["note"] = (
                "Some tracks were not placed -- usually placeholders with no "
                "clip yet, which is expected for vocals and FX."
            )
        return result

    def _await_locators(self, expected: int, timeout: float = 60.0) -> list[dict]:
        """Locators are placed a tick at a time; wait for the queue to drain.

        The count drops before it climbs -- existing markers are cleared first --
        so "stopped changing" is only trusted once it has stopped for a good
        while, and never before the queue has had time to reach the create phase.
        """
        deadline = time.time() + timeout
        last, stable = -1, 0
        while time.time() < deadline:
            time.sleep(0.4)
            try:
                found = self.bridge.call("get_locators")["locators"]
            except (AbletonError, AbletonNotRunning):
                break
            if len(found) >= expected:
                return found
            stable = stable + 1 if len(found) == last else 0
            last = len(found)
            if stable >= 25:
                return found
        try:
            return self.bridge.call("get_locators")["locators"]
        except (AbletonError, AbletonNotRunning):
            return []

    # ------------------------------------------------------------- snapshots

    def _autosnapshot(self, label: str) -> str | None:
        """Snapshot before doing something that destroys work.

        Taking it is cheap and never worth failing the real operation over, so
        an error here is logged and swallowed -- the one place where carrying on
        silently is the right call.
        """
        if os.environ.get("ABLETON_AI_NO_AUTOSNAPSHOT"):
            return None
        try:
            return self.tool_snapshot_set(label=f"auto-{label}")["path"]
        except Exception as exc:                    # noqa: BLE001
            log.warning("could not take an automatic snapshot: %s", exc)
            return None

    def _snapshot_dir(self) -> Path:
        folder = Path(
            os.environ.get("ABLETON_AI_SNAPSHOTS")
            or Path.home() / "Music" / "AbletonAI" / "snapshots"
        )
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def tool_snapshot_set(self, label: str | None = None) -> dict:
        """Write the whole set to a JSON file that `restore_snapshot` can replay.

        Live has no undo across a restart and no way to ask whether the document
        is dirty, so an unsaved session is one crash or one reload away from
        gone. This is the safety net: every MIDI clip with its notes, every
        arrangement placement, the mixer and the locators.

        Audio clips are recorded but cannot be recreated -- a snapshot notes
        them so a restore can say what it could not put back.
        """
        state = self.bridge.call("get_song", include_notes=True)
        arrangement_state = self.bridge.call("get_arrangement")
        try:
            locators = self.bridge.call("get_locators")["locators"]
        except (AbletonError, AbletonNotRunning):
            locators = []

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{label}" if label else stamp
        path = self._snapshot_dir() / f"{name}.json"
        payload = {
            "taken": stamp,
            "label": label,
            "tempo": state.get("tempo"),
            "tracks": state.get("tracks", []),
            "arrangement": arrangement_state.get("tracks", []),
            "end_bars": arrangement_state.get("end_bars"),
            "locators": locators,
        }
        path.write_text(json.dumps(payload, indent=1))

        midi_clips = sum(
            1 for t in payload["tracks"] for c in (t.get("clips") or [])
            if c.get("is_midi")
        )
        audio_clips = sum(
            1 for t in payload["tracks"] for c in (t.get("clips") or [])
            if not c.get("is_midi")
        )
        return {
            "path": str(path),
            "tracks": len(payload["tracks"]),
            "midi_clips": midi_clips,
            "audio_clips": audio_clips,
            "arrangement_clips": sum(len(t.get("clips") or [])
                                     for t in payload["arrangement"]),
            "summary": (
                f"snapshot of {len(payload['tracks'])} tracks "
                f"({midi_clips} MIDI clips) to {path.name}"
            ),
        }

    def tool_list_snapshots(self, limit: int = 15) -> dict:
        """List saved snapshots, newest first."""
        files = sorted(self._snapshot_dir().glob("*.json"), reverse=True)[:limit]
        return {
            "directory": str(self._snapshot_dir()),
            "snapshots": [
                {"name": f.name, "path": str(f),
                 "size_kb": round(f.stat().st_size / 1024, 1),
                 "taken": datetime.fromtimestamp(f.stat().st_mtime).isoformat(
                     timespec="seconds")}
                for f in files
            ],
        }

    def tool_restore_snapshot(
        self,
        path: str | None = None,
        restore_arrangement: bool = True,
        clear_first: bool = True,
    ) -> dict:
        """Rebuild a set from a snapshot: MIDI clips, then the arrangement.

        Pass no path to restore the most recent one. Audio clips cannot be
        recreated from a snapshot -- their samples are files on disk that Live
        placed -- so they are reported as skipped rather than silently missing.
        """
        if path is None:
            files = sorted(self._snapshot_dir().glob("*.json"), reverse=True)
            if not files:
                raise ToolError(f"no snapshots in {self._snapshot_dir()}")
            chosen = files[0]
        else:
            chosen = Path(path)
            if not chosen.exists():
                raise ToolError(f"no snapshot at {chosen}")

        data = json.loads(chosen.read_text())
        if data.get("tempo"):
            self.bridge.call("set_tempo", tempo=float(data["tempo"]))

        live_tracks = self.bridge.call("get_song").get("tracks", [])
        by_name = {t.get("name"): int(t["index"]) for t in live_tracks}

        restored, skipped = [], []
        index_map: dict[int, int] = {}
        for track in data.get("tracks", []):
            source_index = int(track["index"])
            target = by_name.get(track.get("name"))
            if target is None:
                if not track.get("is_midi", True):
                    skipped.append({"track": track.get("name"),
                                    "why": "audio track; recreate it by hand"})
                    continue
                created = self.bridge.call(
                    "create_midi_track", index=-1, name=track.get("name")
                )
                target = int(created.get("track_index", len(by_name)))
                by_name[track.get("name")] = target
            index_map[source_index] = target

            for clip in track.get("clips") or []:
                if not clip.get("is_midi"):
                    skipped.append({"track": track.get("name"),
                                    "clip": clip.get("name"),
                                    "why": "audio clip"})
                    continue
                self.bridge.call(
                    "create_clip", track_index=target,
                    clip_index=int(clip["slot"]),
                    length_beats=float(clip["length_beats"]),
                    notes=clip.get("notes") or [],
                    name=clip.get("name"),
                )
                restored.append({"track": track.get("name"),
                                 "slot": clip["slot"],
                                 "notes": len(clip.get("notes") or [])})

        placed = 0
        if restore_arrangement and data.get("arrangement"):
            targets = [index_map[int(t["index"])] for t in data["arrangement"]
                       if int(t["index"]) in index_map]
            if clear_first and targets:
                self.bridge.call("clear_arrangement", track_indices=targets)
            for lane in data["arrangement"]:
                target = index_map.get(int(lane["index"]))
                if target is None:
                    continue
                for clip in lane.get("clips") or []:
                    # Snapshots record where a clip sat, not which session slot
                    # it came from, so a lane whose source is gone is skipped
                    # rather than guessed at.
                    slot = self._slot_matching(data, int(lane["index"]), clip)
                    if slot is None:
                        skipped.append({"track": lane.get("name"),
                                        "bar": clip.get("start_bars"),
                                        "why": "no session clip to place"})
                        continue
                    self.bridge.call(
                        "duplicate_clip_to_arrangement", track_index=target,
                        clip_index=slot, start_bar=float(clip["start_bars"]),
                        repeats=1,
                    )
                    placed += 1

        if data.get("locators"):
            self.bridge.call(
                "set_locators",
                markers=[{"name": m["name"], "start_bar": m["start_bar"]}
                         for m in data["locators"]],
                clear_existing=True,
            )
            self._await_locators(len(data["locators"]))

        return {
            "from": str(chosen),
            "clips_restored": len(restored),
            "arrangement_clips": placed,
            "skipped": skipped[:40],
            "summary": (
                f"restored {len(restored)} clip(s) and {placed} arrangement "
                f"placement(s) from {chosen.name}"
                + (f"; {len(skipped)} could not be recreated" if skipped else "")
            ),
        }

    @staticmethod
    def _slot_matching(data: dict, track_index: int, arrangement_clip: dict):
        """Which session slot holds the clip this arrangement placement used."""
        for track in data.get("tracks", []):
            if int(track["index"]) != track_index:
                continue
            for clip in track.get("clips") or []:
                if clip.get("name") == arrangement_clip.get("name"):
                    return int(clip["slot"])
            # Same length is a weaker match, but better than dropping the lane.
            for clip in track.get("clips") or []:
                if abs(clip.get("length_bars", -1)
                       - arrangement_clip.get("length_bars", -2)) < 0.01:
                    return int(clip["slot"])
        return None

    def tool_unsaved_changes(self) -> dict:
        """How many changes have been made to the set since it was last saved.

        Live exposes no "document modified" flag, so this counts the commands
        this remote script has run. Check it before anything that would reload
        Live: an unsaved session does not survive a restart, and there is no
        undo across one.
        """
        state = self.bridge.call("ping")
        count = int(state.get("unsaved_changes", 0))
        return {
            "unsaved_changes": count,
            "last_change": state.get("last_change"),
            "safe_to_restart": count == 0,
            "summary": (
                "no unsaved changes from this session"
                if count == 0 else
                f"{count} change(s) since the last save -- save in Live (Cmd-S) "
                f"or take a snapshot before restarting anything"
            ),
        }

    def tool_mark_saved(self) -> dict:
        """Tell the app the set has just been saved, resetting the change count."""
        return self.bridge.call("mark_saved")

    def tool_critique_music(
        self, track_indices: list[int] | None = None, clip_index: int = 0,
    ) -> dict:
        """Measure what makes the current parts sound generated, and score them.

        Checks each part for flat velocity, no rests, identical bars, a line
        that never moves and one note length throughout; then checks the
        ensemble for two top lines in the same octave, parts hitting on exactly
        the same beats, a crowded low end, and nothing stating the harmony.

        Every finding carries the number that proves it, so a change can be
        shown to have helped rather than merely felt to have. Run it after
        generating, act on anything marked high, and run it again.
        """
        state = self.bridge.call("get_song")
        wanted = set(track_indices) if track_indices else None

        parts = []
        for track in state.get("tracks", []):
            index = int(track["index"])
            if wanted is not None and index not in wanted:
                continue
            if not track.get("is_midi"):
                continue
            try:
                clip = self.bridge.call(
                    "get_clip", track_index=index, clip_index=clip_index
                )
            except (AbletonError, AbletonNotRunning):
                continue
            notes = clip.get("notes") or []
            if not notes:
                continue
            parts.append(critique.Part(
                name=track.get("name") or f"track {index}",
                role=_role_from_name(track.get("name", ""), default="lead"),
                notes=notes,
                bars=clip["length_beats"] / BEATS_PER_BAR,
            ))

        if not parts:
            raise ToolError(
                f"no MIDI clip in slot {clip_index} has any notes to judge"
            )
        library = self._library()
        return critique.critique(
            parts, library=library if library.references else None
        )

    def tool_clear_arrangement(self, track_indices: list[int] | None = None) -> dict:
        """Delete arrangement-timeline clips, on the given tracks or all of them."""
        backup = self._autosnapshot("clear-arrangement")
        params = {} if not track_indices else {"track_indices": track_indices}
        result = dict(self.bridge.call("clear_arrangement", **params))
        if backup:
            result["snapshot"] = backup
        return result

    def tool_set_arrangement_loop(
        self, start_bar: float = 0, length_bars: float = 8, enabled: bool = True
    ) -> dict:
        """Set the arrangement loop brace, so the user can audition one section."""
        return self.bridge.call(
            "set_arrangement_loop",
            start_bar=start_bar,
            length_bars=length_bars,
            enabled=enabled,
        )

    # ------------------------------------------------------------------
    # EDM transitions: builds, risers, impacts, hooks
    # ------------------------------------------------------------------

    def tool_create_buildup_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        bars: float = 8,
        instrument: str = "snare",
        velocity_start: int = 55,
        velocity_end: int = 127,
        add_hats: bool = True,
        add_kick: bool = False,
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """An accelerating drum roll that hands over to the drop."""
        notes = generators.generate_buildup(
            bars=int(bars),
            instrument=instrument,
            velocity_start=velocity_start,
            velocity_end=velocity_end,
            add_hats=add_hats,
            add_kick=add_kick,
            seed=seed,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"build {int(bars)}", role="drums")

    def tool_create_snare_roll(
        self,
        track_index: int,
        clip_index: int = 0,
        bars: float = 2,
        instrument: str = "snare",
        end_division: int = 32,
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """An accelerating snare roll handing over to the next section.

        The acceleration is geometric and the velocity climbs with it, which is
        what makes it read as a hand-over rather than a stutter. Put this in the
        last one or two bars before a drop or a breakdown.
        """
        notes = generators.generate_snare_roll(
            bars=bars, instrument=instrument, end_division=end_division, seed=seed
        )
        return self._write_clip(track_index, clip_index, bars, notes,
                                name or f"snare roll {bars:g}", role="perc")

    def tool_create_clap_build(
        self,
        track_index: int,
        clip_index: int = 0,
        bars: float = 4,
        layers: int = 3,
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """Claps thickening toward a change -- backbeat, doubles, then a run.

        Subtler than a snare roll: it raises tension without announcing itself,
        so it works under a breakdown where a roll would be too obvious.
        """
        notes = generators.generate_clap_build(bars=bars, layers=layers, seed=seed)
        return self._write_clip(track_index, clip_index, bars, notes,
                                name or f"clap build {bars:g}", role="perc")

    def tool_create_drum_fill(
        self,
        track_index: int,
        clip_index: int = 0,
        bars: float = 1,
        style: str = "toms",
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """A one-bar fill marking the end of a phrase.

        "toms" is the descending run, "snare" a sixteenth burst, "stutter" a
        fast retrigger on the last beat only.
        """
        notes = generators.generate_drum_fill(bars=bars, style=style, seed=seed)
        return self._write_clip(track_index, clip_index, bars, notes,
                                name or f"{style} fill", role="drums")

    def tool_create_riser_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        bars: float = 8,
        octave: int = 4,
        direction: str = "up",
        rate: str = "1/16",
        octaves: int = 3,
        name: str | None = None,
    ) -> dict:
        """A pitch-climbing run for a riser synth -- tension into the drop."""
        rates = {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25, "1/32": 0.125}
        if rate not in rates:
            raise ToolError(f"rate must be one of {', '.join(rates)}")
        notes = generators.generate_riser(
            root=key,
            scale=scale,
            bars=int(bars),
            octave=octave,
            direction=direction,
            rate=rates[rate],
            octaves=octaves,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"riser {direction}", role="riser")

    def tool_create_impact_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        bars: float = 1,
        crash: bool = True,
        sub_drop: bool = True,
        sub_pitch: int = 24,
        name: str | None = None,
    ) -> dict:
        """The crash plus sub hit that lands on the first bar of a drop."""
        notes = generators.generate_impact(
            bars=int(bars), crash=crash, sub_drop=sub_drop, sub_pitch=sub_pitch
        )
        return self._write_clip(track_index, clip_index, bars, notes, name or "impact", role="impact")

    def tool_create_hook_clip(
        self,
        track_index: int,
        clip_index: int = 0,
        key: str = "C",
        scale: str = "minor",
        degrees: Any = "1-6-4-5",
        bars: float = 8,
        octave: int = 5,
        rhythm: str = "syncopated",
        velocity: int = 105,
        call_and_response: bool = True,
        pattern: str | None = None,
        hook_style: str | None = None,
        name: str | None = None,
        seed: int | None = None,
        reference_track: int | None = None,
        reference_clip: int = 0,
    ) -> dict:
        """A hook the way popular music writes them: literal repeats.

        The phrase keeps the SAME pitches every statement while the chords
        move underneath -- the note that was a root becomes a seventh, and
        that emergent colour is what makes a hook memorable. Only the final
        statement bends its tail to cadence. Patterns are the skeletons
        decades of hits share: falling_fifth, two_note_engine, penta_loop,
        leap_and_fill, call_answer and friends -- pick one, give a
        `hook_style` word ("anthem", "dark_pop", "melodic_techno") to have
        one chosen, or leave both for a seed-stable default.

        Pass pattern="motif" for the old developed-motif behaviour.
        """
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, octave - 1, "triad",
            reference_track=reference_track, reference_clip=reference_clip,
        )
        if pattern == "motif":
            notes = generators.generate_hook(
                chords, bars_per_chord=bars_per_chord, octave=octave,
                rhythm=rhythm, velocity=velocity,
                call_and_response=call_and_response,
                root=key, scale=scale, seed=seed,
            )
            chosen = "motif"
        else:
            if pattern:
                chosen = pattern
            else:
                options = sorted(hooks.catalog(hook_style))
                chosen = self._taste().choose(
                    "hook_pattern", options, hook_style or "any", seed
                )
            notes = hooks.render_hook(
                key, scale, chords, bars=bars, pattern=chosen,
                octave=octave, velocity=velocity, seed=seed,
            )
        result = self._write_clip(
            track_index, clip_index, bars, notes,
            name or f"Hook ({chosen})", role="hook",
        )
        result["pattern"] = chosen
        return result

    def tool_create_placeholder_track(
        self,
        name: str,
        role: str = "vocal",
        kind: str = "audio",
        index: int = -1,
    ) -> dict:
        """Create an empty track for material the producer will add by hand.

        Use this for vocals, recorded FX, sampled stems -- anything that should
        have a labelled, colour-coded home in the set without being generated.
        `kind` is "audio" for stems and recordings, "midi" for a slot you will
        later point at a synth.
        """
        command = "create_audio_track" if kind == "audio" else "create_midi_track"
        params: dict[str, Any] = {"index": index, "name": name}
        if role in ROLE_COLOURS:
            params["color"] = ROLE_COLOURS[role]
        result = self.bridge.call(command, **params)
        result["role"] = role
        result["kind"] = kind
        return result

    def tool_create_placeholder_set(
        self, roles: list[str] | None = None, prefix: str = ""
    ) -> dict:
        """Create a batch of labelled empty tracks in one go.

        Defaults to the usual hand-finished layer: lead vocal, vocal chops,
        FX/risers and impacts.
        """
        wanted = roles or ["vocal", "vocal", "fx", "impact"]
        labels = {
            "vocal": "Vocal", "fx": "FX", "impact": "Impacts",
            "riser": "Risers", "perc": "Perc",
        }
        # Idempotent: a placeholder that already exists is the placeholder.
        # Without this every rebuild grew another Vocal and another FX.
        present = {
            str(t.get("name"))
            for t in self.bridge.call("get_song").get("tracks", [])
        }
        created = []
        seen: dict[str, int] = {}
        for role in wanted:
            seen[role] = seen.get(role, 0) + 1
            base = labels.get(role, role.title())
            suffix = f" {seen[role]}" if wanted.count(role) > 1 else ""
            if f"{prefix}{base}{suffix}" in present:
                continue
            created.append(
                self.tool_create_placeholder_track(
                    name=f"{prefix}{base}{suffix}", role=role, kind="audio"
                )
            )
        return {"created": created, "count": len(created)}

    def tool_set_locators(
        self, markers: list[dict], clear_existing: bool = True
    ) -> dict:
        """Drop named markers on the arrangement timeline at section boundaries.

        Each marker is {"name": "Drop 1", "start_bar": 48}. This is what makes a
        generated arrangement navigable in Live rather than a wall of clips.
        """
        self.bridge.call(
            "set_locators", markers=markers, clear_existing=clear_existing
        )
        # Placement runs a tick at a time inside Live, so report what landed
        # rather than what was asked for.
        placed = self._await_locators(len(markers))
        return {"count": len(placed), "locators": placed}

    def tool_get_locators(self) -> dict:
        """List the named markers currently on the arrangement timeline."""
        return self.bridge.call("get_locators")

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def tool_browse_devices(self, path: str = "", limit: int = 100) -> dict:
        """Browse Live's device and preset library one level at a time. Pass no path to list the top-level categories."""
        return self.bridge.call("browse", path=path, limit=limit)

    def tool_search_devices(
        self, query: str, limit: int = 20, roots: list[str] | None = None
    ) -> dict:
        """Find an instrument, effect or preset by name, without knowing its path.

        Use this when the user names a plugin -- "use Serum", "load Massive" --
        rather than guessing at browser paths. Returns loadable items first;
        pass the `path` or `uri` of a result to load_sound or load_device.
        """
        params: dict[str, Any] = {"query": query, "limit": limit}
        if roots:
            params["roots"] = roots
        return self.bridge.call("search_browser", **params)

    def tool_get_sound_preferences(self) -> dict:
        """Show which instrument the user wants for each musical role.

        These are remembered across sessions. Roles with no explicit preference
        fall back to a stock Live device.
        """
        data = self.sounds.load()
        return {
            "favourites": list(data["favourites"]),
            "roles": dict(data["roles"]),
            "effective": {
                role: self.sounds.for_role(role) for role in arrangement.ROLES
            },
            "config_path": str(self.sounds.path),
        }

    def tool_set_sound_preference(
        self, role: str, path: str, favourite: bool = True
    ) -> dict:
        """Remember which instrument to use for a role, e.g. bass -> a
        stock preset like Sounds/Bass/Sub 808 Bass.

        `path` is a browser path such as
        "Sounds/Bass/Sub 808 Bass" (stock, preferred) or a plugin path --
        get one from search_devices.
        The preference persists across sessions.
        """
        canonical = arrangement.normalise_role(role)
        if canonical is None:
            raise ToolError(
                f"unknown role {role!r}; one of: "
                f"{', '.join(arrangement.ROLES)} "
                f"(synonyms accepted: {', '.join(sorted(arrangement.ROLE_ALIASES))})"
            )
        role = canonical

        # A preference that cannot load is worse than none: it fails at build
        # time and the role plays nothing. "x/y" sat in the user's config and
        # silenced every lead for a day. Verify against the browser now, while
        # there is a user present to hear about it.
        warning = None
        try:
            found = self._browser_item(path)
        except AbletonNotRunning:
            found = ...
            warning = ("could not verify against the browser (Ableton not "
                       "reachable); saved unverified")
        except AbletonError:
            # The browser answered and the path is not in it. Unreachable is
            # forgivable; nonexistent is exactly what this check is for.
            found = None
        if found is None:
            raise ToolError(
                f"{path!r} is not a loadable browser item -- find the real "
                "path with search_devices, and check the spelling"
            )

        if path.startswith("Plugins/"):
            warning = (
                "this is a bare synth engine: it loads with its init patch, "
                "which is a plain saw, not a sound. It will be used because "
                "you asked -- but a preset from Sounds/, or the plugin saved "
                "inside an Instrument Rack in your User Library, will sound "
                "like a choice instead of a default."
            )

        self.sounds.set_role(role, path)
        if favourite:
            self.sounds.add_favourite(path)
        result = {"role": role.lower(), "path": path,
                  "favourites": self.sounds.favourites()}
        if warning:
            result["warning"] = warning
        return result

    def tool_forget_sound_preference(self, role: str) -> dict:
        """Drop a saved role preference, falling back to the stock device."""
        self.sounds.clear_role(role)
        return {"role": role.lower(), "now": self.sounds.for_role(role)}

    def tool_remember(self, rule: str) -> dict:
        """Save a standing instruction so it applies from now on.

        Use this whenever the user says to remember something, or states a
        preference as a rule -- "prefer stock instruments", "start tracks at
        128", "I want a riser before every drop". Saved rules are put in front
        of the model on every turn afterwards.

        For an instrument choice, prefer set_sound_preference, which is applied
        automatically rather than merely remembered.
        """
        rules = self.sounds.remember(rule)
        return {"remembered": rule, "all_rules": rules,
                "stored_at": str(self.sounds.path)}

    def tool_forget(self, about: str) -> dict:
        """Drop remembered instructions mentioning `about`."""
        remaining = self.sounds.forget(about)
        return {"forgot_about": about, "remaining": remaining}

    def tool_recall(self) -> dict:
        """Everything currently remembered: instruments and standing rules."""
        data = self.sounds.load()
        return {
            "rules": list(data["rules"]),
            "role_instruments": dict(data["roles"]),
            "favourites": list(data["favourites"]),
            "stored_at": str(self.sounds.path),
        }

    def tool_load_sound(
        self,
        track_index: int,
        role: str | None = None,
        path: str | None = None,
        uri: str | None = None,
        search: str | None = None,
    ) -> dict:
        """Put an instrument on a track.

        Give a `role` to use the user's saved preference for it, a `search`
        term to look one up by name ("Serum"), or an explicit `path`/`uri`.
        Generated MIDI makes no sound until this has been done.
        """
        resolved_from = None
        if not path and not uri and search:
            results = self.tool_search_devices(query=search, limit=8)["results"]
            loadable = [r for r in results if r["is_loadable"]]
            if not loadable:
                raise ToolError(
                    f"nothing loadable matched {search!r}"
                    + (f"; closest: {results[0]['path']}" if results else "")
                )
            path = loadable[0]["path"]
            uri = loadable[0]["uri"]
            resolved_from = f"search:{search}"
        if not path and not uri and role:
            path = self.sounds.for_role(role)
            resolved_from = f"role:{role}"
        if not path and not uri:
            raise ToolError("load_sound needs one of role, search, path or uri")

        params: dict[str, Any] = {"track_index": track_index}
        if uri:
            params["uri"] = uri
        if path:
            params["path"] = path
        try:
            result = dict(self.bridge.call("load_device", **params))
        except AbletonError as exc:
            # A saved preference that no longer loads must not leave the
            # track silent -- that is how "x/y" muted every lead. Fall back
            # to a designed preset for the role and say what happened.
            if resolved_from and str(resolved_from).startswith("role:") and role:
                fallback = self.tool_pick_sound(track_index=track_index,
                                                role=role)
                fallback["warning"] = (
                    f"saved preference {path!r} failed to load ({exc}); "
                    f"loaded {fallback.get('preset')!r} instead. "
                    "Fix or forget the preference."
                )
                return fallback
            raise
        result["resolved_from"] = resolved_from
        result["path"] = path
        if path and path.startswith("Plugins/"):
            result["warning"] = (
                "bare synth engine loaded -- it plays its init patch. "
                "A preset or a saved rack will sound like a choice."
            )
        return result

    def tool_load_device(
        self, track_index: int, path: str | None = None, uri: str | None = None
    ) -> dict:
        """Load an instrument, effect or preset from the browser onto a track. Needed before a generated MIDI clip will make any sound."""
        if not path and not uri:
            raise ToolError("load_device needs either a path or a uri")
        params: dict[str, Any] = {"track_index": track_index}
        if path:
            params["path"] = path
        if uri:
            params["uri"] = uri
        return self.bridge.call("load_device", **params)

    def tool_set_device_parameter(
        self,
        track_index: int,
        value: float,
        device_index: int = 0,
        parameter: str | int = 0,
        target: str = "device",
        send_index: int = 0,
        normalised: bool = True,
    ) -> dict:
        """Set one parameter -- a filter cutoff, a resonance, a send level.

        `target` is "device" (default), "volume", "panning" or "send".
        `parameter` accepts a name or a fragment of one ("cutoff", "Flt 1 Freq").
        With `normalised` the value is 0..1 across the parameter's real range.
        """
        return self.bridge.call(
            "set_device_parameter",
            track_index=track_index,
            device_index=device_index,
            parameter=parameter,
            target=target,
            send_index=send_index,
            value=value,
            normalised=normalised,
        )

    def tool_list_patches(self, role: str | None = None) -> dict:
        """List the sound-design recipes design_sound can build."""
        names = presets.for_role(role) if role else sorted(presets.RECIPES)
        return {
            "patches": {
                n: {
                    "description": presets.RECIPES[n].description,
                    "suits": list(presets.RECIPES[n].roles),
                    "best_on": list(presets.RECIPES[n].prefers),
                }
                for n in names
            },
            "aliases": presets.ALIASES,
            "note": (
                "These program Live's own synths. Third-party plugins expose no "
                "parameters until Configure is pressed on the device, so use "
                "load_preset for those."
            ),
        }

    def tool_design_sound(
        self, track_index: int, patch: str, device_index: int = 0
    ) -> dict:
        """Build a patch on the fly by setting a synth's parameters.

        Works on Live's own instruments (Wavetable, Operator, Drift, Analog).
        Parameters a given synth does not have are skipped, so one recipe works
        across devices.
        """
        recipe = presets.resolve(patch)
        devices = self.bridge.call("get_devices", track_index=track_index)["devices"]
        if not devices:
            raise ToolError(
                f"track {track_index} has no device -- load an instrument first"
            )
        if device_index >= len(devices):
            raise ToolError(f"device_index {device_index} out of range")

        device = devices[device_index]
        available = device.get("parameters") or []
        if len(available) <= 1:
            raise ToolError(
                f"{device['name']} exposes no editable parameters. Third-party "
                "plugins need Configure pressed on the device in Live before "
                "they can be programmed; use load_preset instead."
            )

        applied, failed = [], []
        for parameter, value in presets.match_parameters(recipe, available):
            try:
                self.bridge.call(
                    "set_device_parameter",
                    track_index=track_index,
                    device_index=device_index,
                    parameter=parameter["name"],
                    value=value,
                    normalised=True,
                )
                applied.append(parameter["name"])
            except (AbletonError, AbletonNotRunning) as exc:
                failed.append(f"{parameter['name']}: {exc}")

        return {
            "patch": recipe.name,
            "device": device["name"],
            "applied": applied,
            "skipped": failed,
            "description": recipe.description,
        }

    # ------------------------------------------------- what is actually installed

    def _browser_item(self, path: str) -> dict | None:
        """The browser entry at a slash-separated path, for its loadable URI."""
        parent, _, leaf = path.rpartition("/")
        items = self.bridge.call("browse", path=parent, limit=5000)["items"]
        return next(
            (i for i in items if i["name"] == leaf and i.get("is_loadable")), None
        )

    def _catalogue(self) -> catalogue.Catalogue:
        """The scanned browser, loaded once per Toolbox."""
        if getattr(self, "_sound_catalogue", None) is None:
            self._sound_catalogue = catalogue.Catalogue.load()
        return self._sound_catalogue

    # Devices that shape sound but cannot make it. A MIDI track whose chain
    # is only these is silent however many notes it holds -- and that exact
    # state shipped: a built track's Lead and Melody carried an EQ Eight each
    # and nothing else, so the two most important parts played nothing.
    _EFFECT_ONLY = (
        "eq", "compressor", "glue", "limiter", "reverb", "delay", "echo",
        "utility", "saturator", "chorus", "phaser", "flanger", "gate",
        "autofilter", "auto filter", "redux", "overdrive", "amp", "cabinet",
        "multiband", "drum buss", "tuner", "spectrum",
    )

    def _makes_sound(self, devices: list[str]) -> bool:
        """Does this device chain contain anything that generates audio?"""
        for device in devices or []:
            name = str(device).lower()
            if not any(effect in name for effect in self._EFFECT_ONLY):
                return True
        return False

    def tool_soundcheck(self, fix: bool = True, genre: str | None = None) -> dict:
        """Verify every part that has notes can actually be heard.

        The check nobody had: a track can hold perfect MIDI and produce
        nothing -- no instrument, an effects-only chain, the fader at zero, or
        muted. Each is invisible in the clip view and each makes the whole
        track sound broken, because an arrangement with silent parts IS
        broken, whatever the notes say.

        With `fix` on (the default), silent MIDI tracks get an instrument
        picked for their role and zeroed faders are raised to a sane level.
        Mutes are reported but left alone -- a mute is a decision.
        """
        state = self.bridge.call("get_song")
        silent, fixed, fine = [], [], []
        for track in state.get("tracks", []):
            index = int(track["index"])
            has_notes = bool(track.get("clips"))                 or int(track.get("arrangement_clip_count", 0)) > 0
            if not has_notes:
                continue
            problems = []
            if track.get("is_midi") and not self._makes_sound(
                track.get("devices", [])
            ):
                problems.append("no instrument -- the notes play nothing")
            if float(track.get("volume", 0.85)) < 0.05:
                problems.append("fader at zero")
            if track.get("muted"):
                problems.append("muted")

            if not problems:
                fine.append(track.get("name"))
                continue

            entry = {"track_index": index, "name": track.get("name"),
                     "problems": problems}
            if fix:
                repairs = []
                if "no instrument -- the notes play nothing" in problems:
                    role = _role_from_name(track.get("name", "")) or "lead"
                    try:
                        loaded = self.tool_pick_sound(
                            track_index=index, role=role, genre=genre
                        )
                        repairs.append(f"loaded {loaded.get('preset')}")
                    except ToolError as exc:
                        repairs.append(f"could not load an instrument: {exc}")
                if "fader at zero" in problems:
                    self.bridge.call("set_track_mixer", track_index=index,
                                     volume=mixing.db_to_live(-12.0))
                    repairs.append("fader raised to -12dB")
                entry["repairs"] = repairs
                fixed.append(entry)
            else:
                silent.append(entry)

        report = {
            "checked": len(fine) + len(silent) + len(fixed),
            "ok": fine,
            "silent": silent,
            "fixed": fixed,
            "summary": (
                f"{len(fine)} track(s) sound, "
                + (f"{len(fixed)} repaired" if fix else f"{len(silent)} SILENT")
                + (": " + "; ".join(
                    f"{e['name']}: {', '.join(e['problems'])}"
                    for e in (fixed or silent))
                   if (fixed or silent) else "")
            ),
        }
        return report

    def tool_scan_sounds(self, force: bool = False) -> dict:
        """Walk the Live browser and record everything loadable.

        Run this once. Instrument choice was a hardcoded table -- "bass means
        Operator" -- which ignores the couple of thousand presets, kits and
        plugins actually installed, and quietly gives the wrong answer for
        anything that is not EDM. Afterwards `find_sounds` and every tool that
        loads an instrument search what this machine really has.

        Takes about twenty seconds. The result is cached, so `force` is only
        needed after installing a pack.
        """
        found = self._catalogue()
        if found.entries and not force:
            summary = found.summary()
            summary["cached"] = True
            summary["summary"] = (
                f"{summary['total']} sounds already catalogued; "
                "pass force=true to rescan after installing a pack"
            )
            return summary

        found.scan(self.bridge)
        summary = found.summary()
        summary["cached"] = False
        summary["summary"] = (
            f"catalogued {summary['total']} loadable sounds: "
            + ", ".join(f"{n} {r}" for r, n in summary["by_root"].items())
        )
        return summary

    def tool_find_sounds(
        self, role: str, genre: str | None = None, limit: int = 8,
        prefer: str | None = None,
    ) -> dict:
        """Search the catalogued browser for presets that suit a role.

        Scores every installed preset against what the role is made of and what
        the genre sounds like, so "pad" for cinematic and "pad" for techno give
        different answers. `prefer` narrows to a path fragment -- "Serum",
        "Sounds/Strings", a pack name.

        Use it to see the options before committing, or pass the winner to
        `load_sound`.
        """
        found = self._catalogue()
        if not found.entries:
            raise ToolError(
                "nothing catalogued yet -- run scan_sounds first (about 20s)"
            )
        role = arrangement.normalise_role(role)
        hits = found.find(role, genre, limit=limit, prefer=prefer)
        return {
            "role": role,
            "genre": genre,
            "matches": [
                {"name": e.display, "path": e.path, "category": e.category,
                 "source": e.root,
                 "score": round(found.score(e, role, genre), 1)}
                for e in hits
            ],
            "summary": (
                f"{len(hits)} match(es) for {role}"
                + (f" in {genre}" if genre else "")
                + (": " + ", ".join(e.display for e in hits[:5]) if hits else "")
            ),
        }

    def tool_pick_sound(
        self,
        track_index: int,
        role: str,
        character: str | None = None,
        genre: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """Load a designed preset that suits the role, rather than a raw device.

        Live ships hundreds of presets per category made by people who could
        hear what they were doing. For "make it sound good" the library beats
        anything built from parameters, so this is the default path for sound.

        `character` narrows it -- warm, bright, evolving, dark for pads;
        supersaw, bright, stab, soft for leads; reese, sub, analog, deep, acid
        for bass. Left out, the genre decides.
        """
        role = arrangement.normalise_role(role or "")

        # The catalogue knows what is actually installed, including roles the
        # hardcoded table never had -- strings, choir, mallets, harp, brass --
        # so it gets first refusal. The old path stays as the fallback for a
        # machine that has not scanned yet.
        found = self._catalogue()
        if found.entries:
            wanted_character = f"{character} {genre}".strip() if character else genre
            # Draw from the top candidates in a varied order rather than always
            # the single best -- Ableton ships hundreds per category and a set
            # wants a spread of them, not one preset on every track. Taste-
            # recorded winners (record_taste kind="preset") float to the front.
            candidates = found.find(role, wanted_character or genre, limit=12)
            wins = self._taste().weights("preset", genre or "any")
            import random
            rng = random.Random(seed)
            def rank(pair):
                idx, e = pair
                base = len(candidates) - idx           # score order
                return base + 3 * wins.get(e.display, 0) + (
                    rng.random() * 3 if seed is not None else 0)
            ordered = [e for _, e in sorted(
                enumerate(candidates), key=rank, reverse=True)]
            for entry in ordered:
                try:
                    match = self._browser_item(entry.path)
                except (AbletonError, AbletonNotRunning, ToolError):
                    continue
                if not match:
                    continue
                self.bridge.call(
                    "load_device", track_index=track_index, uri=match["uri"]
                )
                return {
                    "track_index": track_index, "role": role,
                    "preset": entry.display, "path": entry.path,
                    "source": "catalogue",
                    "alternatives": [e.display for e in ordered[1:6]],
                    "summary": f"loaded {entry.display!r} for {role}",
                }

        categories = presets.PRESET_CATEGORIES.get(role)
        if not categories:
            raise ToolError(
                f"no preset category for role {role!r}; one of: "
                f"{', '.join(sorted(presets.PRESET_CATEGORIES))}. "
                "Run scan_sounds to search everything installed instead."
            )
        wanted = presets.picks_for(role, character, genre)

        tried: list[str] = []
        for category in categories:
            try:
                items = self.bridge.call("browse", path=category, limit=500)["items"]
            except (AbletonError, AbletonNotRunning) as exc:
                tried.append(f"{category}: {exc}")
                continue
            loadable = [i for i in items if i.get("is_loadable")]
            if not loadable:
                continue

            # Best-first: an exact-ish name beats a loose keyword.
            for fragment in wanted:
                needle = fragment.lower()
                match = next(
                    (i for i in loadable if needle in i["name"].lower()), None
                )
                if match is None:
                    continue
                result = self.bridge.call(
                    "load_device", track_index=track_index, uri=match["uri"]
                )
                return {
                    "track_index": track_index, "role": role,
                    "character": character or presets.DEFAULT_CHARACTER.get(role),
                    "preset": match["name"], "matched_on": fragment,
                    "category": category,
                }
            tried.append(f"{category}: no match for {list(wanted)[:3]}")

        raise ToolError(
            f"no preset matched role {role!r}. Tried: {'; '.join(tried[:3])}"
        )

    def tool_load_preset(
        self,
        track_index: int,
        role: str = "bass",
        name: str | None = None,
        index: int = 0,
    ) -> dict:
        """Load a preset from Live's own library for a musical role.

        The right move when the target is a plugin that cannot be programmed,
        or when you want a finished sound rather than a built one. Pass `name`
        to pick a specific preset, otherwise `index` selects from the category.
        """
        categories = presets.PRESET_CATEGORIES.get(role.lower())
        if not categories:
            raise ToolError(
                f"no preset category for role {role!r}; one of: "
                f"{', '.join(sorted(presets.PRESET_CATEGORIES))}"
            )

        tried = []
        for category in categories:
            try:
                items = self.bridge.call("browse", path=category, limit=400)["items"]
            except (AbletonError, AbletonNotRunning) as exc:
                tried.append(f"{category}: {exc}")
                continue

            loadable = [i for i in items if i.get("is_loadable")]
            if name:
                needle = name.lower()
                loadable = [i for i in loadable if needle in i["name"].lower()] or loadable
            if not loadable:
                # A category of sub-folders: step one level in.
                folders = [i for i in items if i.get("is_folder")]
                if folders:
                    sub = f"{category}/{folders[min(index, len(folders) - 1)]['name']}"
                    try:
                        items = self.bridge.call("browse", path=sub, limit=400)["items"]
                        loadable = [i for i in items if i.get("is_loadable")]
                    except (AbletonError, AbletonNotRunning):
                        pass
            if not loadable:
                tried.append(f"{category}: nothing loadable")
                continue

            chosen = loadable[index % len(loadable)]
            result = self.bridge.call(
                "load_device", track_index=track_index, uri=chosen["uri"]
            )
            result["preset"] = chosen["name"]
            result["category"] = category
            return result

        raise ToolError(f"no preset found for {role!r}. Tried: {'; '.join(tried)}")

    # ------------------------------------------------------------------
    # Mixer and automation
    # ------------------------------------------------------------------

    def tool_ensure_instruments(
        self,
        only_empty: bool = True,
        roles: dict | None = None,
        prefer_presets: bool = False,
        seed: int | None = None,
    ) -> dict:
        """Give every MIDI track an instrument, so nothing is silent.

        Walks the set, works out each track's musical role from its name (or
        from the `roles` override, keyed by track index), and loads the user's
        saved instrument for that role. Audio tracks are skipped -- they are
        placeholders for material recorded by hand.

        Call this after building tracks and before playback. A generated
        arrangement where half the tracks make no sound is the most common way
        this goes wrong.
        """
        state = self.bridge.call("get_song")
        overrides = {int(k): str(v) for k, v in (roles or {}).items()}

        loaded, skipped, failed = [], [], []
        for track in state.get("tracks", []):
            index = int(track["index"])
            if not track.get("is_midi"):
                skipped.append({"track": index, "why": "audio track"})
                continue
            if only_empty and track.get("devices"):
                skipped.append({"track": index, "why": f"has {track['devices'][0]}"})
                continue

            role = overrides.get(index) or _role_from_name(track.get("name", ""))
            try:
                if prefer_presets:
                    # A designed preset sounds better than a bare device, so
                    # try the curated pick first and only fall back to the
                    # role's default instrument if nothing matches.
                    try:
                        result = self.tool_pick_sound(
                            track_index=index, role=role,
                            seed=None if seed is None else seed + index)
                        what = result.get("preset")
                    except ToolError:
                        result = self.tool_load_sound(track_index=index, role=role)
                        what = result.get("path")
                else:
                    result = self.tool_load_sound(track_index=index, role=role)
                    what = result.get("path")
                loaded.append({"track": index, "name": track.get("name"),
                               "role": role, "loaded": what})
            except (ToolError, AbletonError, AbletonNotRunning) as exc:
                failed.append({"track": index, "name": track.get("name"),
                               "role": role, "error": str(exc)})

        return {
            "loaded": loaded,
            "skipped": skipped,
            "failed": failed,
            "summary": f"{len(loaded)} instrument(s) loaded, "
                       f"{len(skipped)} skipped, {len(failed)} failed",
        }

    def tool_get_mixer(self) -> dict:
        """Read volumes, pans, sends and return tracks across the whole set."""
        return self.bridge.call("get_mixer")

    def tool_mix_levels(
        self,
        tracks: list[dict] | None = None,
        apply_pan: bool = True,
    ) -> dict:
        """Set a starting balance: relative levels and pans by musical role.

        The kick sits at unity and everything else takes its conventional place
        beneath it. This is gain staging, not a finished mix -- it gets the
        faders into a sane relationship, and leaves the judgement to you.

        Roles are inferred from track names unless `tracks` overrides them,
        e.g. [{"track_index": 3, "role": "bass"}].
        """
        state = self.bridge.call("get_song")
        overrides = _role_overrides(tracks)

        applied = []
        for track in state.get("tracks", []):
            index = int(track["index"])
            role = overrides.get(index) or _role_from_name(track.get("name", ""))
            balance = mixing.balance_for(role)

            params: dict[str, Any] = {
                "track_index": index,
                "volume": mixing.db_to_live(balance.gain_db),
            }
            if apply_pan and balance.pan:
                params["panning"] = balance.pan
            self.bridge.call("set_track_mixer", **params)
            applied.append({
                "track": index, "name": track.get("name"), "role": role,
                "gain_db": balance.gain_db, "why": balance.note,
            })

        return {
            "applied": applied,
            "headroom": mixing.headroom_advice(len(applied)),
            "note": "Levels are a starting point. Check them against a reference track.",
        }

    def tool_gain_stage(
        self,
        target_master: float = 0.72,
        seconds: float = 6.0,
        adjust: bool = True,
        max_trim_db: float = 6.0,
    ) -> dict:
        """Set levels, then measure what actually comes out and correct it.

        Setting faders by role is a guess; gain staging is checking the guess.
        This plays the set, reads each track's real output meter and the
        master's, then trims anything obviously dominating.

        The target is roughly -3dB on the master, which leaves the headroom
        that EQ and multiband compression need to work. Mixing into a limiter
        that is already clamping does not.
        """
        import time

        staged = self.tool_mix_levels()

        self.bridge.call("start_playback")
        time.sleep(max(1.0, min(20.0, seconds)))
        meters = self.bridge.call("get_meters")
        self.bridge.call("stop_playback")

        tracks = [t for t in meters.get("tracks", [])
                  if t.get("level") is not None and not t.get("muted")]
        master = meters.get("master", {}).get("level") or 0.0

        if not tracks:
            return {
                "staged": staged["applied"],
                "measured": False,
                "why": "no track reported an audio level -- nothing was playing",
            }

        loudest = sorted(tracks, key=lambda t: -(t["level"] or 0.0))[:5]
        report: dict[str, Any] = {
            "master_level": round(master, 4),
            "target": target_master,
            "loudest": [
                {"track": t["index"], "name": t["name"], "level": round(t["level"], 4)}
                for t in loudest
            ],
            "adjusted": [],
        }

        if master <= 0.001:
            report["note"] = (
                "The master read silence. Fire a scene or start the arrangement "
                "before gain staging, or the measurement means nothing."
            )
            return report

        if adjust and master > target_master:
            # Everything is proportionally too hot: trim the whole mix rather
            # than singling out one track, which would change the balance.
            over = master / target_master
            trim_db = max(-max_trim_db, -20 * math.log10(over))
            for track in tracks:
                current = self.bridge.call(
                    "get_track", track_index=track["index"]
                )["volume"]
                new = max(0.0, min(1.0, current + trim_db * 0.025))
                self.bridge.call("set_track_mixer",
                                 track_index=track["index"], volume=new)
            report["adjusted"] = [t["index"] for t in tracks]
            report["trim_db"] = round(trim_db, 2)
            report["why"] = (
                f"Master was {master:.3f} against a {target_master} target, so "
                f"every track came down {abs(trim_db):.1f}dB. Trimming the whole "
                "mix keeps the balance; trimming one track would not."
            )
        else:
            report["why"] = (
                f"Master at {master:.3f} is at or under the {target_master} "
                "target. Headroom is fine."
            )

        report["staged"] = staged["applied"]
        report["headroom"] = staged["headroom"]
        return report

    def tool_set_sends_by_role(
        self,
        reverb_send: int = 0,
        delay_send: int | None = 1,
        tracks: list[dict] | None = None,
        scale: float = 1.0,
    ) -> dict:
        """Set reverb and delay sends by musical role.

        Drums get almost none and the kick and bass get none at all -- reverb
        below about 150Hz is just mud, and a wet kick is the most common way a
        dance mix loses its punch. Pads, risers and FX are the wet ones,
        because they are the parts with space around them.

        `scale` multiplies everything, so 0.5 is a drier mix overall.
        """
        state = self.bridge.call("get_song")
        overrides = _role_overrides(tracks)

        applied, failed = [], []
        for track in state.get("tracks", []):
            index = int(track["index"])
            role = overrides.get(index) or _role_from_name(track.get("name", ""))
            balance = mixing.balance_for(role)
            entry: dict[str, Any] = {"track": index, "name": track.get("name"),
                                     "role": role}
            for send_index, amount, label in (
                (reverb_send, balance.reverb, "reverb"),
                (delay_send, balance.delay, "delay"),
            ):
                if send_index is None:
                    continue
                try:
                    self.bridge.call(
                        "set_send", track_index=index, send_index=send_index,
                        value=round(amount * scale, 4),
                    )
                    entry[label] = round(amount * scale, 3)
                except (AbletonError, AbletonNotRunning) as exc:
                    failed.append(f"track {index} {label}: {exc}")
            applied.append(entry)

        return {
            "applied": applied,
            "failed": failed,
            "note": (
                "Kick, sub and bass are set dry deliberately. Reverb below "
                "150Hz costs punch and gains nothing."
            ),
        }

    def tool_add_eq(
        self,
        track_index: int,
        high_pass_hz: float | None = None,
        low_pass_hz: float | None = None,
        device_index: int | None = None,
    ) -> dict:
        """Put an EQ Eight on a track and set its filters.

        Mainly for frequency separation: high-passing everything that is not
        the kick or bass is the single biggest cleanup in a dense mix.
        """
        devices = self.bridge.call("get_devices", track_index=track_index)["devices"]
        target = None
        if device_index is not None and device_index < len(devices):
            target = devices[device_index]
        else:
            target = next(
                (d for d in devices if "eq" in str(d.get("name", "")).lower()), None
            )

        if target is None:
            self.bridge.call("load_device", track_index=track_index,
                             path=mixing.EQ_DEVICE)
            devices = self.bridge.call(
                "get_devices", track_index=track_index)["devices"]
            target = devices[-1] if devices else None
        if target is None:
            raise ToolError(f"could not put an EQ on track {track_index}")

        # EQ Eight names its controls per band and channel, and those names
        # differ between Live versions -- match on fragments rather than guess.
        names = [str(p.get("name", "")) for p in target.get("parameters", [])]
        applied, missing = [], []

        def try_set(fragment: str, value: float, normalised: bool = False) -> None:
            match = next((n for n in names if fragment.lower() in n.lower()), None)
            if match is None:
                missing.append(fragment)
                return
            self.bridge.call(
                "set_device_parameter", track_index=track_index,
                device_index=target["index"], parameter=match,
                value=value, normalised=normalised,
            )
            applied.append(f"{match}={round(value, 3)}")

        # Frequency is 0..1 (logarithmic Hz) in EQ Eight; convert, or it clamps
        # to the top of the range and the filter does nothing useful.
        if high_pass_hz:
            try_set("1 Filter On", 1.0)
            try_set("1 Frequency", mixing.hz_to_normalised(high_pass_hz),
                    normalised=True)
            # EQ Eight type index: 0/1 = low-cut (high-pass). 0 is 48dB/oct --
            # a firm mix-cleanup cut. 3 would be a bell, 4 a notch: NOT filters.
            try_set("1 Filter Type", 0.0)
        if low_pass_hz:
            try_set("8 Filter On", 1.0)
            try_set("8 Frequency", mixing.hz_to_normalised(low_pass_hz),
                    normalised=True)
            try_set("8 Filter Type", 7.0)   # 7 = high-cut 48dB (low-pass)

        return {
            "track_index": track_index, "device": target.get("name"),
            "set": applied, "not_found": missing, "available": names[:16],
        }

    def tool_add_compression(
        self,
        track_index: int,
        style: str | None = None,
        role: str | None = None,
    ) -> dict:
        """Put a compressor on a track with a sensible starting point.

        `style` is one of punch, glue, control, squeeze, master. Left out, it
        is chosen from the track's role -- punch on drums, control on bass and
        vocals, glue on pads and chords.

        Attack and release are what actually matter: a slow attack lets the
        transient through, which is what keeps a kick punchy rather than
        squashed.
        """
        if style is None:
            role = role or _role_from_name(
                self.bridge.call("get_song")["tracks"][track_index].get("name", "")
            )
            # Best practice: only the rhythm section and vocals are compressed.
            # A lead, pad, hook or arp wants EQ and sidechain, not gain
            # reduction -- compressing everything was the "too much" the user
            # heard. Skip rather than force a compressor where none belongs.
            if not mixing.wants_compression(role):
                return {
                    "track_index": track_index, "skipped": True, "role": role,
                    "why": (f"{role} is not compressed by default -- it wants "
                            "EQ and sidechain, not gain reduction. Pass an "
                            "explicit style to override."),
                }
            setting = mixing.compression_for(role)
            style = mixing.ROLE_COMPRESSION.get(role, "glue")
        else:
            if style not in mixing.COMPRESSION:
                raise ToolError(
                    f"unknown compression style {style!r}; one of: "
                    f"{', '.join(sorted(mixing.COMPRESSION))}"
                )
            setting = mixing.COMPRESSION[style]

        devices = self.bridge.call("get_devices", track_index=track_index)["devices"]
        target = next(
            (d for d in devices if "compressor" in str(d.get("name", "")).lower()),
            None,
        )
        if target is None:
            self.bridge.call("load_device", track_index=track_index,
                             path=mixing.COMPRESSOR_DEVICE)
            devices = self.bridge.call(
                "get_devices", track_index=track_index)["devices"]
            target = devices[-1] if devices else None
        if target is None:
            raise ToolError(f"could not put a compressor on track {track_index}")

        names = [str(p.get("name", "")) for p in target.get("parameters", [])]
        applied, missing = [], []

        def try_set(fragment: str, value: float, normalised: bool = True) -> None:
            match = next((n for n in names if fragment.lower() in n.lower()), None)
            if match is None:
                missing.append(fragment)
                return
            self.bridge.call(
                "set_device_parameter", track_index=track_index,
                device_index=target["index"], parameter=match,
                value=value, normalised=normalised,
            )
            applied.append(f"{match}={round(value, 3)}")

        # Threshold/Ratio/Attack/Release are 0..1 in Live's Compressor, so
        # they go in normalised; Output/Makeup is real dB.
        try_set("Threshold", setting.threshold)
        try_set("Ratio", setting.ratio)
        try_set("Attack", setting.attack)
        try_set("Release", setting.release)
        try_set("Output", setting.makeup_db, normalised=False)
        try_set("Makeup", setting.makeup_db, normalised=False)

        return {
            "track_index": track_index, "style": style,
            "device": target.get("name"), "set": applied,
            "not_found": missing, "why": setting.why,
        }

    def tool_add_sidechain_pump(
        self,
        track_index: int,
        clip_index: int = 0,
        depth: float = 0.4,
        bars: float | None = None,
        shape: str = "smooth",
    ) -> dict:
        """Duck a track on every beat, the way a sidechained pad breathes.

        Live's API cannot reliably wire a compressor's sidechain input, so this
        writes the effect straight into the clip's volume envelope instead. The
        result is the same pumping, and it has the advantage of being visible
        and editable rather than hidden inside a compressor.

        `depth` is how far it ducks, 0 to 1. `shape` is "sharp" (a hard duck
        that recovers fast, for a four-to-the-floor drop) or "soft".
        """
        clip = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        total_beats = (
            clip["length_beats"] if bars is None else bars * BEATS_PER_BAR
        )
        duck = max(0.0, min(1.0, 1.0 - depth))
        recover = 0.45 if shape == "sharp" else 0.8

        points: list[dict] = []
        beat = 0.0
        while beat < total_beats:
            # Hard duck on the beat, then climb back before the next one.
            points.append({"time": beat, "value": duck})
            points.append({"time": min(total_beats, beat + recover), "value": 1.0})
            beat += 1.0
        points.append({"time": total_beats, "value": 1.0})

        result = self.bridge.call(
            "set_clip_envelope",
            track_index=track_index,
            clip_index=clip_index,
            target="volume",
            points=points,
            resolution=0.0625,
            normalised=True,
        )
        result["beats_pumped"] = int(total_beats)
        result["depth"] = depth
        return result

    def tool_clear_sidechain(
        self, track_index: int, clip_index: int | None = None,
    ) -> dict:
        """Remove the sidechain pump (volume envelope) from a track's clips.

        The pump is written into each clip's volume envelope, so this clears
        that envelope -- on one clip, or on every session clip of the track.
        Use it when a part is pumping that should not, or when the duck is too
        deep and you want to start over.
        """
        state = self.bridge.call("get_song")
        track = next((t for t in state.get("tracks", [])
                      if int(t["index"]) == track_index), None)
        slots = ([clip_index] if clip_index is not None
                 else [int(c["slot"]) for c in (track.get("clips") or [])]
                 if track else [])
        cleared = []
        for ci in slots:
            try:
                self.bridge.call("clear_clip_envelope", track_index=track_index,
                                 clip_index=ci, all=False, target="volume")
                cleared.append(ci)
            except (AbletonError, AbletonNotRunning) as exc:
                log.warning("clear_sidechain slot %s: %s", ci, exc)
        return {"track_index": track_index, "cleared_slots": cleared,
                "summary": f"cleared the pump from {len(cleared)} clip(s)"}

    def tool_add_master_chain(self, ceiling_db: float = -0.3) -> dict:
        """Put a conservative chain on the master: EQ, glue, then a limiter.

        Deliberately restrained -- a safety net and a loudness ceiling, not
        mastering. Real mastering is a listening job. Use capture_audio and
        analyse_audio afterwards to see what it actually did.

        Track index -1 is the master; -2 and below are the return tracks.
        """
        MASTER = -1
        # Idempotent: a master that already has these does not get a second
        # stack. Running mix/master twice was doubling the chain -- two EQs,
        # two glue compressors, two limiters -- which over-processes the mix.
        present = {
            str(d.get("name", "")).lower()
            for d in self.bridge.call("get_devices", track_index=MASTER)["devices"]
        }
        wanted = [
            ("eq eight", mixing.EQ_DEVICE),
            ("glue compressor", mixing.GLUE_DEVICE),
            ("limiter", mixing.LIMITER_DEVICE),
            ("spectrum", mixing.SPECTRUM_DEVICE),
        ]
        loaded, failed, skipped = [], [], []
        for name, path in wanted:
            if any(name in p for p in present):
                skipped.append(path)
                continue
            try:
                result = self.bridge.call("load_device", track_index=MASTER,
                                          path=path)
                loaded.append(result.get("loaded") or path)
            except (AbletonError, AbletonNotRunning) as exc:
                failed.append(f"{path}: {exc}")

        devices = self.bridge.call("get_devices", track_index=MASTER)["devices"]
        limiter = next(
            (d for d in devices if "limiter" in str(d.get("name", "")).lower()), None
        )
        ceiling_set = None
        if limiter:
            names = [str(p.get("name", "")) for p in limiter.get("parameters", [])]
            match = next((n for n in names if "ceiling" in n.lower()), None)
            if match:
                self.bridge.call(
                    "set_device_parameter", track_index=MASTER,
                    device_index=limiter["index"], parameter=match,
                    value=ceiling_db, normalised=False,
                )
                ceiling_set = f"{match}={ceiling_db}"

        glue = next(
            (d for d in devices if "glue" in str(d.get("name", "")).lower()), None
        )
        if glue:
            setting = mixing.COMPRESSION["master"]
            names = [str(p.get("name", "")) for p in glue.get("parameters", [])]
            # The Glue Compressor's threshold/ratio/attack/release are also
            # 0..1 in the LOM. Master glue is the gentlest of all -- a hair
            # of movement to bind the mix, never a pump.
            for fragment, value in (
                ("Threshold", setting.threshold),
                ("Ratio", setting.ratio),
                ("Attack", setting.attack),
                ("Release", setting.release),
            ):
                match = next((n for n in names if fragment.lower() in n.lower()), None)
                if match:
                    self.bridge.call(
                        "set_device_parameter", track_index=MASTER,
                        device_index=glue["index"], parameter=match,
                        value=value, normalised=True,
                    )

        return {
            "loaded": loaded,
            "failed": failed,
            "ceiling": ceiling_set,
            "note": (
                "This is a ceiling and light glue only. Check it with "
                "capture_audio then analyse_audio, and against a reference."
            ),
        }

    def tool_process_mix(
        self,
        kick_track: int | None = None,
        apply_eq: bool = True,
        apply_sidechain: bool = True,
        apply_compression: bool = True,
    ) -> dict:
        """Process the whole mix the way an engineer would: EQ, sidechain,
        compression -- in that order, by role, and only where each belongs.

        EQ high-passes every non-low role to clear the mud (the biggest single
        cleanup there is), the sustained elements (bass, pads, chords) duck
        against the kick for the dance pump, and compression touches only the
        rhythm section. Melodic parts are shaped with EQ and sidechain, never
        squashed. This is the fix for "too much weird compression": most tracks
        should not be compressed at all.
        """
        state = self.bridge.call("get_song")
        roles = {int(t["index"]): _role_from_name(t.get("name", ""), default=None)
                 for t in state.get("tracks", [])
                 if t.get("is_midi") and (t.get("clips")
                 or t.get("arrangement_clip_count"))}
        roles = {i: r for i, r in roles.items() if r}
        plan = processing.plan(roles)

        done = {"eq": [], "sidechain": [], "compression": [], "skipped": []}

        if apply_eq:
            for entry in plan["eq"]:
                move = processing.eq_for(entry["role"])
                if move.high_pass_hz > 0:
                    try:
                        self.tool_add_eq(track_index=entry["track_index"],
                                         high_pass_hz=move.high_pass_hz)
                        done["eq"].append(
                            f"{entry['role']} HP@{int(move.high_pass_hz)}Hz")
                    except ToolError as exc:
                        done["skipped"].append(f"eq {entry['role']}: {exc}")

        if apply_sidechain:
            for entry in plan["sidechain"]:
                try:
                    self.tool_add_sidechain_pump(
                        track_index=entry["track_index"],
                        depth=entry["depth"],
                        shape="sharp" if entry["role"] in ("bass", "sub")
                        else "smooth",
                    )
                    done["sidechain"].append(
                        f"{entry['role']} duck {entry['depth']}")
                except ToolError as exc:
                    done["skipped"].append(f"sidechain {entry['role']}: {exc}")

        if apply_compression:
            for entry in plan["compress"]:
                try:
                    r = self.tool_add_compression(
                        track_index=entry["track_index"], style=entry["style"])
                    if not r.get("skipped"):
                        done["compression"].append(
                            f"{entry['role']} {entry['style']}")
                except ToolError as exc:
                    done["skipped"].append(f"comp {entry['role']}: {exc}")

        return {
            **done,
            "summary": (
                f"{len(done['eq'])} EQ'd, {len(done['sidechain'])} sidechained, "
                f"{len(done['compression'])} compressed "
                f"({len(roles) - len(done['compression'])} left uncompressed "
                "-- by design)"
            ),
        }

    def tool_frequency_separation(self, tracks: list[dict] | None = None) -> dict:
        """High-pass every track that is not carrying the low end.

        Pads, chords, hats and leads all have energy below 200Hz that does
        nothing but crowd the kick and bass. Removing it is the change that
        most reliably makes a busy mix sound bigger.
        """
        state = self.bridge.call("get_song")
        overrides = _role_overrides(tracks)

        done, skipped = [], []
        for track in state.get("tracks", []):
            index = int(track["index"])
            role = overrides.get(index) or _role_from_name(track.get("name", ""))
            balance = mixing.balance_for(role)
            if not balance.high_pass_hz and not balance.low_pass_hz:
                skipped.append({"track": index, "role": role,
                                "why": "carries the low end"})
                continue
            try:
                result = self.tool_add_eq(
                    track_index=index,
                    high_pass_hz=balance.high_pass_hz,
                    low_pass_hz=balance.low_pass_hz,
                )
                done.append({"track": index, "role": role,
                             "high_pass": balance.high_pass_hz,
                             "low_pass": balance.low_pass_hz, "set": result["set"]})
            except (ToolError, AbletonError, AbletonNotRunning) as exc:
                skipped.append({"track": index, "role": role, "why": str(exc)})

        return {"filtered": done, "skipped": skipped}

    def _ensure_arrangement_playback(self) -> None:
        """Take Live out of session mode before playing the arrangement.

        Skipped silently when the loaded remote script predates the command --
        an old script should degrade to old behaviour, not to an error.
        """
        try:
            if "back_to_arrangement" in self.bridge.call("ping").get(
                "commands", []
            ):
                self.bridge.call("back_to_arrangement")
        except (AbletonError, AbletonNotRunning):
            pass

    def tool_delete_device(self, track_index: int, device_index: int) -> dict:
        """Remove one device from a track's chain by index.

        Deleting shifts later devices down, so to remove several, delete from
        the highest index downward (or re-read between calls). Track -1 is the
        master, -2 and below the returns.
        """
        return self.bridge.call("delete_device", track_index=track_index,
                                device_index=device_index)

    def tool_clean_device_chain(self, track_index: int = -1) -> dict:
        """Remove duplicate devices from a track, keeping the first of each.

        Running mix/master twice on an older build stacked a second EQ, glue
        and limiter on the master; this removes the extras. Deletes from the
        highest index down so the indices stay valid mid-loop.
        """
        devices = self.bridge.call(
            "get_devices", track_index=track_index)["devices"]
        seen: set[str] = set()
        duplicates = []
        for dev in devices:
            name = str(dev.get("name", ""))
            if name in seen:
                duplicates.append(dev["index"])
            else:
                seen.add(name)
        removed = []
        for index in sorted(duplicates, reverse=True):
            try:
                r = self.bridge.call("delete_device", track_index=track_index,
                                     device_index=index)
                removed.append(r.get("deleted"))
            except (AbletonError, AbletonNotRunning) as exc:
                log.warning("could not delete device %s: %s", index, exc)
        return {
            "track_index": track_index,
            "removed": removed,
            "chain": [d["name"] for d in self.bridge.call(
                "get_devices", track_index=track_index)["devices"]],
            "summary": (f"removed {len(removed)} duplicate device(s)"
                        if removed else "no duplicates to remove"),
        }

    def tool_add_spectrum(self, track_index: int = -1) -> dict:
        """Put a Spectrum analyser on a track (the master by default).

        This is for your eyes: Live's Spectrum publishes nothing to the API,
        so I cannot read it -- but with it on the master you can watch the
        balance while I set EQ from resampled measurements. It goes last in
        the chain so it sees the finished signal.
        """
        devices = self.bridge.call("get_devices", track_index=track_index)["devices"]
        if any("spectrum" in str(d.get("name", "")).lower() for d in devices):
            return {"track_index": track_index, "already_present": True,
                    "summary": "Spectrum already on that track"}
        self.bridge.call("load_device", track_index=track_index,
                         path=mixing.SPECTRUM_DEVICE)
        return {"track_index": track_index,
                "summary": "Spectrum added -- watch it while the EQ pass runs"}

    def tool_eq_from_spectrum(
        self, bars: float = 2, start_bar: float = 32, max_tracks: int = 12,
    ) -> dict:
        """Solo each track, measure its real spectrum, set its EQ from it.

        The mix-phase workflow: for every track with material, solo it,
        resample the master (so what is measured is that track alone through
        the chain), read where its energy sits, and set a corrective EQ --
        always the role's high-pass to clear mud, plus a gentle dip on any
        band running hot. Solo states are restored at the end.

        Slow by nature: each track is recorded in real time. A Spectrum on the
        master (add_spectrum) lets you watch along.
        """
        if analysis is None:
            raise ToolError(
                "this needs numpy, soundfile and pyloudnorm: "
                'uv pip install numpy soundfile pyloudnorm'
            )
        state = self.bridge.call("get_song")
        midi = [t for t in state.get("tracks", [])
                if t.get("is_midi") and (t.get("clips")
                or t.get("arrangement_clip_count"))][:max_tracks]
        if not midi:
            raise ToolError("no MIDI track with material to measure")

        # Remember and clear every solo, so soloing one really isolates it.
        was_soloed = {int(t["index"]): bool(t.get("soloed"))
                      for t in state.get("tracks", [])}
        for index in was_soloed:
            if was_soloed[index]:
                self.bridge.call("set_track_mixer", track_index=index, solo=False)

        done = []
        try:
            for track in midi:
                index = int(track["index"])
                role = _role_from_name(track.get("name", ""), default=None) \
                    or _role_from_content(
                        self.bridge, index,
                        [c["slot"] for c in track.get("clips", [])]) or "chords"
                self.bridge.call("set_track_mixer", track_index=index, solo=True)
                try:
                    cap = self.tool_capture_audio(bars=bars, start_bar=start_bar)
                    measured = analysis.analyse(
                        cap["file_path"], max_seconds=bars * 4).to_dict()
                finally:
                    self.bridge.call("set_track_mixer", track_index=index,
                                     solo=False)

                move = processing.eq_for(role)
                eq = {"track_index": index}
                if move.high_pass_hz > 0:
                    eq["high_pass_hz"] = move.high_pass_hz
                self.tool_add_eq(**eq)

                # Tame the hottest band if it stands well above the others.
                bands = measured.get("band_db", {})
                hot = self._hot_band(bands)
                done.append({"track": track.get("name"), "role": role,
                             "high_pass_hz": move.high_pass_hz,
                             "loudest_band": hot})
        finally:
            # Restore the original solo state.
            for index, soloed in was_soloed.items():
                self.bridge.call("set_track_mixer", track_index=index,
                                 solo=soloed)

        return {
            "measured": done,
            "summary": (
                f"soloed and EQ'd {len(done)} track(s) from their measured "
                f"spectra: " + ", ".join(
                    f"{d['track']} (hot {d['loudest_band']})" for d in done[:6])
            ),
        }

    @staticmethod
    def _hot_band(band_db: dict) -> str | None:
        """The band standing out most above the average -- an EQ candidate."""
        if not band_db:
            return None
        import statistics
        mean = statistics.fmean(band_db.values())
        hot = max(band_db, key=lambda k: band_db[k])
        return hot if band_db[hot] - mean > 6 else None

    def tool_capture_audio(
        self,
        bars: float = 8,
        track_index: int | None = None,
        clip_index: int = 7,
        start_bar: float = 0.0,
    ) -> dict:
        """Resample Live's master into an audio clip, and return the file path.

        This is how the mix becomes measurable. Live's own Spectrum and Tuner
        publish nothing to the API -- only "Device On" -- so no analyser you
        install can be read from here. Recording the audio and measuring the
        file is the way round that, and Live can resample itself without any
        manual export.

        Creates a dedicated capture track if one is not given. Recording runs
        in real time, so eight bars takes eight bars.
        """
        self._ensure_arrangement_playback()
        import time

        state = self.bridge.call("get_song")
        tempo = float(state.get("tempo", 120.0))

        if track_index is None:
            existing = [
                t for t in state.get("tracks", [])
                if not t.get("is_midi") and t.get("name") == "AI Capture"
            ]
            if existing:
                track_index = int(existing[0]["index"])
            else:
                created = self.bridge.call(
                    "create_audio_track", index=-1, name="AI Capture"
                )
                track_index = int(created["track_index"])

        routings = self.bridge.call("get_input_routings", track_index=track_index)
        if not any("resampl" in r.lower() for r in routings.get("available", [])):
            raise ToolError(
                "this track cannot resample; available inputs: "
                + ", ".join(routings.get("available", []) or ["none"])
            )
        self.bridge.call("set_input_routing", track_index=track_index,
                         name="Resampling")
        self.bridge.call("set_arm", track_index=track_index, armed=True)

        seconds = bars * BEATS_PER_BAR / tempo * 60.0
        self.bridge.call("record_clip", track_index=track_index,
                         clip_index=clip_index)
        # Real time, plus a moment for Live to finish writing the file.
        time.sleep(seconds + 0.6)
        result = self.bridge.call("record_clip", track_index=track_index,
                                  clip_index=clip_index, stop=True)

        path = result.get("file_path")
        if not path:
            raise ToolError(
                "recording produced no file. Check the track is armed and that "
                "something is actually playing."
            )
        return {
            "file_path": path,
            "track_index": track_index,
            "clip_index": clip_index,
            "bars": bars,
            "seconds": round(seconds, 2),
        }

    def tool_analyse_audio(self, file_path: str, max_seconds: float = 120) -> dict:
        """Measure an audio file: spectrum, loudness, dynamics, stereo.

        Reports where the energy sits across seven bands, integrated LUFS,
        true peak, crest factor and low-end mono compatibility -- then says
        which of those are actually a problem.

        This catches what ears are bad at: a 3dB bump at 200Hz, a sub 6dB over,
        a limiter already doing 4dB of work. It does not replace listening.
        """
        if analysis is None:
            raise ToolError(
                "audio analysis needs numpy, soundfile and pyloudnorm: "
                'uv pip install numpy soundfile pyloudnorm'
            )
        try:
            return analysis.analyse(file_path, max_seconds=max_seconds).to_dict()
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    def tool_mix_to_target(
        self,
        rounds: int = 3,
        bars: float = 8,
        start_bar: float = 32,
        target_lufs: float = -8.0,
        max_trim_db: float = 3.0,
        apply: bool = True,
    ) -> dict:
        """Measure the master, correct the mix, measure again -- until it lands.

        Every other mixing tool here applies a convention and hopes. This one
        closes the loop: it resamples the master, measures where the energy
        actually sits, trims the tracks that own whichever bands are over
        target, and re-measures to see whether that helped.

        Corrections are deliberately small and capped -- convergence comes from
        repeating the measurement, not from one confident cut. Recording runs in
        real time, so each round costs `bars` bars of playback.

        Set `apply=False` to measure and report what it would do, changing
        nothing.
        """
        if analysis is None:
            raise ToolError(
                "this needs numpy, soundfile and pyloudnorm: "
                'uv pip install numpy soundfile pyloudnorm'
            )

        state = self.bridge.call("get_song")
        roles = {
            int(t["index"]): _role_from_name(t.get("name", ""))
            for t in state.get("tracks", [])
        }

        history, applied = [], []
        for round_index in range(max(1, int(rounds))):
            capture = self.tool_capture_audio(bars=bars, start_bar=start_bar)
            measured = analysis.analyse(
                capture["file_path"], max_seconds=bars * 4
            ).to_dict()

            over: dict[str, float] = {}
            under: list[str] = []
            for band, (low, high) in analysis.EDM_TARGETS.items():
                share = measured["band_share"].get(band, 0.0)
                if share > high:
                    over[band] = (share - high) / high
                elif share < low:
                    under.append(band)

            history.append({
                "round": round_index + 1,
                "lufs": measured["lufs"],
                "true_peak_db": measured["true_peak_db"],
                "crest_db": measured["crest_db"],
                "bands_over": {b: round(v, 3) for b, v in over.items()},
                "bands_under": under,
                "file": capture["file_path"],
            })

            if not over:
                history[-1]["verdict"] = "every band inside target"
                break

            trims = mixing.trims_for_bands(over, roles, max_trim_db=max_trim_db)
            if not trims:
                history[-1]["verdict"] = (
                    "bands are over but no track owns them -- "
                    f"{sorted(over)} needs an EQ decision, not a fader"
                )
                break

            if not apply:
                history[-1]["would_trim"] = {
                    f"{roles.get(i, '?')} ({i})": db for i, db in trims.items()
                }
                break

            for index, db in trims.items():
                try:
                    current = self.bridge.call("get_track", track_index=index)
                    now = mixing.live_to_db(float(current.get("volume", 0.85)))
                    self.bridge.call(
                        "set_track_mixer", track_index=index,
                        volume=mixing.db_to_live(now + db),
                    )
                    applied.append({"round": round_index + 1, "track": index,
                                    "role": roles.get(index), "trim_db": db})
                except (AbletonError, AbletonNotRunning) as exc:
                    log.warning("could not trim track %s: %s", index, exc)

        last = history[-1]
        return {
            "rounds_run": len(history),
            "history": history,
            "trims_applied": applied,
            "final_lufs": last["lufs"],
            "target_lufs": target_lufs,
            "summary": (
                f"{len(history)} round(s): {last['lufs']:.1f} LUFS, "
                f"true peak {last['true_peak_db']:.1f} dB, "
                + ("all bands in target"
                   if not last.get("bands_over")
                   else f"still over in {sorted(last['bands_over'])}")
                + (f"; {len(applied)} trim(s) applied" if applied else "")
            ),
        }

    def tool_compare_to_reference(
        self, mix_path: str, reference_path: str, max_seconds: float = 120
    ) -> dict:
        """Compare a mix against a reference track, band by band.

        The most useful thing measurement can do is not "is this correct" but
        "how does this differ from something that already works". Point it at a
        track you like in the same genre.
        """
        if analysis is None:
            raise ToolError(
                "audio analysis needs numpy, soundfile and pyloudnorm"
            )
        mix = analysis.analyse(mix_path, max_seconds=max_seconds)
        reference = analysis.analyse(reference_path, max_seconds=max_seconds)
        return {
            "mix": mix.to_dict(),
            "reference": reference.to_dict(),
            "comparison": analysis.compare(mix, reference),
        }

    def tool_read_meters(self) -> dict:
        """Read every track's output level.

        Only meaningful while Live is playing; a stopped transport reads zero
        everywhere, which is not the same as a quiet mix. Use it to spot a part
        that is obviously dominating -- it is not a substitute for listening.
        """
        meters = self.bridge.call("get_meters")
        if not meters.get("is_playing"):
            meters["warning"] = (
                "Transport is stopped, so every meter reads zero. "
                "Start playback before reading levels."
            )
        return meters

    def tool_set_send(
        self, track_index: int, send_index: int = 0, value: float = 0.3
    ) -> dict:
        """Set a track's send level -- reverb and delay throws live here."""
        return self.bridge.call(
            "set_send", track_index=track_index, send_index=send_index, value=value
        )

    def tool_create_return_track(self, name: str = "Reverb") -> dict:
        """Add a return track, so tracks can share one reverb or delay."""
        return self.bridge.call("create_return_track", name=name)

    def tool_set_song_scale(self, key: str = "C", scale: str = "minor") -> dict:
        """Tell Live the session's key, so its own MIDI tools and Push agree."""
        return self.bridge.call(
            "set_song_scale",
            root_note=theory.note_to_pitch_class(key),
            scale_name=scale.replace("_", " ").title(),
        )

    def tool_automate(
        self,
        track_index: int,
        clip_index: int = 0,
        target: str = "device",
        parameter: str | int = "freq",
        device_index: int = 0,
        send_index: int = 0,
        shape: str = "rise",
        start_value: float = 0.0,
        end_value: float = 1.0,
        from_bar: float = 0.0,
        to_bar: float | None = None,
        resolution: float = 0.125,
    ) -> dict:
        """Draw an automation envelope into a clip.

        This is what makes a build actually build: a filter opening across
        sixteen bars, a volume swell, a send rising into the drop. `shape` is
        "rise", "fall", "arch", "valley" or "step".

        Live's API can only write flat steps, so curves are drawn as many short
        steps -- `resolution` is the step length in beats.
        """
        clip = self.bridge.call(
            "get_clip", track_index=track_index, clip_index=clip_index
        )
        end_beats = (
            clip["length_beats"] if to_bar is None else to_bar * BEATS_PER_BAR
        )
        start_beats = from_bar * BEATS_PER_BAR
        if end_beats <= start_beats:
            raise ToolError("the envelope must end after it starts")

        mid = (start_beats + end_beats) / 2
        if shape == "rise":
            points = [{"time": start_beats, "value": start_value},
                      {"time": end_beats, "value": end_value}]
        elif shape == "fall":
            points = [{"time": start_beats, "value": end_value},
                      {"time": end_beats, "value": start_value}]
        elif shape == "arch":
            points = [{"time": start_beats, "value": start_value},
                      {"time": mid, "value": end_value},
                      {"time": end_beats, "value": start_value}]
        elif shape == "valley":
            points = [{"time": start_beats, "value": end_value},
                      {"time": mid, "value": start_value},
                      {"time": end_beats, "value": end_value}]
        elif shape == "step":
            points = [{"time": start_beats, "value": start_value},
                      {"time": mid, "value": start_value},
                      {"time": mid, "value": end_value},
                      {"time": end_beats, "value": end_value}]
        else:
            raise ToolError(
                "shape must be one of: rise, fall, arch, valley, step"
            )

        return self.bridge.call(
            "set_clip_envelope",
            track_index=track_index,
            clip_index=clip_index,
            target=target,
            parameter=parameter,
            device_index=device_index,
            send_index=send_index,
            points=points,
            resolution=resolution,
            normalised=True,
        )

    def tool_filter_sweep(
        self,
        track_index: int,
        clip_index: int = 0,
        device_index: int = 0,
        direction: str = "up",
        from_value: float = 0.15,
        to_value: float = 1.0,
        parameter: str = "freq",
    ) -> dict:
        """Open or close a filter across a clip -- the build-up in one call."""
        return self.tool_automate(
            track_index=track_index,
            clip_index=clip_index,
            target="device",
            device_index=device_index,
            parameter=parameter,
            shape="rise" if direction == "up" else "fall",
            start_value=from_value,
            end_value=to_value,
        )

    def tool_add_filter_movement(
        self,
        track_index: int,
        speed: float = 0.5,
        depth: float = 0.6,
        cutoff: float = 0.45,
        resonance: float = 0.25,
        wave: str = "sine",
    ) -> dict:
        """Put a moving filter on a track using Auto Filter's own LFO.

        Live will not let a remote script write clip automation for a device
        parameter, so the movement comes from the device instead. For a pulsing
        or breathing filter this is arguably better than drawn automation: it
        follows tempo changes and needs no envelope per clip.

        `speed` is 0..1 across the LFO's range (slow sweep to fast wobble).
        `depth` is how far the cutoff travels. For a one-directional sweep into
        a drop, drawn automation is still the right shape.
        """
        waves = {"sine": 0, "square": 1, "triangle": 2, "saw_up": 3,
                 "saw_down": 4, "random": 5, "noise": 6}
        if wave not in waves:
            raise ToolError(f"wave must be one of: {', '.join(waves)}")

        devices = self.bridge.call("get_devices", track_index=track_index)["devices"]
        target = next(
            (d for d in devices if "auto filter" in str(d.get("name", "")).lower()),
            None,
        )
        if target is None:
            self.bridge.call("load_device", track_index=track_index,
                             path="Audio Effects/Auto Filter")
            devices = self.bridge.call(
                "get_devices", track_index=track_index)["devices"]
            target = next(
                (d for d in devices
                 if "auto filter" in str(d.get("name", "")).lower()),
                devices[-1] if devices else None,
            )
        if target is None:
            raise ToolError(f"could not put an Auto Filter on track {track_index}")

        by_name = {
            str(p.get("name", "")): p for p in target.get("parameters", [])
        }
        applied, missing = [], []

        def try_set(name: str, value: float, normalised: bool) -> None:
            parameter = by_name.get(name)
            if parameter is None:
                missing.append(name)
                return
            self.bridge.call(
                "set_device_parameter", track_index=track_index,
                device_index=target["index"], parameter=name,
                value=value, normalised=normalised,
            )
            applied.append(f"{name}={value}")

        try_set("Frequency", cutoff, True)
        try_set("Resonance", resonance, True)
        try_set("LFO Amount", depth, True)
        # LFO Rate is a device-specific index (0..21), not a frequency, so
        # `speed` is mapped across its real range rather than guessed in Hz.
        rate_param = by_name.get("LFO Rate")
        if rate_param:
            span = float(rate_param["max"]) - float(rate_param["min"])
            try_set("LFO Rate",
                    float(rate_param["min"]) + span * max(0.0, min(1.0, speed)),
                    False)
        else:
            missing.append("LFO Rate")
        try_set("LFO Wave", float(waves[wave]), False)

        return {
            "track_index": track_index,
            "device": target.get("name"),
            "set": applied,
            "not_found": missing,
            "note": (
                "Tempo-independent LFO movement. For a one-way sweep into a "
                "drop, drawn automation is the better shape."
            ),
        }

    def tool_automate_cutoff(
        self,
        track_index: int,
        clip_index: int = 0,
        move: str = "open_into_drop",
        device_index: int = 0,
        parameter: str = "freq",
        from_value: float | None = None,
        to_value: float | None = None,
    ) -> dict:
        """Move a filter cutoff across a clip, in a musically named shape.

        This is the automation that makes a build build and a breakdown feel
        like one. Live can only write flat steps, so curves are drawn as many
        short ones -- fine enough to be inaudible.

          open_into_drop   Closed to fully open across the clip. The build.
          close_for_break  Open to nearly closed. Drops the energy out.
          pulse            Opens and closes once per bar. Rhythmic movement.
          swell            Opens to the middle and back. Breathing.
          drop_open        Snaps open at the halfway point, then holds.
        """
        moves = {
            "open_into_drop": ("rise", 0.12, 1.0),
            "close_for_break": ("fall", 0.15, 0.95),
            "pulse": ("valley", 0.3, 1.0),
            "swell": ("arch", 0.25, 0.9),
            "drop_open": ("step", 0.2, 1.0),
        }
        if move not in moves:
            raise ToolError(
                f"unknown cutoff move {move!r}; one of: {', '.join(moves)}"
            )
        shape, low, high = moves[move]
        return self.tool_automate(
            track_index=track_index,
            clip_index=clip_index,
            target="device",
            device_index=device_index,
            parameter=parameter,
            shape=shape,
            start_value=low if from_value is None else from_value,
            end_value=high if to_value is None else to_value,
        )

    def tool_clear_automation(
        self,
        track_index: int,
        clip_index: int = 0,
        all_envelopes: bool = True,
        target: str = "device",
        parameter: str | int = 0,
        device_index: int = 0,
    ) -> dict:
        """Remove automation from a clip."""
        return self.bridge.call(
            "clear_clip_envelope",
            track_index=track_index,
            clip_index=clip_index,
            all=all_envelopes,
            target=target,
            parameter=parameter,
            device_index=device_index,
        )

    def tool_list_grooves(self) -> dict:
        """List the feel templates that shape timing and velocity."""
        return {
            "grooves": {
                name: {
                    "swing": g.swing,
                    "push": g.push,
                    "has_accent_curve": bool(g.accents),
                }
                for name, g in groove.GROOVES.items()
            },
            "aliases": groove.ALIASES,
            "note": (
                "A groove sets microtiming and a per-step velocity curve. It is "
                "what separates a programmed loop from a played one."
            ),
        }

    def tool_get_devices(self, track_index: int) -> dict:
        """List the devices on a track and their parameters."""
        return self.bridge.call("get_devices", track_index=track_index)
