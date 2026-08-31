"""Sound design recipes -- build a patch on the fly.

Targets are given as *name fragments* rather than parameter indices, because
parameter layouts differ between Live's synths and shift between versions.
"Flt 1 Freq" on Wavetable and "LP Freq" on Drift both match a filter-cutoff
recipe that asks for "freq", so one recipe works across devices and simply
skips whatever a given synth does not have.

Values are normalised 0..1 and mapped onto each parameter's real range.

A caveat worth knowing: third-party plugins (Serum, Massive, Pigments) expose
no parameters to Live's API until you press Configure on the device and choose
which controls to expose. Until then a plugin can be loaded and played, but not
programmed from here -- so for those, load a preset instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Recipe:
    """A named patch: parameter name fragments mapped to normalised values."""

    name: str
    description: str
    settings: tuple[tuple[str, float], ...]
    prefers: tuple[str, ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = field(default_factory=tuple)


# Ordered so that later entries override earlier ones when fragments overlap.
RECIPES: dict[str, Recipe] = {
    # -- pads -----------------------------------------------------------
    "warm_pad": Recipe(
        "warm_pad",
        "Slow swell, soft top, long tail. Breakdown material.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 detune", 0.3), ("osc 2 detune", 0.38),
            ("osc 1 pan", 0.3), ("osc 2 pan", 0.7),
            ("flt 1 freq", 0.5), ("flt 1 res", 0.08),
            ("amp attack", 0.45), ("amp decay", 0.6),
            ("amp sustain", 0.9), ("amp release", 0.72),
            ("unison amount", 0.4),
        ),
        prefers=("Wavetable", "Analog", "Drift"),
        roles=("pad", "chords"),
    ),
    "evolving_pad": Recipe(
        "evolving_pad",
        "Moves while it sustains -- the wavetable position drifts under an LFO.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 pos", 0.35), ("osc 2 pos", 0.6),
            ("osc 2 detune", 0.42),
            ("flt 1 freq", 0.55), ("flt 1 res", 0.12),
            ("amp attack", 0.55), ("amp sustain", 0.95), ("amp release", 0.8),
            ("lfo 1 amount", 0.35), ("lfo 1 rate", 0.15),
            ("unison amount", 0.5),
        ),
        prefers=("Wavetable",),
        roles=("pad", "chords"),
    ),
    "glass_pad": Recipe(
        "glass_pad",
        "Bright and thin. Sits above a busy mix without adding weight.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 transp", 0.58), ("osc 2 detune", 0.25),
            ("flt 1 freq", 0.85), ("flt 1 res", 0.15),
            ("amp attack", 0.3), ("amp sustain", 0.85), ("amp release", 0.6),
            ("sub gain", 0.0),
        ),
        prefers=("Wavetable", "Drift"),
        roles=("pad", "chords"),
    ),

    # -- leads ----------------------------------------------------------
    "supersaw": Recipe(
        "supersaw",
        "Wide detuned saws -- the festival lead. Huge and slightly unstable.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 detune", 0.55), ("osc 2 detune", 0.62),
            ("osc 2 transp", 0.5),
            ("osc 1 pan", 0.25), ("osc 2 pan", 0.75),
            ("flt 1 freq", 0.95), ("flt 1 res", 0.12),
            ("amp attack", 0.02), ("amp decay", 0.6),
            ("amp sustain", 0.85), ("amp release", 0.35),
            ("unison amount", 0.8),
        ),
        prefers=("Wavetable", "Analog", "Drift"),
        roles=("lead", "hook", "chords"),
    ),
    "trance_lead": Recipe(
        "trance_lead",
        "Bright, tight and cutting. Built to sit on top of a full drop.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 detune", 0.35), ("osc 2 transp", 0.54),
            ("flt 1 freq", 0.9), ("flt 1 res", 0.22), ("flt 1 drive", 0.3),
            ("amp attack", 0.0), ("amp decay", 0.35),
            ("amp sustain", 0.8), ("amp release", 0.2),
            ("unison amount", 0.6),
        ),
        prefers=("Wavetable", "Drift"),
        roles=("lead", "hook"),
    ),
    "hoover": Recipe(
        "hoover",
        "Detuned, resonant and aggressive. Rave lead.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 detune", 0.7), ("osc 2 detune", 0.75),
            ("flt 1 freq", 0.55), ("flt 1 res", 0.5), ("flt 1 drive", 0.45),
            ("amp attack", 0.03), ("amp decay", 0.4),
            ("amp sustain", 0.7), ("amp release", 0.2),
        ),
        prefers=("Wavetable", "Analog"),
        roles=("lead", "hook"),
    ),

    # -- plucks ---------------------------------------------------------
    "pluck": Recipe(
        "pluck",
        "Short and percussive with a fast filter decay. Sits in a busy mix.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0), ("osc 2 detune", 0.2),
            ("flt 1 freq", 0.62), ("flt 1 res", 0.3),
            ("amp attack", 0.0), ("amp decay", 0.18),
            ("amp sustain", 0.0), ("amp release", 0.14),
            ("env 2 attack", 0.0), ("env 2 decay", 0.22),
            ("env 2 sustain", 0.0),
        ),
        prefers=("Wavetable", "Drift", "Operator"),
        roles=("arp", "chords", "hook", "lead"),
    ),
    "gated_pluck": Recipe(
        "gated_pluck",
        "Even shorter, with no tail at all. Trance sixteenths.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0), ("osc 2 detune", 0.3),
            ("flt 1 freq", 0.7), ("flt 1 res", 0.35),
            ("amp attack", 0.0), ("amp decay", 0.1),
            ("amp sustain", 0.0), ("amp release", 0.05),
            ("unison amount", 0.35),
        ),
        prefers=("Wavetable", "Drift"),
        roles=("arp", "hook", "lead"),
    ),
    "stab": Recipe(
        "stab",
        "Bright, short, hard-edged. Offbeat house chord hits.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0), ("osc 1 detune", 0.3),
            ("flt 1 freq", 0.8), ("flt 1 res", 0.22),
            ("amp attack", 0.0), ("amp decay", 0.25),
            ("amp sustain", 0.1), ("amp release", 0.1),
        ),
        prefers=("Wavetable", "Analog"),
        roles=("chords",),
    ),

    # -- basses ---------------------------------------------------------
    "reese": Recipe(
        "reese",
        "Two detuned saws filtered low and resonant. DnB and dubstep.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0),
            ("osc 1 detune", 0.42), ("osc 2 detune", 0.48),
            ("flt 1 freq", 0.28), ("flt 1 res", 0.45),
            ("amp attack", 0.0), ("amp decay", 0.5),
            ("amp sustain", 1.0), ("amp release", 0.12),
            ("sub gain", 0.4),
        ),
        prefers=("Wavetable", "Drift", "Operator"),
        roles=("bass", "sub"),
    ),
    "sub": Recipe(
        "sub",
        "A clean sine an octave down. No character -- just weight.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 0.0),
            ("osc 1 detune", 0.0),
            ("flt 1 freq", 1.0), ("flt 1 res", 0.0),
            ("amp attack", 0.01), ("amp decay", 0.4),
            ("amp sustain", 1.0), ("amp release", 0.08),
            ("sub on", 1.0), ("sub gain", 0.8),
        ),
        prefers=("Operator", "Drift", "Wavetable"),
        roles=("sub", "bass"),
    ),
    "rolling_bass": Recipe(
        "rolling_bass",
        "Short, tight and mid-forward. Built for sixteenths between the kicks.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0), ("osc 2 detune", 0.18),
            ("flt 1 freq", 0.42), ("flt 1 res", 0.3), ("flt 1 drive", 0.35),
            ("amp attack", 0.0), ("amp decay", 0.2),
            ("amp sustain", 0.35), ("amp release", 0.08),
            ("sub gain", 0.5),
        ),
        prefers=("Wavetable", "Drift", "Operator"),
        roles=("bass",),
    ),
    "acid": Recipe(
        "acid",
        "Low cutoff, high resonance, snappy envelope. 303 territory.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 0.0),
            ("flt 1 freq", 0.3), ("flt 1 res", 0.82), ("flt 1 drive", 0.5),
            ("amp attack", 0.0), ("amp decay", 0.22),
            ("amp sustain", 0.2), ("amp release", 0.08),
            ("env 2 decay", 0.25),
        ),
        prefers=("Operator", "Drift", "Wavetable"),
        roles=("bass", "lead"),
    ),

    # -- texture --------------------------------------------------------
    "dark_drone": Recipe(
        "dark_drone",
        "Filtered right down, slow and static. Tension under a breakdown.",
        (
            ("osc 1 on", 1.0), ("osc 2 on", 1.0), ("osc 1 detune", 0.2),
            ("flt 1 freq", 0.18), ("flt 1 res", 0.2),
            ("amp attack", 0.6), ("amp decay", 0.8),
            ("amp sustain", 1.0), ("amp release", 0.85),
        ),
        prefers=("Wavetable", "Drift"),
        roles=("pad", "fx"),
    ),
}

ALIASES = {
    "saw": "supersaw", "super_saw": "supersaw", "festival": "supersaw",
    "wide": "supersaw", "bright_lead": "trance_lead", "lead": "trance_lead",
    "uplifting": "trance_lead", "trance": "trance_lead",
    "reese_bass": "reese", "growl": "reese",
    "sine": "sub", "808": "sub", "subbass": "sub",
    "rolling": "rolling_bass", "tech_bass": "rolling_bass",
    "plucked": "pluck", "pizz": "pluck", "gate": "gated_pluck",
    "chord_stab": "stab", "organ_stab": "stab",
    "pad": "warm_pad", "soft_pad": "warm_pad", "evolving": "evolving_pad",
    "glass": "glass_pad", "bright_pad": "glass_pad",
    "303": "acid", "squelch": "acid",
    "rave": "hoover",
    "drone": "dark_drone", "dark": "dark_drone",
}

# Live's own preset library, by role. Browsable and loadable, which is the
# right answer when the target is a plugin we cannot program.
PRESET_CATEGORIES: dict[str, tuple[str, ...]] = {
    "bass": ("Sounds/Bass", "Instruments/Wavetable/Bass", "Instruments/Operator/Bass"),
    "sub": ("Sounds/Bass", "Instruments/Operator/Bass"),
    "lead": ("Sounds/Synth Lead", "Instruments/Wavetable/Synth Lead"),
    "hook": ("Sounds/Synth Lead", "Instruments/Wavetable/Synth Lead"),
    "chords": ("Sounds/Synth Keys", "Instruments/Wavetable/Synth Keys"),
    "arp": ("Sounds/Guitar & Plucked", "Instruments/Wavetable/Synth Keys"),
    "pad": ("Sounds/Pad", "Instruments/Wavetable/Pad"),
    # "Drums" lists kits as .adg presets; the bare "Drum Rack" entry in there is
    # an empty container and must not be what a drum role loads.
    "drums": ("Drums",),
    "kick": ("Drums",),
    "perc": ("Sounds/Percussive", "Drums"),
    "fx": ("Sounds/Effects", "Sounds/Ambient & Evolving"),
    "riser": ("Sounds/Effects", "Sounds/Ambient & Evolving"),
    "impact": ("Drums", "Sounds/Percussive"),
}


def resolve(name: str) -> Recipe:
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in RECIPES:
        raise ValueError(
            f"unknown patch {name!r}; try one of: "
            f"{', '.join(sorted(set(RECIPES) | set(ALIASES)))}"
        )
    return RECIPES[key]


def for_role(role: str) -> list[str]:
    """Which patches suit a musical role."""
    role = (role or "").lower()
    return [name for name, r in RECIPES.items() if role in r.roles]


def match_parameters(
    recipe: Recipe, available: list[dict]
) -> list[tuple[dict, float]]:
    """Pair a recipe's fragments with the parameters a device actually has.

    Longest fragment wins, and each parameter is claimed once, so "osc 1 detune"
    does not also get matched by a looser "detune" entry later in the recipe.
    """
    claimed: set[str] = set()
    matched: list[tuple[dict, float]] = []

    ordered = sorted(recipe.settings, key=lambda kv: -len(kv[0]))
    for fragment, value in ordered:
        needle = fragment.lower()
        best = None
        for parameter in available:
            name = str(parameter.get("name", "")).lower()
            if name in claimed or needle not in name:
                continue
            # Prefer the shortest containing name -- the most specific match.
            if best is None or len(name) < len(str(best.get("name", ""))):
                best = parameter
        if best is not None:
            claimed.add(str(best.get("name", "")).lower())
            matched.append((best, value))

    return matched


# ----------------------------------------------------------------------
# Picking a designed preset, which beats building a patch from parameters
# ----------------------------------------------------------------------

# Live ships hundreds of presets per category, made by people who could hear
# what they were doing. Programming a synth from a parameter recipe is useful
# when nothing suitable exists, but for "make it sound good" the library wins.
#
# Ordered best-first; the picker takes the first that matches something in the
# user's library, so a missing preset falls through rather than failing.
PRESET_PICKS: dict[str, dict[str, tuple[str, ...]]] = {
    "pad": {
        "warm":    ("Warm Analog Pad", "Analog Soft Pad", "Soft Shimmer", "warm", "soft pad"),
        "bright":  ("Bright Filterless Sweep", "Shimmer", "Glass Motion", "bright"),
        "evolving": ("Slow Sweep", "Motion Pad", "Filter Sweep Pad", "sweep"),
        "dark":    ("Dark", "Deep Pad", "Muted", "analog pad"),
    },
    "lead": {
        "supersaw": ("Mega Saw", "Classic Club Saw", "Superstring Lead", "Bright SAW", "saw lead"),
        "bright":   ("Bright SAW Lead", "Bright Overtone", "Dual OSC Sync Bright", "bright lead"),
        "stab":     ("Analog Stab Lead", "Trident Stab", "stab"),
        "soft":     ("Super Soover", "Air Flute", "soft lead"),
    },
    "hook": {
        "supersaw": ("Mega Saw", "Classic Club Saw", "Bright SAW", "saw lead"),
        "pluck":    ("Basic Pluck Keys", "Plucked Keys", "pluck"),
    },
    "arp": {
        "pluck":  ("Basic Pluck Keys", "Plucked Keys", "pluck", "Basic Bell Keys"),
        "bell":   ("Basic Bell Keys", "Retro Bell", "bell"),
    },
    "chords": {
        "warm":   ("Warm Analog Pad", "A Soft Chord", "Analog Soft Pad", "keys"),
        "pluck":  ("Basic Pluck Keys", "Plucked Keys", "pluck"),
        "stab":   ("Analog Stab Lead", "Trident Stab", "stab"),
    },
    "bass": {
        "reese":   ("Complex Reese Bass", "Basic Reese Bass", "reese"),
        "sub":     ("Basic Sub Sine", "Basic Sub Boom", "sub bass"),
        "analog":  ("Basic Analog Bass", "Analog Bass", "analog bass"),
        "deep":    ("Deep Bass", "FM Deep Bass", "deep bass"),
        "acid":    ("Acid Bass", "acid"),
    },
    "sub": {
        "sub": ("Basic Sub Sine", "Basic Sub Boom", "sub"),
    },
}

# What each role gets when no character is asked for.
DEFAULT_CHARACTER: dict[str, str] = {
    "pad": "warm", "lead": "supersaw", "hook": "supersaw", "arp": "pluck",
    "chords": "warm", "bass": "analog", "sub": "sub",
}

# Which character suits which genre, where it differs from the default.
GENRE_CHARACTER: dict[str, dict[str, str]] = {
    "trance":         {"lead": "supersaw", "pad": "evolving", "bass": "analog"},
    "big_room":       {"lead": "supersaw", "pad": "bright", "bass": "analog"},
    "progressive":    {"lead": "bright", "pad": "evolving", "chords": "pluck", "bass": "deep"},
    "melodic_techno": {"lead": "bright", "pad": "dark", "arp": "pluck", "bass": "analog"},
    "deep_house":     {"chords": "warm", "pad": "warm", "bass": "deep", "lead": "soft"},
    "tech_house":     {"chords": "stab", "bass": "analog", "lead": "stab"},
    "house":          {"chords": "pluck", "pad": "warm", "bass": "analog"},
    "techno":         {"pad": "dark", "bass": "acid", "lead": "stab"},
    "dnb":            {"bass": "reese", "pad": "dark", "lead": "bright"},
    # --- outside dance music. These pick acoustic and orchestral sources,
    # which is why the roles exist alongside the synth ones.
    "cinematic":  {"strings": "swell", "pad": "dark", "piano": "grand",
                   "brass": "epic", "perc": "taiko", "choir": "epic"},
    "trailer":    {"strings": "tension", "brass": "epic", "perc": "taiko",
                   "impact": "boom", "choir": "epic"},
    "orchestral": {"strings": "ensemble", "brass": "section",
                   "woodwind": "solo", "harp": "harp", "perc": "timpani"},
    "classical":  {"piano": "grand", "strings": "chamber",
                   "woodwind": "solo", "harp": "harp"},
    "chamber":    {"strings": "chamber", "piano": "grand", "woodwind": "solo"},
    "score":      {"strings": "swell", "pad": "drone", "piano": "felt",
                   "woodwind": "solo"},
    "ambient":    {"pad": "evolving", "strings": "swell", "piano": "felt",
                   "mallet": "bell"},
    "jazz":       {"piano": "rhodes", "bass": "upright", "drums": "brush",
                   "brass": "sax", "guitar": "hollow"},
    "lo_fi":      {"piano": "rhodes", "drums": "dusty", "bass": "warm",
                   "guitar": "nylon", "pad": "tape"},
    "hip_hop":    {"bass": "808", "drums": "boom", "piano": "rhodes",
                   "pad": "dusty"},
    "trap":       {"bass": "808", "drums": "trap", "lead": "bell",
                   "pad": "dark"},
    "pop":        {"chords": "bright", "lead": "pluck", "bass": "clean",
                   "piano": "grand", "strings": "section"},
    "rock":       {"guitar": "drive", "bass": "pick", "drums": "acoustic",
                   "organ": "hammond"},
    "folk":       {"guitar": "nylon", "strings": "chamber", "piano": "upright",
                   "mallet": "glock"},
}


def picks_for(role: str, character: str | None = None,
              genre: str | None = None) -> tuple[str, ...]:
    """The ordered preset name fragments to try for a role."""
    from .arrangement import normalise_role
    role = normalise_role(role) or (role or "").lower()
    table = PRESET_PICKS.get(role)
    if not table:
        return ()
    if character is None and genre:
        character = GENRE_CHARACTER.get(genre.lower(), {}).get(role)
    character = character or DEFAULT_CHARACTER.get(role)
    if character and character in table:
        return table[character]
    # Fall back to every fragment the role knows, best-first.
    return tuple(f for group in table.values() for f in group)
