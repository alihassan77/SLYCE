// slyce_export.jsx - SLYCE export step: renders each element of the job from the run's PSD copy to a
// PNG with transparency, cropped to its pixels (clamped to the canvas), and writes the render log.
// Started by a launcher that defines SL_JOB_PATH and SL_SCRIPTS.
//
// Variants:
//   full   - the layer (or group) with its effects
//   merged - the layer plus its dependent layers (clipped, adjustment, blend-mode layers), rendered together
//   frame  - fill 0% and the Gradient Overlay hidden: the stroke/shadow frame of a gradient box
//   style  - fill 0%: the layer style only (frame of a gradient fill layer)
//   fill   - effects hidden: the shape itself, the mask of a gradient box
//   part   - some layers of a composed group (layerIds), or layers inside a smart object (smartObject)
// Each render: select the layers, Layer > Duplicate into a new document of the same canvas, add an empty
// layer and Merge Visible (bakes the effects), crop, save PNG-24. Every change to the PSD copy is undone
// through its history; the copy is closed unsaved at the end. The original PSD is never opened.

$.evalFile(new File(SL_SCRIPTS + "/json2.jsx"));
$.evalFile(new File(SL_SCRIPTS + "/slyce_common.jsx"));

function slx_cropAndSave(out, path, offsetX, offsetY, docWidth, docHeight) {
    out.artLayers.add();
    out.mergeVisibleLayers();
    var b = sl_activeLayerBounds();
    var x0 = Math.max(b.x, -offsetX);
    var y0 = Math.max(b.y, -offsetY);
    var x1 = Math.min(b.x + b.width, docWidth - offsetX);
    var y1 = Math.min(b.y + b.height, docHeight - offsetY);
    if (x1 <= x0 || y1 <= y0) throw new Error("the render is empty or off the canvas");
    out.crop([x0, y0, x1, y1]);
    sl_preparePng(out);
    sl_savePng(out, path);
    return { x: offsetX + x0, y: offsetY + y0, width: x1 - x0, height: y1 - y0 };
}

function slx_render(source, element, path) {
    var history = source.activeHistoryState;
    var out = null;
    var i;
    try {
        if (element.show) for (i = 0; i < element.show.length; i++) sl_setLayerVisible(element.show[i], true);
        for (i = 0; i < element.hide.length; i++) sl_setLayerVisible(element.hide[i], false);
        // style: fill 0% (effects only); fill: effects hidden (the shape's own pixels, used as a mask);
        // frame: fill 0% and the Gradient Overlay hidden (the stroke/shadow frame of a gradient box).
        if (element.variant === "style" || element.variant === "frame") sl_setFillOpacity(element.layerIds[0], 0);
        if (element.variant === "frame") sl_setEffectVisible(element.layerIds[0], "gradientFill", false);
        if (element.variant === "fill") sl_setEffectsVisible(element.layerIds[0], false);
        sl_selectLayers(element.layerIds);
        out = sl_newDocumentFromSelectedLayer("sl_export");
        if (element.upright) {
            // Upright: turn the layers back (a smart object re-renders from its source) around the pivot, so a
            // tinted / styled smart object comes out straight with its whole look; Unity applies the rotation.
            var ids = sl_transformableLayerIds(out);
            if (ids.length) {
                sl_selectLayers(ids);
                sl_rotateSelected(element.upright.angle, element.upright.cx, element.upright.cy);
            }
        }
        return slx_cropAndSave(out, path, 0, 0, Math.round(source.width.as("px")), Math.round(source.height.as("px")));
    } finally {
        if (out !== null) out.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = source;
        source.activeHistoryState = history;
    }
}

// A part inside a smart object: rendered in the smart object's contents (opened from this copy and closed
// unsaved), resampled to the placed size, then cropped. Bounds are in document pixels.
function slx_renderSmartPart(source, element, path) {
    var so = element.smartObject;
    var p = so.corners;
    var contents = null;
    var out = null;
    try {
        contents = sl_openSmartObjectContents(so.layerId);
        sl_selectLayers(so.contentIds);
        out = sl_newDocumentFromSelectedLayer("sl_export");
        out.artLayers.add();
        out.mergeVisibleLayers();
        sl_resizeImage(Math.max(1, Math.round(p[2] - p[0])), Math.max(1, Math.round(p[7] - p[1])));
        return slx_cropAndSave(out, path, Math.round(p[0]), Math.round(p[1]),
                               Math.round(source.width.as("px")), Math.round(source.height.as("px")));
    } finally {
        if (out !== null) out.close(SaveOptions.DONOTSAVECHANGES);
        if (contents !== null) contents.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = source;
    }
}

// A smart object's whole contents as one image (opened from this copy, closed unsaved): with some hidden
// layers inside shown or visible ones hidden (a state such as a light's lit glow), and/or at a higher
// resolution than its placed size (scale > 1: headroom for art that animates bigger). The contents are
// flattened, resampled to the placed size x scale, cropped to their pixels. Bounds are in document pixels.
function slx_renderSmartContents(source, element, path) {
    var r = element.soRender;
    var p = r.corners;
    var contents = null;
    var flat = null;
    var i;
    try {
        contents = sl_openSmartObjectContents(r.layerId);
        for (i = 0; i < r.show.length; i++) sl_setLayerVisible(r.show[i], true);
        for (i = 0; i < r.hide.length; i++) sl_setLayerVisible(r.hide[i], false);
        flat = contents.duplicate("sl_flat", true);
        app.activeDocument = flat;
        var uw = r.unrotatedWidth || (p[2] - p[0]);
        var uh = r.unrotatedHeight || (p[7] - p[1]);
        sl_resizeImage(Math.max(1, Math.round(uw * r.scale)), Math.max(1, Math.round(uh * r.scale)));
        // Turned like the smart object in the PSD (Photoshop rotates the canvas clockwise for a positive angle).
        if (r.rotation && Math.abs(r.rotation) > 0.05) flat.rotateCanvas(r.rotation);
        var W = flat.width.as("px");
        var H = flat.height.as("px");
        var b = sl_activeLayerBounds();
        if (b.width <= 0 || b.height <= 0) throw new Error("the render is empty");
        flat.crop([b.x, b.y, b.x + b.width, b.y + b.height]);
        sl_preparePng(flat);
        sl_savePng(flat, path);
        // The canvas centre is the smart object's centre (the mean of its four corners).
        var cx = (p[0] + p[2] + p[4] + p[6]) / 4;
        var cy = (p[1] + p[3] + p[5] + p[7]) / 4;
        return { x: Math.round(cx + (b.x - W / 2) / r.scale), y: Math.round(cy + (b.y - H / 2) / r.scale),
                 width: Math.round(b.width / r.scale), height: Math.round(b.height / r.scale), scale: r.scale };
    } finally {
        if (flat !== null) flat.close(SaveOptions.DONOTSAVECHANGES);
        if (contents !== null) contents.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = source;
    }
}

function slx_main(job, warnings) {
    var major = parseInt(app.version, 10);
    if (major < job.minMajorVersion)
        throw new Error("Photoshop " + app.version + " is too old; version " + job.minMajorVersion + " (2022) or later is required.");

    // Safety: only the PSD copy inside this run folder is ever opened.
    var copy = new File(job.psdCopy);
    var runDir = new Folder(job.runDir);
    if (!/^psd_copy\.(psd|psb)$/i.test(copy.name) || copy.parent.fsName !== runDir.fsName)
        throw new Error("Refusing to open " + copy.fsName + ": not the PSD copy of this run.");
    if (!copy.exists) throw new Error("PSD copy not found: " + copy.fsName);

    var i;
    for (i = app.documents.length - 1; i >= 0; i--) {
        var openPath = null;
        try { openPath = app.documents[i].fullName.fsName; } catch (e) { /* never saved */ }
        if (openPath === copy.fsName) app.documents[i].close(SaveOptions.DONOTSAVECHANGES);
    }

    var log = { items: [] };
    var source = app.open(copy);
    try {
        app.activeDocument = source;
        for (i = 0; i < job.elements.length; i++) {
            var element = job.elements[i];
            var item = { key: element.key, file: element.file, bounds: null, status: "error", error: null, attempts: 0 };
            // One retry per render; failures are reported, not fatal.
            while (item.attempts < 2 && item.status !== "ok") {
                item.attempts++;
                try {
                    // Plain if/else: ExtendScript misreads a nested ?: split over lines.
                    if (element.soRender) item.bounds = slx_renderSmartContents(source, element, element.file);
                    else if (element.smartObject) item.bounds = slx_renderSmartPart(source, element, element.file);
                    else item.bounds = slx_render(source, element, element.file);
                    item.status = "ok";
                    item.error = null;
                } catch (e) {
                    item.error = sl_errorText(e);
                }
            }
            log.items.push(item);
        }
    } finally {
        source.close(SaveOptions.DONOTSAVECHANGES);
    }
    sl_writeAtomic(job.logPath, JSON.stringify(log, null, 2));
    return log.items.length;
}

(function () {
    var job = null;
    var status = "error";
    var errors = [];
    var warnings = [];
    var renders = 0;
    var savedUnits = app.preferences.rulerUnits;
    var savedDialogs = app.displayDialogs;
    try {
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        job = JSON.parse(sl_readText(SL_JOB_PATH));
        renders = slx_main(job, warnings);
        status = "ok";
    } catch (e) {
        errors.push(sl_errorText(e));
    } finally {
        app.preferences.rulerUnits = savedUnits;
        app.displayDialogs = savedDialogs;
        var donePath = job && job.donePath ? job.donePath : new File(SL_JOB_PATH).parent.fsName + "/ps_done.json";
        try {
            sl_writeAtomic(donePath, JSON.stringify({ status: status, errors: errors, warnings: warnings, renders: renders, photoshopVersion: app.version }, null, 2));
        } catch (e2) {
            // Nothing else can report this; SLYCE reports a missing result.
        }
    }
}());
