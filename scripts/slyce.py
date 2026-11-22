#!/usr/bin/env python3
"""Slyce: PSD exporting assets and UI skill by Ali. SLYCE analyses a PSD in Photoshop and exports its UI and 2D art as optimized sprites.
Python standard library only; drives Photoshop over COM (Windows) or AppleScript (macOS).

  python slyce.py check                              Photoshop reachable? version?
  python slyce.py run     --psd <file.psd>           analyse + plan + export + process + review (the usual way)
  python slyce.py redo    --psd <file.psd>           plan + export + process again on the latest analysis
                                                     (after editing slyce_rules.json; no new PSD copy)
  python slyce.py analyse --psd <file.psd>           new run: copy the PSD, read every layer (tree.json, thumbs)
  python slyce.py plan    --psd <file.psd>           plan.json from tree.json + slyce_rules.json
  python slyce.py export  --psd <file.psd> [--full]  render planned elements (unchanged ones come from the cache)
  python slyce.py process --psd <file.psd> [--clean]  sprites, manifest.json, review.html
  python slyce.py test [--psd <file.psd>] [--save]  re-process exported PSDs without Photoshop, compare with a baseline
                                                     (--clean deletes sprites earlier runs made that are no longer produced)

Output: a folder named after the PSD, next to it (or --out): Sprites/, manifest.json, review.html,
slyce_rules.json, and .slyce/ (runs with the last 3 PSD copies, render cache). The original PSD is only
ever copied, never opened.
"""
import argparse
import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import slyce_plan  # noqa: E402
import slyce_png  # noqa: E402
import slyce_process  # noqa: E402
import slyce_review  # noqa: E402
import slyce_rules  # noqa: E402
import slyce_sheet  # noqa: E402

VERSION = "0.1.0"
SKILL_ROOT = os.path.dirname(HERE)
PS_DIR = os.path.join(SKILL_ROOT, "photoshop")
MIN_PS_MAJOR = 23


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def fwd(p):
    return os.path.abspath(p).replace("\\", "/")


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ---------- Photoshop ----------

def _progid():
    if os.environ.get("SLYCE_PS_PROGID"):
        return os.environ["SLYCE_PS_PROGID"]
    try:
        import winreg
        return winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, r"Photoshop.Application\CurVer") or "Photoshop.Application"
    except Exception:
        return "Photoshop.Application"


def run_jsx(work_dir, step, script, job, timeout):
    """Runs photoshop/<script> with the job in Photoshop; returns its done.json."""
    job = dict(job)
    job["donePath"] = fwd(os.path.join(work_dir, step + "_done.json"))
    job["minMajorVersion"] = MIN_PS_MAJOR
    job_path = os.path.join(work_dir, step + "_job.json")
    write_json(job_path, job)
    if os.path.exists(job["donePath"]):
        os.remove(job["donePath"])
    launcher = os.path.join(work_dir, step + "_launch.jsx")
    with open(launcher, "w", encoding="utf-8") as f:
        f.write("var SL_JOB_PATH = %s;\nvar SL_SCRIPTS = %s;\n$.evalFile(new File(SL_SCRIPTS + %s));\n" % (
            json.dumps(fwd(job_path)), json.dumps(fwd(PS_DIR)), json.dumps("/" + script)))
    if platform.system() == "Windows":
        cmd = ["cscript", "//nologo", "//E:jscript", os.path.join(PS_DIR, "slyce_com.js"), _progid(), os.path.abspath(launcher)]
    elif platform.system() == "Darwin":
        js = "$.evalFile(new File(%s));" % json.dumps(fwd(launcher))
        cmd = ["osascript", "-e", "on run argv", "-e", "with timeout of %d seconds" % timeout,
               "-e", 'tell application id "com.adobe.Photoshop" to do javascript (item 1 of argv)',
               "-e", "end timeout", "-e", "end run", js]
    else:
        raise SystemExit("SLYCE needs Photoshop on Windows or macOS.")
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
    if not os.path.exists(job["donePath"]):
        raise SystemExit("Photoshop did not finish %s (exit %d): %s%s" % (script, proc.returncode, proc.stderr.strip(), proc.stdout.strip()))
    done = read_json(job["donePath"])
    done["seconds"] = round(time.time() - started, 1)
    if done.get("status") != "ok":
        raise SystemExit("Photoshop %s failed: %s" % (script, "; ".join(done.get("errors") or ["unknown error"])))
    return done


# ---------- folders and runs ----------

class Ctx(object):
    def __init__(self, psd, out=None):
        self.psd = os.path.abspath(psd)
        if not os.path.isfile(self.psd):
            raise SystemExit("PSD not found: " + self.psd)
        if not self.psd.lower().endswith((".psd", ".psb")):
            raise SystemExit("Not a .psd/.psb file: " + self.psd)
        self.name = os.path.splitext(os.path.basename(self.psd))[0]
        self.out = os.path.abspath(out) if out else os.path.join(os.path.dirname(self.psd), self.name)
        self.state = os.path.join(self.out, ".slyce")
        self.runs = os.path.join(self.state, "runs")
        self.cache = os.path.join(self.state, "cache")
        self.rules = slyce_rules.load(self.out)

    def run_ids(self):
        if not os.path.isdir(self.runs):
            return []
        return sorted(d for d in os.listdir(self.runs) if os.path.isfile(os.path.join(self.runs, d, "tree.json")))

    def run_dir(self, run_id=None):
        ids = self.run_ids()
        if run_id:
            if run_id not in ids:
                raise SystemExit("No analysed run %s in %s" % (run_id, self.runs))
            return os.path.join(self.runs, run_id)
        if not ids:
            raise SystemExit("No analysed run yet for this PSD: run `analyse` or `run` first.")
        return os.path.join(self.runs, ids[-1])

    def prune(self):
        keep = max(1, int(self.rules.get("keepRuns")))
        dirs = sorted(d for d in os.listdir(self.runs) if os.path.isdir(os.path.join(self.runs, d)))
        removed = []
        for d in dirs[:-keep]:
            shutil.rmtree(os.path.join(self.runs, d), ignore_errors=True)
            removed.append(d)
        return removed


def cmd_analyse(ctx, timeout):
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run = os.path.join(ctx.runs, run_id)
    os.makedirs(os.path.join(run, "thumbs"), exist_ok=True)
    ext = os.path.splitext(ctx.psd)[1].lower()
    copy = os.path.join(run, "psd_copy" + ext)
    log("SLYCE: copying %s" % ctx.psd)
    shutil.copy2(ctx.psd, copy)
    write_json(os.path.join(run, "source.json"), {"psd": ctx.psd, "size": os.path.getsize(ctx.psd),
                                                   "modified": os.path.getmtime(ctx.psd), "slyce": VERSION})
    removed = ctx.prune()
    log("SLYCE: reading layers in Photoshop (this takes a while on big PSDs)")
    done = run_jsx(run, "read", "slyce_read.jsx", {
        "psdCopy": fwd(copy), "runDir": fwd(run), "treePath": fwd(os.path.join(run, "tree.json")),
        "thumbsDir": fwd(os.path.join(run, "thumbs")), "thumbSize": 256, "documentThumbSize": 1024,
        "compositePath": fwd(os.path.join(run, "composite.png")), "progressPath": fwd(os.path.join(run, "read_progress.json")),
    }, timeout)
    return {"run": run_id, "runDir": run, "layers": done.get("layers"), "seconds": done["seconds"],
            "warnings": done.get("warnings"), "prunedRuns": removed}


def cmd_plan(ctx, run):
    tree = read_json(os.path.join(run, "tree.json"))
    plan = slyce_plan.make_plan(tree, ctx.rules, os.path.basename(run), ctx.name)
    write_json(os.path.join(run, "plan.json"), plan)
    # Plan sheet for the planner agent: every planned element's thumbnail (with effects), numbered.
    items = []
    for el in plan["elements"]:
        thumb = os.path.join(run, "thumbs", "%d.png" % el["layerId"])
        img = slyce_png.read_png(thumb) if os.path.exists(thumb) else None
        items.append((img, {"name": el["name"], "role": el["role"], "path": el["path"], "layerId": el["layerId"],
                            "parts": [p["name"] for p in el.get("parts") or []], "hide": len(el.get("hide") or []),
                            "overlays": el.get("overlays"), "notes": el.get("notes")}))
    sheet = os.path.join(ctx.state, "plan_sheet.png")
    slyce_sheet.make_sheet(items, sheet)
    cats = {}
    for s in plan["skipped"]:
        cats[s["category"]] = cats.get(s["category"], 0) + 1
    return {"run": os.path.basename(run), "elements": len(plan["elements"]),
            "parts": sum(len(e["parts"]) for e in plan["elements"]), "texts": len(plan["texts"]),
            "skipped": cats, "warnings": plan["warnings"], "planSheet": sheet,
            "documentPreview": os.path.join(run, "thumbs", "_document.png")}


# ---------- export with a render cache ----------

def _pixel_digest(path):
    # Photoshop writes timestamps into PNG metadata, so hash the decoded pixels, not the file.
    return slyce_png.read_png(path).digest()


def _layer_sig(layer, run):
    d = dict(layer)
    d.pop("order", None)
    thumb = d.pop("thumb", None)
    comp = d.get("composition")
    if comp:
        d["composition"] = {"type": comp.get("type"), "parts": [dict((k, v) for k, v in p.items() if k != "thumb") for p in comp.get("parts") or []]}
    if thumb and os.path.exists(os.path.join(run, thumb)):
        d["_pixels"] = _pixel_digest(os.path.join(run, thumb))
    return d


def render_specs(plan, tree, run):
    by_id = dict((l["id"], l) for l in tree["layers"])
    children = {}
    for l in tree["layers"]:
        children.setdefault(l["parentId"], []).append(l["id"])

    def members(ids):
        out = []
        stack = list(ids)
        while stack:
            i = stack.pop(0)
            if i in by_id and i not in out:
                out.append(i)
                stack.extend(children.get(i, []))
        return out

    specs = []
    for el in plan["elements"]:
        spec = {"key": el["key"], "layerIds": el["layerIds"], "hide": el["hide"], "variant": el["variant"], "smartObject": None,
                "show": el.get("show") or []}
        specs.append((spec, members(el["layerIds"] + el["hide"])))
        # Gradient box candidates: the frame (or style) and the fill renders of the element's own layer.
        for v in el.get("extras") or []:
            spec = {"key": "%s:%s" % (el["key"], v), "layerIds": [el["layerId"]], "hide": [], "variant": v, "smartObject": None}
            specs.append((spec, members([el["layerId"]])))
        if el.get("uprightRender"):
            spec = {"key": el["key"] + ":upright", "layerIds": el["layerIds"], "hide": el["hide"], "variant": el["variant"],
                    "smartObject": None, "show": el.get("show") or [], "upright": el["uprightRender"]}
            specs.append((spec, members(el["layerIds"] + el["hide"])))
        if el.get("hires"):
            spec = {"key": el["key"] + ":hires", "layerIds": [], "hide": [], "variant": "soRender", "smartObject": None,
                    "soRender": el["hires"]}
            specs.append((spec, members([el["layerId"]])))
        for st in el.get("states") or []:
            spec = {"key": st["key"], "layerIds": [], "hide": [], "variant": "soRender", "smartObject": None, "soRender": st["soRender"]}
            specs.append((spec, members([el["layerId"]])))
        for p in el.get("parts") or []:
            if p.get("smartObject"):
                spec = {"key": p["key"], "layerIds": [], "hide": [], "variant": "part", "smartObject": p["smartObject"]}
                specs.append((spec, members([p["smartObject"]["layerId"]])))
            else:
                spec = {"key": p["key"], "layerIds": p["layerIds"], "hide": [], "variant": "part", "smartObject": None}
                specs.append((spec, members(p["layerIds"])))
    doc = tree["document"]
    out = []
    for spec, ids in specs:
        sig_src = {"spec": dict((k, v) for k, v in spec.items() if k != "key"), "doc": [doc["width"], doc["height"]],
                   "layers": [_layer_sig(by_id[i], run) for i in ids]}
        spec["sig"] = hashlib.sha1(json.dumps(sig_src, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        out.append(spec)
    return out


def cmd_export(ctx, run, full, timeout):
    plan = read_json(os.path.join(run, "plan.json"))
    tree = read_json(os.path.join(run, "tree.json"))
    os.makedirs(ctx.cache, exist_ok=True)
    specs = render_specs(plan, tree, run)
    todo = []
    for s in specs:
        png = os.path.join(ctx.cache, s["sig"] + ".png")
        meta = os.path.join(ctx.cache, s["sig"] + ".json")
        if full or not (os.path.exists(png) and os.path.exists(meta)):
            job_el = {"key": s["key"], "file": fwd(png), "layerIds": s["layerIds"], "hide": s["hide"], "variant": s["variant"],
                      "show": s.get("show") or []}
            if s["smartObject"]:
                job_el["smartObject"] = s["smartObject"]
            if s.get("soRender"):
                job_el["soRender"] = s["soRender"]
            if s.get("upright"):
                job_el["upright"] = s["upright"]
            todo.append(job_el)
    done = None
    if todo:
        ext = os.path.splitext(ctx.psd)[1].lower()
        log("SLYCE: rendering %d of %d elements in Photoshop (%d unchanged, from cache)" % (len(todo), len(specs), len(specs) - len(todo)))
        done = run_jsx(run, "export", "slyce_export.jsx", {
            "psdCopy": fwd(os.path.join(run, "psd_copy" + ext)), "runDir": fwd(run), "elements": todo,
            "logPath": fwd(os.path.join(run, "export_log.json")),
        }, timeout)
        for item in read_json(os.path.join(run, "export_log.json"))["items"]:
            sig = os.path.splitext(os.path.basename(item["file"]))[0]
            if item["status"] == "ok":
                write_json(os.path.join(ctx.cache, sig + ".json"), {"bounds": item["bounds"]})
            else:
                write_json(os.path.join(run, "render_error_%s.json" % item["key"].replace(".", "_").replace(":", "_")), item)
    renders = {}
    errors = []
    for s in specs:
        meta = os.path.join(ctx.cache, s["sig"] + ".json")
        png = os.path.join(ctx.cache, s["sig"] + ".png")
        if os.path.exists(meta) and os.path.exists(png):
            renders[s["key"]] = {"file": png, "bounds": read_json(meta)["bounds"], "sig": s["sig"]}
        else:
            err_path = os.path.join(run, "render_error_%s.json" % s["key"].replace(".", "_").replace(":", "_"))
            err = read_json(err_path)["error"] if os.path.exists(err_path) else "not rendered"
            renders[s["key"]] = {"error": err, "sig": s["sig"]}
            errors.append("%s: %s" % (s["key"], err))
    write_json(os.path.join(run, "renders.json"), renders)
    _prune_cache(ctx)
    return {"run": os.path.basename(run), "renders": len(specs), "rendered": len(todo), "fromCache": len(specs) - len(todo),
            "seconds": done["seconds"] if done else 0, "errors": errors}


def _prune_cache(ctx):
    used = set()
    for rid in os.listdir(ctx.runs):
        rp = os.path.join(ctx.runs, rid, "renders.json")
        if os.path.exists(rp):
            used.update(r["sig"] for r in read_json(rp).values())
    for f in glob.glob(os.path.join(ctx.cache, "*")):
        if os.path.splitext(os.path.basename(f))[0] not in used:
            os.remove(f)


def cmd_process(ctx, run, clean=False):
    plan = read_json(os.path.join(run, "plan.json"))
    renders = read_json(os.path.join(run, "renders.json"))
    log("SLYCE: processing %d elements" % len(plan["elements"]))
    res = slyce_process.process(plan, renders, ctx.out, ctx.rules, log=log, clean=clean)
    manifest = {
        "slyce": VERSION, "psd": ctx.psd, "run": os.path.basename(run), "root": plan["rootName"],
        "document": {"width": plan["document"]["width"], "height": plan["document"]["height"]},
        "rules": slyce_rules.rules_path(ctx.out),
    }
    manifest.update(res)
    manifest["summary"] = summarise(res)
    write_json(os.path.join(ctx.out, "manifest.json"), manifest)
    comp = os.path.relpath(os.path.join(run, "composite.png"), ctx.out).replace("\\", "/")
    review = slyce_review.write(ctx.out, manifest, comp if os.path.exists(os.path.join(run, "composite.png")) else None)
    # Review sheet for the reviewer agent: every exported sprite as written, numbered.
    items = []
    for sp in res["sprites"]:
        f = os.path.join(ctx.out, sp["file"])
        items.append((slyce_png.read_png(f) if os.path.exists(f) else None,
                      {"name": sp["name"], "file": sp["file"], "role": sp["role"], "kind": sp["kind"],
                       "texture": sp["texture"], "scale": sp.get("scale"), "placed": sp["placed"], "path": sp["path"]}))
    review_sheet = os.path.join(ctx.state, "review_sheet.png")
    slyce_sheet.make_sheet(items, review_sheet)
    status = {}
    for s in res["sprites"]:
        status[s["status"]] = status.get(s["status"], 0) + 1
    out = {"output": ctx.out, "review": review, "manifest": os.path.join(ctx.out, "manifest.json"),
           "reviewSheet": review_sheet, "composite": os.path.join(run, "composite.png"), "files": status}
    out.update(manifest["summary"])
    out.update({"stale": res["stale"], "removed": res["removed"], "warnings": res["warnings"]})
    return out


def summarise(res):
    """The manifest's summary block: counts, textures by size and their memory (RGBA, uncompressed)."""
    cats, sizes = {}, {}
    for s in res["skipped"]:
        cats[s["category"]] = cats.get(s["category"], 0) + 1
    px = 0
    for s in res["sprites"]:
        t = s.get("texture") or {}
        k = "%dx%d" % (t.get("width", 0), t.get("height", 0))
        sizes[k] = sizes.get(k, 0) + 1
        px += t.get("width", 0) * t.get("height", 0)
    return {"sprites": len([s for s in res["sprites"] if s["kind"] != "part"]),
            "parts": len([s for s in res["sprites"] if s["kind"] == "part"]),
            "duplicates": len(res["duplicates"]), "tintedFills": len(res["tintedFills"]), "gradients": len(res["gradients"]),
            "texts": len(res["texts"]), "skipped": cats, "warnings": len(res["warnings"]),
            "textures": dict(sorted(sizes.items(), key=lambda kv: -int(kv[0].split("x")[0]))),
            "textureMB": round(px * 4 / 1048576.0, 1)}


def cmd_check(timeout):
    import tempfile
    work = tempfile.mkdtemp(prefix="slyce_check_")
    try:
        done = run_jsx(work, "check", "slyce_check.jsx", {}, timeout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    major = int(str(done.get("version", "0")).split(".")[0])
    return {"photoshop": done.get("version"), "ok": major >= MIN_PS_MAJOR, "openDocuments": done.get("documents"),
            "platform": platform.system(), "python": platform.python_version(), "slyce": VERSION}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    t = sub.add_parser("test", help="re-process already exported PSDs without Photoshop and compare with a baseline")
    t.add_argument("--psd", action="append", help="PSD to test (repeatable; default: tests/cases.json)")
    t.add_argument("--save", action="store_true", help="accept this result as the new baseline")
    for name in ("run", "redo", "analyse", "plan", "export", "process"):
        p = sub.add_parser(name)
        p.add_argument("--psd", required=True)
        p.add_argument("--out", help="output folder (default: a folder named after the PSD, next to it)")
        p.add_argument("--run", dest="run_id", help="run id (default: latest)")
        p.add_argument("--full", action="store_true", help="render everything again, ignoring the render cache")
        p.add_argument("--clean", action="store_true", help="delete sprites an earlier SLYCE run wrote that this run no longer makes")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per Photoshop step")
    a = ap.parse_args()

    if a.cmd == "check":
        print(json.dumps(cmd_check(min(a.timeout, 120)), indent=2))
        return
    if a.cmd == "test":
        import slyce_test
        results = []
        for psd in slyce_test.cases(a.psd):
            t0 = time.time()
            r = slyce_test.run_case(psd, a.save)
            r["minutes"] = round((time.time() - t0) / 60.0, 1)
            results.append(r)
        print(json.dumps({"pass": all(r["pass"] for r in results), "cases": results}, indent=2))
        sys.exit(0 if all(r["pass"] for r in results) else 1)
    ctx = Ctx(a.psd, a.out)
    result = {}
    timings = {}
    started = time.time()

    def timed(stage, fn, *args):
        t0 = time.time()
        out = fn(*args)
        timings[stage] = round(time.time() - t0, 1)
        return out

    if a.cmd in ("run", "analyse"):
        result["analyse"] = timed("analyse", cmd_analyse, ctx, a.timeout)
    if a.cmd != "analyse":
        run = ctx.run_dir(None if a.cmd == "run" else a.run_id)
        if a.cmd in ("run", "redo", "plan"):
            result["plan"] = timed("plan", cmd_plan, ctx, run)
        if a.cmd in ("run", "redo", "export"):
            result["export"] = timed("export", cmd_export, ctx, run, a.full, a.timeout)
        if a.cmd in ("run", "redo", "process"):
            result["process"] = timed("process", cmd_process, ctx, run, a.clean)
    timings["total"] = round(time.time() - started, 1)
    result["timings"] = timings
    _log_timings(ctx, a.cmd, result)
    print(json.dumps(result, indent=2))


def _log_timings(ctx, cmd, result):
    """Appends one line per command to <out>/.slyce/timings.jsonl (seconds per stage, sprite counts), so the
    time a PSD takes can be measured across runs; also stored in manifest.json after a process step."""
    p = result.get("process") or {}
    e = result.get("export") or {}
    line = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "command": cmd, "psd": ctx.psd, "seconds": result["timings"],
            "layers": (result.get("analyse") or {}).get("layers"), "renders": e.get("renders"), "rendered": e.get("rendered"),
            "sprites": p.get("sprites"), "parts": p.get("parts"), "duplicates": p.get("duplicates")}
    try:
        os.makedirs(ctx.state, exist_ok=True)
        with open(os.path.join(ctx.state, "timings.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        mpath = os.path.join(ctx.out, "manifest.json")
        if p and os.path.exists(mpath):
            m = read_json(mpath)
            m["timings"] = line
            write_json(mpath, m)
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0) and not isinstance(e.code, int):
            print(json.dumps({"error": str(e.code)}, indent=2))
            log("SLYCE error: %s" % e.code)
            sys.exit(1)
        raise
