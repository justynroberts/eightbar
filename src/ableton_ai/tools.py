"""The tool surface the model drives.

Each tool is deliberately high-level: the model says "a rising i-VI-III-VII in C
minor over 8 bars" and the theory/generator modules decide the notes. The raw
`write_clip_notes` escape hatch exists for when the model genuinely does want to
place individual notes.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Callable

from . import (
    arrangement, basslines, corpus, generators, groove, harmony, leads,
    melody, mixing, presets, theory, variations, voicings,
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
}

# Roles placed once at a section's start rather than looped across it.
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


def _role_from_name(name: str) -> str:
    """Guess a track's musical role from what the producer called it."""
    lowered = (name or "").lower()
    for needle, role in _ROLE_HINTS:
        if needle in lowered:
            return role
    return "lead"


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

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        handler: Callable[..., Any] | None = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ToolError(f"no such tool: {name}")

        # Check the arguments against the signature *before* calling, so a
        # TypeError raised inside the tool body is not misreported as a bad
        # argument list -- which hides the real fault completely.
        import inspect

        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as exc:
            raise ToolError(f"{name}: bad arguments -- {exc}") from exc

        try:
            return handler(**arguments)
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
        """Delete a track by index."""
        return self.bridge.call("delete_track", track_index=track_index)

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
    ) -> dict:
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

    def _progression(
        self,
        key: str,
        scale: str,
        degrees: Any,
        bars: float,
        octave: int,
        extension: str,
        smooth: bool = True,
    ) -> tuple[list[theory.Chord], float]:
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
        rhythm: str = "whole",
        velocity: int = 85,
        spread: float = 0.0,
        humanise: float = 0.0,
        smooth_voicing: bool = True,
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """Generate a chord progression clip. Voice leading is applied automatically so the chords move smoothly instead of jumping in octaves."""
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, octave, extension, smooth_voicing
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
            track_index, clip_index, bars, notes, name or f"{key} {scale} chords"
        )
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
        octave: int = 1,
        velocity: int = 100,
        swing: float = 0.0,
        humanise: float = 0.0,
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """Generate a bassline that follows a chord progression."""
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, 3, "triad"
        )
        notes = generators.generate_bassline(
            chords,
            bars_per_chord=bars_per_chord,
            rhythm=rhythm,
            octave=octave,
            velocity=velocity,
            style=style,
            swing=swing,
            humanise=humanise,
            seed=seed,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} {scale} bass"
        )

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
        ]
        made: dict[str, int] = {}
        for name, role in layout:
            created = step(f"track {name}",
                           lambda n=name, r=role: self.tool_create_track(n, r))
            if created:
                made[name] = int(created["track_index"])

        step("instruments", lambda: self.tool_ensure_instruments(only_empty=True))

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
            step("chords", lambda: self.tool_create_varied_chords(
                made["Chords"], bars=8, variation="rich",
                rhythm=recipe["chord_rhythm"], **common))
        if "Hook" in made:
            step("hook", lambda: self.tool_create_hook_clip(
                made["Hook"], bars=8, **common))
        if "Lead" in made:
            step("lead", lambda: self.tool_create_lead_clip(
                made["Lead"], bars=8, style=recipe["lead_style"], **common))
        if "Melody" in made:
            step("melody", lambda: self.tool_create_melody_clip(
                made["Melody"], bars=8, octave=5, **common))
        if "Riser" in made:
            step("riser", lambda: self.tool_create_riser_clip(
                made["Riser"], bars=8, key=key, scale=mode))
        if "Impact" in made:
            step("impact", lambda: self.tool_create_impact_clip(made["Impact"]))
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
        role_of = {"Kick": "kick", "Drums": "drums", "Bass": "bass",
                   "Chords": "chords", "Hook": "hook", "Lead": "lead",
                   "Melody": "arp", "Riser": "riser", "Impact": "impact",
                   "Build": "drums"}
        entries = []
        for name, index in made.items():
            entry: dict[str, Any] = {"track_index": index, "role": role_of[name]}
            if name in ladders:
                entry["clip_indices"] = ladders[name]
            else:
                entry["clip_index"] = 0
            entries.append(entry)

        plan = step("plan", lambda: self.tool_plan_arrangement(
            target_seconds=duration_seconds, tempo=bpm,
            template=recipe["template"]))
        arranged = None
        if plan:
            arranged = step("arrange", lambda: self.tool_arrange_to_timeline(
                sections=plan["sections"], tracks=entries, clear_first=True))
            report["structure"] = [
                f"{s['name']}@{s['start_bar']}" for s in plan["sections"]
            ]
            report["duration"] = plan["duration"]

        # -- mix ----------------------------------------------------------
        if mix:
            step("gain staging", lambda: self.tool_mix_levels())
            # Measured gain staging needs the transport running; do it after
            # the arrangement exists rather than against silence.
            step("frequency separation", lambda: self.tool_frequency_separation())
            # Two returns so sends have somewhere to go, then role-based
            # amounts -- drums nearly dry, pads and FX wet.
            step("reverb return",
                 lambda: self.tool_create_return_track("Reverb"))
            step("delay return", lambda: self.tool_create_return_track("Delay"))
            step("sends", lambda: self.tool_set_sends_by_role())
            for name in ("Kick", "Drums", "Bass", "Chords", "Lead"):
                if name in made:
                    step(f"compression {name}",
                         lambda n=name: self.tool_add_compression(made[n]))
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
        library = corpus.Library()
        result = library.learn_folder(folder, limit=limit)
        result["summary"] = library.summary()
        return result

    def tool_corpus_summary(self) -> dict:
        """What the learned references have in common.

        Reports the keys, tempos, chord qualities, recurring progressions and
        -- most usefully -- the chord-to-chord movements, which is what new
        progressions are generated from.
        """
        library = corpus.Library()
        if not library.references:
            raise ToolError(
                "nothing learned yet. Put MIDI files in references/ and call "
                "learn_references."
            )
        return library.summary()

    def tool_suggest_progression(
        self, length: int = 4, start: int = 1, seed: int | None = None
    ) -> dict:
        """Propose a progression by walking the learned chord movements.

        Whole progressions rarely repeat across a corpus, but the moves inside
        them do -- so this produces something new that still behaves like the
        references.
        """
        library = corpus.Library()
        if not library.references:
            raise ToolError("nothing learned yet -- call learn_references first")
        return library.suggest_progression(length=length, start=start, seed=seed)

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
        octave: int = 1,
        drums_track: int | None = None,
        humanise: float = 0.2,
        name: str | None = None,
        seed: int | None = None,
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
                                                   "triad")
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
            chords, style=style, bars_per_chord=bars_per_chord, octave=octave,
            against_drums=drum_notes, humanise=humanise, seed=seed,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes,
            name or f"{key} bass ({style})",
        )
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
    ) -> dict:
        """Build a lead that arcs across the whole phrase.

        A trance lead is a continuous sixteenth-note stream that climbs over
        eight or sixteen bars and reaches its highest note exactly where the
        drop lands. The register is driven by position in the phrase rather
        than by the bar, which is what makes it soar instead of merely
        repeating. Styles: soaring, pluck, rolling, arp_climb, call, stab.
        """
        chords, bars_per_chord = self._progression(key, scale, degrees, bars,
                                                   octave, "triad")
        notes = leads.generate(
            chords, root=key, scale=scale, style=style,
            bars_per_chord=bars_per_chord, octave=octave,
            groove=groove_name, seed=seed,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} lead ({style})"
        )
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
        variation: str = "rich",
        octave: int | None = None,
        extension: str = "seventh",
        voicing: str = "open",
        rhythm: str = "pad",
        velocity: int = 85,
        spread: float = 0.0,
        name: str | None = None,
        seed: int | None = None,
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
        if isinstance(degrees, str) and degrees.lower() in theory.PROGRESSIONS:
            resolved = list(theory.PROGRESSIONS[degrees.lower()])
        else:
            resolved = theory.parse_degrees(degrees)

        # Extended chords sit higher than triads. A ninth voiced from a triad's
        # home register puts its 7th and 9th in the low mids, which is the
        # single fastest way to make a chord part sound like mud.
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
                voicings.extend(base, extension), style=voicing,
                centre=centre, quality=base.quality,
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
            name or f"{key} {scale} chords ({variation})",
        )
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
            seed=seed,
            groove=groove,
            instruments=instruments,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"{pattern} drums"
        )

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
    ) -> dict:
        """Generate an arpeggio over a chord progression."""
        rates = {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25, "1/32": 0.125}
        if rate not in rates:
            raise ToolError(f"rate must be one of {', '.join(rates)}")
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, octave, "triad"
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
            track_index, clip_index, bars, notes, name or f"{key} arp"
        )

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
            chords, _ = self._progression(key, scale, degrees, bars, 3, "triad")
            durations = None

        notes = melody.write(
            root=key, scale=scale, chords=chords, bars=bars, octave=octave,
            rhythm=rhythm, tension=tension, velocity=velocity, seed=seed,
            durations=durations,
        )
        result = self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} {scale} melody"
        )
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
            for required in ("start_bar", "bars"):
                if section.get(required) is None:
                    raise ToolError(
                        f"{label} has no {required}. Every section needs "
                        "start_bar and bars -- use the list plan_arrangement "
                        "returns rather than building it by hand."
                    )
            try:
                start = float(section["start_bar"])
                length = float(section["bars"])
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{label}: start_bar and bars must be numbers ({exc})")
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

        if clear_first:
            self.bridge.call(
                "clear_arrangement",
                track_indices=[int(t["track_index"]) for t in tracks],
            )

        # Cache clip lengths so we know how many repeats fill a section. A slot
        # that holds no clip is skipped rather than fatal: a placeholder track
        # for vocals or FX legitimately has nothing in it yet.
        lengths: dict[tuple[int, int], float] = {}
        placeable: list[dict] = []
        for entry in tracks:
            ti = int(entry["track_index"])
            usable: list[int] = []
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

        placements = []
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

                if len(slots) == 1:
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
                    repeats = max(1, int(round(bars / clip_bars)))
                    at = start_bar

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
        except (AbletonError, AbletonNotRunning) as exc:
            log.warning("could not set locators: %s", exc)

        self.bridge.call("set_view", view="arrangement")
        summary = self.bridge.call("get_arrangement")
        result = {
            "placements": len(placements),
            "end_bars": summary.get("end_bars"),
            "duration_seconds": summary.get("duration_seconds"),
            "detail": placements[:60],
        }
        if skipped_tracks:
            result["skipped_tracks"] = skipped_tracks
            result["note"] = (
                "Some tracks were not placed -- usually placeholders with no "
                "clip yet, which is expected for vocals and FX."
            )
        return result

    def tool_clear_arrangement(self, track_indices: list[int] | None = None) -> dict:
        """Delete arrangement-timeline clips, on the given tracks or all of them."""
        params = {} if not track_indices else {"track_indices": track_indices}
        return self.bridge.call("clear_arrangement", **params)

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
            track_index, clip_index, bars, notes, name or f"build {int(bars)}"
        )

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
                                name or f"snare roll {bars:g}")

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
                                name or f"clap build {bars:g}")

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
                                name or f"{style} fill")

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
            track_index, clip_index, bars, notes, name or f"riser {direction}"
        )

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
        return self._write_clip(track_index, clip_index, bars, notes, name or "impact")

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
        name: str | None = None,
        seed: int | None = None,
    ) -> dict:
        """The top-line that carries the drop -- short, high and repetitive."""
        chords, bars_per_chord = self._progression(
            key, scale, degrees, bars, octave, "triad"
        )
        notes = generators.generate_hook(
            chords,
            bars_per_chord=bars_per_chord,
            octave=octave,
            rhythm=rhythm,
            velocity=velocity,
            call_and_response=call_and_response,
            seed=seed,
        )
        return self._write_clip(
            track_index, clip_index, bars, notes, name or f"{key} hook"
        )

    # ------------------------------------------------------------------
    # Placeholders
    # ------------------------------------------------------------------

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
        created = []
        seen: dict[str, int] = {}
        for role in wanted:
            seen[role] = seen.get(role, 0) + 1
            base = labels.get(role, role.title())
            suffix = f" {seen[role]}" if wanted.count(role) > 1 else ""
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
        return self.bridge.call(
            "set_locators", markers=markers, clear_existing=clear_existing
        )

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
        """Remember which instrument to use for a role, e.g. bass -> Serum 2.

        `path` is a browser path such as
        "Plugins/VST3/Xfer Records/Serum 2" -- get one from search_devices.
        The preference persists across sessions.
        """
        if role.lower() not in arrangement.ROLES:
            raise ToolError(
                f"unknown role {role!r}; one of: {', '.join(arrangement.ROLES)}"
            )
        self.sounds.set_role(role, path)
        if favourite:
            self.sounds.add_favourite(path)
        return {"role": role.lower(), "path": path,
                "favourites": self.sounds.favourites()}

    def tool_forget_sound_preference(self, role: str) -> dict:
        """Drop a saved role preference, falling back to the stock device."""
        self.sounds.clear_role(role)
        return {"role": role.lower(), "now": self.sounds.for_role(role)}

    def tool_remember(self, rule: str) -> dict:
        """Save a standing instruction so it applies from now on.

        Use this whenever the user says to remember something, or states a
        preference as a rule -- "always use Serum for bass", "start tracks at
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
        result = self.bridge.call("load_device", **params)
        result["resolved_from"] = resolved_from
        result["path"] = path
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

    def tool_pick_sound(
        self,
        track_index: int,
        role: str,
        character: str | None = None,
        genre: str | None = None,
    ) -> dict:
        """Load a designed preset that suits the role, rather than a raw device.

        Live ships hundreds of presets per category made by people who could
        hear what they were doing. For "make it sound good" the library beats
        anything built from parameters, so this is the default path for sound.

        `character` narrows it -- warm, bright, evolving, dark for pads;
        supersaw, bright, stab, soft for leads; reese, sub, analog, deep, acid
        for bass. Left out, the genre decides.
        """
        role = (role or "").lower()
        categories = presets.PRESET_CATEGORIES.get(role)
        if not categories:
            raise ToolError(
                f"no preset category for role {role!r}; one of: "
                f"{', '.join(sorted(presets.PRESET_CATEGORIES))}"
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
                        result = self.tool_pick_sound(track_index=index, role=role)
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
        overrides = {
            int(t["track_index"]): str(t["role"]).lower()
            for t in (tracks or []) if "track_index" in t and "role" in t
        }

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
        overrides = {
            int(t["track_index"]): str(t["role"]).lower()
            for t in (tracks or []) if "track_index" in t and "role" in t
        }

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

        def try_set(fragment: str, value: float) -> None:
            match = next((n for n in names if fragment.lower() in n.lower()), None)
            if match is None:
                missing.append(fragment)
                return
            self.bridge.call(
                "set_device_parameter", track_index=track_index,
                device_index=target["index"], parameter=match,
                value=value, normalised=False,
            )
            applied.append(f"{match}={value}")

        if high_pass_hz:
            try_set("1 Filter On", 1.0)
            try_set("1 Frequency", float(high_pass_hz))
        if low_pass_hz:
            try_set("8 Filter On", 1.0)
            try_set("8 Frequency", float(low_pass_hz))

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

        def try_set(fragment: str, value: float) -> None:
            match = next((n for n in names if fragment.lower() in n.lower()), None)
            if match is None:
                missing.append(fragment)
                return
            self.bridge.call(
                "set_device_parameter", track_index=track_index,
                device_index=target["index"], parameter=match,
                value=value, normalised=False,
            )
            applied.append(f"{match}={value}")

        try_set("Threshold", setting.threshold_db)
        try_set("Ratio", setting.ratio)
        try_set("Attack", setting.attack_ms)
        try_set("Release", setting.release_ms)
        try_set("Makeup", setting.makeup_db)

        return {
            "track_index": track_index, "style": style,
            "device": target.get("name"), "set": applied,
            "not_found": missing, "why": setting.why,
        }

    def tool_add_sidechain_pump(
        self,
        track_index: int,
        clip_index: int = 0,
        depth: float = 0.6,
        bars: float | None = None,
        shape: str = "sharp",
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

    def tool_add_master_chain(self, ceiling_db: float = -0.3) -> dict:
        """Put a conservative chain on the master: EQ, glue, then a limiter.

        Deliberately restrained -- a safety net and a loudness ceiling, not
        mastering. Real mastering is a listening job. Use capture_audio and
        analyse_audio afterwards to see what it actually did.

        Track index -1 is the master; -2 and below are the return tracks.
        """
        MASTER = -1
        loaded, failed = [], []
        for path in (mixing.EQ_DEVICE, mixing.GLUE_DEVICE, mixing.LIMITER_DEVICE):
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
            for fragment, value in (
                ("Threshold", setting.threshold_db),
                ("Ratio", setting.ratio),
                ("Attack", setting.attack_ms),
                ("Release", setting.release_ms),
            ):
                match = next((n for n in names if fragment.lower() in n.lower()), None)
                if match:
                    self.bridge.call(
                        "set_device_parameter", track_index=MASTER,
                        device_index=glue["index"], parameter=match,
                        value=value, normalised=False,
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

    def tool_frequency_separation(self, tracks: list[dict] | None = None) -> dict:
        """High-pass every track that is not carrying the low end.

        Pads, chords, hats and leads all have energy below 200Hz that does
        nothing but crowd the kick and bass. Removing it is the change that
        most reliably makes a busy mix sound bigger.
        """
        state = self.bridge.call("get_song")
        overrides = {
            int(t["track_index"]): str(t["role"]).lower()
            for t in (tracks or []) if "track_index" in t and "role" in t
        }

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
