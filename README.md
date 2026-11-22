# SLYCE

**Slyce: PSD exporting assets and UI skill by Ali.** A Claude Code skill that takes a Photoshop PSD and exports its UI and 2D art as optimized, engine-ready sprites, with a planner and a reviewer agent around one export command.

Give Claude a PSD and say "slice this PSD" (or "export the assets from this PSD"). SLYCE:

- **copies** the PSD (the original is never opened) and reads every layer in the open Photoshop, including hidden layers inside smart objects;
- **plans** with a planner agent that thinks like the artist and the Unity developer: semantic `Role-Variant` names, the background baked as one image, hidden states (locked, grey, socket, lit), pieces grouped for animation (tree tiers), headroom for art that animates bigger, what to ignore (references, mockups, paint-overs);
- **exports** with FUZIO's slicing rules: square power-of-two sprites (32 to 1024 px, title 512, background 2048 x 2048), flat boxes as tinted fills, 9-slice / 3-slice / gradient boxes / header splits, fill bars rounded at both ends, duplicates reused even when resized, rotated or mirrored, rotated smart objects exported upright with rotation and pivot for Unity;
- **skips and lists** what an engine should rebuild: text (font, size, colour), glows, shadows and particles, hidden and covered layers;
- **reviews** with a reviewer agent against the PSD composite and the rules, with rule fixes applied and the export re-run at most 3 times (the goal is one) before asking you;
- writes `manifest.json`, a `review.html` sheet and `timings.jsonl`, remembers your corrections per PSD in `slyce_rules.json`, and re-renders only what changed.

## Install

Needs Photoshop 2022 or later (Windows or macOS) and Python 3.8+. The installer also installs **numpy** (for fast pixel work); without it SLYCE still runs, only slower, with the same sprites.

```
python scripts/install_skill.py          # copies the skill to ~/.claude/skills/slyce and the agents to ~/.claude/agents
python scripts/install_skill.py --link   # or links the skill, so edits here are live (agents are always copied)
```

Restart Claude Code. Check Photoshop is reachable:

```
python scripts/slyce.py check
```

## Use without Claude

```
python scripts/slyce.py run  --psd "D:/UI/Popup.psd"            # analyse + plan + export + process
python scripts/slyce.py redo --psd "D:/UI/Popup.psd" --clean    # after editing slyce_rules.json
```

Output goes to a folder named after the PSD, next to it (`D:/UI/Popup/`): `Sprites/` (one flat folder; one subfolder per artboard only when several artboards have sprites), `manifest.json`, `review.html`, `slyce_rules.json`, and `.slyce/` (the last 3 PSD copies, a render cache, plan and review sheets, `timings.jsonl`).

## Measuring a run

Every command appends a line to `<output>/.slyce/timings.jsonl`: seconds per stage (analyse, plan, export, process, total), layers, renders, sprites, parts and duplicates. The last process run's line is also in `manifest.json` under `timings`.

## Regression test

After changing SLYCE's scripts, check that already exported PSDs still come out the same, without Photoshop:

```
python scripts/slyce.py test                          # the PSDs in tests/cases.json (copy tests/cases.example.json)
python scripts/slyce.py test --psd "D:/UI/Popup.psd"  # one PSD
python scripts/slyce.py test --save                   # accept the current result as the new baseline
```

It re-plans and re-processes from the latest analysis and the render cache into a temp folder and lists every sprite, duplicate, tinted fill or skip that changed. The baseline lives in `<output>/.slyce/baseline.json`.

## House rules

Ali's export rules (naming, sizes, families, backgrounds, states) are in [reference/house_rules.md](reference/house_rules.md). The planner plans by them and the reviewer checks them; add new feedback there.

See [SKILL.md](SKILL.md) for the workflow, [reference/rules.md](reference/rules.md) for every rule and threshold, and [agents/](agents/) for the planner and reviewer.
