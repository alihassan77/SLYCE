"""SLYCE review sheet: <output>/review.html, one page with every sprite, part, duplicate and skipped layer."""
import html
import os

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e2e2de;--accent:#2f6fde;--new:#1f9d55;--chg:#c77700;--chk1:#e9e9e9;--chk2:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17181a;--card:#222326;--ink:#ececee;--mute:#9a9aa2;--line:#34353a;--accent:#7aa7ff;--new:#4cc488;--chg:#f0a640;--chk1:#2b2c30;--chk2:#35363b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1280px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.mute{color:var(--mute)}.stats{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.stat b{font-size:18px;display:block}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}.thumb{aspect-ratio:1;display:flex;align-items:center;justify-content:center;
background:repeating-conic-gradient(var(--chk1) 0 25%,var(--chk2) 0 50%) 0 0/16px 16px}.thumb img{max-width:100%;max-height:100%;image-rendering:auto}
.meta{padding:8px 10px;font-size:12px}.meta .n{font-weight:600;font-size:13px;word-break:break-all}.tag{display:inline-block;border-radius:4px;padding:0 5px;margin:2px 4px 0 0;background:var(--chk1);font-size:11px}
.tag.new{background:var(--new);color:#fff}.tag.changed{background:var(--chg);color:#fff}code{font-size:11px;word-break:break-all;color:var(--mute)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:12px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mute);font-weight:600}
.sw{display:inline-block;width:14px;height:14px;border-radius:3px;border:1px solid var(--line);vertical-align:-2px;margin-right:4px}
.comp{max-width:100%;max-height:520px;border:1px solid var(--line);border-radius:8px;background:repeating-conic-gradient(var(--chk1) 0 25%,var(--chk2) 0 50%) 0 0/16px 16px}
details summary{cursor:pointer;color:var(--accent);margin:6px 0}.tw{overflow-x:auto;max-width:100%}
"""


def _e(s):
    return html.escape("" if s is None else str(s))


def _b(b):
    return "%d,%d %dx%d" % (b["x"], b["y"], b["width"], b["height"]) if b else ""


def _swatch(c):
    if not c:
        return ""
    rgb = c[:7]
    return '<span class="sw" style="background:%s"></span>%s' % (_e(rgb), _e(c))


def write(out_dir, manifest, composite_rel):
    m = manifest
    sprites = [s for s in m["sprites"] if s["kind"] != "part"]
    parts = [s for s in m["sprites"] if s["kind"] == "part"]
    by_cat = {}
    for s in m["skipped"]:
        by_cat.setdefault(s["category"], []).append(s)
    changed = sum(1 for s in m["sprites"] if s["status"] in ("new", "changed"))
    o = []
    o.append("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>")
    o.append("<title>SLYCE review</title><style>%s</style></head><body><main>" % CSS)
    o.append("<h1>SLYCE review: %s</h1><div class=mute>%s &middot; run %s &middot; %dx%d px</div>" % (
        _e(m["root"]), _e(m["psd"]), _e(m["run"]), m["document"]["width"], m["document"]["height"]))
    stats = [("sprites", len(sprites)), ("parts", len(parts)), ("new / changed", changed), ("duplicates", len(m["duplicates"])),
             ("tinted fills", len(m["kit"])), ("gradient boxes", len(m["gradients"])), ("texts", len(m["texts"])), ("skipped", len(m["skipped"]))]
    o.append("<div class=stats>" + "".join("<div class=stat><b>%d</b><span class=mute>%s</span></div>" % (v, k) for k, v in stats) + "</div>")
    if m.get("warnings"):
        o.append("<h2>Warnings</h2><ul>" + "".join("<li>%s</li>" % _e(w) for w in m["warnings"]) + "</ul>")
    if m.get("stale"):
        o.append("<h2>Stale files</h2><p class=mute>In Sprites/ from an earlier run but not produced now. SLYCE leaves them; delete them if they are no longer needed.</p><ul>"
                 + "".join("<li><code>%s</code></li>" % _e(f) for f in m["stale"]) + "</ul>")

    def card(s):
        kind = s["kind"] + (" " + s["axis"] if s.get("axis") else "")
        tags = ['<span class="tag %s">%s</span>' % (_e(s["status"]), _e(s["status"])), '<span class=tag>%s</span>' % _e(kind),
                '<span class=tag>%dpx</span>' % s["texture"]["width"]]
        if s.get("purpose") not in (None, "main", "part"):
            tags.append("<span class=tag>%s</span>" % _e(s["purpose"]))
        if s.get("background"):
            tags.append("<span class=tag>background, import max 2048</span>")
        if s.get("scale", 1) < 0.999:
            tags.append("<span class=tag>scaled %.2f</span>" % s["scale"])
        if s.get("borders"):
            b = s["borders"]
            tags.append("<span class=tag>L%d R%d T%d B%d</span>" % (b["left"], b["right"], b["top"], b["bottom"]))
        if s.get("mergeWith"):
            tags.append("<span class=tag>+%d merged</span>" % len(s["mergeWith"]))
        if s.get("gradient"):
            tags.append("<span class=tag>frame of gradient box</span>")
        why = "\n".join(s.get("notes") or [])
        return ("<div class=card title='%s'><div class=thumb><img loading=lazy src='%s'></div><div class=meta><div class=n>%s</div>%s"
                "<div><code>%s</code></div><div class=mute>at %s &middot; id:%s</div></div></div>") % (
            _e(why), _e(s["file"]), _e(s["name"]), "".join(tags), _e(s["path"]), _b(s["placed"]), _e(s["layerId"]))

    o.append("<h2>Sprites (%d)</h2><div class=grid>%s</div>" % (len(sprites), "".join(card(s) for s in sprites)))
    if parts:
        o.append("<h2>Composed art parts (%d)</h2><p class=mute>Parts keep the whole's scale; placed bounds are in PSD pixels for reassembly.</p><div class=grid>%s</div>"
                 % (len(parts), "".join(card(s) for s in parts)))
    if m["duplicates"]:
        o.append("<h2>Duplicates</h2><p class=mute>Each copy reuses the source sprite with this transform (FUZIO 10.3); a similarity below 0.985 needs a look.</p>"
                 "<table><tr><th>Element</th><th>Uses sprite</th><th>Match</th><th>Transform</th><th>Scale</th><th>Similarity</th><th>Placed</th><th>Layer</th></tr>"
                 + "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s%s</td><td>%s</td><td><code>%s</code></td></tr>" % (
                     _e(d["name"]), _e(d["source"]), _e(d.get("match")), _e(d.get("transform")), _e(d.get("scale")), _e(d.get("similarity")),
                     " (check)" if d.get("review") else "", _b(d["placed"]), _e(d["path"])) for d in m["duplicates"]) + "</table>")
    if m["kit"]:
        o.append("<h2>Tinted fills (not exported)</h2><p class=mute>One flat colour in a box, rounded box or ellipse, checked by redrawing it (FUZIO 9.10): use the white box or circle sprite, tinted.</p>"
                 "<table><tr><th>Element</th><th>Shape</th><th>Colour</th><th>Radius</th><th>Draw with</th><th>Placed</th><th>Layer</th></tr>"
                 + "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s (%s)</td><td>%s</td><td><code>%s</code></td></tr>" % (
                     _e(k["name"]), _e(k["shape"]), _swatch(k["color"]), _e(k.get("radius")), _e(k.get("sprite")), _e(k.get("imageType")),
                     _b(k["placed"]), _e(k["path"])) for k in m["kit"]) + "</table>")
    if m["gradients"]:
        rows = []
        for g in m["gradients"]:
            fill = g.get("fill") or {}
            g2 = fill.get("gradient2") or {}
            keys = g2.get("colorKeys") or []
            css = ", ".join("%s %d%%" % (k["color"], round(100 * k["time"])) for k in keys)
            bar = "<span class=sw style='width:80px;background:linear-gradient(%s,%s)'></span>" % (
                "0deg" if g2.get("gradientType") == "Vertical" else "90deg", _e(css)) if keys else ""
            rows.append("<tr><td>%s</td><td>%s %s, %d keys</td><td>%s%s</td><td>%s</td><td>%s</td><td><code>%s</code></td></tr>" % (
                _e(g["name"]), bar, _e(g2.get("gradientType")), len(keys), _e(fill.get("sprite")),
                ", radius %s" % fill.get("cornerRadius") if fill.get("cornerRadius") else "", _e(g.get("frame") or "none"),
                _b(g.get("placed")), _e(g["path"])))
        o.append("<h2>Gradient boxes</h2><p class=mute>Linear gradients in a fixed orientation: the frame sprite plus a Gradient2 fill on the white box or circle (FUZIO 9.4), checked by rebuilding them.</p>"
                 "<table><tr><th>Element</th><th>Gradient2</th><th>Fill</th><th>Frame sprite</th><th>Placed</th><th>Layer</th></tr>%s</table>" % "".join(rows))
    if m["texts"]:
        rows = []
        for t in m["texts"]:
            run = (t.get("runs") or [{}])[0]
            fx = ", ".join(e["type"] for e in t.get("effects") or [])
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                _e(t.get("string")), _e(run.get("font")), _e(run.get("sizePx")), _swatch(run.get("color")), _e(fx), _b(t.get("bounds"))))
        o.append("<h2>Texts (not exported, rebuild as TextMeshPro)</h2><table><tr><th>Text</th><th>Font</th><th>Size px</th><th>Colour</th><th>Effects</th><th>Bounds</th></tr>%s</table>" % "".join(rows))
    order = ["behind", "mockup", "covered", "fx", "kit", "rule", "hidden", "empty", "orphan", "other", "error", "text"]
    sk = []
    for cat in order + sorted(c for c in by_cat if c not in order):
        items = by_cat.get(cat)
        if not items or cat in ("text",):
            continue
        sk.append("<details%s><summary>%s (%d)</summary><table><tr><th>Layer</th><th>Reason</th><th>Rule pattern</th></tr>%s</table></details>" % (
            "" if cat in ("hidden", "kit") else " open", _e(cat), len(items),
            "".join("<tr><td><code>%s</code></td><td>%s</td><td><code>id:%s</code></td></tr>" % (_e(s["path"]), _e(s["reason"]), _e(s["layerId"])) for s in items)))
    o.append("<h2>Skipped layers</h2><p class=mute>To change a decision, add the layer's path or id to <code>slyce_rules.json</code> (keep, ignore, whole, split, noSlice, ...) and run <code>redo</code>.</p>" + "".join(sk))
    if composite_rel:
        o.append("<h2>PSD composite</h2><img class=comp src='%s'>" % _e(composite_rel))
    o.append("<p class=mute style='margin-top:40px'>Slyce: PSD exporting assets and UI skill by Ali</p></main></body></html>")
    path = os.path.join(out_dir, "review.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(o).replace("<table>", "<div class=tw><table>").replace("</table>", "</table></div>"))
    return path
