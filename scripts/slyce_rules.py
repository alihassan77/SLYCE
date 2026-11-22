"""SLYCE per-PSD rules file (<output>/slyce_rules.json): what to ignore, keep, rename and how to treat layers.

Patterns match a layer's full path in the PSD ("Group/Sub/Layer", case-insensitive, * and ? wildcards)
or its id ("id:123"). A pattern without "/" also matches the layer name alone.
"""
import fnmatch
import json
import os

DEFAULT_FX_NAMES = ["glow", "shine", "shiny", "light", "lights", "ray", "rays", "sunburst", "burst", "sparkle",
                    "sparkles", "particle", "particles", "flare", "bloom", "fx", "vfx", "confetti", "twinkle", "glitter"]

DEFAULTS = {
    "_help": {
        "patterns": "Layer path 'Group/Sub/Layer' (case-insensitive, * and ? wildcards), a bare layer name, or 'id:123'.",
        "ignore": "Never export these layers (and nothing inside an ignored group).",
        "keep": "Always export these, even when SLYCE would skip them as glow/fx, flat kit shape, covered, mockup or behind the background.",
        "box": "Treat as a UI box (tinted fill, 9-slice, gradient box, 3-slice, header split) instead of art.",
        "art": "Treat as art: exported whole, never sliced or drawn as a kit shape.",
        "title": "The main title text: exported as an image of at most 512 px (otherwise SLYCE picks the largest text with bevel, inner shadow, glow, satin, pattern or warp).",
        "whole": "Export as one image and never slice it. On a group: the whole group is one image.",
        "split": "Groups only: export the layers inside separately instead of as one composed image.",
        "noParts": "Composed art (group or smart object): export the whole only, without its parts.",
        "noSlice": "Never 9-slice or 3-slice these.",
        "textAsImage": "Text layers exported as an image (for an illustrative title); all other text is skipped and listed.",
        "background": "The background as a whole: on a group, everything static inside it is baked into one image (fitted and padded into backgroundSize); on a layer, it is background art.",
        "headroom": "Art that will animate or show bigger (an active collectible, a reward): rendered from its smart object's own contents at up to headroomScale x its placed size (never beyond the source's resolution), so it stays sharp when it scales up.",
        "headroomScale": "How much bigger than placed a headroom sprite may be (default 2).",
        "states": "Smart objects whose contents hide extra states the artist will animate: {\"<layer pattern>\": {\"<State>\": {\"name\": \"Light-On\", \"show\": [\"<layer name inside>\"], \"hide\": [\"<layer name inside>\"]}}} exports each state as its own sprite.",
        "artboardFolders": "true: one Sprites subfolder per artboard when several artboards have sprites; false: one Sprites folder for all (artboards that are variants of one feature).",
        "keepRotation": "Rotated smart objects to export as rotated in the PSD (by default they are exported upright and the rotation goes into the manifest for Unity).",
        "sameCanvas": "Families of sliced UI that swap in one prefab (card headers; card backgrounds with or without shadow or stroke): {\"DayHeaders\": [\"<pattern or sprite name>\", ...]}. Every member gets the same texture size, the same slice borders and its body at the same place; shadows and strokes grow outward.",
        "symmetric": "3-sliced pieces that look the same at both ends in the PSD but were built unevenly (a ribbon whose second end is a dragged copy): {\"<pattern>\": \"left\"|\"right\"|\"top\"|\"bottom\"|\"auto\"} mirrors that end's cap onto the other, so Unity can line-slice it.",
        "sameSize": "Families of interchangeable items the developer swaps between slots (boosters, packs, rewards): {\"Boosters\": [\"<pattern or sprite name>\", ...]}. Every member is exported at the same size, the biggest slot's, from its smart object's contents when it is placed smaller; a member whose source is too small is padded and flagged.",
        "parts": "Groups or smart objects to split into their parts for animation even when SLYCE would not (e.g. a tree whose tiers are grouped separately inside its smart object).",
        "noReuse": "Never reuse another sprite automatically for these (each keeps its own sprite, e.g. soft shadows of different shapes).",
        "noRoundEnds": "A 3-sliced bar cut straight at one end stays cut (by default the rounded end is mirrored so fills are round at both ends).",
        "showHidden": "Hidden layers or groups to export anyway, as alternative states (locked, grey, pressed): rendered as if visible and never skipped as covered.",
        "bake": "Inside a background group: keep this in the baked background even though it looks like an overlay.",
        "animate": "Inside a background group: pull this out of the baked background (shine, sparkles, anything that animates) and export or skip it on its own.",
        "backgroundSize": "Square texture the whole background is fitted and padded into (default 2048).",
        "rename": "Pattern -> sprite name, e.g. {\"Group/Close btn\": \"Btn-Close\"}.",
        "reuse": "Pattern -> sprite name: this element looks the same as that sprite, so it reuses it (best rotation/mirror/scale found) instead of exporting its own.",
        "fxNames": "Name words that mark glow/particle/light layers (skipped unless kept).",
        "minSize / maxSize": "Square power-of-two sprite sizes allowed (FUZIO 9.8: 32..1024; the title is at most 512).",
        "keepRuns": "How many runs (tree, thumbnails, plan) to keep in .slyce/runs; only the latest keeps its PSD copy."
    },
    "ignore": [],
    "keep": [],
    "box": [],
    "art": [],
    "title": [],
    "whole": [],
    "split": [],
    "noParts": [],
    "noSlice": [],
    "textAsImage": [],
    "background": [],
    "bake": [],
    "animate": [],
    "showHidden": [],
    "noReuse": [],
    "headroom": [],
    "headroomScale": 2,
    "states": {},
    "parts": [],
    "sameSize": {},
    "symmetric": {},
    "sameCanvas": {},
    "keepRotation": [],
    "artboardFolders": True,
    "noRoundEnds": [],
    "rename": {},
    "reuse": {},
    "fxNames": DEFAULT_FX_NAMES,
    "minSize": 32,
    "maxSize": 1024,
    "keepRuns": 3,
    "backgroundSize": 2048,
    "schema": 2,
}


class Rules(object):
    def __init__(self, data):
        self.data = data

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def matches(self, key, layer):
        return any(pattern_matches(p, layer) for p in self.get(key) or [])

    def reuse(self, layer):
        for pattern, name in (self.get("reuse") or {}).items():
            if pattern_matches(pattern, layer):
                return name
        return None

    def states(self, layer):
        for pattern, states in (self.get("states") or {}).items():
            if pattern_matches(pattern, layer):
                return states
        return None

    def canvas_family(self, layer, name=None):
        """The sameCanvas family a layer belongs to, matched by layer pattern or by its sprite name."""
        for fam, patterns in (self.get("sameCanvas") or {}).items():
            for p in patterns:
                if pattern_matches(p, layer) or (name and fnmatch.fnmatchcase(name.lower(), p.lower())):
                    return fam
        return None

    def symmetric(self, layer):
        """Rule `symmetric`: pattern -> the end whose cap is mirrored onto the other ("left", "right", "top",
        "bottom", or "auto"). A list of patterns means "auto"."""
        v = self.get("symmetric") or {}
        if isinstance(v, list):
            v = dict((p, "auto") for p in v)
        for pattern, side in v.items():
            if pattern_matches(pattern, layer):
                return side or "auto"
        return None

    def family(self, layer, name=None):
        """The sameSize family a layer belongs to, matched by layer pattern or by its sprite name."""
        for fam, patterns in (self.get("sameSize") or {}).items():
            for p in patterns:
                if pattern_matches(p, layer) or (name and fnmatch.fnmatchcase(name.lower(), p.lower())):
                    return fam
        return None

    def rename(self, layer):
        for pattern, name in (self.get("rename") or {}).items():
            if pattern_matches(pattern, layer):
                return name
        return None


def full_path(layer):
    return (layer["path"] + "/" if layer.get("path") else "") + layer["name"]


def pattern_matches(pattern, layer):
    p = pattern.strip()
    if p.lower().startswith("id:"):
        try:
            return int(p[3:]) == layer["id"]
        except ValueError:
            return False
    target = full_path(layer).lower()
    p = p.lower()
    if fnmatch.fnmatchcase(target, p):
        return True
    return "/" not in p and fnmatch.fnmatchcase(layer["name"].lower(), p)


def rules_path(out_dir):
    return os.path.join(out_dir, "slyce_rules.json")


def load(out_dir, create=True):
    path = rules_path(out_dir)
    if not os.path.exists(path):
        if create:
            os.makedirs(out_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULTS, f, indent=2)
        return Rules(dict(DEFAULTS))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("schema", 1) < 2:
        # Rules files from SLYCE 0.1 carried the old 128..512 default sizes; FUZIO's 32..1024 replace them
        # (sizes someone set by hand are kept). The help text is refreshed too.
        if data.get("minSize") == 128:
            data["minSize"] = DEFAULTS["minSize"]
        if data.get("maxSize") == 512:
            data["maxSize"] = DEFAULTS["maxSize"]
        data["_help"] = DEFAULTS["_help"]
        for k in ("box", "art", "title"):
            data.setdefault(k, [])
        data.setdefault("reuse", {})
        data.setdefault("bake", [])
        data.setdefault("animate", [])
        data["schema"] = 2
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    merged = dict(DEFAULTS)
    merged.update(data)
    return Rules(merged)
