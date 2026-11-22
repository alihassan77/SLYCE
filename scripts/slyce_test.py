"""SLYCE regression test: re-plans and re-processes PSDs that were already exported, without Photoshop, and
compares the result with a saved baseline (or the PSD's current manifest.json).

  python slyce.py test --psd A.psd --psd B.psd     # compare
  python slyce.py test --psd A.psd --save          # accept this result as the new baseline
  python slyce.py test                             # the PSDs listed in <skill>/tests/cases.json
                                                   (local, not in git; copy tests/cases.example.json)

It reuses the latest analysis (tree.json) and the render cache, so it takes about a minute per PSD. Nothing in the
PSD's output folder changes except `.slyce/baseline.json` with --save; the test writes into a temp folder.
Renders the current code would need but the cache lacks are reported as "needs Photoshop" (run `redo` once).
"""
import json
import os
import shutil
import tempfile

import slyce
import slyce_plan
import slyce_rules

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(SKILL, "tests", "cases.json")

# Fields compared per manifest section. Everything else (paths of runs, timings, file status) is ignored.
SECTIONS = {
    "sprites": ("name", ["file", "kind", "role", "texture", "content", "slice", "scale", "rotation", "pivot", "digest", "layerId"]),
    "duplicates": ("name", ["source", "match", "scale", "rotation", "flip", "placed", "tint"]),
    "tintedFills": ("name", ["shape", "radius", "color", "sprite", "placed"]),
    "gradients": ("name", ["frame", "placed", "fill"]),
    "texts": ("layerId", ["name", "font", "size", "color"]),
    "skipped": ("layerId", ["category"]),
    "elements": ("name", ["role", "treatment", "sprites"]),
}


def fingerprint(manifest):
    fp = {}
    m = dict(manifest)
    if "kit" in m and "tintedFills" not in m:  # manifests written before the rename
        m["tintedFills"] = m["kit"]
    for sec, (key, fields) in SECTIONS.items():
        rows = {}
        for r in m.get(sec) or []:
            k = str(r.get(key))
            while k in rows:
                k += "+"
            rows[k] = dict((f, r.get(f)) for f in fields if r.get(f) is not None)
        fp[sec] = rows
    fp["warnings"] = sorted(m.get("warnings") or [])
    return fp


def diff(old, new):
    out = []
    for sec in SECTIONS:
        a, b = old.get(sec) or {}, new.get(sec) or {}
        for k in sorted(set(a) - set(b)):
            out.append("%s: removed %s" % (sec, k))
        for k in sorted(set(b) - set(a)):
            out.append("%s: added %s" % (sec, k))
        for k in sorted(set(a) & set(b)):
            changed = [f for f in set(a[k]) | set(b[k]) if a[k].get(f) != b[k].get(f)]
            if changed:
                out.append("%s: %s changed %s" % (sec, k, ", ".join(
                    "%s %s -> %s" % (f, _short(a[k].get(f)), _short(b[k].get(f))) for f in sorted(changed))))
    wa, wb = set(old.get("warnings") or []), set(new.get("warnings") or [])
    out += ["warnings: removed %s" % w for w in sorted(wa - wb)] + ["warnings: added %s" % w for w in sorted(wb - wa)]
    return out


def _short(v):
    s = json.dumps(v, sort_keys=True)
    return s if len(s) <= 60 else s[:57] + "..."


def run_case(psd, save=False):
    ctx = slyce.Ctx(psd)
    run = ctx.run_dir()
    tmp = tempfile.mkdtemp(prefix="slyce_test_")
    try:
        if os.path.exists(slyce_rules.rules_path(ctx.out)):
            shutil.copy2(slyce_rules.rules_path(ctx.out), slyce_rules.rules_path(tmp))
        tctx = slyce.Ctx(psd, tmp)
        trun = os.path.join(tctx.runs, os.path.basename(run))
        os.makedirs(trun)
        tree = slyce.read_json(os.path.join(run, "tree.json"))
        plan = slyce_plan.make_plan(tree, tctx.rules, os.path.basename(run), ctx.name)
        slyce.write_json(os.path.join(trun, "plan.json"), plan)
        renders, missing = {}, []
        for s in slyce.render_specs(plan, tree, run):
            png = os.path.join(ctx.cache, s["sig"] + ".png")
            meta = os.path.join(ctx.cache, s["sig"] + ".json")
            if os.path.exists(png) and os.path.exists(meta):
                renders[s["key"]] = {"file": png, "bounds": slyce.read_json(meta)["bounds"], "sig": s["sig"]}
            else:
                renders[s["key"]] = {"error": "not in the render cache", "sig": s["sig"]}
                missing.append(s["key"])
        slyce.write_json(os.path.join(trun, "renders.json"), renders)
        slyce.cmd_process(tctx, trun)
        new = fingerprint(slyce.read_json(os.path.join(tmp, "manifest.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    base_path = os.path.join(ctx.state, "baseline.json")
    result = {"psd": ctx.psd, "sprites": len(new["sprites"]), "duplicates": len(new["duplicates"]),
              "needsPhotoshop": missing}
    if save:
        slyce.write_json(base_path, new)
        result.update({"saved": base_path, "pass": True})
        return result
    if os.path.exists(base_path):
        old, against = slyce.read_json(base_path), base_path
    else:
        old, against = fingerprint(slyce.read_json(os.path.join(ctx.out, "manifest.json"))), os.path.join(ctx.out, "manifest.json")
    changes = diff(old, new)
    result.update({"against": against, "changes": changes, "pass": not changes and not missing})
    return result


def cases(psds):
    if psds:
        return psds
    if os.path.exists(CASES):
        return [c["psd"] if isinstance(c, dict) else c for c in slyce.read_json(CASES)]
    raise SystemExit("No PSDs to test: pass --psd, or list them in " + CASES)
