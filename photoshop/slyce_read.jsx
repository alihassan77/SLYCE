// slyce_read.jsx - SLYCE analyse step: reads every layer of the run's PSD copy into tree.json,
// writes a 256 px thumbnail (with effects) per visible layer, a small document preview and the
// full-size composite. Started by a launcher that defines SL_JOB_PATH and SL_SCRIPTS.
// Safety: opens only psd_copy.* inside the run folder, never saves it, closes it unsaved.

$.evalFile(new File(SL_SCRIPTS + "/json2.jsx"));
$.evalFile(new File(SL_SCRIPTS + "/slyce_common.jsx"));

var SL_EFFECT_KEYS = [
    ["dropShadow", "dropShadow"],
    ["innerShadow", "innerShadow"],
    ["outerGlow", "outerGlow"],
    ["innerGlow", "innerGlow"],
    ["bevelEmboss", "bevelEmboss"],
    ["chromeFX", "satin"],
    ["solidFill", "colorOverlay"],
    ["gradientFill", "gradientOverlay"],
    ["patternFill", "patternOverlay"],
    ["frameFX", "stroke"]
];

function slr_kind(kindNumber, section) {
    if (section === "layerSectionStart") return "group";
    switch (kindNumber) {
    case 2: return "adjustment";
    case 3: return "text";
    case 4: return "shape";
    case 5: return "smartObject";
    case 6: return "video";
    case 7: return "group";
    case 8: return "3d";
    case 9:
    case 10:
    case 11: return "fill";
    }
    return "pixel";
}

function slr_effect(psKey, type, desc) {
    var params = sl_descToObject(desc);
    if (params.enabled === false) return null;
    var effect = { type: type, psKey: psKey, params: params };
    var gradient = sl_gradient(desc);
    if (gradient !== null) effect.gradient = gradient;
    return effect;
}

function slr_effects(d) {
    var list = [];
    if (!sl_hasKey(d, "layerEffects")) return list;
    if (sl_hasKey(d, "layerFXVisible") && !d.getBoolean(sl_s("layerFXVisible"))) return list;
    var fx = d.getObjectValue(sl_s("layerEffects"));
    if (sl_hasKey(fx, "masterFXSwitch") && !fx.getBoolean(sl_s("masterFXSwitch"))) return list;

    var i, j, key, effect, multi;
    for (i = 0; i < SL_EFFECT_KEYS.length; i++) {
        key = SL_EFFECT_KEYS[i][0];
        // With several of one kind Photoshop stores a "...Multi" list (which includes the single one).
        if (sl_hasKey(fx, key + "Multi")) {
            multi = fx.getList(sl_s(key + "Multi"));
            for (j = 0; j < multi.count; j++) {
                effect = slr_effect(key, SL_EFFECT_KEYS[i][1], multi.getObjectValue(j));
                if (effect !== null) list.push(effect);
            }
        } else if (sl_hasKey(fx, key)) {
            effect = slr_effect(key, SL_EFFECT_KEYS[i][1], fx.getObjectValue(sl_s(key)));
            if (effect !== null) list.push(effect);
        }
    }
    return list;
}

function slr_styleValue(style, key) {
    if (sl_hasKey(style, key)) return style;
    if (sl_hasKey(style, "baseParentStyle")) {
        var base = style.getObjectValue(sl_s("baseParentStyle"));
        if (sl_hasKey(base, key)) return base;
    }
    return null;
}

function slr_text(d, resolution) {
    var t = d.getObjectValue(sl_s("textKey"));
    var out = { string: t.getString(sl_s("textKey")), runs: [], alignment: null, warp: false, boxText: false, transform: null };
    var scale = 1;
    if (sl_hasKey(t, "transform")) {
        var tr = t.getObjectValue(sl_s("transform"));
        out.transform = sl_descToObject(tr);
        var yx = tr.getDouble(sl_s("yx"));
        var yy = tr.getDouble(sl_s("yy"));
        scale = Math.sqrt(yx * yx + yy * yy);
    }

    var i, range, style, src, run, size, unit;
    if (sl_hasKey(t, "textStyleRange")) {
        var ranges = t.getList(sl_s("textStyleRange"));
        for (i = 0; i < ranges.count; i++) {
            range = ranges.getObjectValue(i);
            style = range.getObjectValue(sl_s("textStyle"));
            run = { from: range.getInteger(sl_s("from")), to: range.getInteger(sl_s("to")) };

            src = slr_styleValue(style, "fontPostScriptName");
            run.font = src ? src.getString(sl_s("fontPostScriptName")) : null;

            src = slr_styleValue(style, "size");
            if (src) {
                size = src.getUnitDoubleValue(sl_s("size"));
                unit = sl_keyName(src.getUnitDoubleType(sl_s("size")));
                run.sizePx = sl_round((unit === "pointsUnit" ? size * resolution / 72 : size) * scale, 2);
            } else {
                run.sizePx = null;
            }

            src = slr_styleValue(style, "color");
            run.color = src ? sl_colorHex(src.getObjectValue(sl_s("color"))) : null;

            src = slr_styleValue(style, "tracking");
            run.tracking = src ? src.getInteger(sl_s("tracking")) : 0;

            src = slr_styleValue(style, "autoLeading");
            run.autoLeading = src ? src.getBoolean(sl_s("autoLeading")) : true;
            src = slr_styleValue(style, "leading");
            run.leadingPx = (!run.autoLeading && src) ? sl_round(src.getUnitDoubleValue(sl_s("leading")) * resolution / 72 * scale, 2) : null;

            // All Caps / Small Caps (Character panel): the text string itself keeps its typed case.
            src = slr_styleValue(style, "fontCaps");
            run.caps = src ? sl_keyName(src.getEnumerationValue(sl_s("fontCaps"))) : "normal";

            src = slr_styleValue(style, "syntheticBold");
            run.fauxBold = src ? src.getBoolean(sl_s("syntheticBold")) : false;
            src = slr_styleValue(style, "syntheticItalic");
            run.fauxItalic = src ? src.getBoolean(sl_s("syntheticItalic")) : false;
            out.runs.push(run);
        }
    }

    if (sl_hasKey(t, "paragraphStyleRange")) {
        var paragraphs = t.getList(sl_s("paragraphStyleRange"));
        if (paragraphs.count > 0) {
            var ps = paragraphs.getObjectValue(0).getObjectValue(sl_s("paragraphStyle"));
            src = slr_styleValue(ps, "align");
            out.alignment = src ? sl_keyName(src.getEnumerationValue(sl_s("align"))) : "left";
        }
    }

    if (sl_hasKey(t, "warp")) {
        var warp = t.getObjectValue(sl_s("warp"));
        out.warp = sl_hasKey(warp, "warpStyle") && sl_keyName(warp.getEnumerationValue(sl_s("warpStyle"))) !== "warpNone";
    }

    if (sl_hasKey(t, "textShape")) {
        var shapes = t.getList(sl_s("textShape"));
        if (shapes.count > 0) {
            var shape = shapes.getObjectValue(0);
            out.boxText = sl_hasKey(shape, "char") && sl_keyName(shape.getEnumerationValue(sl_s("char"))) === "box";
        }
    }
    return out;
}

function slr_smartObject(d) {
    var so = d.getObjectValue(sl_s("smartObjectMore"));
    // placedId: the embedded source, shared by every copy of the smart object;
    // instanceId: this layer's own placement.
    var out = { placedId: null, instanceId: null, transform: null };
    if (sl_hasKey(so, "ID")) out.placedId = so.getString(sl_s("ID"));
    if (sl_hasKey(so, "placed")) out.instanceId = so.getString(sl_s("placed"));
    if (out.placedId === null) out.placedId = out.instanceId;

    if (sl_hasKey(so, "transform") && sl_hasKey(so, "size")) {
        var list = so.getList(sl_s("transform"));
        var p = [];
        var i;
        for (i = 0; i < list.count; i++) p.push(list.getDouble(i));
        var size = so.getObjectValue(sl_s("size"));
        var w = size.getDouble(sl_s("width"));
        var h = size.getDouble(sl_s("height"));
        // Corners: top-left, top-right, bottom-right, bottom-left.
        var ux = p[2] - p[0], uy = p[3] - p[1];
        var vx = p[6] - p[0], vy = p[7] - p[1];
        var top = Math.sqrt(ux * ux + uy * uy);
        var left = Math.sqrt(vx * vx + vy * vy);
        var corners = [];
        for (i = 0; i < p.length; i++) corners.push(sl_round(p[i], 2));
        out.transform = {
            corners: corners,
            sourceWidth: sl_round(w, 2),
            sourceHeight: sl_round(h, 2),
            scaleX: w > 0 ? sl_round(top / w, 4) : null,
            scaleY: h > 0 ? sl_round(left / h, 4) : null,
            rotation: sl_round(Math.atan2(uy, ux) * 180 / Math.PI, 2),
            flipped: (ux * vy - uy * vx) < 0,
            unrotatedWidth: sl_round(top, 2),
            unrotatedHeight: sl_round(left, 2)
        };
    }
    return out;
}

// ---------- composition ----------

// Gives repeated part names a number (" 2", " 3"), so a plan can name each part.
function slr_uniqueNames(parts) {
    var seen = {};
    var i, base, n;
    for (i = 0; i < parts.length; i++) {
        base = parts[i].name;
        if (!seen[base]) { seen[base] = 1; continue; }
        n = ++seen[base];
        while (seen[base + " " + n]) n++;
        parts[i].name = base + " " + n;
        seen[parts[i].name] = 1;
    }
}

// Parts from items listed top to bottom: a clipped or adjustment layer joins the next part below it.
// Each item: { id, name, kind, clipped, bounds, thumb }.
function slr_parts(items) {
    var parts = [];
    var pending = [];
    var i, item, part;
    for (i = 0; i < items.length; i++) {
        item = items[i];
        if (item.clipped || item.kind === "adjustment") { pending.push(item.id); continue; }
        part = { id: item.id, name: item.name, kind: item.kind, layerIds: [item.id].concat(pending), contentIds: [], bounds: item.bounds, thumb: item.thumb };
        pending = [];
        parts.push(part);
    }
    if (pending.length > 0 && parts.length > 0) parts[parts.length - 1].layerIds = parts[parts.length - 1].layerIds.concat(pending);
    slr_uniqueNames(parts);
    return parts;
}

function slr_composition(type, parts, reason) {
    if (reason || parts.length < 2) return { type: "flat", reason: reason || "only one visible part", parts: parts };
    return { type: type, reason: null, parts: parts };
}

// Visible children of a group in the tree, top to bottom, as part items.
function slr_childItems(layers, groupId) {
    var items = [];
    var j, l;
    for (j = 0; j < layers.length; j++) {
        l = layers[j];
        if (l.parentId !== groupId || !l.visible) continue;
        if (l.kind !== "adjustment" && (l.boundsWithEffects.width <= 0 || l.boundsWithEffects.height <= 0)) continue;
        items.push({ id: l.id, name: l.name, kind: l.kind, clipped: l.clipped, bounds: l.boundsWithEffects, thumb: l.thumb });
    }
    return items;
}

// Groups: each visible child is a part (with the layers clipped to it). A lone group inside is looked into.
function slr_groupCompositions(layers) {
    var i, layer, items;
    for (i = 0; i < layers.length; i++) {
        layer = layers[i];
        if (layer.kind !== "group" || !layer.visible) continue;
        items = slr_childItems(layers, layer.id);
        while (items.length === 1 && items[0].kind === "group") items = slr_childItems(layers, items[0].id);
        layer.composition = slr_composition("group", slr_parts(items), null);
    }
}

// The visible layers of the active document (a smart object's contents) with their parent group, top to bottom.
function slr_contentLayers() {
    var count = sl_layerCount();
    var start = sl_hasBackground() ? 0 : 1;
    var stack = [];
    var layers = [];
    var i, d, section, kindNumber, layer;
    for (i = count; i >= start; i--) {
        d = sl_layerByIndex(i);
        section = sl_hasKey(d, "layerSection") ? sl_keyName(d.getEnumerationValue(sl_s("layerSection"))) : "layerSectionContent";
        if (section === "layerSectionEnd") { stack.pop(); continue; }
        kindNumber = sl_hasKey(d, "layerKind") ? d.getInteger(sl_s("layerKind")) : 1;
        layer = {
            id: d.getInteger(sl_s("layerID")),
            name: d.getString(sl_s("name")),
            kind: slr_kind(kindNumber, section),
            parentId: stack.length > 0 ? stack[stack.length - 1].id : null,
            visible: d.getBoolean(sl_s("visible")) && (stack.length > 0 ? stack[stack.length - 1].visible : true),
            ownVisible: d.getBoolean(sl_s("visible")),
            clipped: sl_hasKey(d, "group") ? d.getBoolean(sl_s("group")) : false,
            boundsWithEffects: sl_rect(d.getObjectValue(sl_s("bounds"))),
            thumb: null
        };
        layers.push(layer);
        if (section === "layerSectionStart") stack.push({ id: layer.id, visible: layer.visible });
    }
    return layers;
}

// The parts of the active document: its top-level layers, or inside a lone top-level group.
function slr_topLevelItems() {
    var layers = slr_contentLayers();
    var items = slr_childItems(layers, null);
    while (items.length === 1 && items[0].kind === "group") items = slr_childItems(layers, items[0].id);
    return items;
}

// Reads a smart object's contents once: parts in content pixels, with thumbnails. Closed unsaved.
function slr_readContents(source, layer, thumbsDir, thumbSize, warnings) {
    var contents = sl_openSmartObjectContents(layer.id);
    var parts = [];
    var all = [];
    try {
        // Every layer inside, hidden ones too: states the artist kept for animation (a light's lit glow).
        var listed = slr_contentLayers();
        var k;
        for (k = 0; k < listed.length; k++) {
            all.push({ id: listed[k].id, name: listed[k].name, kind: listed[k].kind, parentId: listed[k].parentId,
                       visible: listed[k].visible, ownVisible: listed[k].ownVisible, bounds: listed[k].boundsWithEffects });
        }
        parts = slr_parts(slr_topLevelItems());
        var i;
        for (i = 0; i < parts.length; i++) {
            parts[i].contentIds = parts[i].layerIds;
            try {
                slr_thumb(contents, { id: parts[i].contentIds[0] }, thumbsDir + "/so" + layer.id + "_" + i + ".png", thumbSize);
                parts[i].thumb = "thumbs/so" + layer.id + "_" + i + ".png";
            } catch (e) {
                warnings.push("No thumbnail for part " + parts[i].name + " of " + layer.name + ": " + sl_errorText(e));
            }
        }
    } finally {
        contents.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = source;
    }
    return { parts: parts, contents: all, width: 0 };
}

// Smart objects: parts from the contents of the run's PSD copy, placed through the smart object's transform.
// Only one moved-and-scaled level is split; anything else counts as flattened art.
function slr_smartCompositions(source, layers, thumbsDir, thumbSize, warnings) {
    var cache = {};
    var i, j, layer, t, p, src, sx, sy, cached, parts, part, b, x0, y0, x1, y1;
    for (i = 0; i < layers.length; i++) {
        layer = layers[i];
        if (layer.kind !== "smartObject" || !layer.visible || !layer.smartObject) continue;
        t = layer.smartObject.transform;
        src = layer.smartObject.source || { file: "", linked: false };
        if (src.linked) { layer.composition = slr_composition("smartObject", [], "linked smart object"); continue; }
        if (!/[.](psd|psb)$/i.test(src.file)) { layer.composition = slr_composition("smartObject", [], "not a layered Photoshop smart object (" + src.file + ")"); continue; }
        if (!t || layer.smartObject.nonAffine) { layer.composition = slr_composition("smartObject", [], "warped or in perspective"); continue; }
        p = t.corners;
        if (Math.abs(p[1] - p[3]) > 0.05 || Math.abs(p[0] - p[6]) > 0.05 || Math.abs(p[2] - p[4]) > 0.05 || Math.abs(p[5] - p[7]) > 0.05 || p[2] <= p[0] || p[7] <= p[1]) {
            layer.composition = slr_composition("smartObject", [], "rotated, skewed or flipped");
            continue;
        }
        cached = cache[layer.smartObject.placedId];
        if (!cached) {
            try {
                var read = slr_readContents(source, layer, thumbsDir, thumbSize, warnings);
                cached = { parts: read.parts, contents: read.contents, error: null };
            } catch (e) {
                warnings.push("Could not open the contents of " + layer.name + ": " + sl_errorText(e));
                cached = { parts: [], error: "contents could not be opened" };
            }
            cache[layer.smartObject.placedId] = cached;
        }
        sx = (p[2] - p[0]) / t.sourceWidth;
        sy = (p[7] - p[1]) / t.sourceHeight;
        parts = [];
        for (j = 0; j < cached.parts.length; j++) {
            part = cached.parts[j];
            b = part.bounds;
            x0 = Math.floor(p[0] + b.x * sx);
            y0 = Math.floor(p[1] + b.y * sy);
            x1 = Math.ceil(p[0] + (b.x + b.width) * sx);
            y1 = Math.ceil(p[1] + (b.y + b.height) * sy);
            parts.push({ id: -(layer.id * 1000 + j + 1), name: part.name, kind: part.kind, layerIds: [], contentIds: part.contentIds,
                         bounds: { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }, thumb: part.thumb });
        }
        layer.composition = slr_composition("smartObject", parts, cached.error);
        if (cached.contents) layer.smartObject.contents = cached.contents;
    }
}

function slr_adjustment(d) {
    if (!sl_hasKey(d, "adjustment")) return null;
    var list = d.getList(sl_s("adjustment"));
    if (list.count === 0) return null;
    var desc = list.getObjectValue(0);
    var out = { type: sl_keyName(list.getObjectType(0)), params: sl_descToObject(desc) };
    if (out.type === "solidColorLayer" && sl_hasKey(desc, "color")) out.color = sl_colorHex(desc.getObjectValue(sl_s("color")));
    var gradient = sl_gradient(desc);
    if (gradient !== null) out.gradient = gradient;
    return out;
}

// The artboard rectangle of an artboard group, in document pixels; null for an ordinary group.
function slr_artboardRect(d) {
    if (!sl_hasKey(d, "artboardEnabled") || !d.getBoolean(sl_s("artboardEnabled"))) return null;
    if (!sl_hasKey(d, "artboard")) return null;
    var ab = d.getObjectValue(sl_s("artboard"));
    if (!sl_hasKey(ab, "artboardRect")) return null;
    var r = ab.getObjectValue(sl_s("artboardRect"));
    function side(charId, strId) {
        if (r.hasKey(sl_c(charId))) return r.getDouble(sl_c(charId));
        return r.getDouble(sl_s(strId));
    }
    var left = side("Left", "left"), top = side("Top ", "top"), right = side("Rght", "right"), bottom = side("Btom", "bottom");
    return { x: Math.round(left), y: Math.round(top), width: Math.round(right) - Math.round(left), height: Math.round(bottom) - Math.round(top) };
}

function slr_readLayer(d, stack, order, resolution, warnings) {
    var section = sl_hasKey(d, "layerSection") ? sl_keyName(d.getEnumerationValue(sl_s("layerSection"))) : "layerSectionContent";
    var kindNumber = sl_hasKey(d, "layerKind") ? d.getInteger(sl_s("layerKind")) : 1;
    var parent = stack.length > 0 ? stack[stack.length - 1] : null;
    var names = [];
    var i;
    for (i = 0; i < stack.length; i++) names.push(stack[i].name);

    var layer = {
        id: d.getInteger(sl_s("layerID")),
        name: d.getString(sl_s("name")),
        parentId: parent ? parent.id : null,
        path: names.join("/"),
        order: order,
        kind: slr_kind(kindNumber, section),
        isBackground: sl_hasKey(d, "background") ? d.getBoolean(sl_s("background")) : false,
        visible: d.getBoolean(sl_s("visible")) && (parent ? parent.visible : true),
        opacity: Math.round(d.getInteger(sl_s("opacity")) / 255 * 100),
        fillOpacity: sl_hasKey(d, "fillOpacity") ? Math.round(d.getInteger(sl_s("fillOpacity")) / 255 * 100) : 100,
        blendMode: sl_keyName(d.getEnumerationValue(sl_s("mode"))),
        clipped: sl_hasKey(d, "group") ? d.getBoolean(sl_s("group")) : false,
        hasMask: sl_hasKey(d, "hasUserMask") ? d.getBoolean(sl_s("hasUserMask")) : false,
        hasVectorMask: sl_hasKey(d, "hasVectorMask") ? d.getBoolean(sl_s("hasVectorMask")) : false,
        bounds: sl_rect(d.getObjectValue(sl_s(sl_hasKey(d, "boundsNoEffects") ? "boundsNoEffects" : "bounds"))),
        boundsWithEffects: sl_rect(d.getObjectValue(sl_s("bounds"))),
        effects: slr_effects(d),
        shape: null,
        smartObject: null,
        text: null,
        adjustment: null,
        thumb: null,
        composition: null,
        artboard: null
    };
    try {
        if (section === "layerSectionStart") layer.artboard = slr_artboardRect(d);
    } catch (eArt) { /* not an artboard */ }

    try {
        if (layer.kind === "shape") layer.shape = sl_shapeInfo(d);
        if (layer.kind === "smartObject" && sl_hasKey(d, "smartObjectMore")) {
            layer.smartObject = slr_smartObject(d);
            layer.smartObject.source = sl_smartObjectSource(d);
            layer.smartObject.nonAffine = sl_smartObjectNonAffine(d);
        }
        if (layer.kind === "text" && sl_hasKey(d, "textKey")) layer.text = slr_text(d, resolution);
        if (layer.kind === "shape" || layer.kind === "fill" || layer.kind === "adjustment") layer.adjustment = slr_adjustment(d);
    } catch (e) {
        warnings.push("Layer " + layer.id + " (" + layer.name + "): details incomplete: " + sl_errorText(e));
    }
    return layer;
}

function slr_thumb(source, layer, path, maxSide) {
    sl_selectLayerById(layer.id);
    var thumbDoc = sl_newDocumentFromSelectedLayer("sl_thumb");
    try {
        sl_preparePng(thumbDoc);
        thumbDoc.trim(TrimType.TRANSPARENT, true, true, true, true);
        sl_fitImage(thumbDoc, maxSide);
        sl_savePng(thumbDoc, path);
    } finally {
        thumbDoc.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = source;
    }
}

function slr_documentThumb(source, path, maxSide) {
    var copy = source.duplicate("sl_document", true);
    try {
        sl_preparePng(copy);
        sl_fitImage(copy, maxSide);
        sl_savePng(copy, path);
    } finally {
        copy.close(SaveOptions.DONOTSAVECHANGES);
        app.activeDocument = source;
    }
}

function slr_progress(job, done, total, message) {
    try { sl_writeAtomic(job.progressPath, JSON.stringify({ done: done, total: total, message: message })); }
    catch (e) { /* progress is best effort */ }
}

function slr_modeName(mode) {
    var s = String(mode);
    return s.indexOf(".") >= 0 ? s.substring(s.indexOf(".") + 1) : s;
}

function slr_bits(doc) {
    if (doc.bitsPerChannel === BitsPerChannelType.SIXTEEN) return 16;
    if (doc.bitsPerChannel === BitsPerChannelType.THIRTYTWO) return 32;
    if (doc.bitsPerChannel === BitsPerChannelType.ONE) return 1;
    return 8;
}

function slr_checkMissingFonts(layers, warnings) {
    var seen = {};
    var i, j, runs, font;
    for (i = 0; i < layers.length; i++) {
        if (!layers[i].text) continue;
        runs = layers[i].text.runs;
        for (j = 0; j < runs.length; j++) {
            font = runs[j].font;
            if (!font || seen[font]) continue;
            seen[font] = true;
            try { app.fonts.getByName(font); }
            catch (e) { warnings.push("Font not installed on this machine: " + font); }
        }
    }
}

function slr_main(job, warnings) {
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

    var doc = app.open(copy);
    try {
        app.activeDocument = doc;
        var document = {
            width: Math.round(doc.width.as("px")),
            height: Math.round(doc.height.as("px")),
            mode: slr_modeName(doc.mode),
            colorProfile: "",
            resolution: sl_round(doc.resolution, 4),
            bitsPerChannel: slr_bits(doc),
            thumb: null
        };
        try { document.colorProfile = doc.colorProfileName; } catch (e) { document.colorProfile = ""; }

        // Action Manager indexes run bottom (1, or 0 for a background) to top. Walking from the top
        // gives document order: a group's start comes before its children, its end marker after.
        var count = sl_layerCount();
        var start = sl_hasBackground() ? 0 : 1;
        var stack = [];
        var layers = [];
        var d, section, layer;
        for (i = count; i >= start; i--) {
            d = sl_layerByIndex(i);
            section = sl_hasKey(d, "layerSection") ? sl_keyName(d.getEnumerationValue(sl_s("layerSection"))) : "layerSectionContent";
            if (section === "layerSectionEnd") {
                stack.pop();
                continue;
            }
            layer = slr_readLayer(d, stack, layers.length, document.resolution, warnings);
            layers.push(layer);
            if (section === "layerSectionStart") stack.push({ id: layer.id, name: layer.name, visible: layer.visible });
            if (i % 10 === 0) slr_progress(job, count - i, count * 2, "Reading layers");
        }

        slr_checkMissingFonts(layers, warnings);

        var thumbsDir = new Folder(job.thumbsDir);
        if (!thumbsDir.exists) thumbsDir.create();
        var targets = [];
        for (i = 0; i < layers.length; i++) {
            layer = layers[i];
            if (!layer.visible || layer.kind === "adjustment") continue;
            if (layer.boundsWithEffects.width <= 0 || layer.boundsWithEffects.height <= 0) continue;
            targets.push(layer);
        }
        for (i = 0; i < targets.length; i++) {
            layer = targets[i];
            try {
                slr_thumb(doc, layer, thumbsDir.fsName + "/" + layer.id + ".png", job.thumbSize);
                layer.thumb = "thumbs/" + layer.id + ".png";
            } catch (e) {
                warnings.push("No thumbnail for layer " + layer.id + " (" + layer.name + "): " + sl_errorText(e));
            }
            if (i % 5 === 0) slr_progress(job, count + Math.round(i / targets.length * count), count * 2, "Thumbnails " + i + "/" + targets.length);
        }

        // Composition: groups from the tree, smart objects from their contents in this copy.
        slr_groupCompositions(layers);
        slr_progress(job, count * 2, count * 2, "Smart object contents");
        slr_smartCompositions(doc, layers, thumbsDir.fsName, job.thumbSize, warnings);

        try {
            slr_documentThumb(doc, thumbsDir.fsName + "/_document.png", job.documentThumbSize);
            document.thumb = "thumbs/_document.png";
        } catch (e) {
            warnings.push("No document thumbnail: " + sl_errorText(e));
        }
        try {
            slr_documentThumb(doc, job.compositePath, 100000);
            document.composite = "composite.png";
        } catch (e) {
            warnings.push("No composite: " + sl_errorText(e));
        }

        sl_writeAtomic(job.treePath, JSON.stringify({ schema: 1, document: document, layers: layers }, null, 2));
        slr_progress(job, count * 2, count * 2, "Done");
        return layers.length;
    } finally {
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
}

(function () {
    var job = null;
    var status = "error";
    var errors = [];
    var warnings = [];
    var layerCount = 0;
    var savedUnits = app.preferences.rulerUnits;
    var savedDialogs = app.displayDialogs;
    try {
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        job = JSON.parse(sl_readText(SL_JOB_PATH));
        layerCount = slr_main(job, warnings);
        status = "ok";
    } catch (e) {
        errors.push(sl_errorText(e));
    } finally {
        app.preferences.rulerUnits = savedUnits;
        app.displayDialogs = savedDialogs;
        var donePath = job && job.donePath ? job.donePath : new File(SL_JOB_PATH).parent.fsName + "/ps_done.json";
        try {
            sl_writeAtomic(donePath, JSON.stringify({
                status: status,
                errors: errors,
                warnings: warnings,
                layers: layerCount,
                photoshopVersion: app.version
            }, null, 2));
        } catch (e2) {
            // Nothing else can report this; SLYCE reports a missing result.
        }
    }
}());
