# eightbar

Natural-language control of a running Ableton Live set. Ask for a chord
progression, a loop, a variation or a whole six-minute arrangement, and it
writes MIDI straight into your project.

**User guide → https://justynroberts.github.io/eightbar/**

Built for EDM: build-ups, breakdowns, drops, hooks, risers and impacts are
first-class, and every looped part can be given a ladder of variations so a
generated track does not sound like one bar on repeat.

```
"create me a MIDI chord progression rising in C minor"
"build a dark rolling tech house loop at 128"
"here's my loop — arrange it for a 6 minute dance track"
"make four variations of the drums, bigger each time"
"add placeholder tracks for vocals and FX"
```

## How it fits together

```
  Browser UI  ──HTTP/SSE──▶  FastAPI  ──▶  Agent loop  ──▶  Claude
   or CLI                                      │          (API or claude -p)
                                               │
                                          35 tools
                                    (theory · generators ·
                                     variations · arrangement)
                                               │
                                        TCP :9878, JSON
                                               ▼
                                    AbletonAI Remote Script
                                       inside Ableton Live
```

The model never guesses MIDI note numbers. It asks for *"a rising i-VI-III-VII
in C minor over 8 bars"* and the theory engine decides the pitches, applies
voice leading, and returns a summary the model can reason about.

## Setup

**1. Install the Python package**

```bash
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"
```

**2. Install the remote script into Live**

```bash
python install_remote_script.py
```

This installs to `~/Music/Ableton/User Library/Remote Scripts/AbletonAI`.

Do **not** install into Ableton's app bundle on macOS Sequoia or later. The copy
appears to succeed and even reads back correctly, then macOS reverts the signed
bundle and the script is gone by the next launch. `--bundle` is still there if
you need it, and `--uninstall` cleans both locations.

**3. Turn it on in Ableton**

Quit and reopen Live, then **Preferences → Link, Tempo & MIDI → Control Surface**
and pick **AbletonAI** in any free slot. The status bar should read
`AbletonAI: listening on port 9878`.

```bash
python -m ableton_ai.cli --check
```

**4. Pick a model backend**

| Backend | When | How |
|---|---|---|
| `anthropic` | Normal use | `export ANTHROPIC_API_KEY=sk-ant-...` |
| `claude-cli` | Testing without a key | needs Claude Code on `PATH`; uses `claude -p` |

`auto` (the default) uses the API when a key is present and falls back to
`claude -p`.

## Running it

```bash
python -m ableton_ai.server                 # web UI on http://127.0.0.1:7817
python -m ableton_ai.cli                    # terminal chat
python -m ableton_ai.cli "make a house loop in F minor"   # one-shot
python -m ableton_ai.cli --backend claude-cli             # force the CLI backend
```

CLI commands: `/state`, `/arrange`, `/tools`, `/reset`, `/quit`.

## What it can do

**Read** — the whole set, any clip's notes, the arrangement timeline. It looks
before it writes.

**Generate** — chords (with voice leading), basslines, drums, arpeggios,
melodies, hooks, build-ups, risers, impacts. Raw note writing is there as an
escape hatch.

**Vary** — 15 mutation operators (`thin`, `densify`, `half_time`, `stutter`,
`octave_up`, …) and 10 named recipes (`stripped`, `breakdown`, `pre_drop`,
`climax`, …). `create_variation_set` fills consecutive slots with a ladder of
versions.

**Arrange** — 11 templates (`house`, `big_room`, `progressive_house`,
`future_bass`, `techno`, `melodic_techno`, `trance`, `dnb`, `dubstep`, `pop`,
`ambient`). Sections always land on 8-bar phrases, risers finish exactly on the
drop, impacts hit the downbeat, and named locators are dropped on the timeline.

**Placeholders** — labelled, colour-coded empty audio tracks for the vocals and
FX you will record yourself.

## Sound

Generated MIDI is silent until a track has an instrument. The agent can load one
via `browse_devices` / `load_device`, but a Drum Rack on the drum track and a
bass patch on the bass track is worth doing by hand once and saving as a
template.

## Development

```bash
python -m pytest tests/ -q
```

108 tests, none of which need Ableton running — `tests/fake_live.py` implements
the same command surface in memory, so the tools, the agent loop and a full
six-minute arrangement can all be exercised offline.

## Layout

| Path | What |
|---|---|
| `remote_script/AbletonAI/` | Runs inside Live. Stdlib only. |
| `src/ableton_ai/theory.py` | Scales, chords, voice leading |
| `src/ableton_ai/generators.py` | Note generation per part type |
| `src/ableton_ai/variations.py` | Mutation operators and recipes |
| `src/ableton_ai/arrangement.py` | Section planning and bar maths |
| `src/ableton_ai/tools.py` | The 35 tools the model calls |
| `src/ableton_ai/schemas.py` | Tool schemas, derived from signatures |
| `src/ableton_ai/agent.py` | The agent loop and system prompt |
| `src/ableton_ai/llm/` | Anthropic and `claude -p` backends |
| `web/` | Terminal-archetype UI (see `DESIGN.md`) |

## Licence

MIT
