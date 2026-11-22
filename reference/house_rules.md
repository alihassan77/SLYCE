# SLYCE house rules (Ali)

The one place for Ali's export rules. The planner plans by them, the reviewer checks against them, and new feedback is added **here**, under the topic it belongs to, not in the agent files. Each rule says why, so the agents can reason about cases the rule does not name. Ali will not define every rule: for every element, ask **why the artist built it this way and how a Unity developer will use it**, then plan for that.

Sources: CQ = Charm Quest test, DL = Daily Login test (both 2026-11).

## 1. Reading a mock PSD

Artists build these PSDs as mocks: during production every state is drawn, and at the final stage only the active state stays visible. A PSD holds many evolutions; work out the current design and its related states before exporting anything hidden.

- **Hidden states are part of the asset.** Look through hidden groups and layers for other states of visible items: off / locked / grey / socket / disabled / pressed / selected / empty / filled. Export them with `showHidden` (plus `split` so each state is its own sprite) and name them `<Item>-<State>` (`Charm-Gift-Grey`, `Btn-Play-Pressed`). (CQ)
- **Which hidden layers to export**: ignore older hidden versions (previous titles, cards, panels, borrowed icons). Export a hidden layer only when it is a **locked or disabled state** of a visible item, or clearly **linked to an active state** (a highlight, badge or arrow that only shows on the active item). Hidden alternatives that are just other design ideas are not exported unless Ali asks. (DL)
- **Leftovers are judged by the current active state.** An inactive badge next to the today card (`Badge-Today`) was not exported: the active today state already looks complete (glow behind, arrow) in both artboards, so the badge is most likely from an older version. (DL)
- **Expect every state for every item.** When some items have a state that others lack (10 charms have grey and socket versions, 4 do not), list the missing ones in `plan_notes.md` as "missing in the PSD"; do not invent them. (CQ)
- **States drawn from older art are fine** (silhouettes may not match the new art exactly); mention it, do not block on it. (CQ)
- **Alternative design options**: when several **visible** versions of one thing exist (two lock styles) and you cannot tell which is final, export all of them with clear names (`Icon-Lock`, `CharmsLocked-Slot`) and say in `plan_notes.md` that the artist picks. Ignore only what is clearly reference or junk. (CQ)
- **Paint-overs made for the mock** are not assets: strokes or wires painted on top only to make the mock look finished (wire bits painted over charms so they look entangled in the garland, `Tree-Garland`) are ignored. Pieces placed **inside** a group (wire segments inside each tree tier) belong to that piece and stay in it. (CQ)
- **One object drawn in pieces is still one object.** The ribbon header and its cap together make one ribbon: artists often drag in an older ribbon and extend it by duplicating a piece, so one end may be an editable shape and the other a raster copy. Export **one** `Ribbon-Header` (3-sliced if its middle is uniform), not a body plus a cap. When the placed ribbon looks the same at both ends, export it **symmetrical**, both ends built from the cap with the **clearest pixels** (sharpest edges; mirrored), so both ends of the export are the same, because Unity line-slices it (rule `symmetric`; `auto` picks the sharper cap). The visual result decides, not how it was built. (DL)
- **Open smart objects and groups and ask why they are layered.** `tree.json` lists every layer inside each smart object, hidden ones too (`smartObject.contents`, `visible` / `ownVisible`), and each group's children. Separate tiers, limbs or lids mean animation (see 6); hidden layers inside a smart object are usually states (see 6). (CQ)

## 2. Names

- FUZIO 10.2 `Role-Variant`, letters and digits joined by single hyphens, unique. Shared sprites carry the plain name (`Light-Tree`), never an instance number. (CQ)
- **Name by function in the game, not by looks.** The round lock icon that is the universal socket behind every locked charm is `CharmsLocked-Slot`, not `Icon-LockRound`. Prefer what a developer would search for: what it is for (Slot, Socket, Progress, Reward, Unlock), then which one. (CQ)
- **Name by feature when a sprite belongs to one.** On Daily Login the numbered circles above the weekly reward slots are `Badge-WeeklyReward`, the circles on the daily-streak bar `Bar-Node-DailyStreak`, the weekly reward's panel `Panel-WeeklyReward`; never a bare `Badge-Number`, `Bar-Node`, `Panel-Reward`. Check in the PSD which feature each piece really sits on before naming. The test: any developer or a later team finds where a sprite belongs from its name alone. (DL)

## 3. Background

- A **painted** static backdrop (room, floor, props, garlands, shadows that never move) is ONE image fitted and padded into **2048 x 2048** (a 1870 x 2436 tablet PSD's art becomes 1572 x 2048 inside it). (CQ)
- Bake everything static into it, thin details too (garland wires: separate sprites waste texture space); pull out what will animate in Unity (string lights, bulbs, shine, sparkles) as their own shared sprites. (CQ)
- A backdrop that is **only a gradient or a plain colour box** is not exported: SLYCE detects a vertical or horizontal gradient and lists its keys in the manifest (`gradients`) for a Unity gradient component; `ignore` other plain ones and note their colours. Such a gradient needs at most a 32 x 32 texture, never a 2048 one. (DL)

## 4. Sizes

FUZIO's square powers of two, 32-1024; the title at most 512.

- **Size for future use, not only the placed size.** Art that will animate, pop or zoom bigger (the active, colourful state of a collectible, a reward, a hero item) gets **`headroom`**: rendered from its smart object's contents at up to 2x its placed size (a 128 charm at 256). Locked / grey / socket states, slots and shadows stay at their placed size. Headroom needs a smart object whose source is bigger than placed (`smartObject.transform.sourceWidth`); raster layers cannot gain pixels, so say so. (CQ)
- **Interchangeable items share one size.** Boosters, collection packs, currency piles and reward icons are placeholders the developer swaps between slots: a booster placed small today may sit in the big Day 7 card tomorrow. Export every member of the family at the size of the biggest slot any of them uses (256 on Daily Login): rule `sameSize`. Items that only ever sit in their own small slot stay at their own size (Daily Login's coins at 128). A member whose source cannot reach it is still exported at that size and **flagged** (plan notes and warnings) so the artist can supply a bigger source. (DL)
- **Reused art is exported at its largest placed size** (the tick placed at two sizes ships at 128): a small export used big blurs, a big export used small is fine. (DL)
- **Shadows** tolerate blur: never larger than their object; an object near 128 keeps its shadow at or below 128. (CQ)
- **Tiny sprites (under 64 px) and animation states go up a size, never down**, and keep their soft glow edges; a glow's edge is never cut. (CQ)
- **Parts of one object share one scale**, even when that puts a wide tier in 1024. (CQ)
- **Stroked UI shapes are not shrunk below 0.8** (a 3 px stroke would turn soft and stop matching its look-alikes): they go up a size (`Bar-Node-DailyStreak` 89 px -> 128). (DL)
- Confirmed sizes: `Title-CharmQuest` 512, `Btn-Close` / `Btn-Info` 128, `Timer-Needle` 32, `Icon-Tick` 128. (CQ, DL)

## 5. Slicing and boxes

- **Bar backgrounds** 3-slice down to their caps plus a short centre, keeping stroke and inner shadow at the caps, so Unity line-slices them to any length. (CQ, DL)
- **Fill bars** are rounded at both ends, even when the PSD masks one end straight to show a half fill (`noRoundEnds` keeps a cut). (CQ, DL)
- **3-sliced buttons** keep a centre wide enough that the sprite is about square in its texture (Btn-Play 212 x 212 in 256), never a 4 px sliver under a shine. (CQ)
- **Panels with different corner radii stay separate sprites** (Panel-Main and Panel-Bottom): one sprite scaled with pixels-per-unit would change the stroke width. (DL)
- **A frame around a gradient** (the Day 7 card) exports only its 9-sliced frame; the inside is rebuilt in Unity from a flat white circle plus a gradient component. (DL)
- **Never export a plain white circle or square**: Unity projects already have them. (DL)

## 6. Animation pieces, states and rotation

- **Separately grouped stages, tiers, limbs, lids** (a tree whose five tiers are grouped inside its smart object) mean animation (a bounce of every tier when an item hits the tree): add them to `parts`. A title smart object holding separate objects (gifts on the Daily Drop title) splits them out the same way; the lettering stays one piece. (CQ, DL)
- **Parts replace the whole**: an object exported in parts ships only as its parts. (CQ)
- **Hidden layers inside a smart object are states** (a light's lit glow): export them with `states`, and think what each state needs: a lit light casts no shadow, so the lit state drops the shadow and one lit sprite serves every copy. (CQ)
- **Export straight, rotate in Unity.** A smart object placed rotated is exported upright with `rotation` and `pivot` in the manifest; `keepRotation` only when the tilt is part of the drawing. (CQ)
- **States of one item swap cleanly**: locked, grey, socket and active versions are exported the same way (all upright), so Unity only swaps the sprite. (CQ)

## 7. Swappable families (prefab variants)

UI pieces that replace one another in one prefab, at runtime or as prefab variants, must swap without re-layout. (DL)

- All day-card **headers** (normal, today, claimed, and the Day 7 header too: it differs in size and colour in the PSD, but one canvas is the cleaner practice) share **one texture size, the same slice borders and the inner area at the same place** in the canvas.
- The day-card **backgrounds** (normal, today, claimed, dimmed, with or without shadow) do the same: every variant has the same inner body size and position; a **shadow** grows outward from that body (stacked, the shadowless one covers the shadowed one exactly except the shadow); a **stroke** grows outward too, keeping the inner area the same; a variant without shadow or stroke is padded to the same canvas.
- Set them with rule `sameCanvas` (one family for the headers, one for the backgrounds) and list each family in `plan_notes.md` (family name, members) so the reviewer can check it.

## 8. Reuse and look-alikes

- **Same art drawn twice ships once** (the link hook used twice; Unity repeats it). (DL)
- **Look-alikes from different features stay separate** when strokes or colours differ, even slightly (the day-streak badge and the weekly bar node): one sprite scaled with pixels-per-unit changes the stroke width. Name them by feature (see 2). (DL)
- **Colour variants are never one sprite** (a grey locked charm and its coloured version). (CQ)
- **Same object, different context**: keep separate sprites when the contexts really differ (a light on the tree with a shadow vs one in mid air without); merge when one sprite serves both. (CQ)
- **Shapes that always show together and never animate apart are one sprite** (two glow shapes behind the today card, `Glow-Today`). (DL)

## 9. Folders

- Every sprite in one `Sprites` folder; one subfolder per artboard only when several artboards have sprites. When the artboards are variants of one feature (two layouts of the same screen), `"artboardFolders": false` puts all sprites in one folder and shared art exports once. (CQ, DL)

## 10. Working rules

- **One cycle**: plan, one export, one review that passes. The 3-round review limit only stops an endless loop.
- **Ask, don't guess.** When something is ambiguous, or a rule here seems to contradict another rule or the PSD, any agent lists it as a question for Ali (planner: open questions in `plan_notes.md`; reviewer: "Questions for Ali"; main agent: asks the user before applying a fix) instead of picking one.
- Report times in minutes.
