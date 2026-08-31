"""Build Anthropic tool schemas from the Toolbox's Python signatures.

Hand-maintained JSON schemas drift from the code the moment anyone adds a
parameter. These are derived from the real signatures and docstrings instead, so
the model always sees what the tools actually accept.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any, get_args, get_origin

from . import (
    arrangement, basslines, generators, groove, harmony, hooks, leads, melody,
    mixing, presets, theory, variations, voicings,
)
from .tools import Toolbox

# Prose for parameters whose meaning isn't obvious from the name alone.
PARAM_DOCS: dict[str, str] = {
    "degrees": (
        "The chord progression, as scale degrees (\"1-6-4-5\" or [1, 6, 4, 5]) "
        "or the name of a stock progression. Pass \"learned\" to walk the "
        "chord-to-chord moves of the references in the corpus, which is how a "
        "generated progression comes out sounding like the material that was "
        "fed in rather than like a preset. Ignored when reference_track is set."
    ),
    "reference_track": (
        "Index of a track whose clip already holds the harmony this part should "
        "follow. When set, the key, scale, chord qualities and harmonic rhythm "
        "are read from that clip and the key/scale/degrees arguments are "
        "ignored. Use it whenever the set already contains chords: a degree "
        "number alone rebuilds the second chord of D minor as E diminished even "
        "when the clip plainly plays Em7, and a part generated from that "
        "disagrees with what is actually sounding."
    ),
    "reference_clip": (
        "Which slot on reference_track to read the harmony from. Defaults to 0."
    ),
    "track_index": "Zero-based track index in the Live set.",
    "clip_index": "Zero-based clip slot (scene row) on that track.",
    "key": "Root note, e.g. 'C', 'F#', 'Eb'.",
    "scale": f"Scale name. One of: {', '.join(sorted(theory.SCALES))}.",
    "degrees": (
        "Chord progression as scale degrees: '1-6-4-5', a list like [1,6,4,5], "
        "roman numerals 'i-VI-IV-V', or a named progression "
        f"({', '.join(sorted(theory.PROGRESSIONS))})."
    ),
    "bars": "Clip length in bars (4 beats each).",
    "octave": "Octave for the generated material. 3 is around middle C.",
    "extension": "Chord size: 'triad', 'seventh' or 'ninth'.",
    "rhythm": f"Rhythm pattern. One of: {', '.join(sorted(generators.RHYTHM_PATTERNS))}.",
    "pattern": f"Drum pattern. One of: {', '.join(sorted(generators.DRUM_PATTERNS))}.",
    "style": (
        "Playing style for the part. \"learned\" takes the articulation the "
        "corpus favours, from the references learn_references was run on."
    ),
    "velocity": "Base MIDI velocity, 1-127.",
    "swing": "Swing amount 0.0-1.0; delays off-beat sixteenths.",
    "humanise": "Timing/velocity jitter, 0.0-1.0. Keep low (0.1-0.3) for EDM.",
    "seed": "Random seed, so a result can be reproduced or deliberately varied.",
    "spread": "Strum amount in sixteenths; 0 plays the chord as a block.",
    "template": (
        "Arrangement template. One of: "
        f"{', '.join(sorted(set(arrangement.TEMPLATES) | set(arrangement.ALIASES)))}."
    ),
    "target_seconds": "Desired track length in seconds (360 = 6 minutes).",
    "phrase_bars": "Phrase length sections snap to. 8 is standard for dance music.",
    "role": (
        "Musical role this track plays, used for colour and for arrangement "
        f"placement. One of: {', '.join(arrangement.ROLES)}."
    ),
    "sections": (
        "Section list from plan_arrangement -- objects with name, start_bar, "
        "bars and roles."
    ),
    "tracks": (
        "Maps roles onto real clips: "
        '[{"track_index": 0, "clip_index": 0, "role": "kick"}, ...]. '
        "A track is placed in every section whose roles include its role."
    ),
    "notes": (
        "Raw MIDI notes: [{'pitch': 60, 'start': 0.0, 'duration': 1.0, "
        "'velocity': 100}]. start and duration are in beats from the clip start."
    ),
    "markers": '[{"name": "Drop 1", "start_bar": 48}, ...]',
    "rate": "Note rate: '1/4', '1/8', '1/16' or '1/32'.",
    "contour": "Melodic shape: 'rise', 'fall', 'arch', 'valley' or 'random'.",
    "kind": "'audio' for stems and recordings, 'midi' for a synth slot.",
    "path": "Browser path, e.g. 'Instruments/Drums/Drum Rack'.",
    "action": (
        "One of: play, stop, fire_clip, stop_clip, show_session, show_arrangement."
    ),
    "include_notes": "Include every clip's MIDI notes. Costly -- only when needed.",
    "mode": "'replace' wipes the clip first; 'add' merges into what's there.",
    "direction": "'up' or 'down'.",
    "call_and_response": "Answer each phrase with a lower, sparser variation.",
    "fill_last_bar": "Replace the last bar's hats with a tom fill.",
    "clear_first": "Wipe existing arrangement clips on these tracks first.",
}

def _vocab(*sources: Any) -> list[str]:
    """Flatten dict keys and iterables into a sorted list of allowed values."""
    values: set[str] = set()
    for source in sources:
        values.update(str(v) for v in source)
    return sorted(values)


# Parameters with a closed vocabulary. Without these the model passes a
# plausible-sounding word that does not exist -- "extended" for a variation,
# say -- and the call fails at the far end where the error is least useful.
# Sourced from the modules themselves so the schema cannot drift from the code.
ENUMS: dict[str, list[str]] = {
    "scale": _vocab(theory.SCALES, theory.ALIASES),
    "variation": _vocab(harmony.RECIPES),
    "mutations": variations.mutation_vocabulary(),
    "voicing": _vocab(voicings.STYLES, voicings.ALIASES),
    "extension": voicings.extension_vocabulary(),
    "pattern": generators.pattern_vocabulary(),
    "groove": _vocab(groove.GROOVES, groove.ALIASES, ["learned"]),
    "groove_name": _vocab(groove.GROOVES, groove.ALIASES, ["learned"]),
    "genre": _vocab(presets.GENRE_CHARACTER),
    "patch": _vocab(presets.RECIPES, presets.ALIASES),
    "role": arrangement.role_vocabulary(),
    "template": _vocab(arrangement.TEMPLATES, arrangement.ALIASES),
    "contour": ["arch", "fall", "random", "rise", "valley"],
    "arc": ["arch", "cadence", "calm", "drive", "rise"],
    "breakdown": ["dominant", "lift", "parallel", "reharmonise", "relative",
                  "semitone_lift", "subdominant"],
    "climax": ["dominant", "lift", "parallel", "relative", "semitone_lift",
               "subdominant"],
    "hook_style": sorted({s for v in hooks.HOOK_PATTERNS.values()
                          for s in v["styles"]}),
    "kind": ["audio", "midi"],
    "mode": ["add", "replace"],
    "kick_mode": ["avoid", "ignore", "lock", "shorten"],
    "direction": ["down", "up"],
    "rate": ["1/4", "1/8", "1/16", "1/32"],
    "shape": ["arch", "fall", "rise", "step", "valley"],
    "move": ["close_for_break", "drop_open", "open_into_drop", "pulse", "swell"],
    "wave": ["noise", "random", "saw_down", "saw_up", "sine", "square", "triangle"],
    "action": ["fire_clip", "play", "show_arrangement", "show_session", "stop",
               "stop_clip"],
    "instrument": _vocab(generators.DRUM_MAP),
    "instruments": _vocab(generators.DRUM_MAP),
}

# Object-shaped parameters need their fields described, or the model invents a
# shape. A bare {"type": "object"} tells it nothing.
ITEM_SCHEMAS: dict[str, dict[str, Any]] = {
    "sections": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "start_bar": {"type": "number"},
            "bars": {"type": "number"},
            "end_bar": {"type": "number"},
            "energy": {"type": "number"},
            "roles": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["start_bar", "bars", "roles"],
    },
    "tracks": {
        "type": "object",
        "properties": {
            "track_index": {"type": "integer"},
            "role": {"type": "string", "enum": list(arrangement.ROLES)},
            "clip_index": {"type": "integer"},
            "clip_indices": {"type": "array", "items": {"type": "integer"}},
            "variation_policy": {
                "type": "string",
                "enum": ["cycle", "escalate", "random"],
            },
        },
        "required": ["track_index", "role"],
    },
    "notes": {
        "type": "object",
        "properties": {
            "pitch": {"type": "integer"},
            "start": {"type": "number"},
            "duration": {"type": "number"},
            "velocity": {"type": "integer"},
        },
        "required": ["pitch", "start"],
    },
    "markers": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "start_bar": {"type": "number"},
        },
        "required": ["name", "start_bar"],
    },
}


# The same parameter name means different things in different tools, so these
# win over the table above.
TOOL_ENUMS: dict[str, dict[str, list[str]]] = {
    "create_varied_chords": {"rhythm": _vocab(generators.CHORD_COMPS)},
    "create_chord_clip": {"rhythm": _vocab(generators.RHYTHM_PATTERNS)},
    "create_hook_clip": {
        "pattern": sorted(hooks.HOOK_PATTERNS) + ["motif"],
    },
    "create_styled_bass": {
        "style": _vocab(basslines.STYLES, basslines.ALIASES, ["learned"])
    },
    "create_bass_clip": {
        "style": ["fifth", "octave", "root", "walk"],
        "rhythm": _vocab(generators.RHYTHM_PATTERNS),
    },
    "create_lead_clip": {"style": _vocab(leads.STYLES, leads.ALIASES)},
    "create_melody_clip": {
        "rhythm": _vocab(melody.RHYTHMS),
        "variation": _vocab(harmony.RECIPES),
    },
    "create_arpeggio_clip": {"style": list(generators.ARP_STYLES)},
    "create_hook_clip": {"rhythm": _vocab(generators.RHYTHM_PATTERNS)},
    "add_compression": {"style": _vocab(mixing.COMPRESSION)},
    "create_drum_fill": {"style": ["snare", "stutter", "toms"]},
    "design_sound": {"patch": _vocab(presets.RECIPES, presets.ALIASES)},
    "pick_sound": {
        "character": _vocab(
            {c for table in presets.PRESET_PICKS.values() for c in table}
        ),
    },
    "build_track": {"genre": _vocab({"trance", "tech_house", "house",
                                     "deep_house", "techno", "melodic_techno",
                                     "big_room", "progressive", "dnb",
                                     "edm", "festival", "prog", "trap"})},
    "set_view": {"view": ["arrangement", "session"]},
}


_JSON_TYPES: dict[Any, str] = {
    int: "integer",
    float: "number",
    bool: "boolean",
    str: "string",
    dict: "object",
    list: "array",
}


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    """Map a Python type hint onto a JSON-schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = get_origin(annotation)

    # Optional[X] / X | None -- unwrap to the non-None member.
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _schema_for_annotation(members[0])
        # A genuine multi-type union: let the model send anything sensible.
        return {}

    if origin in (list, typing.List):
        args = get_args(annotation)
        item = _schema_for_annotation(args[0]) if args else {}
        return {"type": "array", "items": item or {"type": "object"}}

    if origin in (dict, typing.Dict):
        return {"type": "object"}

    if origin in (tuple, typing.Tuple):
        return {"type": "array"}

    if annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}

    return {"type": "string"}


def _describe(func: Any) -> str:
    doc = inspect.getdoc(func) or ""
    return doc.strip() or "No description."


def tool_schemas() -> list[dict[str, Any]]:
    """Every Toolbox method exposed to the model, as Anthropic tool definitions."""
    schemas: list[dict[str, Any]] = []

    for attr in sorted(dir(Toolbox)):
        if not attr.startswith("tool_"):
            continue
        func = getattr(Toolbox, attr)
        name = attr[len("tool_"):]
        signature = inspect.signature(func)
        # PEP 563 turns annotations into strings; resolve them back to types.
        try:
            hints = typing.get_type_hints(func)
        except Exception:  # a hint referencing something unimportable
            hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in signature.parameters.items():
            if param_name == "self":
                continue
            prop = _schema_for_annotation(hints.get(param_name, param.annotation))
            description = PARAM_DOCS.get(param_name)
            if description:
                prop = {**prop, "description": description}

            # An object-shaped list needs its fields spelled out.
            item_schema = ITEM_SCHEMAS.get(param_name)
            if item_schema and prop.get("type") == "array":
                prop = {**prop, "items": item_schema}

            # A closed vocabulary belongs in the schema, not only in the prose.
            allowed = TOOL_ENUMS.get(name, {}).get(param_name) or ENUMS.get(param_name)
            if allowed:
                if prop.get("type") == "array":
                    prop = {**prop, "items": {"type": "string", "enum": allowed}}
                elif prop.get("type") in (None, "string"):
                    prop = {**prop, "type": "string", "enum": allowed}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            elif param.default is not None:
                prop = {**prop, "default": param.default}
            properties[param_name] = prop

        schemas.append(
            {
                "name": name,
                "description": _describe(func),
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )

    return schemas


def render_for_text_protocol(schemas: list[dict[str, Any]]) -> str:
    """A compact plain-text rendering, for backends without native tool use."""
    lines: list[str] = []
    for schema in schemas:
        params = []
        for param_name, prop in schema["input_schema"]["properties"].items():
            kind = prop.get("type", "any")
            if param_name in schema["input_schema"]["required"]:
                params.append(f"{param_name}: {kind}  (required)")
            else:
                default = prop.get("default")
                params.append(f"{param_name}: {kind} = {default!r}")
        headline = schema["description"].split("\n")[0]
        lines.append(f"- {schema['name']}({', '.join(params)})\n    {headline}")
    return "\n".join(lines)
