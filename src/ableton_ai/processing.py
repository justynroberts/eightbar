"""Mix processing as an engineer's rules, not a device on every track.

The failure this replaces: a compressor slapped on every track with its ratio
clamped to infinity, and no EQ or sidechain doing the work compression was
wrongly asked to do. What follows is how a mix engineer actually approaches a
dance record, written down as rules keyed by role.

The three moves, in the order they matter:

1. **EQ carves space.** The single most important mix decision is that two
   sounds do not fight for the same frequencies. High-pass everything that is
   not kick or bass so the low end is clean; dip the low-mids where they pile
   up; add air only where it is wanted. This is subtractive first -- take
   away the mud before adding anything.

2. **Sidechain makes it breathe.** In dance music the kick owns the downbeat,
   and the bass, pads and chords duck out of its way. That pump IS the genre.
   It is not compression for level -- it is rhythmic space, and it belongs on
   the sustained elements, never on the drums or the transient parts.

3. **Compression controls, sparingly.** Only where a part's dynamics genuinely
   need evening out: the drum bus for glue and punch, the bass for
   consistency. A lead, a pad, a hook does not want gain reduction -- it wants
   EQ and sidechain. Compressing them is what sounded "weird".

Every rule here is a starting point a human would then trust their ears over,
not a finished mix.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EQMove:
    """One EQ decision for a role, in real Hz and dB."""

    high_pass_hz: float = 0.0        # 0 = no high-pass (kick, bass, sub)
    low_cut_hz: float = 0.0          # a gentle dip, not a cut, centred here
    low_cut_db: float = 0.0
    presence_hz: float = 0.0         # a small boost for clarity/air
    presence_db: float = 0.0
    why: str = ""


# High-passing everything that is not the low end is the highest-leverage EQ
# move there is: it clears the mud a dozen sustained parts pile up below 200Hz,
# which no amount of compression can fix. The kick and bass keep their bottom;
# everyone else earns theirs above the high-pass.
EQ_RULES: dict[str, EQMove] = {
    "kick":    EQMove(30, 0, 0, 0, 0, "Only a rumble filter -- the kick is the low end."),
    "sub":     EQMove(0, 0, 0, 0, 0, "Untouched -- the sub is the fundamental."),
    "808":     EQMove(0, 0, 0, 0, 0, "Untouched -- the 808 is the fundamental."),
    "bass":    EQMove(35, 0, 0, 700, 1.5, "Rumble filter; a touch of upper-mid so it reads on small speakers."),
    "drums":   EQMove(120, 300, -2, 0, 0, "High-pass off the kick's territory; dip boxy low-mids."),
    "perc":    EQMove(200, 0, 0, 8000, 1.5, "High-pass hard; a little air up top."),
    "snare":   EQMove(150, 0, 0, 5000, 2.0, "High-pass; presence for the crack."),
    "hat":     EQMove(400, 0, 0, 10000, 1.5, "High-pass hard -- hats have no business low; air on top."),
    "chords":  EQMove(180, 350, -2, 0, 0, "High-pass out of the bass; scoop the mud where chords pile up."),
    "pad":     EQMove(200, 400, -3, 12000, 1.0, "High-pass; scoop the low-mids -- a pad's job is width, not weight."),
    "strings": EQMove(150, 350, -2, 10000, 1.0, "High-pass; gentle low-mid dip; air."),
    "keys":    EQMove(160, 350, -2, 0, 0, "High-pass out of the bass; dip the boxiness."),
    "piano":   EQMove(90, 300, -1.5, 0, 0, "Light high-pass; a small low-mid dip clears the mud."),
    "organ":   EQMove(120, 0, 0, 0, 0, "High-pass off the sub."),
    "guitar":  EQMove(120, 0, 0, 3000, 1.5, "High-pass; a little presence."),
    "lead":    EQMove(200, 0, 0, 5000, 2.0, "High-pass hard -- a lead lives up top; presence so it cuts."),
    "hook":    EQMove(250, 0, 0, 6000, 2.0, "High-pass hard; presence so the hook sits over everything."),
    "melody":  EQMove(180, 0, 0, 4000, 1.5, "High-pass; a touch of presence."),
    "arp":     EQMove(300, 0, 0, 9000, 1.5, "High-pass hard -- an arp is all sparkle, no weight."),
    "choir":   EQMove(180, 400, -2, 10000, 1.0, "High-pass; dip low-mids; air."),
    "vocal":   EQMove(100, 300, -2, 6000, 2.0, "High-pass; dip mud; presence for intelligibility."),
    "riser":   EQMove(300, 0, 0, 0, 0, "High-pass -- a riser is a top-end effect."),
    "fx":      EQMove(200, 0, 0, 0, 0, "High-pass off the low end."),
}

# The sustained elements that duck against the kick. This is the defining
# sound of dance music, and it belongs to exactly these roles -- never the
# drums (which the kick lives inside), never the transient leads.
SIDECHAIN_ROLES: tuple[str, ...] = (
    "bass", "sub", "808", "pad", "chords",
)

# How hard each ducks, as a fraction. These are deliberately GENTLE -- a
# musical trance pump breathes, it does not chop. The bass ducks most (it
# shares the kick's low end and must clear it); the pad a little less; chords
# least. Deeper than this and the whole mix sounds like it is pumping, which
# is the "way too much sidechain" the user heard. Never drums, hats or perc:
# the kick lives inside those and a transient does not pump.
SIDECHAIN_DEPTH: dict[str, float] = {
    "bass": 0.45, "sub": 0.5, "808": 0.5,
    "pad": 0.4, "chords": 0.3,
}


def eq_for(role: str) -> EQMove:
    return EQ_RULES.get((role or "").lower(), EQMove(100, 0, 0, 0, 0,
                        "Default high-pass off the deep low end."))


def wants_sidechain(role: str) -> bool:
    return (role or "").lower() in SIDECHAIN_ROLES


def sidechain_depth(role: str) -> float:
    return SIDECHAIN_DEPTH.get((role or "").lower(), 0.5)


def plan(roles: dict[int, str]) -> dict:
    """A whole-mix processing plan from the roles present -- what a mix
    engineer would reach for, before touching a single fader by ear."""
    eq, sidechain, compress = [], [], []
    from . import mixing

    for index, role in roles.items():
        role = (role or "").lower()
        move = eq_for(role)
        eq.append({"track_index": index, "role": role,
                   "high_pass_hz": move.high_pass_hz, "why": move.why})
        if wants_sidechain(role):
            sidechain.append({"track_index": index, "role": role,
                              "depth": sidechain_depth(role)})
        if mixing.wants_compression(role):
            compress.append({"track_index": index, "role": role,
                             "style": mixing.ROLE_COMPRESSION[role]})
    return {"eq": eq, "sidechain": sidechain, "compress": compress}
