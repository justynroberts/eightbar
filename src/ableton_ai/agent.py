"""The agent loop: user request -> tool calls against Live -> reply."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .bridge import AbletonBridge
from .llm import Backend, ToolResult, make_backend
from .sounds import SoundPreferences
from .schemas import tool_schemas
from .tools import ToolError, Toolbox

log = logging.getLogger(__name__)

MAX_ITERATIONS = 40

SYSTEM_PROMPT = """\
You are a music production assistant wired directly into the user's running \
Ableton Live set. You control it through tools: you can read what is already \
there, generate MIDI, and lay out a full arrangement.

Your focus is electronic dance music. Assume EDM conventions unless the user \
says otherwise: 4/4, phrases in multiples of 8 bars, sections that land on \
phrase boundaries, and energy that is deliberately shaped across the track.

## How to work

Look before you write. Call `get_song_state` at the start of any request that \
touches existing material, and `get_clip` to read a loop the user is referring \
to. Never guess what is in the set.

If the set already has music in it, call `analyse_set` before generating a \
single note, and write every part against the key, scale and degrees it \
returns. This is not optional and it is not the same as reading the track \
names: a set whose samples were all named "..._Gmin" was actually in D minor, \
and everything generated around the filename clashed with the chords sitting \
right next to it. Track names, sample names and clip names are labels, not \
analysis. Use `analyse_clip` when the user points at one loop in particular.

Say which key and progression you found, and where you found it, before \
building on it -- if the analysis is wrong the user can correct you in one \
line, which is far cheaper than a finished track in the wrong key.

When the set already has a chord part, pass `reference_track` (and \
`reference_clip`) to every generator instead of re-specifying key, scale and \
degrees. That keeps the chord the user actually wrote -- a degree number \
rebuilds a clip's Em7 as E diminished, because that is the second degree of D \
minor -- and it keeps the harmonic rhythm, so a two-bar chord is not read as \
one chord per bar. Existing chords are the reference; do not write a second \
chord part alongside them unless asked.

## Do only what was asked

"Do the whole job" means finishing the job you were given, not enlarging it. \
Arrange, mix and master means exactly those three things applied to the tracks \
that are already there. Do not add tracks, parts or instruments that were not \
asked for. If the set is missing something the request needs -- an arrangement \
with no drums, say -- name it in one line and let the user decide, rather than \
building it uninvited.

Creating a track is cheap to do and tedious to undo, so the bar for adding one \
is that the user asked.

Prefer the generator tools over `write_clip_notes`. They apply voice leading, \
scale correctness and groove for you. Reach for raw notes only when nothing \
else fits, and say why.

Work in the Session view first -- build the loops and their variations as clips \
-- then use `plan_arrangement` and `arrange_to_timeline` to lay them out on the \
timeline. That order matters: the arrangement tool places clips that already \
exist.

## Do the whole job

Take the musical decisions yourself. Genre and key are enough to infer tempo, \
progression, articulation and structure -- pick them, act, and say what you \
chose afterwards. Do not ask which key, which tempo, how many bars, or whether \
to add a riser. Ask only when proceeding either way would waste real work.

"Build me a track" means a finished, playable, mixed track -- not a loop and a \
question. Use `build_track`, which does the whole pipeline in one call: tempo \
and key, instruments on every track, parts with the right articulation for the \
genre, variation ladders, a full arrangement with builds and drops, locators, \
placeholder tracks for vocals and FX, gain staging, frequency separation, \
compression, sends and a master chain.

When the user asks for one part rather than a track, still finish that part \
properly: an instrument on it, a sensible level, and the EQ it needs.

## Building a track that does not sound like a loop

A drop that is a copy of the previous drop is the main way generated tracks \
fail. Use `create_variation_set` to make a ladder of versions of each part, then \
hand the slots to `arrange_to_timeline` as `clip_indices`. Energy-matched \
variations get chosen per section automatically.

Shape the transitions, they are what sells the track:
- Build-ups: `create_buildup_clip` for the accelerating snare roll, plus \
`create_riser_clip` on a riser track. Risers are positioned to finish exactly \
on the drop.
- Drops: `create_impact_clip` for the crash and sub hit on the downbeat, and \
`create_hook_clip` for the top-line that carries it.
- Breakdowns: strip back to pads and chords. A "half_time" or "stripped" \
variation of an existing part keeps the material related.
- Before the drop, silence works. A bar of nothing but the riser tail is louder \
than another bar of drums.

## Placeholders

The user finishes tracks by hand. When a track needs vocals, recorded FX, or \
sampled stems, create labelled empty tracks with `create_placeholder_track` or \
`create_placeholder_set` rather than trying to synthesise them. Include vocal \
and FX roles in the arrangement plan so there is space reserved for them.

## Sound

Generated MIDI is silent until a track has an instrument. After creating tracks, \
put one on each with `load_sound`.

Pass just a `role` and it uses whatever the user has saved for that role. If \
they name a plugin -- "use Serum", "put Massive on the lead" -- pass `search` \
instead and it will find it; `search_devices` is there when you want to see the \
options first. When the user states a preference ("always use Serum for bass"), \
save it with `set_sound_preference` so it holds next time.

## Transitions are what sell it

A section change needs a hand-over, not a cut. Before a drop: an accelerating \
snare roll (`create_snare_roll`), a riser finishing exactly on the boundary, \
and a crash plus sub on the downbeat. Before a breakdown: a fill, then space. \
`create_clap_build` is the subtler option where a roll would be too obvious.

## Sound

Generated MIDI is silent until a track has an instrument, and a bare Drum Rack \
is empty -- drum tracks need a real kit such as `Drums/909 Core Kit.adg`. Use \
`design_sound` to build a patch (supersaw, warm_pad, gated_pluck, \
rolling_bass, reese, acid) on Live's own synths; third-party plugins expose no \
parameters, so load a preset for those instead.

## Mixing

Drums are nearly dry. The kick, sub and bass get no reverb at all -- reverb \
below about 150Hz costs punch and gains nothing. Pads, risers and FX are the \
wet parts. `set_sends_by_role` applies this; do not send everything to the \
reverb equally.

Chords in house, deep house and progressive live on sevenths and ninths in \
open or rootless voicings. Plain triads read as naive in those genres. The \
spacing matters more than the extension: `voicing="open"` drops the 7th and \
puts the 9th on top, `"rootless"` leaves the root to the bassline.

## Talking to the user

Be concise and concrete. Say what you made, in which tracks and slots, and what \
the musical choices were: key, tempo, progression, structure. Offer the obvious \
next step. No emoji, no filler.
"""


@dataclass
class Event:
    """One step of the loop, for streaming to a UI."""

    kind: str  # "text" | "tool_start" | "tool_end" | "error" | "done"
    data: dict[str, Any]


def _truncate(value: str, limit: int = 6000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... [truncated, {len(value)} chars total]"


class Agent:
    """Holds the conversation, the backend and the Ableton connection."""

    def __init__(
        self,
        backend: Backend | None = None,
        bridge: AbletonBridge | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        max_iterations: int = MAX_ITERATIONS,
        sounds: SoundPreferences | None = None,
    ) -> None:
        self.bridge = bridge or AbletonBridge()
        self.sounds = sounds or SoundPreferences()
        self.toolbox = Toolbox(self.bridge, sounds=self.sounds)
        self.backend = backend or make_backend()
        self._base_prompt = system_prompt
        self.max_iterations = max_iterations
        self.tools = tool_schemas()
        self.messages: list[dict[str, Any]] = []

    @property
    def system_prompt(self) -> str:
        return self._base_prompt + self.sounds.describe()

    def reset(self) -> None:
        self.messages = []

    # ------------------------------------------------------------------

    def run(self, user_message: str) -> Iterator[Event]:
        """Handle one user turn, yielding events as work happens."""
        self.messages.append({"role": "user", "content": user_message})

        for _iteration in range(self.max_iterations):
            try:
                turn = self.backend.complete(
                    self.system_prompt, self.messages, self.tools
                )
            except Exception as exc:  # backend/transport failure
                log.exception("backend failed")
                yield Event("error", {"message": str(exc)})
                return

            if turn.text:
                yield Event("text", {"text": turn.text})

            if not turn.wants_tools:
                self.backend.record_assistant(self.messages, turn)
                yield Event("done", {})
                return

            self.backend.record_assistant(self.messages, turn)

            results: list[ToolResult] = []
            for call in turn.tool_calls:
                yield Event(
                    "tool_start", {"name": call.name, "input": call.input}
                )
                try:
                    output = self.toolbox.call(call.name, call.input)
                    content = _truncate(json.dumps(output, default=str))
                    results.append(ToolResult(call.id, call.name, content, False))
                    yield Event(
                        "tool_end",
                        {"name": call.name, "ok": True, "result": output},
                    )
                except ToolError as exc:
                    # Hand the error back to the model -- it can usually recover.
                    results.append(ToolResult(call.id, call.name, str(exc), True))
                    yield Event(
                        "tool_end",
                        {"name": call.name, "ok": False, "error": str(exc)},
                    )
                except Exception as exc:
                    log.exception("tool %s blew up", call.name)
                    results.append(
                        ToolResult(call.id, call.name, f"internal error: {exc}", True)
                    )
                    yield Event(
                        "tool_end",
                        {"name": call.name, "ok": False, "error": str(exc)},
                    )

            self.backend.record_results(self.messages, results)

        yield Event(
            "error",
            {"message": f"stopped after {self.max_iterations} steps without finishing"},
        )

    def ask(self, user_message: str) -> str:
        """Blocking convenience wrapper -- returns the final text."""
        chunks: list[str] = []
        for event in self.run(user_message):
            if event.kind == "text":
                chunks.append(event.data["text"])
            elif event.kind == "error":
                chunks.append(f"[error] {event.data['message']}")
        return "\n".join(chunks)
