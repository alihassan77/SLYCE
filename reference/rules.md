# SLYCE rules and detection reference

SLYCE's slicing decisions follow the FUZIO spec (`FUZIO_SPEC.md` sections 8, 9, 10.3, 12.3, 14.3; FUZIO code `Editor/Processing`, `Editor/Imaging`). Section numbers below refer to it. SLYCE adds its own skips for what an engine rebuilds (glows, shadows, mockups, images behind the background).

## slyce_rules.json

Created in the output folder on the first run. Edit it, then run `redo`.

```json
{
  "ignore": ["Artboard 1/Mockup*", "id:80"],
  "keep": ["*/Hero glow"],
  "box": ["Artboard 1/Btn Play"],
  "art": ["Artboard 1/Badge"],
  "title": ["Artboard 1/GET REWARDED"],
  "whole": ["Artboard 1/Group 20"],
  "split": ["Artboard 1/Rewards"],
  "noParts": ["Artboard 1/Btn Play"],
  "noSlice": ["Artboard 1/Header"],
  "textAsImage": ["Artboard 1/Subtitle"],
  "background": ["Artboard 1/Group 7/BG"],
  "bake": ["Artboard 1/Group 7/BG/Floor glow"],
  "animate": ["Artboard 1/Group 7/BG/Group 10"],
  "rename": { "Artboard 1/Cross": "Btn-Close", "id:23": "Btn-Connect" },
  "reuse": { "Artboard 1/BAR/12_bell": "3-Bell" },
  "fxNames": ["glow", "shine", "light", "ray", "sparkle", "particle", "flare", "bloom", "fx"],
  "minSize": 32,
  "maxSize": 1024,
  "keepRuns": 3
}
```

Patterns: the layer's full path as shown in `manifest.json` / `review.html` (`Group/Sub/Layer`), case-insensitive, `*` and `?` wildcards; or `id:<layerId>`; or, without a `/`, the layer name alone (`"*glow*"` matches any layer whose name contains glow). Layer ids are stable while the PSD keeps its layers, so `id:` survives renames.

| Key | Effect |
|---|---|
| `ignore` | never exported; an ignored group skips everything in it; inside a composed group the layer is hidden in the group's render |
| `keep` | exported even when SLYCE would skip it as fx, tinted fill, covered, mockup or behind the background |
| `box` | treated as a UI box: tinted fill, 9-slice, gradient box, 3-slice, header split (9.1) |
| `art` | treated as art: whole, never sliced or drawn as a tinted fill |
| `title` | the main title text: an image of at most 512 px (12.3) |
| `whole` | one image, never sliced; on a group: the group is rendered as one image and its text is baked in |
| `split` | a group's layers are exported separately even when SLYCE would make it one image |
| `noParts` | composed art is exported whole only |
| `noSlice` | never 9-slice / 3-slice |
| `textAsImage` | a text layer is exported as an image; other text is skipped and listed |
| `background` | on a group: the background as a whole, everything static inside baked into one image; on a layer: background art. Fitted and padded into `backgroundSize` |
| `bake` | inside a background group: keep this in the baked background even though it looks like an overlay |
| `animate` | inside a background group: pull this out (shine, sparkles, anything that animates) and handle it on its own |
| `backgroundSize` | the square texture a background is fitted and padded into (default 2048: a 1870 x 2436 tablet background becomes 1572 x 2048 inside 2048 x 2048) |
| `headroom` | art that will animate or zoom bigger (an active collectible, a reward): rendered from its smart object's own contents at up to `headroomScale` (2) x its placed size, never beyond the source's resolution, so it stays sharp when scaled up. Needs an axis-aligned smart object without layer styles |
| `headroomScale` | the most a headroom sprite may exceed its placed size (default 2) |
| `states` | `{"<layer pattern>": {"<State>": {"name": "Light-On", "show": ["<layer inside>"], "hide": ["<layer inside>"], "headroom": true}}}`: states hidden inside a smart object (a light's lit glow), each exported as its own sprite from the smart object's contents with those layers shown or hidden (names match `smartObject.contents` in tree.json, wildcards allowed) |
| `parts` | split a group or smart object into its parts for animation even when SLYCE would not (a tree whose tiers are grouped separately); the parts must still redraw the whole (SSIM >= 0.70) |
| `keepRotation` | rotated smart objects to export as rotated in the PSD. By default every rotated smart object (not mirrored or warped) is exported **upright**: from its contents when its whole look is inside them, otherwise rendered as placed with the smart object turned back around its centre (so layer styles and merged tint layers stay); the sprite gets `rotation` (degrees, clockwise on screen) and `pivot` (its centre in PSD pixels), and `placed` is its upright rect. Copies carry their own `pivot`, upright `placed` and signed `rotation` |
| `noReuse` | never reuse another sprite automatically for these |
| `noRoundEnds` | a 3-sliced bar cut straight at one end stays cut |
| `symmetric` | `{"<pattern>": "left"/"right"/"top"/"bottom"/"auto"}`: a 3-sliced piece that looks the same at both ends but was built unevenly (a ribbon whose second end is a dragged copy) gets that end's cap mirrored onto the other; `auto` keeps the bigger cap |
| `sameSize` | `{"<family>": ["<pattern or sprite name>", ...]}`: interchangeable items (boosters, packs, rewards) all exported at the biggest slot's size; members placed smaller render from their smart object's contents (up to 4x); a member whose source is still too small is padded, not upscaled, and flagged |
| `sameCanvas` | `{"<family>": ["<pattern or sprite name>", ...]}`: sliced pieces that swap in one prefab (card headers, card backgrounds with or without shadow or stroke) share one texture size and slice borders: along a sliced axis each border is padded out to the family's widest, along an unsliced axis the solid bodies (alpha >= 200) line up at the top / left, so shadows and strokes grow outward |
| `rename` | pattern → sprite name |
| `reuse` | pattern → sprite name: the element looks the same as that sprite (your visual call), so it reuses it with the best rotation, mirror and scale found instead of exporting its own |
| `fxNames` | name words that mark glow/light/particle layers |
| `minSize`, `maxSize` | the square power-of-two sizes allowed (9.8: 32–1024) |
| `keepRuns` | runs (PSD copies) kept in `.slyce/runs` |

Rules files written by SLYCE 0.1 (sizes 128..512) are upgraded on load: the old default sizes become 32..1024, sizes set by hand stay.

## Plan step (layer level, `slyce_plan.py`)

Walks the PSD top to bottom; artboards are always walked into, and each element records its artboard.

1. Hidden → `hidden`; rule `ignore` → `rule`; no pixels or opacity 0 → `empty` (8.2).
2. Text → `texts` (string, runs with font/size px/colour, alignment, effects) and `text`, except the main title (12.3): rule `title`, else the largest visible text with bevel, inner shadow, inner/outer glow, satin, pattern overlay or warp; it becomes a `title` element, and a styled group holding it becomes the title image. Without a text title, art (not text) in the top third of its artboard, at least 35 % of its width, named like the PSD or with title/logo/heading in its name, is the title (12.3: "not a text layer at all").
3. Adjustment layers, clipped layers and blend modes other than Normal / Pass Through wait for the next layer below and are rendered merged with it (`mergeWith`, 8.1). With nothing below they are `orphan`.
4. Name contains an `fxNames` word, or blend mode Screen / Linear Dodge (Add) / Color Dodge / Lighten / Lighter Color → `fx`.
5. Groups become **one image** (`composed`) when: rule `whole`; the group has its own layer style; it holds 6+ visible pieces and no text and covers < 60 % of its artboard; or its parts overlap (sum of part areas ≥ 1.25 × their union) with an aspect ≤ 2.2 (not a wide button/bar) or an art-like name (chest, gift, coin, icon, logo, character, ...). Otherwise SLYCE goes inside it.
6. Composed groups and smart objects (layered PSB contents, not linked, warped or rotated) with 2–16 overlapping parts, at least 64 px across, also export their **parts** (14.3); parts named like fx are left out of the parts (they stay in the whole).
7. **Background as a whole**: a group named bg / background / backdrop / environment / scene covering half its artboard, or with rule `background`, is ONE element (role `background`) rendered with everything inside it, except: overlays (name words shine, sparkle, glitter, twinkle, particle, flare, fx, ray, bloom, highlight, glint, star; light blend modes; rule `animate`; not rule `bake`), which are pulled out and walked as their own layers (listed before the background, since they sit on top); text (listed for TMP); rule `ignore`; and large images (half the artboard or more) below the group's first layer that fills 90 % of the artboard (`behind`).
8. Roles: shape and fill layers and layers with a UI name word (btn, button, bar, frame, panel, box, bg, popup, banner, ribbon, tab, slot, fill, plate, board, card, header, pill, rect, base, window) are `box`; other pixel layers and smart objects are `art`.
9. A box with a Gradient Overlay or a gradient fill also gets the `fill` render (effects hidden: the mask) and the `frame` render (fill 0 %, gradient hidden) or, for a gradient fill layer with a style, the `style` render (fill 0 %).

## Process step (pixel level, `slyce_process.py`)

Per element, top of the PSD first, clipped to its artboard (`keep` bypasses 3–7):

1. Trim to visible pixels (alpha ≥ 1). Nothing left on the artboard → `covered`.
2. Max alpha < 16 or ≤ 4 px → `empty` (stray pixels).
3. Full-artboard (≥ 90 % both ways) under a full-artboard translucent tinted fill (the dim overlay) → `mockup`.
4. At least half the artboard's area, below the artboard's **background** (the first full-artboard element with ≥ 60 % of the artboard opaque) → `behind`: reference sheets, alternative backgrounds, mockups under the real background.
5. ≥ 99.5 % of its visible pixels (alpha ≥ 8) under opaque pixels (alpha ≥ 250) of elements above, pixels off the artboard counting as covered → `covered` (8.2).
6. **fx** (not composed, title or text): fewer than 8 % of visible pixels near-opaque (alpha ≥ 230) with mean alpha < 140, or at least 45 % semi-transparent (alpha < 200) with mean alpha < 170. Measured on Charm Quest: shadows 49–81 % semi-transparent, mean ≤ 165; art ≤ 34 %, mean ≥ 183.
7. Role `art` whose silhouette (alpha at half the max) fits a box or rounded box within pixel error, or that slices losslessly to 60 % of its width or height or less (a button, a bar), is treated as box-like.

Then, by role (9.1):

- **title / text-as-image / composed**: whole. The title is capped at 512 px.
- **background**: the background group is one image; a background layer (or art/box filling 90 % of the artboard) is a tinted fill if flat, a 9-slice if it slices, **a gradient** if a vertical or horizontal linear gradient (98 % opaque, one colour per row or column) redraws it at SSIM >= 0.98 and MAE <= 3 (not exported: listed in `gradients` with Gradient2 keys simplified to within 2 per channel, and in `skipped` as `gradient`; Ali: a gradient needs a gradient component or a 32 x 32 texture, never 2048), else whole. Whole backgrounds are fitted (aspect kept, area-averaged) and padded into a `backgroundSize` square (2048).
- **art**: whole (9.1 step 2a).
- **box**:
  1. **Tinted fill** (9.10): every visible pixel one colour within 6 per channel (0.2 % stray allowed), at least half at the inside alpha. Shape from the live shape (rect, rounded rect with equal radii, ellipse; uneven radii fail) or, for pixel layers and smart objects, the silhouette fitted within pixel error (radius max error ≤ max(2 px, 5 % of r), mean ≤ 0.6 px). The shape is redrawn anti-aliased in the colour and must match the render: SSIM ≥ 0.98 and MAE ≤ 3. Result: no sprite; white box (sliced) for rectangles, white circle (sliced) for rounded rectangles, white circle (simple) for ellipses, tinted.
  2. **9-slice** (9.2): the longest run of columns, and of rows, strictly inside the image whose lines all equal the run's first line (every channel within 2; fully transparent pixels match) collapses to 4 px when it is flat fill: it covers a quarter of the axis, or both its ends meet a sharp change (> 6). Expanding back must reproduce the image within 2. Both axes → `sliced9`.
  3. **Gradient box** (9.4): the gradient maps to Gradient2 when it is linear or reflected at 90 / −90 / 0 / 180° (±1), custom stops, Normal blend; stops become keys (midpoints add a key, reverse/reflect/angle flips them, opacity < 100 % pre-blends with the fill colour, more than 8 keys merge the closest), `zoom` = scale / 100. The shape must be a rectangle or a rounded rectangle with equal radii; the frame render must 9-slice. Rebuilt (gradient in the fill mask, frame on top) it must match the render (SSIM ≥ 0.98, MAE ≤ 3). Result: `<Name>-Frame` sprite (if the layer has other effects) + Gradient2 keys in `gradients`.
  4. **3-slice** (9.3): only one axis collapses. Two SLYCE refinements (Ali, 2026-11-21):
     - **About square**: when the texture has room, the flat centre keeps up to (height - left - right) px instead of 4, so the content is about square (a button keeps a wide, obvious middle under its shine); thin bars are unchanged. Still lossless.
     - **Round both ends**: a fill bar cut straight by a mask at one end (that edge line >= 90 % opaque, the other end < 60 %) gets the rounded cap mirrored onto the cut end, with equal borders, because Unity draws fills rounded at both ends on the bar background (rule `noRoundEnds` keeps the cut).
  5. **Header split** (9.7): the smallest top band (at most half the height) below which the body has a collapsible column run of a quarter of the width; the header is whole, the body must slice.
  6. Whole.

Radial, angle and diamond gradients are not gradient boxes; FUZIO builds radial looks from soft circles (9.5), which needs the kit's soft circle sprite, so SLYCE exports them through the steps above and lists a warning.

**Duplicates** (10.3), after all elements: whole sprites of art, box and composed elements without parts, largest alpha mass first. Pairs are shortlisted by a radial shape profile (8 rings, distance ≤ 0.35); copies of one smart object (same placed id) are always compared. Scale = √(mass ratio) (a copy may not be more than 2 % larger). Exact: identical pixels in one of 8 rotations/mirrors (within 2 per channel, 0.1 % may differ). Otherwise the angle from both principal axes (or every 10° for round shapes), mirrored too, is searched on 48 px copies, refined ±3° in 0.5° steps and ±2 % scale, then drawn at full size and compared (SSIM); a grey source may take a tint. SSIM ≥ 0.985 → reused; 0.96–0.985 → reused and flagged; below → its own sprite. SLYCE also merges sliced sprites with identical collapsed pixels and borders, and a whole box that a sliced sprite draws exactly at its size (the same pill at another width, within 8 per channel). Different drawings of the same object (Charm Quest's two bells: best overlay SSIM 0.72) are not duplicates; the rule `reuse` makes one reuse the other.

Soft sprites (at least 45 % of their pixels semi-transparent: shadows, glows) round **down** instead (2^floor(log2(side))): they tolerate blur and stay no bigger than their objects. Headroom sprites size from their hi-res contents render (scale in the manifest can exceed 1).

**Sizing** (9.8): `size = 2^round(log2(longest side))`, clamped to `minSize..maxSize` (32–1024). Within ±5 % of the rounding boundary (√2 × 2ⁿ) it rounds up for text and strokes ≤ 2 px. When the size rounds up the art is padded, centred, never upscaled; when it rounds down (or is over the cap) it is downscaled (area average, premultiplied). Sliced sprites round up instead and are never resampled; their borders include the padding. The title is capped at 512 px. Parts use the whole's scale. Backgrounds go into the 2048 square. Over `maxSize` and not sliceable → flagged.

**Parts** (14.3): drawn together at their PSD positions they must match the whole (SSIM ≥ 0.70); then only the parts are exported (the whole sprite is not: an object exported in parts ships as its parts, Ali); otherwise they are dropped and the art stays one sprite.

Files are written only when their pixels changed (`status` new / changed / same).

## Render cache

Each render's signature is the SHA-1 of its layer ids, variant, hidden layers and, for every layer involved (and everything inside groups), its properties from `tree.json` plus a hash of its 256 px thumbnail's pixels. Unchanged signatures are not rendered again; `--full` ignores the cache. Cache files not used by a kept run are deleted.
