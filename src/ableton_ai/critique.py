"""Measure what makes generated music sound generated.

Every fault here was found by measuring real output rather than by listening and
guessing. Each one has a number attached, so a change can be shown to have
helped instead of merely felt to have:

    Chords, Pad, Arp, Drums   velocity sd 0.0  -- every note identical
    Lead                      128 notes over 6 pitches, no rest anywhere
    Drums                     1 of 8 bars distinct
    Melody vs Arp             12 semitones of overlap
    Chords, Pad               one rhythmic position: one hit per bar, forever

None of these are matters of taste. Flat velocity is not a quiet dynamic, it is
the absence of one; two parts sharing an octave is not counterpoint, it is mud.
A human writing any of these on purpose would do it once, not for eight bars.

The thresholds are deliberately forgiving. This flags what is mechanically
wrong, not what is unfashionable.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

BEATS_PER_BAR = 4.0

Note = dict[str, Any]

# Roles that carry a tune and therefore compete for the listener's attention.
# Two of these in the same octave is the most common reason a generated
# arrangement sounds crowded.
TOP_LINE = ("lead", "melody", "hook", "arp", "riff", "topline")

# Roles that hold notes rather than articulate them, and so are exempt from the
# rhythmic-interest checks.
SUSTAINED = ("pad", "strings", "choir", "drone", "atmos")

# Percussion is exempt from pitch checks entirely: its "range" is a drum map.
PERCUSSIVE = ("drums", "kick", "perc", "snare", "hat", "impact")


@dataclass
class Finding:
    """One measurable problem, with the number that proves it."""

    severity: str          # "high" | "medium" | "low"
    part: str
    problem: str
    measured: str
    fix: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity, "part": self.part,
            "problem": self.problem, "measured": self.measured, "fix": self.fix,
        }


@dataclass
class Part:
    """One track's notes, with the role that says how to judge them."""

    name: str
    role: str
    notes: list[Note] = field(default_factory=list)
    bars: float = 8.0

    @property
    def pitches(self) -> list[int]:
        return [int(n["pitch"]) for n in self.notes]

    @property
    def velocities(self) -> list[int]:
        return [int(n["velocity"]) for n in self.notes]

    @property
    def is_percussive(self) -> bool:
        return self.role in PERCUSSIVE

    @property
    def is_sustained(self) -> bool:
        return self.role in SUSTAINED


def _bar_signatures(part: Part) -> list[tuple]:
    """Each bar reduced to what it plays, so identical bars can be counted."""
    bars: dict[int, list[tuple]] = {}
    for note in part.notes:
        index = int(float(note["start"]) // BEATS_PER_BAR)
        bars.setdefault(index, []).append((
            round(float(note["start"]) % BEATS_PER_BAR, 2),
            int(note["pitch"]),
            round(float(note["duration"]), 2),
        ))
    return [tuple(sorted(v)) for _, v in sorted(bars.items())]


def _occupancy(part: Part) -> float:
    """Share of sixteenth-note slots that carry an onset."""
    slots = max(1, int(round(part.bars * 16)))
    onsets = {round(float(n["start"]) * 4) for n in part.notes}
    return min(1.0, len(onsets) / slots)


def check_part(part: Part) -> list[Finding]:
    """Everything measurably wrong with one part on its own."""
    found: list[Finding] = []
    if not part.notes:
        return found

    velocities = part.velocities

    # --- dynamics ---------------------------------------------------------
    spread = statistics.pstdev(velocities) if len(velocities) > 1 else 0.0
    if spread < 1.0 and len(velocities) > 3:
        found.append(Finding(
            "high", part.name, "every note is the same velocity",
            f"velocity {velocities[0]} on all {len(velocities)} notes, sd 0.0",
            "apply an accent curve, and shape the phrase across the bar",
        ))
    elif spread < 4.0 and len(velocities) > 8:
        found.append(Finding(
            "medium", part.name, "almost no dynamic movement",
            f"velocity sd {spread:.1f} across {len(velocities)} notes",
            "widen the accent curve; sd 8-15 reads as played rather than typed",
        ))

    # --- space ------------------------------------------------------------
    if not part.is_sustained and not part.is_percussive:
        occupancy = _occupancy(part)
        if occupancy > 0.95:
            found.append(Finding(
                "high", part.name, "no rests anywhere",
                f"{occupancy:.0%} of sixteenth slots have an onset",
                "leave gaps: a line that never stops has no phrases in it",
            ))
        elif occupancy > 0.8:
            found.append(Finding(
                "low", part.name, "very dense",
                f"{occupancy:.0%} of sixteenth slots have an onset",
                "thin the weaker beats, or rest at the end of each phrase",
            ))

    # --- repetition -------------------------------------------------------
    signatures = _bar_signatures(part)
    if len(signatures) >= 4:
        distinct = len(set(signatures))
        if distinct == 1:
            found.append(Finding(
                "high" if not part.is_sustained else "medium", part.name,
                "every bar is identical",
                f"1 distinct bar out of {len(signatures)}",
                "vary the last bar of each phrase: a fill, a ghost note, a rest",
            ))
        elif distinct <= len(signatures) // 4:
            found.append(Finding(
                "low", part.name, "very repetitive",
                f"{distinct} distinct bars out of {len(signatures)}",
                "vary the fourth and eighth bars",
            ))

    # --- pitch interest ---------------------------------------------------
    if not part.is_percussive:
        pitches = part.pitches
        span = max(pitches) - min(pitches)
        distinct = len(set(pitches))
        if len(part.notes) > 16 and distinct <= 3:
            found.append(Finding(
                "medium", part.name, "hardly any pitches",
                f"{len(part.notes)} notes over {distinct} distinct pitch(es)",
                "let the line follow the harmony rather than sitting on a root",
            ))
        if part.role in TOP_LINE and span < 5 and len(part.notes) > 8:
            found.append(Finding(
                "medium", part.name, "the line barely moves",
                f"{span} semitones from lowest to highest",
                "give it a contour -- rise into the phrase end, fall away after",
            ))

    # --- articulation -----------------------------------------------------
    durations = [float(n["duration"]) for n in part.notes]
    if len(durations) > 8 and statistics.pstdev(durations) < 0.01:
        found.append(Finding(
            "low", part.name, "every note is exactly the same length",
            f"all {len(durations)} notes last {durations[0]:.2f} beats",
            "vary length with the accent: long on strong beats, short between",
        ))

    return found


def check_ensemble(parts: Sequence[Part]) -> list[Finding]:
    """Everything measurably wrong with how the parts sit together."""
    found: list[Finding] = []
    pitched = [p for p in parts if p.notes and not p.is_percussive]

    # --- register crowding ------------------------------------------------
    tops = [p for p in pitched if p.role in TOP_LINE]
    for a, b in itertools.combinations(tops, 2):
        low = max(min(a.pitches), min(b.pitches))
        high = min(max(a.pitches), max(b.pitches))
        overlap = high - low

        # An arpeggio spanning two octaves overlaps every other part by
        # definition, and that is not a fault. What matters is whether the two
        # lines are centred in the same place.
        centre_gap = abs(
            statistics.fmean(a.pitches) - statistics.fmean(b.pitches)
        )
        wide = max(max(a.pitches) - min(a.pitches),
                   max(b.pitches) - min(b.pitches)) > 18
        if wide and centre_gap >= 7:
            continue

        if overlap >= 10 and centre_gap < 7:
            found.append(Finding(
                "high", f"{a.name} + {b.name}",
                "two top lines are in the same octave",
                f"{overlap} semitones of overlap "
                f"({a.name} {min(a.pitches)}-{max(a.pitches)}, "
                f"{b.name} {min(b.pitches)}-{max(b.pitches)})",
                "move one an octave, or give it a different register band",
            ))
        elif overlap >= 6:
            found.append(Finding(
                "medium", f"{a.name} + {b.name}",
                "two top lines share most of a register",
                f"{overlap} semitones of overlap",
                "separate them by at least an octave of centre",
            ))

    # --- rhythmic collision -----------------------------------------------
    for a, b in itertools.combinations(
        [p for p in pitched if p.role in TOP_LINE], 2
    ):
        onsets_a = {round(float(n["start"]) * 4) for n in a.notes}
        onsets_b = {round(float(n["start"]) * 4) for n in b.notes}
        if not onsets_a or not onsets_b:
            continue
        # Only compare parts of comparable density. A sixteen-note hook sitting
        # inside a 126-note sixteenth stream shares 100% of its onsets with it
        # and always will, which is a fact about arithmetic rather than music.
        ratio = max(len(onsets_a), len(onsets_b)) / min(len(onsets_a), len(onsets_b))
        if ratio > 2.0:
            continue
        shared = len(onsets_a & onsets_b) / min(len(onsets_a), len(onsets_b))
        if shared > 0.9:
            found.append(Finding(
                "medium", f"{a.name} + {b.name}",
                "two lines hit on exactly the same beats",
                f"{shared:.0%} of onsets coincide",
                "answer rather than double: let one rest where the other moves",
            ))

    # --- low end ----------------------------------------------------------
    lows = [p for p in pitched if p.role in ("bass", "sub", "808")]
    if len(lows) > 1:
        for a, b in itertools.combinations(lows, 2):
            if min(min(a.pitches), min(b.pitches)) < 48:
                overlap = min(max(a.pitches), max(b.pitches)) - max(
                    min(a.pitches), min(b.pitches))
                if overlap > 0:
                    found.append(Finding(
                        "medium", f"{a.name} + {b.name}",
                        "two parts in the low register",
                        f"{overlap} semitones of overlap below C3",
                        "one plays the root, the other the movement -- not both",
                    ))

    # --- is anything actually holding the harmony -------------------------
    if pitched and not any(
        p.role in ("chords", "pad", "keys", "piano", "strings", "organ", "guitar")
        for p in pitched
    ):
        found.append(Finding(
            "low", "ensemble", "nothing is stating the harmony",
            "no chord, pad, keys, piano, strings, organ or guitar part",
            "a bass and a lead imply chords; something should state them",
        ))

    return found


def critique(parts: Iterable[Part]) -> dict:
    """Every measurable fault, worst first, with a score out of 100."""
    parts = list(parts)
    findings = [f for p in parts for f in check_part(p)]
    findings += check_ensemble(parts)

    weight = {"high": 12, "medium": 5, "low": 2}
    penalty = sum(weight[f.severity] for f in findings)
    score = max(0, 100 - penalty)

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f.severity], f.part))

    counts = {s: sum(1 for f in findings if f.severity == s)
              for s in ("high", "medium", "low")}
    return {
        "score": score,
        "counts": counts,
        "findings": [f.to_dict() for f in findings],
        "parts_checked": len(parts),
        "summary": (
            f"{score}/100 across {len(parts)} part(s): "
            f"{counts['high']} serious, {counts['medium']} moderate, "
            f"{counts['low']} minor"
            if findings else
            f"{score}/100 across {len(parts)} part(s): nothing measurably wrong"
        ),
    }
