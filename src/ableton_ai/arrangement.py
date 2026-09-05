"""Song-structure planning.

Given a target duration and a tempo, work out a section map in bars -- intro,
build, drop, breakdown, outro -- and which instrument roles play in each one.
The LLM picks the template and tweaks it; this module keeps the bar maths honest
so sections always land on phrase boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

BEATS_PER_BAR = 4.0

# Roles are abstract: the agent maps its actual tracks onto these names.
# The EDM-specific ones (riser/impact/hook/vocal) are what make a build actually
# build and a drop actually land.
ROLES = (
    "kick", "drums", "bass", "sub", "chords", "lead", "hook",
    "pad", "pulse", "arp", "fx", "riser", "impact", "vocal", "perc",
    # Acoustic and orchestral roles. Without these, "write a string quartet"
    # or "a piano and cello cue" had to be forced through "chords" and "lead",
    # which chose synths and voiced them like synths.
    "strings", "brass", "woodwind", "piano", "guitar", "choir", "mallet",
    "harp", "organ",
)

# Roles that exist to be filled in later by hand rather than generated.
PLACEHOLDER_ROLES = ("vocal", "fx")

# The words producers actually use, mapped onto the canonical roles. A closed
# vocabulary is only useful if the obvious synonym for a thing resolves to it:
# "melody" is the natural word for a lead, and refusing it is a worse answer
# than accepting it.
# At most one of these should sound in a section. Three top lines in the same
# register cancel each other out, however good each is alone.
TOP_LINE_ROLES = ("hook", "lead", "arp")

ROLE_ALIASES: dict[str, str] = {
    "melody": "lead", "topline": "lead", "top_line": "lead", "top": "lead",
    "motif": "lead", "riff": "lead", "solo": "lead",
    "keys": "chords", "piano": "chords", "stab": "chords", "stabs": "chords",
    "harmony": "chords", "chord": "chords",
    "pluck": "arp", "plucks": "arp", "arpeggio": "arp", "sequence": "arp",
    "percussion": "perc", "shaker": "perc", "conga": "perc", "tops": "perc",
    "hat": "drums", "hats": "drums", "snare": "drums", "clap": "drums",
    "kit": "drums", "beat": "drums", "loop": "drums",
    "kicks": "kick", "bd": "kick",
    "subbass": "sub", "sub_bass": "sub", "808": "sub",
    "vocals": "vocal", "vox": "vocal", "voice": "vocal",
    "effects": "fx", "sfx": "fx", "atmos": "fx", "ambience": "fx",
    "rise": "riser", "sweep": "riser", "uplifter": "riser", "build": "riser",
    "impacts": "impact", "crash": "impact", "hit": "impact", "downlifter": "impact",
    "pads": "pad", "atmosphere": "pad", "big_pad": "pad", "bigpad": "pad",
    "sustained_pad": "pad", "warm_pad": "pad", "ambient_pad": "pad",
    "pulse_pad": "pulse", "pulsepad": "pulse", "pulses": "pulse",
    "rhythmic_pad": "pulse", "stab_pad": "pulse", "offbeat_pad": "pulse",
    "pumping_pad": "pulse", "gate_pad": "pulse", "gated_pad": "pulse",
    "hooks": "hook", "vocal_chop": "hook",
    "leads": "lead",
}


def normalise_role(role: str) -> str | None:
    """Resolve a role name to its canonical form, or None if unknown."""
    key = (role or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in ROLES:
        return key
    return ROLE_ALIASES.get(key)


def role_vocabulary() -> list[str]:
    """Everything a caller may legitimately say for a role."""
    return sorted(set(ROLES) | set(ROLE_ALIASES))

# When a template asks for a role no track provides, fall back to the nearest
# musical neighbour. Without this, a set with a "Bass" track but no "Sub" goes
# silent through every section that only listed "sub".
# Instrument names people actually use, mapped onto roles. Without these,
# "cello" and "trumpet" matched nothing and the part fell back to a synth.
ROLE_ALIASES.update({
    # The counter-line from compose_theme: a second melodic voice that answers
    # the lead. It behaves like a melody for register and dynamics.
    "counter": "melody", "countermelody": "melody", "counter_melody": "melody",
    "answer": "melody",
    "violin": "strings", "viola": "strings", "cello": "strings",
    "double_bass": "strings", "contrabass": "strings", "orchestra": "strings",
    "orchestral": "strings", "ensemble": "strings", "section": "strings",
    "arco": "strings", "pizzicato": "strings", "pizz": "strings",
    "quartet": "strings", "violins": "strings", "cellos": "strings",
    "trumpet": "brass", "trombone": "brass", "horn": "brass",
    "french_horn": "brass", "tuba": "brass", "horns": "brass",
    "fanfare": "brass",
    "flute": "woodwind", "clarinet": "woodwind", "oboe": "woodwind",
    "bassoon": "woodwind", "sax": "woodwind", "saxophone": "woodwind",
    "winds": "woodwind", "wind": "woodwind", "reed": "woodwind",
    "grand_piano": "piano", "grand": "piano", "upright": "piano",
    "rhodes": "piano", "wurlitzer": "piano", "wurli": "piano",
    "clav": "piano", "clavinet": "piano", "epiano": "piano",
    "electric_piano": "piano", "keyboard": "piano",
    "acoustic_guitar": "guitar", "electric_guitar": "guitar",
    "nylon": "guitar", "banjo": "guitar", "mandolin": "guitar",
    "ukulele": "guitar", "strum": "guitar",
    "choral": "choir", "chorus_voices": "choir", "aah": "choir",
    "ooh": "choir", "vocal_pad": "choir",
    "marimba": "mallet", "vibraphone": "mallet", "vibes": "mallet",
    "xylophone": "mallet", "glockenspiel": "mallet", "glock": "mallet",
    "bells": "mallet", "bell": "mallet", "celesta": "mallet",
    "kalimba": "mallet", "tubular": "mallet",
    "hammond": "organ", "church_organ": "organ", "b3": "organ",
    "harpsichord": "piano", "timpani": "perc", "taiko": "perc",
    "orchestral_perc": "perc", "cymbal": "perc", "gong": "impact",
})

ROLE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "sub": ("bass",),
    "bass": ("sub",),
    "hook": ("lead", "chords"),
    "lead": ("hook", "chords"),
    "arp": ("chords", "lead"),
    "chords": ("pad", "pulse", "arp"),
    "pad": ("chords", "pulse"),
    "pulse": ("chords", "arp", "pad"),
    "perc": ("drums",),
    "kick": ("drums",),
}

# Acoustic roles substitute towards each other before falling back to a synth,
# so a cinematic cue missing brass doubles the strings rather than reaching for
# a supersaw.
ROLE_FALLBACKS.update({
    "strings": ("pad", "chords", "choir"),
    "brass": ("strings", "lead", "chords"),
    "woodwind": ("strings", "lead", "pad"),
    "piano": ("chords", "keys", "pad"),
    "guitar": ("piano", "chords", "arp"),
    "choir": ("pad", "strings", "vocal"),
    "mallet": ("arp", "hook", "piano"),
    "harp": ("arp", "mallet", "piano"),
    "organ": ("chords", "pad", "piano"),
    "drums": ("perc", "kick"),
})

# Deliberately absent: vocal and fx are placeholders the user fills in by hand,
# and riser and impact are placed by position rather than looped. Substituting
# for any of them changes what the arrangement *means* -- an fx slot silently
# becoming a riser put a third riser in a two-build track.


@dataclass
class Section:
    name: str
    start_bar: int
    bars: int
    energy: float  # 0..1, drives velocity and filter decisions
    roles: list[str] = field(default_factory=list)

    @property
    def end_bar(self) -> int:
        return self.start_bar + self.bars

    @property
    def kind(self) -> str:
        """Coarse category, used to decide where risers and impacts go."""
        n = self.name.lower()
        if "build" in n or "rise" in n:
            return "build"
        if "drop" in n or "chorus" in n or "peak" in n:
            return "drop"
        if "break" in n or "bridge" in n:
            return "breakdown"
        if "intro" in n:
            return "intro"
        if "outro" in n or "fade" in n:
            return "outro"
        return "groove"

    def seconds(self, tempo: float) -> float:
        return self.bars * BEATS_PER_BAR / tempo * 60.0

    def to_dict(self, tempo: float) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "start_bar": self.start_bar,
            "bars": self.bars,
            "end_bar": self.end_bar,
            "energy": round(self.energy, 2),
            "roles": list(self.roles),
            "seconds": round(self.seconds(tempo), 1),
        }


# Each entry is (name, relative weight, energy, roles).
# Weights are proportional -- the planner scales them to hit the target length.
# A "build" section always ends by handing over to the drop that follows it, so
# risers belong there and impacts belong on the drop's first bar.
# Roles that sustain and so can "drop out" for the bar before a big lift --
# the classic moment of air before a drop. Drums are handled separately.
SUSTAINED_FOR_DROPOUT = (
    "bass", "sub", "808", "chords", "pad", "pulse", "keys", "strings", "arp",
    "lead", "hook", "melody", "organ", "guitar",
)


def is_dance_form(sections: list["Section"]) -> bool:
    """True when the arrangement is built around drops.

    Dance craft -- the bar of air before the drop, a fill every phrase -- is
    exactly wrong in a through-composed cinematic or classical cue, in a
    beatless ambient piece whose "peak" is a swell, and in a pop song, whose
    chorus reads as a "drop" to the coarse kind() but wants a subtle lift, not
    a full pre-drop cut and a drum fill every eight bars. Dance needs all
    three: a drop, a kick-driven groove, AND an explicit build section handing
    up into it -- which pop and cinematic forms do not have.
    """
    has_drop = any(s.kind == "drop" for s in sections)
    has_beat = any("kick" in s.roles or "drums" in s.roles for s in sections)
    has_build = any(s.kind == "build" for s in sections)
    return has_drop and has_beat and has_build


# What layer of the mix each role occupies. This drives how a section is built
# from the tracks that are actually present, rather than from a fixed
# role list that assumes a particular band.
TIER: dict[str, str] = {
    # foundation -- the groove that almost never drops out
    "kick": "foundation", "drums": "foundation", "bass": "foundation",
    "sub": "foundation", "808": "foundation",
    # core -- the harmonic bed that defines the song
    "chords": "core", "pad": "core", "pulse": "core", "keys": "core",
    "piano": "core",
    "guitar": "core", "organ": "core", "strings": "core", "rhodes": "core",
    # topline -- the tune that carries the section
    "lead": "topline", "hook": "topline", "melody": "topline",
    "arp": "topline", "vocal": "topline", "brass": "topline",
    "woodwind": "topline", "mallet": "topline", "choir": "topline",
    # colour -- accents and transitions
    "perc": "colour", "fx": "colour", "riser": "colour", "impact": "colour",
    "harp": "colour",
}


def tier_of(role: str) -> str:
    """Which mix layer a role sits in; unknown roles are 'core' -- present in
    the song, never silently dropped."""
    return TIER.get((role or "").lower(), "core")


# Which tiers a section carries, by its kind. The principle that fixes "the
# verse is just hi-hats": a verse is not a stripped intro, it is the full
# groove at lower energy -- foundation and core always, a topline usually.
# Only the intro and the outro genuinely thin out.
SECTION_TIERS: dict[str, tuple[str, ...]] = {
    "intro":     ("foundation", "core"),
    "verse":     ("foundation", "core", "topline"),
    "groove":    ("foundation", "core", "topline"),
    "build":     ("foundation", "core", "colour"),
    "drop":      ("foundation", "core", "topline", "colour"),
    "chorus":    ("foundation", "core", "topline", "colour"),
    "breakdown": ("core", "topline"),
    "bridge":    ("core", "topline"),
    "outro":     ("foundation", "core"),
}


def section_roles(present: set[str], section: "Section",
                  progressive_intro: bool = False) -> list[str]:
    """Which of the present roles play in a section, by tier and energy.

    Built from the tracks that exist rather than a fixed list, so a synth-pop
    set (lead, pad, keys) is arranged as sensibly as an acoustic band
    (piano, guitar, drums). The core and foundation carry through every real
    section -- a verse keeps the groove and the harmony, it does not strip to
    a single element -- while intros and outros thin out.

    `progressive_intro` (dance only) lets the intro add elements over its
    length; a pop intro simply starts with fewer layers, it does not filter
    them in one at a time.
    """
    tiers = SECTION_TIERS.get(section.kind, ("foundation", "core", "topline"))
    chosen = [r for r in present if tier_of(r) in tiers]
    # A chorus/drop with a topline present should always feature it; a verse
    # keeps at most the main topline so the chorus has somewhere to lift to.
    if section.kind == "verse":
        toplines = [r for r in chosen if tier_of(r) == "topline"]
        if len(toplines) > 1:
            # Keep the most song-like topline (vocal > lead > melody > hook).
            order = {"vocal": 0, "lead": 1, "melody": 2, "hook": 3, "arp": 4}
            keep = min(toplines, key=lambda r: order.get(r, 9))
            chosen = [r for r in chosen if tier_of(r) != "topline" or r == keep]
    return chosen


def dropout_before_lifts(sections: list["Section"], min_jump: float = 0.25
                         ) -> list[dict]:
    """Where a section hands into a much louder one, drop the last bar out.

    The single most recognisable move in dance arrangement: right before the
    drop, everything cuts for a bar (or the beat does) and only a riser/vocal
    tail carries over, so the downbeat of the drop hits like a door opening.
    Returns one entry per boundary that earns it: the bar to leave empty and
    the section it belongs to.
    """
    dance = is_dance_form(sections)
    out = []
    for i, section in enumerate(sections[:-1]):
        nxt = sections[i + 1]
        into_drop = nxt.kind == "drop"        # chorus/peak/drop all read as this
        from_build = section.kind == "build"
        big_lift = nxt.energy - section.energy >= min_jump
        if dance:
            # The canonical dance dropout: a build handing into a drop, or any
            # sharp energy lift into a drop even from a groove.
            if into_drop and (from_build or big_lift):
                out.append({
                    "at_bar": section.end_bar - 1, "bars": 1,
                    "section": section.name,
                    "why": "the bar of air before the drop",
                })
        else:
            # Every other form still builds into its big moment, just subtly:
            # one bar before a chorus/peak the sustained bed drops while the
            # drums and vocal carry over -- a lift, not a full stop. Pop lives
            # on this, and it was missing entirely.
            if into_drop and big_lift:
                out.append({
                    "at_bar": section.end_bar - 1, "bars": 1,
                    "section": section.name,
                    "why": "a subtle lift into the chorus",
                })
    return out


def phrase_marks(sections: list["Section"], phrase_bars: int = 8) -> list[dict]:
    """Every `phrase_bars`, a small transition -- a fill, a double snare.

    Dance music keeps a loop interesting by marking each phrase, not by
    changing the loop: a fill or an open hat lands on the last bar of every
    eighth. These are positions a transition element (a Build/Perc/FX clip)
    is dropped onto; they are deliberately small and regular, not big rolls.
    """
    if not is_dance_form(sections):
        return []          # a cinematic climax is not marked with drum fills
    marks = []
    for section in sections:
        if section.kind in ("intro", "outro", "breakdown"):
            continue
        # Mark the last bar of each phrase inside the section, except the very
        # last (a dropout may already own it).
        bar = section.start_bar + phrase_bars - 1
        while bar < section.end_bar - 1:
            marks.append({"at_bar": bar, "section": section.name})
            bar += phrase_bars
    return marks


def intro_layers(sections: list["Section"], phrase_bars: int = 8) -> list[dict]:
    """An intro brings elements in one at a time, not all at once.

    Returns, for the intro, the bar at which each successive role first
    appears -- kick alone, then hats, then bass, then the harmony, then the
    melody -- so the track assembles in front of the listener rather than
    arriving whole. Genre-general: a dance intro filters in over whole phrases,
    a short pop intro assembles quickly over two-bar steps. The opener is
    whatever sits lowest in the stack that is present, so a set that starts on
    a pad ("start with the pad and build") layers up from the pad exactly as a
    kick-led one layers up from the kick.
    """
    intro = next((s for s in sections if s.kind == "intro"), None)
    if intro is None or intro.bars < 4:
        return []
    # Order roles from the foundation upward; each enters after the last.
    order = ["kick", "hat", "perc", "bass", "sub", "pad", "chords", "pulse",
             "keys", "piano", "guitar", "strings", "arp", "lead", "hook",
             "melody", "vocal"]
    present = [r for r in order if r in intro.roles]
    present += [r for r in intro.roles if r not in present]   # any stragglers
    if len(present) <= 1:
        return []                     # one element cannot build

    # A whole phrase per layer when the intro is long enough for it; otherwise
    # spread the entries evenly in >=2-bar steps so a short intro still builds
    # quickly instead of dumping everything on bar one.
    phrases = max(1, intro.bars // phrase_bars)
    if phrases >= len(present):
        step = phrase_bars
    else:
        step = max(2, (intro.bars - 2) // len(present))

    layers = []
    for i, role in enumerate(present):
        at = min(intro.start_bar + i * step, intro.end_bar - 2)
        layers.append({"role": role, "at_bar": int(at)})
    return layers


TEMPLATES: dict[str, list[tuple[str, float, float, tuple[str, ...]]]] = {
    # --- forms that are not EDM ------------------------------------------
    # Cinematic writing builds once across the whole piece rather than
    # resetting every drop, so energy climbs almost monotonically and the
    # sections are named for their dramatic function.
    "cinematic": [
        ("statement",   2, 0.20, ("piano", "strings", "pad")),
        ("build",       2, 0.35, ("piano", "strings", "pad", "perc")),
        ("development", 3, 0.55, ("strings", "piano", "perc", "choir", "harp")),
        ("lift",        2, 0.75, ("strings", "brass", "perc", "choir", "impact")),
        ("climax",      3, 1.00, ("strings", "brass", "perc", "choir", "drums",
                                  "impact", "sub")),
        ("resolution",  3, 0.30, ("strings", "piano", "pad")),
    ],
    "trailer": [
        ("hook",        1, 0.30, ("piano", "strings", "impact")),
        ("build",       2, 0.50, ("strings", "perc", "riser", "impact")),
        ("drop_out",    1, 0.15, ("pad", "choir")),
        ("rise",        2, 0.80, ("strings", "brass", "perc", "riser")),
        ("hit",         2, 1.00, ("brass", "strings", "perc", "drums", "impact",
                                  "choir", "sub")),
        ("aftermath",   2, 0.25, ("pad", "strings", "piano")),
    ],
    # Sonata-ish: a theme, a departure that develops it, a return.
    "classical": [
        ("exposition",    4, 0.45, ("piano", "strings")),
        ("transition",    2, 0.55, ("piano", "strings", "woodwind")),
        ("development",   4, 0.70, ("piano", "strings", "woodwind", "brass")),
        ("recapitulation",4, 0.60, ("piano", "strings", "woodwind")),
        ("coda",          2, 0.40, ("piano", "strings")),
    ],
    "chamber": [
        ("theme",      3, 0.40, ("strings", "piano")),
        ("variation",  3, 0.55, ("strings", "piano", "woodwind")),
        ("variation",  3, 0.70, ("strings", "woodwind", "harp")),
        ("return",     3, 0.45, ("strings", "piano")),
    ],
    # Head, solos over the same changes, head again.
    "jazz": [
        ("head",    2, 0.55, ("piano", "bass", "drums")),
        ("solo",    3, 0.70, ("piano", "bass", "drums", "brass")),
        ("solo",    3, 0.80, ("piano", "bass", "drums", "guitar")),
        ("trading", 2, 0.85, ("piano", "bass", "drums")),
        ("head",    2, 0.60, ("piano", "bass", "drums", "brass")),
    ],
    # Verse/chorus, the form most music outside dance music actually uses.
    "song": [
        ("intro",   1, 0.30, ("piano", "guitar", "pad")),
        ("verse",   2, 0.45, ("drums", "bass", "piano", "guitar", "vocal")),
        ("chorus",  2, 0.85, ("drums", "bass", "piano", "guitar", "vocal",
                              "strings")),
        ("verse",   2, 0.50, ("drums", "bass", "piano", "guitar", "vocal")),
        ("chorus",  2, 0.90, ("drums", "bass", "piano", "guitar", "vocal",
                              "strings")),
        ("bridge",  2, 0.40, ("piano", "pad", "vocal", "strings")),
        ("chorus",  3, 1.00, ("drums", "bass", "piano", "guitar", "vocal",
                              "strings", "choir")),
        ("outro",   1, 0.25, ("piano", "pad")),
    ],
    "lo_fi": [
        ("intro",  2, 0.25, ("piano", "pad")),
        ("groove", 4, 0.50, ("drums", "bass", "piano", "perc")),
        ("break",  2, 0.35, ("piano", "pad", "guitar")),
        ("groove", 4, 0.55, ("drums", "bass", "piano", "guitar", "perc")),
        ("outro",  2, 0.25, ("piano", "pad")),
    ],
    "score": [
        ("cue",        3, 0.30, ("strings", "piano")),
        ("tension",    3, 0.50, ("strings", "perc", "pad")),
        ("release",    3, 0.65, ("strings", "woodwind", "harp", "piano")),
        ("tension",    3, 0.75, ("strings", "brass", "perc")),
        ("resolution", 4, 0.35, ("strings", "piano", "pad")),
    ],

    "house": [
        ("intro",      2, 0.25, ("kick", "drums")),
        ("groove",     2, 0.45, ("kick", "drums", "bass", "perc")),
        ("verse",      2, 0.60, ("kick", "drums", "bass", "chords", "vocal")),
        ("build",      1, 0.75, ("drums", "bass", "chords", "riser", "fx")),
        ("drop",       3, 1.00, ("kick", "drums", "bass", "chords", "hook", "impact")),
        ("breakdown",  2, 0.35, ("pad", "chords", "vocal", "lead")),
        ("build",      1, 0.80, ("drums", "chords", "riser", "fx")),
        ("drop",       3, 1.00, ("kick", "drums", "bass", "chords", "hook", "lead", "impact")),
        ("outro",      2, 0.30, ("kick", "drums")),
    ],
    "big_room": [
        ("intro",      2, 0.20, ("kick", "drums", "fx")),
        ("breakdown",  2, 0.35, ("pad", "chords", "vocal")),
        ("build",      2, 0.85, ("drums", "chords", "riser", "fx")),
        ("drop",       3, 1.00, ("kick", "sub", "bass", "drums", "hook", "impact")),
        ("groove",     2, 0.60, ("kick", "drums", "bass", "chords", "arp")),
        ("breakdown",  2, 0.35, ("pad", "vocal", "lead")),
        ("build",      2, 0.90, ("drums", "chords", "riser", "fx")),
        ("drop",       3, 1.00, ("kick", "sub", "bass", "drums", "lead", "hook", "impact")),
        ("outro",      2, 0.25, ("kick", "drums")),
    ],
    "progressive_house": [
        ("intro",      3, 0.25, ("kick", "drums", "perc")),
        ("groove",     3, 0.50, ("kick", "drums", "bass", "arp")),
        ("verse",      3, 0.65, ("kick", "drums", "bass", "chords", "arp", "vocal")),
        ("breakdown",  3, 0.35, ("pad", "chords", "vocal")),
        ("build",      2, 0.85, ("drums", "arp", "chords", "riser", "fx")),
        ("drop",       4, 1.00, ("kick", "drums", "bass", "chords", "lead", "hook", "impact")),
        ("groove",     2, 0.70, ("kick", "drums", "bass", "arp")),
        ("outro",      2, 0.30, ("kick", "drums", "pad")),
    ],
    "future_bass": [
        ("intro",      2, 0.30, ("pad", "chords")),
        ("verse",      2, 0.50, ("drums", "bass", "chords", "vocal")),
        ("build",      1, 0.80, ("drums", "chords", "riser", "fx", "vocal")),
        ("drop",       3, 1.00, ("drums", "bass", "chords", "lead", "hook", "impact")),
        ("breakdown",  2, 0.35, ("pad", "chords", "vocal")),
        ("build",      1, 0.85, ("drums", "riser", "fx")),
        ("drop",       3, 1.00, ("drums", "bass", "chords", "lead", "hook", "impact")),
        ("outro",      1, 0.30, ("pad", "chords")),
    ],
    "techno": [
        ("intro",      3, 0.25, ("kick", "perc")),
        ("build",      2, 0.50, ("kick", "drums", "riser")),
        ("main",       4, 0.85, ("kick", "drums", "bass", "perc")),
        ("peak",       4, 1.00, ("kick", "drums", "bass", "lead", "impact")),
        ("breakdown",  2, 0.35, ("pad", "fx")),
        ("build",      1, 0.90, ("drums", "riser", "fx")),
        ("peak",       4, 1.00, ("kick", "drums", "bass", "lead", "chords", "impact")),
        ("outro",      3, 0.25, ("kick", "drums")),
    ],
    "melodic_techno": [
        ("intro",      3, 0.25, ("kick", "perc")),
        ("groove",     3, 0.50, ("kick", "drums", "bass", "arp")),
        ("breakdown",  3, 0.35, ("pad", "chords", "arp")),
        ("build",      2, 0.80, ("drums", "arp", "riser", "fx")),
        ("peak",       4, 1.00, ("kick", "drums", "bass", "arp", "lead", "impact")),
        ("groove",     3, 0.65, ("kick", "drums", "bass", "arp")),
        ("outro",      2, 0.30, ("kick", "pad")),
    ],
    "trance": [
        ("intro",      2, 0.20, ("kick", "drums")),
        ("groove",     2, 0.45, ("kick", "drums", "bass")),
        ("breakdown",  3, 0.30, ("pad", "chords", "vocal")),
        ("build",      2, 0.80, ("drums", "chords", "arp", "riser", "fx")),
        ("drop",       4, 1.00, ("kick", "drums", "bass", "chords", "lead", "impact")),
        ("groove",     2, 0.70, ("kick", "drums", "bass", "chords", "arp")),
        ("build",      1, 0.85, ("drums", "riser", "fx")),
        ("drop",       3, 1.00, ("kick", "drums", "bass", "chords", "lead", "hook", "impact")),
        ("outro",      2, 0.25, ("kick", "pad")),
    ],
    "dnb": [
        ("intro",      2, 0.30, ("drums", "perc")),
        ("build",      1, 0.55, ("drums", "pad", "riser")),
        ("drop",       4, 1.00, ("drums", "bass", "sub", "lead", "hook", "impact")),
        ("breakdown",  2, 0.35, ("pad", "chords", "vocal")),
        ("build",      1, 0.75, ("drums", "riser", "fx")),
        ("drop",       4, 1.00, ("drums", "bass", "sub", "lead", "chords", "impact")),
        ("outro",      2, 0.30, ("drums",)),
    ],
    "dubstep": [
        ("intro",      2, 0.25, ("drums", "pad")),
        ("verse",      2, 0.50, ("drums", "bass", "chords", "vocal")),
        ("build",      1, 0.85, ("drums", "riser", "fx")),
        ("drop",       3, 1.00, ("drums", "sub", "bass", "lead", "impact")),
        ("breakdown",  2, 0.35, ("pad", "chords", "vocal")),
        ("build",      1, 0.90, ("drums", "riser", "fx")),
        ("drop",       3, 1.00, ("drums", "sub", "bass", "lead", "hook", "impact")),
        ("outro",      1, 0.25, ("pad",)),
    ],
    "pop": [
        ("intro",   1, 0.30, ("chords",)),
        ("verse",   2, 0.50, ("drums", "bass", "chords", "vocal")),
        ("chorus",  2, 0.90, ("drums", "bass", "chords", "lead", "hook", "vocal")),
        ("verse",   2, 0.55, ("drums", "bass", "chords", "vocal")),
        ("chorus",  2, 0.95, ("drums", "bass", "chords", "lead", "hook", "vocal")),
        ("bridge",  1, 0.45, ("pad", "chords", "vocal")),
        ("chorus",  2, 1.00, ("drums", "bass", "chords", "lead", "hook", "vocal")),
        ("outro",   1, 0.30, ("chords",)),
    ],
    "ambient": [
        ("intro",  2, 0.20, ("pad",)),
        ("swell",  3, 0.45, ("pad", "chords")),
        ("peak",   3, 0.70, ("pad", "chords", "lead")),
        ("fade",   2, 0.25, ("pad",)),
    ],
}

ALIASES = {
    "dance": "house",
    "edm": "big_room",
    "festival": "big_room",
    "mainstage": "big_room",
    "electro_house": "big_room",
    "tech_house": "house",
    "deep_house": "house",
    "prog_house": "progressive_house",
    "progressive": "progressive_house",
    "melodic_house": "melodic_techno",
    "future_house": "future_bass",
    "trap": "dubstep",
    "riddim": "dubstep",
    "drum_and_bass": "dnb",
    "drum_n_bass": "dnb",
    "jungle": "dnb",
    "psytrance": "trance",
    "chill": "ambient",
    "downtempo": "ambient",
}


def resolve_template(name: str) -> str:
    key = (name or "house").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in TEMPLATES:
        raise ValueError(
            f"unknown arrangement template {name!r}; "
            f"try one of: {', '.join(sorted(set(TEMPLATES) | set(ALIASES)))}"
        )
    return key


def plan(
    target_seconds: float = 360.0,
    tempo: float = 124.0,
    template: str = "house",
    phrase_bars: int = 8,
) -> list[Section]:
    """Build a section map that lands close to `target_seconds`.

    Every section is rounded to a whole number of `phrase_bars`, so sections
    always start on a phrase boundary -- an arrangement that drops on bar 33
    instead of bar 32 sounds broken no matter how good the parts are.
    """
    key = resolve_template(template)
    spec = TEMPLATES[key]

    total_bars = target_seconds * tempo / 60.0 / BEATS_PER_BAR
    phrases = max(len(spec), int(round(total_bars / phrase_bars)))
    total_weight = sum(w for _, w, _, _ in spec)

    # Hand out whole phrases proportionally, giving every section at least one.
    raw = [phrases * w / total_weight for _, w, _, _ in spec]
    counts = [max(1, int(round(v))) for v in raw]

    # Reconcile rounding drift against the sections with the most to give.
    drift = phrases - sum(counts)
    order = sorted(range(len(counts)), key=lambda i: raw[i], reverse=True)
    while drift != 0:
        for i in order:
            if drift == 0:
                break
            if drift > 0:
                counts[i] += 1
                drift -= 1
            elif counts[i] > 1:
                counts[i] -= 1
                drift += 1

    sections: list[Section] = []
    cursor = 0
    for (name, _weight, energy, roles), count in zip(spec, counts):
        bars = count * phrase_bars
        sections.append(Section(name, cursor, bars, energy, list(roles)))
        cursor += bars
    return sections


def summarise(sections: list[Section], tempo: float) -> dict:
    total_bars = sections[-1].end_bar if sections else 0
    total_seconds = total_bars * BEATS_PER_BAR / tempo * 60.0
    return {
        "tempo": tempo,
        "total_bars": total_bars,
        "total_seconds": round(total_seconds, 1),
        "duration": f"{int(total_seconds // 60)}:{int(total_seconds % 60):02d}",
        "sections": [s.to_dict(tempo) for s in sections],
    }


def bars_to_seconds(bars: float, tempo: float) -> float:
    return bars * BEATS_PER_BAR / tempo * 60.0


def seconds_to_bars(seconds: float, tempo: float) -> float:
    return seconds * tempo / 60.0 / BEATS_PER_BAR


# Which template suits a set, judged by what is in it. Ordered most specific
# first: the first whose "needs" are all present wins.
TEMPLATE_FOR_ROLES: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (
    # name,          needs,                                  forbids
    ("cinematic",    frozenset({"strings"}),                 frozenset({"kick"})),
    ("score",        frozenset({"piano", "pad"}),            frozenset({"kick"})),
    ("chamber",      frozenset({"piano"}),                   frozenset({"kick", "bass"})),
    ("ambient",      frozenset({"pad"}),                     frozenset({"kick"})),
    ("jazz",         frozenset({"piano", "bass", "drums"}),  frozenset({"riser"})),
    ("song",         frozenset({"guitar", "vocal"}),         frozenset()),
    ("lo_fi",        frozenset({"piano", "drums"}),          frozenset({"riser"})),
    ("trance",       frozenset({"riser", "lead", "kick"}),   frozenset()),
    ("progressive_house", frozenset({"arp", "kick", "bass"}), frozenset()),
    ("house",        frozenset({"kick", "bass"}),            frozenset()),
    ("techno",       frozenset({"kick"}),                    frozenset()),
)


def template_for(roles: Iterable[str]) -> str:
    """Pick an arrangement form that suits the material actually present.

    A set with no drums is not a house track. Arranging one as house produces a
    build-up into a drop that never arrives, which is the single most obvious
    way a generated arrangement sounds wrong.
    """
    present = {normalise_role(r) or r for r in roles}
    for name, needs, forbids in TEMPLATE_FOR_ROLES:
        if needs <= present and not (forbids & present):
            return name
    # Something tonal but unclassifiable: a shape that assumes nothing.
    return "ambient"
