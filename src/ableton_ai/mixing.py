"""Gain staging, frequency separation and a master chain.

What is actually possible through Live's API is worth stating plainly, because
it decides what this module can honestly claim:

  Possible   -- track volume, pan and sends; loading stock devices and setting
                every one of their parameters; reading each track's output
                meter while Live is playing; automation envelopes.
  Not possible -- hearing anything. There is no spectrum, no LUFS, no loudness
                match. Nothing here is a substitute for listening.

So this is *structural* mixing: put faders at sensible relative levels for the
role each track plays, keep instruments out of each other's frequency range,
and build a defensible master chain. The judgement calls stay with the producer.
"""

from __future__ import annotations

from dataclasses import dataclass

# Live's volume parameter is 0..1 and maps non-linearly onto decibels, with
# 0.85 sitting at unity (0 dB). These are the useful anchors.
UNITY = 0.85


def db_to_live(db: float) -> float:
    """Approximate Live's fader curve for small trims around unity.

    Live's mapping is not published; this is fitted to the region that matters
    for gain staging (roughly -24 dB to +6 dB) and is accurate to about half a
    decibel there. Outside that range treat it as indicative only.
    """
    # Live's fader is roughly linear-in-dB near unity at ~0.025 units per dB.
    return max(0.0, min(1.0, UNITY + db * 0.025))


def live_to_db(value: float) -> float:
    return (value - UNITY) / 0.025


@dataclass(frozen=True)
class RoleMix:
    """A starting level and frequency treatment for one musical role."""

    gain_db: float
    high_pass_hz: float | None
    low_pass_hz: float | None
    pan: float
    note: str
    reverb: float = 0.0
    delay: float = 0.0


# A conventional EDM balance, with the kick as the reference at unity.
# These are starting points that leave headroom, not finished mixes.
# Send amounts matter as much as levels, and reverb on drums is the most
# common way a dance mix loses its punch. The kick gets none at all; hats and
# claps get a trace; bass gets none, because reverb below 150Hz is just mud.
# The wet tracks are the ones with space around them -- pads, leads, vocals.
BALANCE: dict[str, RoleMix] = {
    "kick":   RoleMix(0.0,   None,  None, 0.0,  "Reference. Everything else sits under this. No reverb, ever.", 0.00, 0.00),
    "sub":    RoleMix(-2.0,  None,  120,  0.0,  "Mono and low-passed; nothing above 120Hz. Bone dry.", 0.00, 0.00),
    "bass":   RoleMix(-3.0,  40,    None, 0.0,  "High-passed at 40 to clear the kick's floor. Dry -- reverb here is mud.", 0.00, 0.00),
    "drums":  RoleMix(-4.0,  120,   None, 0.0,  "Hats and claps out of the low end. A trace of reverb only.", 0.06, 0.04),
    "perc":   RoleMix(-8.0,  200,   None, 0.25, "Panned off-centre. Takes a little more space than the kit.", 0.14, 0.10),
    "chords": RoleMix(-8.0,  200,   None, 0.0,  "High-passed hard; chords do not need low end.", 0.22, 0.12),
    "arp":    RoleMix(-10.0, 300,   None, -0.3, "Panned opposite the perc. Delay suits it more than reverb.", 0.16, 0.28),
    "pad":    RoleMix(-12.0, 250,   8000, 0.0,  "Wide and quiet; rolled off top and bottom. The wettest thing in the mix.", 0.40, 0.10),
    "pulse":  RoleMix(-9.0,  220,   None, 0.15, "The rhythmic chord layer; a touch louder and drier than the big pad, slightly off-centre, so the pump reads as groove.", 0.18, 0.14),
    "lead":   RoleMix(-6.0,  200,   None, 0.0,  "Centre, forward, but under the kick.", 0.18, 0.22),
    "hook":   RoleMix(-5.0,  250,   None, 0.0,  "The part people remember; keep it audible and fairly dry.", 0.14, 0.18),
    "vocal":  RoleMix(-4.0,  100,   None, 0.0,  "Centre and prominent once recorded.", 0.24, 0.18),
    "riser":  RoleMix(-10.0, 300,   None, 0.0,  "Rises into the drop; wet, because it is pure effect.", 0.38, 0.20),
    "impact": RoleMix(-3.0,  None,  None, 0.0,  "Short and loud by design. A long tail is the point.", 0.30, 0.05),
    "fx":     RoleMix(-12.0, None,  None, 0.0,  "Support, not foreground.", 0.35, 0.25),
}

# EQ Eight parameter names vary by band and channel. Rather than hardcode them,
# the tool discovers what a loaded EQ Eight actually exposes and matches by
# fragment -- the same approach the patch recipes use.
EQ_DEVICE = "Audio Effects/EQ Eight"
COMPRESSOR_DEVICE = "Audio Effects/Compressor"
GLUE_DEVICE = "Audio Effects/Glue Compressor"
LIMITER_DEVICE = "Audio Effects/Limiter"
SPECTRUM_DEVICE = "Audio Effects/Spectrum"

# Club/dance masters sit loud: -9 to -7 LUFS integrated, well above the -14
# streaming reference. A quiet mix is quiet because nothing drives the limiter,
# not because the ceiling is wrong.
LOUDNESS_TARGET_LUFS = -9.0
LOUDNESS_CEILING_DB = -1.0        # true-peak headroom under 0 dBFS

# Absolute gain-staging anchors, in dBFS. The kick is the reference the whole
# mix is levelled against; the sub sits ~8 dB under it. These are the two the
# trance ruleset pins with hard numbers, and the two that can be measured on
# their own -- the kick by its peak, the sub by its RMS in a band nothing else
# occupies. Everything else stays on the relative BALANCE offsets from the
# kick. A recipe can override with a "targets" dict.
MIX_TARGETS: dict[str, float] = {
    "kick_peak_dbfs": -12.0,
    "sub_rms_dbfs": -20.0,
}

# Which measured quantity each anchor role is trimmed against.
ANCHOR_METRIC: dict[str, str] = {"kick": "peak_db", "sub": "rms_db"}


def loudness_gain(
    measured_lufs: float,
    target_lufs: float,
    current_gain_db: float,
    max_gain_db: float = 12.0,
) -> float:
    """The limiter input gain that brings `measured_lufs` up to the target.

    Loudness is made by driving signal into the limiter, not by lowering the
    ceiling. The gap between measured and target is how much more input the
    limiter needs; add it to the gain already applied and cap it, so one
    over-quiet measurement cannot ask for +40dB. The loop re-measures, so
    limiting's diminishing returns are corrected on the next round rather than
    guessed at here.
    """
    gap = target_lufs - measured_lufs
    return max(0.0, min(max_gain_db, current_gain_db + gap))


@dataclass(frozen=True)
class CompressorSetting:
    """A compressor starting point.

    threshold/ratio/attack/release are NORMALISED 0..1 positions, because
    that is what Live's Compressor exposes for them in the LOM -- passing
    real dB or a raw ratio clamps to the 0..1 range, which is how a 4:1 ratio
    silently became infinity:1 and crushed every track. makeup is real dB
    (the Output parameter has a true -36..36 range). The positions here are
    calibrated to gentle, musical amounts -- a few dB of movement, never a
    brick wall.
    """

    threshold: float      # 0..1, lower = compress more of the signal
    ratio: float          # 0..1, lower = gentler
    attack: float         # 0..1, lower = faster
    release: float        # 0..1, lower = faster
    makeup_db: float      # real dB
    why: str


# Deliberately restrained. The failure mode this replaces was every parameter
# clamped to its extreme; these are all in the gentle third of their range.
COMPRESSION: dict[str, CompressorSetting] = {
    "punch": CompressorSetting(
        0.35, 0.30, 0.30, 0.35, 2.0,
        "Medium attack keeps the transient, quick release breathes. Drums.",
    ),
    "glue": CompressorSetting(
        0.45, 0.18, 0.45, 0.55, 1.5,
        "Barely there -- holds a group together without flattening it.",
    ),
    "control": CompressorSetting(
        0.38, 0.25, 0.20, 0.40, 2.0,
        "Evens out level. Bass, where consistency matters most.",
    ),
    "squeeze": CompressorSetting(
        0.30, 0.45, 0.12, 0.30, 3.0,
        "Aggressive and obvious. A deliberate effect, not a corrective one.",
    ),
    "master": CompressorSetting(
        0.55, 0.15, 0.45, 0.60, 0.5,
        "Barely working -- 1-2dB of movement at most on the master bus.",
    ),
}

# Which roles get a compressor at all, and which style. Best practice for
# dance: compress the rhythm section and leave the melodic parts alone. A
# compressor on every lead, pad and hook is the "too much weird compression"
# the user heard -- those parts want EQ and sidechain, not gain reduction.
ROLE_COMPRESSION: dict[str, str] = {
    "kick": "punch", "drums": "punch", "perc": "punch",
    "bass": "control", "sub": "control",
    "vocal": "control",
}


def wants_compression(role: str) -> bool:
    """Only the rhythm section and vocals are compressed by default."""
    return (role or "").lower() in ROLE_COMPRESSION


def compression_for(role: str) -> CompressorSetting:
    return COMPRESSION[ROLE_COMPRESSION.get((role or "").lower(), "glue")]

# A defensible master chain. Deliberately conservative: this is a safety net
# and a loudness ceiling, not a mastering engineer.
MASTER_CHAIN = (
    (EQ_DEVICE, "Gentle corrective EQ, high-passed below 25Hz."),
    (LIMITER_DEVICE, "Ceiling at -0.3dB to stop inter-sample peaks."),
    (SPECTRUM_DEVICE, "A Spectrum you can watch; the API cannot read it."),
)


def balance_for(role: str) -> RoleMix:
    from .arrangement import normalise_role
    return BALANCE.get(normalise_role(role) or (role or "").lower(), BALANCE["fx"])


def headroom_advice(track_count: int) -> str:
    """How much to pull the master down so a busy mix does not clip.

    The convention is to leave the mix bus peaking between -6 and -3dB before
    any limiting. That headroom is what gives EQ and multiband compression room
    to work; mixing into a limiter that is already clamping does not.
    """
    if track_count <= 6:
        return (
            "Aim for the mix bus peaking around -6dB before limiting. "
            "Unity is fine with this few tracks."
        )
    if track_count <= 12:
        return (
            "Pull the master down 2-3dB. Target -6 to -3dB peak on the mix bus "
            "before any limiting."
        )
    return (
        "Pull the master down 4-6dB and check the meter on the loudest drop. "
        "Target -6 to -3dB peak on the mix bus before limiting."
    )


# Which roles own which part of the spectrum. A band that is over target is
# fixed by trimming the tracks that put energy there, not by an EQ on the
# master -- the master cannot tell whose sub it is.
BAND_OWNERS: dict[str, tuple[str, ...]] = {
    "sub": ("sub", "808", "kick", "bass"),
    "bass": ("bass", "sub", "kick", "808"),
    "low_mid": ("chords", "pad", "pulse", "keys", "perc", "snare"),
    "mid": ("chords", "pad", "pulse", "lead", "hook", "vocal", "keys", "arp"),
    "high_mid": ("lead", "hook", "vocal", "arp", "clap", "snare"),
    "high": ("hat", "hats", "perc", "riser", "top"),
    "air": ("hat", "hats", "riser", "fx", "atmos"),
}


def trims_for_bands(
    over: dict[str, float],
    roles_present: dict[int, str],
    max_trim_db: float = 3.0,
) -> dict[int, float]:
    """Turn "these bands are hot by this much" into per-track trims in dB.

    A band that is 40% over its target does not want a 40% cut: the excess is
    shared between every track contributing to it, and the correction is capped
    so one measurement can never wreck a mix. Repeating the measurement is what
    converges it, not the size of a single step.
    """
    trims: dict[int, float] = {}
    for band, excess in over.items():
        owners = BAND_OWNERS.get(band, ())
        contributors = [i for i, role in roles_present.items() if role in owners]
        if not contributors:
            continue
        # Excess is a share-of-power ratio; 10*log10 puts it in dB, and half of
        # that is a deliberately conservative step.
        import math

        step = min(max_trim_db, abs(10.0 * math.log10(1.0 + max(0.0, excess))) / 2.0)
        for index in contributors:
            trims[index] = min(max_trim_db, trims.get(index, 0.0) + step)
    return {i: -round(db, 2) for i, db in trims.items() if db >= 0.1}


import math as _math

# Ableton's EQ Eight and Auto Filter expose Frequency as a 0..1 parameter whose
# display is logarithmic from ~10Hz to ~22050Hz. Writing raw Hz clamps to 1.0
# (max) -- which is why every high-pass ended up pinned at 22kHz. Convert.
_FREQ_MIN_HZ = 10.0
_FREQ_MAX_HZ = 22050.0


def hz_to_normalised(hz: float) -> float:
    """A frequency in Hz to EQ Eight's 0..1 parameter position."""
    hz = max(_FREQ_MIN_HZ, min(_FREQ_MAX_HZ, float(hz)))
    lo, hi = _math.log10(_FREQ_MIN_HZ), _math.log10(_FREQ_MAX_HZ)
    return (_math.log10(hz) - lo) / (hi - lo)


def normalised_to_hz(value: float) -> float:
    """The inverse -- for reading back and reporting what actually landed."""
    lo, hi = _math.log10(_FREQ_MIN_HZ), _math.log10(_FREQ_MAX_HZ)
    return round(10 ** (lo + max(0.0, min(1.0, value)) * (hi - lo)), 1)


# The ten mix/master moves applied, in order, whenever the user asks to
# mix/master. Each is a real engineering practice, not a preference; the
# orchestrator (tool_mix_and_master) runs them top to bottom and reports each.
MIX_MASTER_PRACTICES = [
    ("gain staging",
     "Set every track to a sane level with headroom -- the master peaking "
     "around -6dB, nothing clipping into the chain."),
    ("balance and pan",
     "Levels and panning by role: kick/bass/lead centred, everything else "
     "spread for width, so the stereo field is used and nothing masks."),
    ("subtractive EQ",
     "High-pass every non-low role and dip the mud -- two sounds must not "
     "fight for the same frequencies. Cut before boosting."),
    ("low-end mono",
     "Sum everything below ~120Hz to mono (Utility) so the sub translates "
     "on club systems and vinyl and the low end stays solid."),
    ("sidechain",
     "Duck the sustained bed (bass, pad, chords) to the kick, gently -- the "
     "pump is rhythmic space, never on drums or transients."),
    ("compression",
     "Only where dynamics need evening out -- drums for punch, bass for "
     "consistency. Melodic parts get EQ and sidechain, not gain reduction."),
    ("reverb and delay sends",
     "Space on return tracks by role: drums nearly dry, pads and leads wet, "
     "high-passed so the tails do not muddy the low end."),
    ("master bus",
     "A gentle chain on the master: corrective EQ, glue compression doing "
     "1-2dB, then a limiter -- glue, not loudness."),
    ("loudness to target",
     "Balance the spectrum, then drive the master to a club-ready loudness "
     "by gaining into the limiter -- measured, with true peak held under the "
     "ceiling. A ceiling alone makes nothing loud."),
    ("translation check",
     "Resample and measure -- band balance, mono compatibility, crest -- and "
     "flag anything that will not survive a small speaker or a phone."),
]
