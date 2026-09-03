"""The user's own progression library, indexed and used directly.

`references/` holds ~700 progressions the user chose -- "these are good ones"
-- each one written out in every key, with the label doing half the work:

    A - i VI III VII - Nostalgic Hopeful.mid
    ^   ^               ^
    key roman degrees   mood words

plus four subfolders (`pop style`, `soul style`, ...) holding the SAME
progressions comped in those styles rather than as block chords.

This module does not statistically "learn" from them -- averaging 700 good
progressions produces none of them. It indexes them, so a request can say
what it wants in the vocabulary the library already speaks: a mood
("nostalgic", "dark"), a length, a key -- and get back real voicings from a
file, transposed, rather than degrees rebuilt from theory. The distinction
matters for the same reason reference_track does: the file's i7 carries its
actual seventh and its actual spacing, where degree 1 rebuilds a plain triad.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import theory

BEATS_PER_BAR = 4.0
Note = dict[str, Any]

REFERENCES_DIR = Path(__file__).resolve().parents[2] / "references"

# Roman numeral -> scale degree. Case carries quality in the filenames, but
# the degree is what we index by; the file itself carries the true voicing.
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}

_TOKEN = re.compile(r"^([ivIV]+)(.*)$")


def _parse_roman(token: str) -> tuple[int, str] | None:
    """"III7" -> (3, "7"); "VIIsus4" -> (7, "sus4"); "i" -> (1, "")."""
    match = _TOKEN.match(token.strip())
    if not match:
        return None
    numeral, suffix = match.groups()
    degree = _ROMAN.get(numeral.lower())
    if degree is None:
        return None
    return degree, suffix


@dataclass
class BankEntry:
    """One progression the user vouched for."""

    name: str                 # the roman-numeral spelling, as labelled
    key: str                  # the key the file is written in
    degrees: list[int]
    suffixes: list[str]       # per-chord colour: "7", "sus4", "69", ""
    moods: tuple[str, ...]
    path: Path
    styles: dict[str, Path] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.degrees)


class ChordBank:
    """The indexed reference library. Scanning is a directory listing, so it
    is cheap enough to do per-process rather than cache to disk."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else REFERENCES_DIR
        self.entries: list[BankEntry] = []
        self._scan()

    def _scan(self) -> None:
        if not self.root.is_dir():
            return
        style_dirs = {
            p.name.replace(" style", ""): p
            for p in self.root.iterdir()
            if p.is_dir() and p.name.endswith("style")
        }
        for file in sorted(self.root.glob("*.mid")):
            parts = file.stem.split(" - ")
            if len(parts) != 3:
                continue
            key, romans, moods = parts
            parsed = [_parse_roman(t) for t in romans.split()]
            if not parsed or any(p is None for p in parsed):
                continue
            mood_words = tuple(
                w.lower() for w in moods.split() if w.lower() != "new"
            )
            self.entries.append(BankEntry(
                name=romans,
                key=key.strip(),
                degrees=[p[0] for p in parsed],      # type: ignore[index]
                suffixes=[p[1] for p in parsed],     # type: ignore[index]
                moods=mood_words,
                path=file,
                styles={
                    style: folder / file.name
                    for style, folder in style_dirs.items()
                    if (folder / file.name).is_file()
                },
            ))

    # ---------------------------------------------------------------- search

    def moods(self) -> dict[str, int]:
        """Every mood word in the library, with how many progressions carry it."""
        counts: dict[str, int] = {}
        for entry in self.entries:
            for mood in entry.moods:
                counts[mood] = counts.get(mood, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def styles(self) -> list[str]:
        seen: set[str] = set()
        for entry in self.entries:
            seen.update(entry.styles)
        return sorted(seen)

    def find(
        self,
        mood: str | None = None,
        key: str | None = None,
        length: int | None = None,
        limit: int = 12,
        seed: int | None = None,
    ) -> list[BankEntry]:
        """Progressions matching a mood phrase, ranked by how well they match.

        A multi-word mood ("dark nostalgic") prefers entries carrying every
        word but keeps partial matches; an unknown word refuses with the
        vocabulary rather than silently matching everything.
        """
        wanted = [w.lower() for w in (mood or "").split() if w]
        known = self.moods()
        for word in wanted:
            if word not in known:
                raise ValueError(
                    f"no progression is tagged {word!r}; moods: "
                    f"{', '.join(known)}"
                )

        candidates = []
        for entry in self.entries:
            if key and entry.key.lower() != key.lower():
                continue
            if length and entry.length != length:
                continue
            hits = sum(1 for w in wanted if w in entry.moods)
            if wanted and hits == 0:
                continue
            # Full matches first; among equals, shorter mood lists are purer
            # examples of the asked-for feeling.
            candidates.append((-hits, len(entry.moods), entry))

        rng = random.Random(seed)
        rng.shuffle(candidates)
        candidates.sort(key=lambda c: (c[0], c[1]))
        return [c[2] for c in candidates[:limit]]

    def pick(self, mood: str | None = None, key: str | None = None,
             length: int | None = None, seed: int | None = None) -> BankEntry:
        found = self.find(mood=mood, key=key, length=length, limit=8, seed=seed)
        if not found:
            raise ValueError(
                "nothing in the bank matches"
                + (f" mood {mood!r}" if mood else "")
                + (f" length {length}" if length else "")
            )
        return found[random.Random(seed).randrange(len(found))]

    # ---------------------------------------------------------------- loading

    def load_notes(
        self,
        entry: BankEntry,
        key: str | None = None,
        bars: float = 8.0,
        style: str | None = None,
    ) -> list[Note]:
        """The progression's actual notes: real voicings, transposed, tiled.

        `style` swaps the block-chord original for the comped version from a
        style folder -- same harmony, played. Transposition is by pitch class
        with minimal movement, so an A-minor file asked for in C moves up
        three semitones rather than up nine.
        """
        from . import corpus

        path = entry.path
        if style:
            if style not in entry.styles:
                raise ValueError(
                    f"{entry.name!r} has no {style!r} version; styles: "
                    f"{', '.join(sorted(entry.styles)) or 'none'}"
                )
            path = entry.styles[style]

        raw, _tempo = corpus.read_midi(path)
        if not raw:
            raise ValueError(f"{path.name} contains no notes")

        shift = 0
        if key:
            delta = (theory.note_to_pitch_class(key)
                     - theory.note_to_pitch_class(entry.key)) % 12
            shift = delta - 12 if delta > 6 else delta

        source_beats = max(n.start + n.duration for n in raw)
        source_bars = max(1.0, round(source_beats / BEATS_PER_BAR))
        wanted_beats = bars * BEATS_PER_BAR

        notes: list[Note] = []
        repeats = int(wanted_beats // (source_bars * BEATS_PER_BAR)) + 1
        for repeat in range(repeats):
            offset = repeat * source_bars * BEATS_PER_BAR
            for note in raw:
                start = note.start + offset
                if start >= wanted_beats - 1e-6:
                    continue
                notes.append({
                    "pitch": int(note.pitch) + shift,
                    "start": round(start, 4),
                    "duration": round(
                        min(note.duration, wanted_beats - start), 4
                    ),
                    "velocity": int(note.velocity),
                })
        notes.sort(key=lambda n: (n["start"], n["pitch"]))
        return notes

    def summary(self) -> dict:
        return {
            "progressions": len(self.entries),
            "keys": len({e.key for e in self.entries}),
            "styles": self.styles(),
            "moods": self.moods(),
        }
