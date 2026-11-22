---
name: slyce
description: Slyce: PSD exporting assets and UI skill by Ali. Plans (planner agent), exports (the main agent runs one command) and reviews (reviewer agent) a Photoshop PSD/PSB, turning all its UI and 2D art into optimized sprites with FUZIO's slicing rules and semantic names, the background baked as one 2048 x 2048 image (square power-of-two PNGs, 9-slice/3-slice boxes, deduplicated copies, composed art split into parts for animation) plus a manifest and an HTML review sheet. Skips what an engine should rebuild itself (text, glows, particles, plain gradients, flat colour boxes and circles, mockup screens, hidden and covered layers) and lists it with colours, radii, gradient stops and font info. Use whenever someone wants to export, slice, cut or extract the assets/sprites/UI/art from a PSD, prepare a Photoshop UI mockup for Unity or another engine, re-export a PSD after changes, or tune what a PSD export keeps or skips.
---

# Slyce: PSD exporting assets and UI skill by Ali

You give it a PSD; it copies the PSD, reads every layer in Photoshop, decides what is an asset, renders those, and writes optimized sprites. Everything goes through `scripts/slyce.py` (Python standard library only), which drives **the open Photoshop** over COM on Windows or AppleScript on macOS.

```
<skill>/
  SKILL.md                this file (workflow)
  README.md               team guide
  reference/rules.md      slyce_rules.json reference and every detection rule with its thresholds
  reference/house_rules.md  Ali's export rules (the planner plans by them, the reviewer checks them); add new feedback here
  agents/                 slyce-planner.md, slyce-reviewer.md (the two agents)
  scripts/slyce.py        CLI: check, run, redo, analyse, plan, export, process, test
  scripts/slyce_plan.py   tree.json + rules -> plan.json (what to render, what to skip)
  scripts/slyce_process.py renders -> sprites: skips, then slyce_decide.py (fills, slices, gradient boxes),
                          slyce_dupes.py (duplicates), slyce_write.py (sizing, states, parts); helpers in slyce_shapes.py
  scripts/slyce_test.py   regression test: re-process exported PSDs without Photoshop, compare with a baseline
  scripts/slyce_imaging.py, slyce_png.py, slyce_sheet.py, slyce_review.py, slyce_rules.py
  scripts/install_skill.py installs the skill into ~/.claude/skills/slyce and the agents into ~/.claude/agents
  photoshop/              slyce_read.jsx, slyce_export.jsx, slyce_common.jsx, slyce_check.jsx, json2.jsx, slyce_com.js
```

`SLYCE="python <skill>/scripts/slyce.py"` below.

## Safety (never break these)

- The original PSD is only **copied**, never opened, changed or saved. Photoshop opens the copy in `.slyce/runs/<run>/psd_copy.psd` and closes it unsaved.
- SLYCE writes only inside the output folder: a folder **named after the PSD, next to it** (`--out` to put it elsewhere). It keeps the data of the last 3 runs but only the latest run's PSD copy, and deletes older runs and copies in `.slyce/runs/` only.
- `--clean` deletes only sprites that SLYCE itself wrote in an earlier run (tracked in `manifest.json` `owned`).
- Photoshop may have the user's own documents open; SLYCE touches only its own copy and temporary documents. Ask the user to finish any text edit or close open dialogs if Photoshop is busy (exit code 3).

## Workflow: planner -> export -> reviewer (at most 3 review rounds)

SLYCE works with two agents. Start each with the Agent tool: `subagent_type` `slyce-planner`, `slyce-reviewer` when they are installed (`install_skill.py` copies them to `~/.claude/agents`); otherwise a `general-purpose` agent told to "follow <skill>/agents/slyce-<role>.md". Give every agent the skill folder, the PSD path and the output folder; they know the rest from their file. You (the main agent) run every command yourself (check, analyse, export), relay questions to the user, apply the reviewer's rule fixes, and keep the loop count.

1. **Check:** `$SLYCE check` -> Photoshop version, `ok: true` (2022 / v23 or later). If Photoshop is not running, ask the user to open it.
2. **Analyse + draft plan:** `$SLYCE analyse --psd "<file.psd>"` then `$SLYCE plan --psd "<file.psd>"` (analysis of a 280 MB PSD with 660 layers takes ~2.5 min: run it in the background if your tool times out). This writes `tree.json`, thumbnails, the draft `plan.json` and the numbered `.slyce/plan_sheet.png`.
3. **Planner** (`slyce-planner`): studies the PSD preview, plan sheet and tree, and writes `slyce_rules.json`: semantic `Role-Variant` names, the background as a whole and what to pull out of it, ignores (references, mockups, helpers), the title, roles, reuse. It writes `.slyce/plan_notes.md` and returns open questions. **Ask the user the open questions** (only those) before exporting, apply their answers as rules, and show them the plan in a few lines.
4. **Export (you):** run `$SLYCE redo --clean --psd ...` (render, slice, size, write; minutes on a big PSD, so run it in the background) and read its JSON: `export.errors`, `process.warnings`, the counts. If Photoshop is busy or not running, ask the user; retry at most once.
5. **Reviewer** (`slyce-reviewer`, read-only): checks the export against the PSD composite and the rules (checklist in its file) and returns `PASS` or `FAIL` with concrete rule fixes; it writes `.slyce/review_round<N>.md`.
6. **Loop:** the goal is **one cycle**: plan, export, review, PASS. Stop as soon as the reviewer says PASS; never run extra rounds to "double-check". Only on `FAIL`, apply all the reviewer's fixes in one pass as edits to `slyce_rules.json` (JSON-aware, keep `_help`; rules only, never SLYCE's scripts or the PSD; a fix that contradicts another rule goes to the user as a question), run step 4 again and review again. **3 rounds is a safety cap against an endless loop, not a target.** If round 3 still fails, or a fix "needs a person", stop and ask the user for a suggestion, listing the open issues with the reviewer's evidence.
7. **Report to the user:** what was exported (count and semantic names), the background (one image, 2048 x 2048), tinted fills, gradient boxes, duplicates, texts for TMP, what was skipped and why (by category), the review verdict and rounds, and `<output>/review.html` (open it with the browser tool if available).
8. **When the PSD itself changed**, start again at step 2 with `analyse` (new copy); the planner keeps the existing rules and only plans what is new. Unchanged layers come from the render cache, and `manifest.json` marks every sprite `new`, `changed` or `same`, so only real changes need reimporting.

Small follow-ups ("rename this", "ignore that") need no agents: edit `slyce_rules.json` and run `$SLYCE redo --clean --psd ...` yourself, then look at the result.

Never report success when `process` lists render `errors` or `warnings` you have not looked at.

## What SLYCE decides (FUZIO's slicing rules; thresholds in reference/rules.md)

SLYCE follows the FUZIO spec (sections 8, 9, 10.3, 12.3, 14.3) for every slicing decision, plus SLYCE's own skips for what an engine rebuilds. Each artboard is its own canvas.

**Skipped and listed** (manifest `skipped`, with a category):

| Found | Category |
|---|---|
| Hidden layer / group, opacity 0, empty or stray pixels | `hidden`, `empty` |
| Text layer: string, font, size, colour, effects in `texts` for TextMeshPro (12.3). Text inside a styled group is baked into that image | `text` |
| Name word glow/shine/light/ray/sparkle/particle/flare/bloom/fx/..., blend mode Screen/Add/Lighten/Color Dodge, or soft translucent pixels (glows, bokeh, feathered shadows) | `fx` |
| Layer under opaque layers above, or off its artboard (8.2) | `covered` |
| Full-screen image under a translucent full-screen dim box | `mockup` |
| Large image (half the artboard or more) below the artboard's background: references and alternative backgrounds | `behind` |
| Inside the background group: overlays added on the painting (shine, sparkles, twinkle, particles, flares, light blend modes, rule `animate`) are pulled out of the baked background and handled on their own; rule `bake` keeps one in | pulled out |
| Clipped, adjustment and non-Normal blend layers | rendered merged with the layer below (8.1) |

**Roles.** `title`: the largest text with a style TMP cannot draw (bevel, inner shadow, glow, satin, pattern, warp), or the styled group holding it, or (with no such text) art at the top named like the PSD or with title/logo in its name; an image of at most 512 px. `composed`: a group with a layer style, a group of 6+ pieces without text (vector titles, icons), or overlapping art (chest, gift); one image, or its **parts** instead when drawn together they redraw it (SSIM >= 0.70, 14.3): an object exported in parts ships only as its parts. `background`: the background **as a whole**: a group named bg/background/backdrop covering half its artboard (or rule `background` on a group) is rendered as ONE image with everything static in it (room, floor, glows, shadows, props), large images below its base layer left out; otherwise art filling 90 % of its artboard. Backgrounds are fitted (aspect kept) and padded into one 2048 x 2048 texture (`backgroundSize`): a 1870 x 2436 tablet background becomes 1572 x 2048 inside 2048 x 2048. `box`: shape and fill layers, UI names (btn, bar, frame, panel, ...), and art whose silhouette is a box or rounded box or that slices losslessly to 60 % or less (buttons, bars). `art`: everything else. Rules `box`, `art`, `title`, `background` override.

**Decision order** (9.1):

| Role | Result |
|---|---|
| art, composed, title | whole sprite |
| background | the background group: one image fitted and padded into 2048 x 2048; a single layer: tinted fill if flat, 9-slice if it slices, else the same 2048 square |
| box | 1. **tinted fill**: one flat colour in a box, rounded box (equal radii) or ellipse, checked by redrawing it (SSIM >= 0.98, MAE <= 3): no sprite, use the white box / circle tinted (9.10)  2. **9-slice** of the whole element (9.2)  3. **gradient box**: linear gradient at 0/90/180/270 deg, frame 9-sliced: frame sprite + Gradient2 keys, checked by rebuilding it (9.4)  4. **3-slice** along the flat axis (9.3), its centre kept wide enough that the content is about square, and a fill bar cut straight by a mask made round at both ends  5. **header split**: header band whole, body sliced (9.7)  6. whole |

Radial and angle gradients are exported (FUZIO rebuilds radial looks from soft circles, 9.5, which needs the kit's soft circle; listed in warnings).

**Duplicates** (10.3), largest first: copies of one smart object; identical pixels in any of 8 rotations/mirrors; resized, freely rotated or mirrored copies (scale from alpha mass, angle from principal axes, SSIM >= 0.985 reused, 0.96–0.985 reused and flagged); a tinted copy of a grey sprite. Identical sliced sprites also merge, and a whole box that a sliced sprite draws at its size. Two drawings of the same object that are not pixel copies stay separate; rule `reuse` (`{"<layer pattern>": "<sprite name>"}`) makes one reuse the other when the user says they look the same.

**Planning for future use** (the planner decides, the reviewer checks): `headroom` renders animating art (active collectibles, rewards) from its smart object at up to 2x its placed size; `states` exports states hidden inside smart objects (a light's lit glow); `parts` splits pieces the artist grouped for animation (tree tiers). `tree.json` lists every layer inside each smart object, hidden ones too (`smartObject.contents`).

**Sizing** (9.8): `size = 2^round(log2(longest side))`, 32–1024 px (`minSize`, `maxSize`); within 5 % of the rounding boundary it rounds up for text and strokes <= 2 px. Art is padded when the size rounds up and downscaled only when it rounds down; sliced sprites are never resampled; the title is at most 512 px; parts keep their whole's scale; soft sprites (shadows, glows) round down; stroked UI shapes are never shrunk below 0.8 (they go up a size); backgrounds are fitted and padded into 2048 x 2048, a gradient-only background is not exported.

## Output

```
<PSD folder>/<PSD name>/
  Sprites/<Name>.png, Sprites/<Art>/<Art>-<Part>.png
  manifest.json      elements (role, treatment, decisions), sprites (texture size, content rect, scale, borders,
                     placed bounds in PSD px, status), duplicates, tintedFills, gradients (gradient boxes
                     with Gradient2 keys), texts, skipped (category + reason), artboards, stale/owned files,
                     summary (counts, textures by size, textureMB), timings
  review.html        the review sheet
  slyce_rules.json   per-PSD rules (created on the first run)
  .slyce/runs/       last 3 runs: psd_copy, tree.json, thumbs/, composite.png, plan.json, renders.json
  .slyce/cache/      rendered elements by content signature
```

Names are semantic, FUZIO 10.2 `Role-Variant` (`Btn-Play`, `Title-CharmQuest`, `Charm-Bell`, `Bg-Room`): the planner sets them with `rename` rules; without one a name comes from the PSD layer name (`Btn close copy 2` -> `Btn-Close`), unique with `-2`, `-3`, and the plan warns about names that break the rule.

## Troubleshooting

- Every command prints one JSON result on stdout; on failure it prints `{"error": "..."}` and exits 1 (progress goes to stderr).
- Timing: every result has `timings` (seconds per stage); each command also appends a line to `<output>/.slyce/timings.jsonl` (stages, layers, renders, sprites). The duplicate search is the slowest pure-Python step (~5 min on a 660-layer PSD).
- `Cannot reach Photoshop over COM` (Windows): Photoshop not installed or never started; open it once. A specific version: set `SLYCE_PS_PROGID=Photoshop.Application.180`.
- Exit code 3 / "stayed busy": a dialog is open or text is being edited in Photoshop.
- macOS: the first run asks to allow the terminal to control Photoshop (System Settings > Privacy & Security > Automation).
- A render error for one element is reported and the rest continues; look at `.slyce/runs/<run>/render_error_*.json`.
- Something looks stale: `redo --full` renders everything again, ignoring the cache.
