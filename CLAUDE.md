# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source .venv/bin/activate            # uv-managed venv, Python 3.12

python -m pytest tests/ -q           # all 108 tests, none need Ableton
python -m pytest tests/test_tools.py::test_full_six_minute_edm_build -q   # one test
python -m pytest tests/ -q -k arrangement                                 # by keyword

python install_remote_script.py      # copy the remote script into Live's bundle
python -m ableton_ai.cli --check     # is Live reachable on :9878?
python -m ableton_ai.server          # web UI on 127.0.0.1:7817
python -m ableton_ai.cli             # terminal chat
python -m ableton_ai.cli --backend claude-cli "make a house loop"   # no API key
```

Backend selection is `auto` by default: the Anthropic API when
`ANTHROPIC_API_KEY` is set, otherwise `claude -p` via the local Claude Code
install. `--backend claude-cli` forces the keyless path, which is the intended
way to test.

## The two-process split

This is the thing to understand before changing anything.

**Inside Ableton** (`remote_script/AbletonAI/__init__.py`) runs in Live's
embedded Python. Standard library only — no pip packages, ever. It is a
`_Framework.ControlSurface` subclass that opens a TCP socket on **9878** and
speaks newline-delimited JSON. Every Live Object Model touch, read or write, is
marshalled onto Live's main thread via `self.schedule_message(0, fn)`; calling
the LOM from the socket thread will crash Live intermittently.

**Outside** (`src/ableton_ai/`) is the normal Python app. It never imports
`Live`; it only sends commands over the socket.

Editing the remote script means **restarting Ableton** — Live only loads control
surfaces at launch. `install_remote_script.py` also clears `__pycache__`,
because stale bytecode silently shadows new source.

**Install to the User Library, not the app bundle.** On macOS Sequoia and later
a copy into `/Applications/Ableton Live 12 Suite.app/...` succeeds, survives a
read-back `diff`, and is then silently reverted when macOS re-validates the
signed bundle — typically by the next Live launch. This was diagnosed the hard
way; `--bundle` still exists but is not the default. The correct path is
`~/Music/Ableton/User Library/Remote Scripts/AbletonAI/`.

An unrelated `AbletonMCP` (port 9877) from `~/work/ableton_ai` is installed in
both locations. Ours is `AbletonAI` on 9878. Both appear in the dropdown.

You can restart Live yourself when a remote-script change needs picking up:

```bash
osascript -e 'tell application "Live" to quit saving no'
open -a "Ableton Live 12 Suite"
# then poll until the script is listening
lsof -nP -iTCP:9878 -sTCP:LISTEN
```

## Live API gotchas paid for the hard way

These all failed against real Ableton while passing against the simulator.

- **`clear_envelope()` invalidates any Envelope handle taken before it.**
  Writing through the stale handle fails with a Boost error about
  `TPyHandle<AAutomation>` that says nothing about the real cause. Clear
  *before* calling `create_automation_envelope()`, never after.
- **`automation_envelope()` only reads.** A clip with no envelope for that
  parameter returns `None`; use `create_automation_envelope()` to make one.
- **A MIDI-routed track raises on `output_meter_left`** rather than lacking the
  attribute, so `getattr(track, name, default)` never returns the default. Wrap
  meter reads in try/except.
- **Don't reset `current_song_time` before recording.** In Session view it
  stops whatever clips are playing, so the capture records silence.
- **Browser walks need a per-root budget.** One global cap is exhausted inside
  `Instruments` and never reaches `Plugins`, so plugin searches find nothing.
- **A bare "Drum Rack" from `Instruments/Drum Rack` is empty and silent.** Drum
  roles must load a real kit preset such as `Drums/909 Core Kit.adg`.
- **Live's analysers publish nothing.** Spectrum and Tuner expose exactly one
  parameter, `Device On`; third-party plugins expose none at all until
  Configure is pressed. Measurement has to come from resampling the master and
  analysing the file (`capture_audio` then `analyse_audio`).

A separate, older `AbletonMCP` script from `~/work/ableton_ai` may also be
installed in the bundle on port 9877. The two coexist; do not merge them.

## Where the intelligence lives

The model is deliberately kept away from raw MIDI. It asks for *"a rising
i-VI-III-VII in C minor over 8 bars"*; the deterministic layer decides pitches.
When adding capability, add it to the generator layer and expose a high-level
tool — do not push the work onto the prompt.

- `theory.py` — scales, chord qualities, diatonic harmony, and `voice_lead()`,
  which re-voices a progression to minimise semitone travel. This is why
  generated chords do not jump octaves between bars.
- `generators.py` — one function per part type, all returning the same note dict
  (`pitch` / `start` / `duration` / `velocity`, times in beats). Rhythms are
  16-character step strings.
- `variations.py` — mutation operators over note lists, plus named recipes.
  Nothing here knows about Ableton.
- `arrangement.py` — section templates and the bar maths. Sections are always
  rounded to whole 8-bar phrases; a drop landing on bar 33 instead of 32 sounds
  broken regardless of how good the parts are.

## Tool schemas are generated, not written

`schemas.py` derives the Anthropic tool definitions from the `Toolbox` method
signatures and docstrings. Consequences:

- **Every `tool_*` method needs a docstring** — it becomes the tool description,
  and `test_every_tool_has_a_schema_and_a_description` fails without one.
- Parameter prose lives in `PARAM_DOCS`, keyed by parameter name across all
  tools.
- `tools.py` uses `from __future__ import annotations`, so type hints are
  strings at runtime; the generator resolves them with `typing.get_type_hints`.
  A hint referencing an unimportable name silently degrades every parameter in
  that tool to `string`.

The same schemas feed both backends — natively for the API, and rendered to text
by `render_for_text_protocol()` for `claude -p`.

## Backends

`llm/base.py` defines `AssistantTurn` / `ToolCall` / `ToolResult`. The agent loop
only ever sees those, so both backends are interchangeable:

- `anthropic_backend.py` — native tool use, streamed, adaptive thinking.
  `record_assistant` echoes `response.content` back verbatim so thinking blocks
  survive the round trip.
- `claude_cli_backend.py` — shells out to `claude -p`. The CLI has no tool
  channel, so tools are negotiated as a JSON object in the text
  (`{"say": ..., "tools": [...]}`) and `_extract_json` tolerates fences and
  surrounding prose.

## Testing without Ableton

`tests/fake_live.py` implements the remote script's command surface against
dicts. It must stay faithful to the real thing — in particular it raises
`AbletonError`, because the tool layer only converts *those* into `ToolError`.
If the fake raises something else, tests pass while the real path would break.

`tests/test_tools.py::_build_track` builds a full seven-track, six-minute EDM
track through the public tools and is the best single check that a change has
not broken the arrangement path.

## UI

`web/` is served by FastAPI at `/static`. The design is recorded in `DESIGN.md`
and is binding: Terminal archetype, split-screen, per-role colour palette,
type-on motion, both themes. Per the user's global rules any visual change must
keep light **and** dark themes, animated effects, Bricolage Grotesque, and the
"Made by FintonLabs" info button.

The UI polls `/api/status` every 8s; `/api/chat` is SSE, one JSON event per agent
step (`text`, `tool_start`, `tool_end`, `error`).

## Conventions that matter

- Times are in **beats**; bars are `beats / 4`. Tools take bars, the wire
  protocol takes beats. `_bars()` converts.
- Drum pitches follow Ableton's Drum Rack: kick is C1 = 36. See `DRUM_MAP`.
- Roles (`kick`, `bass`, `chords`, `hook`, `riser`, `impact`, `vocal`, …) are the
  vocabulary shared by track colours, arrangement templates and the UI palette.
  Adding a role means touching `arrangement.ROLES`, `tools.ROLE_COLOURS` and the
  `--role-*` tokens in `web/style.css`.
- In `arrange_to_timeline`, `impact` is placed once on the section downbeat and
  `riser` is positioned to *finish* on the section boundary. Those are
  `ONE_SHOT_ROLES` / `SPAN_ROLES`, not general looping.

## Testing against real Ableton

`tests/` is two suites. The default run is the simulator only:

```bash
python -m pytest tests/ -q                              # 213, no Ableton needed
python -m pytest tests/test_live_conformance.py -m live -v   # 9, needs Live
```

The conformance suite exists because **the simulator is faithful to the API as
written down, not the API Live has**. Every serious bug found on 2026-08-30
passed the entire simulator suite: `clear_arrangement` removed nothing because
`Clip.delete_clip()` does not exist; the park clip survived because handles die
after any edit; six locators of ten vanished because `current_song_time` lands a
tick late; an empty `track_indices` list wiped the whole timeline. None of those
are expressible against dicts.

Run it after any change to `remote_script/`. It creates and deletes its own
scratch track and restores the locators it moved.

## Two things that do not fail loudly

**Swallowed exceptions.** Handlers call `self._warn(where, exc)` rather than
`except Exception: pass`, and every response carries `warnings`. This is not
tidiness: `clear_arrangement` reported success while deleting nothing for
months, and no test could see it.

**Unsaved work.** Live exposes no document-modified flag, so the remote script
counts non-read commands and `ping` returns `unsaved_changes`. Check
`unsaved_changes` before anything that reloads Live -- an unsaved session does
not survive a restart and there is no undo across one. `snapshot_set` writes the
whole set to JSON that `restore_snapshot` replays; `clear_arrangement`,
`delete_track` and `arrange_to_timeline` take one automatically.

## Analysis before generation

`analyse_set` reads the key, scale and progression out of the clips already in
the set. Call it before generating anything into a set you did not build -- a
sample named `..._Gmin` sat next to a chord clip that was actually in D minor.

Better still, pass `reference_track` to a generator: degrees alone rebuild a
clip's Em7 as E diminished, because that is the second degree of D minor.
`_reference_progression` lifts the sounding pitches, qualities and bar spacing
instead.

## Learning from references

`corpus.py` learns from MIDI files; `degrees="learned"`, `style="learned"` and
`groove="learned"` are how that reaches generation. Without those the corpus is
write-only -- it was, for a while. `corpus_profile` reports what was learned.

## Packaging

`npm run dist:mac` freezes the Python core with PyInstaller (`build_core.spec`
-> `dist/core/`, ~88MB) and ships it in `Resources/core`. The Electron app
prefers that binary and falls back to a checkout's `.venv` in development, so a
packaged build chats on a machine with no Python. `dist/` is gitignored.

## Closed vocabularies must accept the words people use

Every enum parameter has bitten at least once: `variation="extended"`,
`extension="minor9"`, `role="melody"`, `mutations=["stab"]`,
`pattern="offbeat_hats"`. The request was musically sensible each time; the
table just did not hold the word.

Three defences, in `Toolbox._repair_vocabulary` and the module alias tables:

- **Publish the vocabulary in the schema.** `ENUMS` and `TOOL_ENUMS` in
  `schemas.py` are sourced from the modules, so they cannot drift. The Anthropic
  backend enforces them; `claude -p` cannot, which is why the other two matter.
- **Correct near-misses and say so.** A match at difflib cutoff 0.86 is applied
  and reported in the result as `corrected`. Anything looser is *not* guessed --
  substituting a word that merely sounds similar makes different music silently,
  which is worse than failing.
- **Suggest, do not enumerate.** An unknown value returns the three nearest
  names. Twenty-five is the least useful possible reply.

When adding a vocabulary, add its `ALIASES` table alongside it and expose a
`*_vocabulary()` that includes the synonyms, the way `variations`,
`generators`, `voicings` and `arrangement` do. If a request keeps failing
because the concept is missing rather than the word -- `offbeat_hats` described
an instrument, not a kit -- add the concept.
