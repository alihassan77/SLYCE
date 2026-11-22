"""SLYCE plan step: turns tree.json (every layer of the PSD) plus the rules file into plan.json.

The plan lists the elements to render (one per visible UI piece or composed art), their role (box, art,
composed, title, text), the renders each needs, their parts, and every layer that is skipped with a reason.
The process step then follows FUZIO's decision order (spec 9.1) on the pixels.
"""
import fnmatch
import re

from slyce_rules import full_path

LIGHT_BLENDS = {"screen", "linearDodge", "colorDodge", "lighten", "lighterColor", "add"}
NORMAL_BLENDS = {"normal", "passThrough"}
ART_HINTS = ["chest", "character", "char", "logo", "gift", "coin", "gem", "icon", "art", "illustration", "mascot",
             "box", "crate", "trophy", "reward", "item", "prop"]
RENDERABLE = {"pixel", "shape", "smartObject", "fill", "video", "3d"}
# Name words of UI pieces that are box-like (sliced, flat or gradient boxes) rather than art.
BOX_HINTS = ["btn", "button", "bar", "frame", "panel", "box", "bg", "background", "popup", "banner", "ribbon", "tab",
             "slot", "fill", "plate", "board", "card", "header", "pill", "rect", "rectangle", "rounded", "base", "window"]
# Name words of overlays added on top of a background (shine, sparkles, particles): pulled out of the
# baked background, because they feel like effects on it rather than part of the painting.
OVERLAY_NAMES = ["shine", "shiny", "sparkle", "sparkles", "glitter", "twinkle", "particle", "particles", "flare",
                 "fx", "vfx", "confetti", "ray", "rays", "bloom", "lensflare", "highlight", "glint", "star", "stars"]
BG_NAMES = ["bg", "background", "backdrop", "backgrounds", "environment", "scene"]
# FUZIO 10.2: Role-Variant / Role-Part, letters and digits joined by single hyphens.
NAME_RULE = re.compile(r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")
# Styles TMP cannot draw: a main title with one of these stays an image (FUZIO 12.3).
ILLUSTRATIVE = {"bevelEmboss", "innerShadow", "innerGlow", "outerGlow", "satin", "patternOverlay"}


def clean_name(name, fallback):
    name = re.sub(r"\s+copy(\s*\d+)?\s*$", "", name, flags=re.I)
    name = re.sub(r"\.(png|psd|psb|jpe?g)$", "", name, flags=re.I)
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    if not words:
        return fallback
    out = []
    for w in words:
        out.append(w if any(c.isupper() for c in w[1:]) else w[0].upper() + w[1:])
    return "-".join(out)


def _words(name):
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", name)) if w]


def has_fx_name(layer, rules):
    fx = set(w.lower() for w in rules.get("fxNames") or [])
    return any(w in fx for w in _words(layer["name"]))


def _area(b):
    return max(0, b["width"]) * max(0, b["height"])


def _union(bounds):
    x0 = min(b["x"] for b in bounds)
    y0 = min(b["y"] for b in bounds)
    x1 = max(b["x"] + b["width"] for b in bounds)
    y1 = max(b["y"] + b["height"] for b in bounds)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def looks_composed(name, parts, doc):
    """Parts that overlap a lot (a chest: body, lid, lock) or an art-like name; never a whole screen."""
    if len(parts) < 2 or len(parts) > 16:
        return False
    if any(p.get("kind") == "text" for p in parts):
        return False
    bounds = [p["bounds"] for p in parts if _area(p["bounds"]) > 0]
    if len(bounds) < 2:
        return False
    union = _union(bounds)
    if _area(union) > 0.6 * doc["width"] * doc["height"]:
        return False
    if any(w in ART_HINTS for w in _words(name)):
        return True
    # Wide pieces (buttons, bars, banners) are UI, not art to animate.
    aspect = max(union["width"], union["height"]) / float(max(1, min(union["width"], union["height"])))
    overlap = sum(_area(b) for b in bounds) / float(max(1, _area(union)))
    return overlap >= 1.25 and aspect <= 2.2


def _gradient_info(layer):
    for e in layer.get("effects") or []:
        if e["type"] == "gradientOverlay":
            return e.get("gradient")
    adj = layer.get("adjustment") or {}
    return adj.get("gradient")


def _effect_summary(layer):
    out = []
    for e in layer.get("effects") or []:
        p = e.get("params") or {}
        item = {"type": e["type"]}
        if "color" in p and isinstance(p["color"], str):
            item["color"] = p["color"]
        if isinstance(p.get("size"), dict):
            item["size"] = p["size"].get("value")
        if isinstance(p.get("opacity"), dict):
            item["opacity"] = p["opacity"].get("value")
        if e.get("gradient"):
            item["gradient"] = e["gradient"]
        out.append(item)
    return out


class Planner(object):
    def __init__(self, tree, rules):
        self.tree = tree
        self.rules = rules
        self.doc = tree["document"]
        self.layers = tree["layers"]
        self.by_id = dict((l["id"], l) for l in self.layers)
        self.children = {}
        for l in self.layers:
            self.children.setdefault(l["parentId"], []).append(l)
        self.elements = []
        self.skipped = []
        self.texts = []
        self.warnings = []
        self.names = {}
        self.artboards = []
        for l in self.layers:
            if l["parentId"] is None and l["kind"] == "group" and l["visible"]:
                rect = l.get("artboard")
                if rect is None and "artboard" not in l and re.match(r"^artboard\b", l["name"], re.I):
                    rect = l.get("boundsWithEffects")   # runs analysed before SLYCE read artboards
                if rect and rect["width"] > 0 and rect["height"] > 0:
                    self.artboards.append({"layerId": l["id"], "name": l["name"], "rect": rect})
        self.artboard_ids = dict((a["layerId"], i) for i, a in enumerate(self.artboards))
        self.title_id = self.pick_title()
        self.showing = False
        self.art_title_done = False
        self.doc_name = None

    # ---------- helpers ----------

    def unique(self, name):
        n = self.names.get(name.lower(), 0) + 1
        self.names[name.lower()] = n
        return name if n == 1 else "%s-%d" % (name, n)

    def skip(self, layer, category, reason, info=None):
        item = {"layerId": layer["id"], "name": layer["name"], "path": full_path(layer), "kind": layer["kind"],
                "category": category, "reason": reason, "bounds": layer.get("boundsWithEffects")}
        if info:
            item["info"] = info
        self.skipped.append(item)

    def descendants(self, layer_id):
        out = []
        for c in self.children.get(layer_id, []):
            out.append(c)
            if c["kind"] == "group":
                out.extend(self.descendants(c["id"]))
        return out

    def artboard_of(self, layer):
        top = layer
        while top["parentId"] is not None:
            top = self.by_id[top["parentId"]]
        return self.artboard_ids.get(top["id"])

    def canvas(self, layer):
        i = self.artboard_of(layer)
        if i is not None:
            return self.artboards[i]["rect"]
        return {"x": 0, "y": 0, "width": self.doc["width"], "height": self.doc["height"]}

    def pick_title(self):
        """The main title (FUZIO 12.3): rule `title`, else the largest visible text layer with a style TMP
        cannot draw (bevel, inner shadow, glow, satin, pattern) or a warp. It is exported as an image of at
        most 512 px; every other text is skipped and listed for TextMeshPro."""
        texts = [l for l in self.layers if l["kind"] == "text" and l["visible"]]
        for l in texts:
            if self.rules.matches("title", l):
                return l["id"]
        best, best_size = None, 0
        for l in texts:
            t = l.get("text") or {}
            styled = any(e["type"] in ILLUSTRATIVE for e in l.get("effects") or []) or t.get("warp")
            if not styled:
                continue
            size = max([r.get("sizePx") or 0 for r in t.get("runs") or []] or [0])
            if size > best_size:
                best, best_size = l["id"], size
        return best

    def is_art_title(self, layer):
        """The main title made of art, not text (12.3: 'not a text layer at all'): only when no text title was
        found; named like the PSD or with a title/logo word, in the top third of its artboard, 35 % of its width."""
        if self.title_id is not None or self.art_title_done or self.rules.matches("art", layer):
            return False
        cv = self.canvas(layer)
        b = layer.get("boundsWithEffects") or {}
        if not b or b["width"] < 0.35 * cv["width"] or b["y"] + b["height"] / 2.0 > cv["y"] + cv["height"] / 3.0:
            return False
        name = re.sub(r"[^a-z0-9]", "", layer["name"].lower())
        doc = re.sub(r"[^a-z0-9]", "", (self.doc_name or "").lower())
        if not (any(w in ("title", "logo", "heading", "headline") for w in _words(layer["name"])) or (name and len(name) >= 4 and name in doc)):
            return False
        self.art_title_done = True
        return True

    def is_background_group(self, layer):
        """The background as a whole: rule `background` on a group, or a group named bg/background/backdrop that
        covers at least half of its artboard."""
        if self.rules.matches("background", layer):
            return True
        if not any(w in BG_NAMES for w in _words(layer["name"])):
            return False
        cv = self.canvas(layer)
        b = layer.get("boundsWithEffects") or {"width": 0, "height": 0}
        return _area(b) >= 0.5 * cv["width"] * cv["height"]

    def is_overlay(self, d):
        """An overlay added on the background (shine, sparkles, particles, light blend modes), not the painting."""
        if self.rules.matches("bake", d):
            return False
        if self.rules.matches("animate", d):
            return True
        return d["kind"] != "group" and (any(w in OVERLAY_NAMES for w in _words(d["name"])) or d["blendMode"] in LIGHT_BLENDS)

    def background_group(self, layer, pending):
        """Renders the background group as ONE image with everything static in it (room, floor glows, shadows,
        props). Pulled out and handled as their own elements: overlays (shine, sparkles, particles, light blend
        modes, rule `animate`) and text. Left out: rule `ignore`, and large images below the first layer that
        fills the artboard (references and alternative backgrounds behind the real one)."""
        rules = self.rules
        cv = self.canvas(layer)
        hide, overlays = [], []
        hidden_roots = set()

        def under_hidden(d):
            q = d
            while q["parentId"] is not None and q["parentId"] != layer["id"]:
                if q["parentId"] in hidden_roots:
                    return True
                q = self.by_id[q["parentId"]]
            return False

        base = None
        for d in self.descendants(layer["id"]):
            if not d["visible"] or under_hidden(d):
                continue
            b = d.get("boundsWithEffects") or {"width": 0, "height": 0, "x": 0, "y": 0}
            if rules.matches("ignore", d):
                hide.append(d["id"]); hidden_roots.add(d["id"])
                self.skip(d, "rule", "ignored by slyce_rules.json (left out of the background %s)" % layer["name"])
            elif d["kind"] == "text" and d["id"] != self.title_id:
                hide.append(d["id"])
                self.text(d)
            elif self.is_overlay(d) or d["id"] == self.title_id:
                hide.append(d["id"]); hidden_roots.add(d["id"])
                overlays.append(d)
            elif d["kind"] in ("pixel", "smartObject", "fill", "shape") and not rules.matches("keep", d):
                if base is None:
                    if b["width"] >= 0.9 * cv["width"] and b["height"] >= 0.9 * cv["height"]:
                        base = d
                elif _area(b) >= 0.5 * cv["width"] * cv["height"]:
                    hide.append(d["id"])
                    self.skip(d, "behind", "large image below %s in the background (a reference or an alternative background)" % base["name"])
        # Overlays sit on top of the background, so they are listed first (top of the PSD first).
        if overlays:
            self.walk(None, overlays)
        self.add_element(layer, "background", merge=pending, hide=hide, notes=[
            "background as a whole: one image with everything static in it (%d overlay%s pulled out)" % (len(overlays), "" if len(overlays) == 1 else "s")])
        if overlays:
            self.elements[-1]["overlays"] = [o["name"] for o in overlays]

    def element_name(self, layer):
        name = self.rules.rename(layer) or clean_name(layer["name"], "Layer%d" % layer["id"])
        if not NAME_RULE.match(name):
            self.warnings.append("name '%s' (%s) does not follow Role-Variant (FUZIO 10.2)" % (name, full_path(layer)))
        return self.unique(name)

    # ---------- elements ----------

    def add_element(self, layer, role, variant="full", merge=None, hide=None, parts=None, notes=None, gradient=None):
        merge = merge or []
        rules = self.rules
        if rules.matches("box", layer) and role in ("art", "box"):
            role = "box"
        elif rules.matches("art", layer) and role in ("art", "box"):
            role = "art"
        if rules.matches("background", layer):
            role = "background"
        el = {
            "key": str(layer["id"]),
            "name": self.element_name(layer),
            "layerId": layer["id"],
            "path": full_path(layer),
            "kind": layer["kind"],
            "role": role,
            "variant": "merged" if (merge and variant == "full") else variant,
            "layerIds": [layer["id"]] + [m["id"] for m in merge],
            "mergeWith": [{"layerId": m["id"], "name": m["name"], "why": m["why"]} for m in merge],
            "hide": hide or [],
            "bounds": layer.get("boundsWithEffects"),
            "keep": rules.matches("keep", layer),
            "noSlice": rules.matches("noSlice", layer) or (rules.matches("whole", layer) and role != "box") or role in ("composed", "text", "title"),
            "background": rules.matches("background", layer),
            "parts": [],
            "notes": notes or [],
            "artboard": self.artboard_of(layer),
            "shape": layer.get("shape"),
            "shapeBounds": layer.get("bounds"),
            "effectTypes": [e["type"] for e in layer.get("effects") or []],
            "thinStroke": any(e["type"] == "stroke" and isinstance((e.get("params") or {}).get("size"), dict)
                              and (e["params"]["size"].get("value") or 99) <= 2 for e in layer.get("effects") or []),
            "fillColor": (layer.get("adjustment") or {}).get("color"),
            "extras": [],
            "reuse": rules.reuse(layer),
            "noReuse": rules.matches("noReuse", layer),
            "noRoundEnds": rules.matches("noRoundEnds", layer),
            "symmetric": rules.symmetric(layer),
            "states": [],
        }
        if gradient:
            el["gradient"] = gradient
        fam = self.rules.family(layer, el["name"])
        if fam:
            el["family"] = fam
        cfam = self.rules.canvas_family(layer, el["name"])
        if cfam:
            el["canvasFamily"] = cfam
        self.smart_extras(el, layer)
        if self.showing:
            # Show the layer, its hidden ancestors and everything hidden inside it for the render; never skip it.
            ids = [layer["id"]] + [d["id"] for d in self.descendants(layer["id"])]
            q = layer
            while q["parentId"] is not None:
                q = self.by_id[q["parentId"]]
                ids.append(q["id"])
            el["show"] = [i for i in ids if not self.by_id[i]["visible"]]
            el["keep"] = True
            el["notes"].append("hidden in the PSD: exported as an alternative state (rule showHidden)")
        # Gradient box candidates (FUZIO 9.4) also need the frame (or style) render and the fill render (mask).
        if gradient and not merge and role == "box":
            if "gradientOverlay" in el["effectTypes"] and len(el["effectTypes"]) > 1:
                el["extras"].append("frame")
            elif layer["kind"] == "fill" and el["effectTypes"]:
                el["extras"].append("style")
            el["extras"].append("fill")
        so = layer.get("smartObject")
        if so and so.get("placedId") and not merge and not hide:
            t = so.get("transform") or {}
            c4 = t.get("corners") or [0] * 8
            el["smartObject"] = {"placedId": so["placedId"], "scaleX": t.get("scaleX"), "scaleY": t.get("scaleY"),
                                 "centre": [(c4[0] + c4[2] + c4[4] + c4[6]) / 4.0, (c4[1] + c4[3] + c4[5] + c4[7]) / 4.0],
                                 "rotation": t.get("rotation"), "flipped": t.get("flipped"),
                                 "unrotatedWidth": t.get("unrotatedWidth"), "unrotatedHeight": t.get("unrotatedHeight")}
        used = set()
        for i, p in enumerate(parts or []):
            if not self.rules.matches("keep", {"id": p.get("id", 0), "name": p["name"], "path": full_path(layer)}) and                     has_fx_name({"name": p["name"]}, self.rules):
                el["notes"].append("part %s left out (glow/light by name; it stays in the whole image)" % p["name"])
                continue
            pname = "%s-%s" % (el["name"], clean_name(p["name"], "Part%d" % (i + 1)))
            generated = pname
            pname = (self.rules.get("rename") or {}).get(pname, pname)   # rename reaches parts by their generated name
            base, n = pname, 2
            while pname.lower() in used:
                pname = "%s-%d" % (base, n)
                n += 1
            used.add(pname.lower())
            part = {"key": "%d.p%d" % (layer["id"], i), "name": pname, "partName": p["name"], "bounds": p["bounds"],
                    "renamed": pname != generated}
            if p.get("contentIds"):
                corners = (layer.get("smartObject") or {}).get("transform", {}).get("corners")
                part["smartObject"] = {"layerId": layer["id"], "contentIds": p["contentIds"], "corners": corners}
            else:
                part["layerIds"] = p["layerIds"]
            el["parts"].append(part)
        b = el["bounds"] or {"width": 0, "height": 0}
        if len(el["parts"]) < 2 or max(b["width"], b["height"]) < 64:
            el["parts"] = []
        self.elements.append(el)
        return el

    def smart_extras(self, el, layer):
        """Headroom (a sharper render from the smart object's contents) and states hidden inside it."""
        so = layer.get("smartObject") or {}
        t = so.get("transform") or {}
        p = t.get("corners")
        wants = self.rules.matches("headroom", layer) or bool(self.rules.states(layer)) or bool(el.get("family"))
        if layer["kind"] != "smartObject" or not p:
            return
        why = None
        if so.get("nonAffine"):
            why = "the smart object is warped or in perspective"
        elif t.get("flipped"):
            why = "the smart object is mirrored"
        elif (so.get("source") or {}).get("linked"):
            why = "the smart object is linked to an outside file"
        if why:
            if wants:
                el["notes"].append("headroom/states skipped: %s; placed size kept" % why)
                self.warnings.append("%s: headroom/states skipped (%s)" % (el["name"], why))
            return
        placed_w = t.get("unrotatedWidth") or (p[2] - p[0])
        native = (t.get("sourceWidth") or placed_w) / float(placed_w)
        rotation = t.get("rotation") or 0
        upright = abs(rotation) > 0.5 and not self.rules.matches("keepRotation", layer)
        styled_outside = upright and bool(layer.get("effects") or el.get("mergeWith"))
        if styled_outside:
            # The look comes partly from outside the contents (a layer style, tint layers merged on top), so the
            # contents alone would lose it: render the element as placed, but with the smart object (and any
            # pixel layers) turned back around its centre first, so it comes out straight with its whole look.
            upright = False
            centre = [(p[0] + p[2] + p[4] + p[6]) / 4.0, (p[1] + p[3] + p[5] + p[7]) / 4.0]
            el["uprightRender"] = {"angle": -rotation, "cx": centre[0], "cy": centre[1]}
            el["upright"] = {"rotation": round(rotation, 2), "unrotatedWidth": t.get("unrotatedWidth"),
                             "unrotatedHeight": t.get("unrotatedHeight"), "centre": centre}
            el["notes"].append("rotated %.1f deg, styled or tinted outside its smart object: rendered turned back with its look, rotation in the manifest" % rotation)
        # Rotated smart objects are exported straight from their contents; Unity applies the rotation.
        geo = {"layerId": layer["id"], "corners": p, "rotation": 0 if upright else rotation,
               "unrotatedWidth": t.get("unrotatedWidth"), "unrotatedHeight": t.get("unrotatedHeight")}
        if upright:
            el["upright"] = {"rotation": round(rotation, 2), "unrotatedWidth": t.get("unrotatedWidth"),
                             "unrotatedHeight": t.get("unrotatedHeight"),
                             "centre": [(p[0] + p[2] + p[4] + p[6]) / 4.0, (p[1] + p[3] + p[5] + p[7]) / 4.0]}
            el["notes"].append("rotated %.1f deg in the PSD: exported upright, the rotation is in the manifest for Unity" % rotation)
        scale = 1.0
        if el.get("family"):
            # A sameSize family member: render from the contents at the source's resolution (up to 4x), so it can
            # be sized to the family's biggest slot in the process step. Headroom (if asked) sets that slot's size.
            if self.rules.matches("headroom", layer) and not layer.get("effects"):
                el["headroomScale"] = round(min(float(self.rules.get("headroomScale") or 2), native), 3)
            if not layer.get("effects") and native > 1.05:
                el["hires"] = dict(geo, show=[], hide=[], scale=round(min(native, 4.0), 3))
                el["notes"].append("family %s: rendered from the smart object's contents at %.2fx placed" % (el["family"], el["hires"]["scale"]))
        elif self.rules.matches("headroom", layer):
            if layer.get("effects"):
                el["notes"].append("headroom asked, but the smart object has layer styles that a contents render would lose: placed size kept")
            else:
                scale = round(min(float(self.rules.get("headroomScale") or 2), native), 3)
                if scale > 1.05:
                    el["hires"] = dict(geo, show=[], hide=[], scale=scale)
                    el["headroomScale"] = scale
                    el["notes"].append("headroom: rendered from the smart object's contents at %.2fx its placed size" % scale)
                else:
                    scale = 1.0
                    el["notes"].append("headroom asked, but the smart object's source is no bigger than placed: placed size kept")
        if upright and "hires" not in el:
            el["hires"] = dict(geo, show=[], hide=[], scale=1.0)
        states = self.rules.states(layer)
        contents = so.get("contents") or []

        def state_scale(spec):
            # "headroom": true on a state gives it its own headroom (a lit glow that pulses bigger), even when
            # the element itself has none; false keeps it at the placed size; unset follows the element.
            h = spec.get("headroom")
            if h is True:
                return round(max(1.0, min(float(self.rules.get("headroomScale") or 2), native)), 3)
            if h is False:
                return 1.0
            return scale
        for state, spec in (states or {}).items():
            def ids(names):
                out = []
                for n in names or []:
                    out += [c["id"] for c in contents if fnmatch.fnmatchcase(c["name"].lower(), n.lower())]
                return out
            show, hide = ids(spec.get("show")), ids(spec.get("hide"))
            if (spec.get("show") and not show) or (spec.get("hide") and not hide):
                el["notes"].append("state %s: some layer names were not found inside the smart object" % state)
            el["states"].append({"key": "%s:state:%s" % (el["key"], state), "state": state,
                                 "name": spec.get("name") or "%s-%s" % (el["name"], state),
                                 "soRender": dict(geo, show=show, hide=hide, scale=state_scale(spec))})

    def composed_parts(self, layer):
        comp = layer.get("composition") or {}
        parts = comp.get("parts") or []
        if comp.get("type") not in ("group", "smartObject"):
            return None
        return parts

    # ---------- walk ----------

    def walk(self, parent_id, layers=None):
        pending = []
        first_new = len(self.elements)
        for layer in (layers if layers is not None else self.children.get(parent_id, [])):
            rules = self.rules
            if not layer["visible"] and not self.showing and rules.matches("showHidden", layer):
                # An alternative state (locked, grey, pressed): walked as if visible, rendered with it shown.
                self.showing = True
                try:
                    if layer["kind"] == "group":
                        self.group(layer, [])
                    else:
                        self.walk(None, [dict(layer, visible=True)])
                finally:
                    self.showing = False
                continue
            if not layer["visible"] and not self.showing:
                self.skip(layer, "hidden", "hidden layer" + (" (group, with everything inside)" if layer["kind"] == "group" else ""))
                continue
            if rules.matches("ignore", layer):
                self.skip(layer, "rule", "ignored by slyce_rules.json")
                continue
            keep = rules.matches("keep", layer)
            if layer["kind"] == "group":
                self.group(layer, pending)
                pending = []
                continue
            b = layer.get("boundsWithEffects") or {}
            if layer["kind"] != "adjustment" and (b.get("width", 0) <= 0 or b.get("height", 0) <= 0):
                self.skip(layer, "empty", "no pixels")
                continue
            if layer["opacity"] == 0 and not keep:
                self.skip(layer, "empty", "opacity 0%")
                continue
            if layer["kind"] == "text":
                if layer["id"] == self.title_id:
                    self.add_element(layer, "title", merge=pending, notes=[
                        "main title with a style TextMeshPro cannot draw: an image of at most 512 px (FUZIO 12.3)"])
                    pending = []
                elif rules.matches("textAsImage", layer):
                    self.add_element(layer, "text", merge=pending, notes=["text exported as an image (rule textAsImage)"])
                    pending = []
                else:
                    self.text(layer)
                continue
            if layer["kind"] == "adjustment":
                pending.append(dict(layer, why="adjustment layer"))
                continue
            if layer.get("clipped"):
                pending.append(dict(layer, why="clipped to the layer below"))
                continue
            if not keep and has_fx_name(layer, rules):
                self.skip(layer, "fx", "glow/light/particle layer (by name)")
                continue
            if not keep and layer["blendMode"] in LIGHT_BLENDS:
                self.skip(layer, "fx", "light blend mode %s (glow/light)" % layer["blendMode"])
                continue
            if layer["blendMode"] not in NORMAL_BLENDS:
                pending.append(dict(layer, why="blend mode %s" % layer["blendMode"]))
                continue
            if layer["kind"] not in RENDERABLE:
                self.skip(layer, "other", "unsupported layer kind %s" % layer["kind"])
                continue
            self.leaf(layer, pending, keep)
            pending = []
        if pending:
            if len(self.elements) > first_new:
                el = self.elements[first_new]
                for m in pending:
                    el["mergeWith"].append({"layerId": m["id"], "name": m["name"], "why": m["why"] + " (no layer below it; merged with the first element)"})
                    el["layerIds"].append(m["id"])
                el["variant"] = "merged" if el["variant"] == "full" else el["variant"]
            else:
                for m in pending:
                    self.skip(m, "orphan", m["why"] + " with nothing below it to affect")

    def text(self, layer):
        t = layer.get("text") or {}
        self.texts.append({
            "layerId": layer["id"], "name": layer["name"], "path": full_path(layer), "string": t.get("string"),
            "runs": t.get("runs"), "alignment": t.get("alignment"), "warp": t.get("warp"),
            "bounds": layer.get("bounds"), "boundsWithEffects": layer.get("boundsWithEffects"),
            "opacity": layer.get("opacity"), "effects": _effect_summary(layer),
        })
        self.skip(layer, "text", "text layer (rebuild as TextMeshPro; details in manifest texts)")

    def leaf(self, layer, pending, keep):
        grad = _gradient_info(layer)
        words = _words(layer["name"])
        if layer["kind"] in ("shape", "fill") or any(w in BOX_HINTS for w in words):
            role = "box"
        else:
            role = "art"     # the process step treats art whose silhouette is a box or rounded box as box-like
        parts = None
        title = role == "art" and self.is_art_title(layer)
        if layer["kind"] == "smartObject" and (not title or self.rules.matches("parts", layer)) and not self.rules.matches("noParts", layer):
            cand = self.composed_parts(layer)
            b = layer.get("boundsWithEffects") or {"width": 0, "height": 0}
            if cand and (self.rules.matches("parts", layer) or
                         (max(b["width"], b["height"]) >= 64 and looks_composed(layer["name"], cand, self.canvas(layer)))):
                parts = cand
        notes = []
        if grad and role == "box":
            notes.append("gradient: tried as a gradient box (frame sprite + Gradient2 fill, FUZIO 9.4)")
        if title:
            role = "title"
            notes.append("art title (the PSD's name or a title/logo word, at the top): an image of at most 512 px (FUZIO 12.3)")
        self.add_element(layer, "composed" if parts else role, merge=pending, parts=parts,
                         gradient=grad if role == "box" else None, notes=notes)

    def group(self, layer, pending):
        rules = self.rules
        if rules.matches("split", layer) or layer["id"] in self.artboard_ids:
            self.walk(layer["id"])
            return
        if self.is_background_group(layer):
            self.background_group(layer, pending)
            return
        desc = self.descendants(layer["id"])
        visible = [d for d in desc if d["visible"] or self.showing]
        has_text = any(d["kind"] == "text" for d in visible)
        whole = rules.matches("whole", layer)
        cand = self.composed_parts(layer) or []
        styled = bool(layer.get("effects"))
        pieces = [d for d in visible if d["kind"] in RENDERABLE]
        canvas = self.canvas(layer)
        many = not has_text and len(pieces) >= 6 and _area(layer.get("boundsWithEffects") or {"width": 0, "height": 0}) < 0.6 * canvas["width"] * canvas["height"]
        force_parts = rules.matches("parts", layer)
        if whole or styled or many or force_parts or (not has_text and looks_composed(layer["name"], cand, canvas)):
            bake_text = styled or whole
            hide = []
            for d in visible:
                if rules.matches("ignore", d):
                    hide.append(d["id"])
                    self.skip(d, "rule", "ignored by slyce_rules.json (hidden inside %s)" % layer["name"])
                elif not rules.matches("keep", d) and d["kind"] != "group" and (has_fx_name(d, rules) or d["blendMode"] in LIGHT_BLENDS):
                    hide.append(d["id"])
                    self.skip(d, "fx", "glow/light layer inside composed art %s (hidden in its render)" % layer["name"])
                elif d["kind"] == "text" and not rules.matches("textAsImage", d) and d["id"] != self.title_id:
                    if bake_text:
                        self.skip(d, "text", "text baked into %s (%s): exported as part of that image" % (
                            layer["name"], "group has a layer style" if styled else "rule whole"))
                    else:
                        hide.append(d["id"])
                        self.text(d)
            parts = None
            if not rules.matches("noParts", layer) and (force_parts or looks_composed(layer["name"], cand, canvas)):
                parts = [p for p in cand if p["id"] not in hide]
                if len(parts) < 2:
                    parts = None
            why = "rule whole" if whole else ("group has a layer style" if styled else ("made of %d pieces" % len(pieces) if many else "composed art"))
            notes = ["group exported as one image (%s)%s" % (why, " with its parts" if parts else "")]
            role = "composed"
            if rules.matches("box", layer) and not parts:
                role = "box"   # a styled group that is really a UI box (a highlighted card): sliced, not whole
            if any(d["id"] == self.title_id for d in visible):
                role, parts = "title", None   # the main title's group: an image of at most 512 px (12.3)
                notes.append("holds the main title text: an image of at most 512 px (FUZIO 12.3)")
            self.add_element(layer, role, merge=pending, hide=hide, parts=parts, notes=notes)
            return
        if pending:
            self.warnings.append("%s: dependent layers above group %s were merged with its first element" % (
                ", ".join(m["name"] for m in pending), layer["name"]))
        first = len(self.elements)
        self.walk(layer["id"])
        if pending and len(self.elements) > first:
            el = self.elements[first]
            for m in pending:
                el["mergeWith"].append({"layerId": m["id"], "name": m["name"], "why": m["why"]})
                el["layerIds"].append(m["id"])
            if el["variant"] == "full":
                el["variant"] = "merged"


def make_plan(tree, rules, run_id, root_name):
    p = Planner(tree, rules)
    p.doc_name = root_name
    p.walk(None)
    return {
        "schema": 2,
        "run": run_id,
        "rootName": root_name,
        "document": tree["document"],
        "artboards": p.artboards,
        "title": p.title_id,
        "elements": p.elements,
        "texts": p.texts,
        "skipped": p.skipped,
        "warnings": p.warnings,
    }
