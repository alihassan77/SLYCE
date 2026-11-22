// slyce_common.jsx - SLYCE shared helpers. Every Action Manager call (executeAction / executeActionGet)
// lives here behind a small function.
// ExtendScript (ES3): var only, no JSON object (json2.jsx), no Array.prototype.map/indexOf.

function sl_c(s) { return charIDToTypeID(s); }
function sl_s(s) { return stringIDToTypeID(s); }

function sl_keyName(id) {
    var s = typeIDToStringID(id);
    return s ? s : typeIDToCharID(id);
}

function sl_round(v, digits) {
    var f = Math.pow(10, digits);
    return Math.round(v * f) / f;
}

function sl_errorText(e) {
    var text = e && e.message ? e.message : String(e);
    if (e && e.line) text += " (line " + e.line + (e.fileName ? " of " + String(e.fileName).split("/").pop() : "") + ")";
    return text;
}

// ---------- files ----------

function sl_readText(path) {
    var f = new File(path);
    f.encoding = "UTF-8";
    if (!f.open("r")) throw new Error("Cannot read " + path);
    var text = f.read();
    f.close();
    return text;
}

function sl_writeText(path, text) {
    var f = new File(path);
    f.encoding = "UTF-8";
    f.lineFeed = "Unix";
    if (!f.open("w")) throw new Error("Cannot write " + path);
    f.write(text);
    f.close();
}

// Writes through a .tmp file and a rename, so readers never see half a file.
function sl_writeAtomic(path, text) {
    var target = new File(path);
    var tmp = new File(path + ".tmp");
    sl_writeText(tmp.fsName, text);
    if (target.exists) target.remove();
    if (!tmp.rename(target.name)) throw new Error("Cannot rename " + tmp.fsName);
}

// ---------- descriptor conversion ----------

function sl_hasKey(desc, key) { return desc.hasKey(sl_s(key)); }

function sl_colorHex(colorDesc) {
    function channel(keys) {
        var i;
        for (i = 0; i < keys.length; i++) {
            if (colorDesc.hasKey(sl_s(keys[i]))) {
                var v = colorDesc.getDouble(sl_s(keys[i]));
                v = Math.max(0, Math.min(255, Math.round(v)));
                return ("0" + v.toString(16)).slice(-2);
            }
        }
        return null;
    }
    var r = channel(["red"]);
    var g = channel(["grain", "green"]);
    var b = channel(["blue"]);
    if (r === null || g === null || b === null) return null;
    return "#" + (r + g + b).toUpperCase();
}

// Reads one value from an ActionDescriptor (key = type id) or an ActionList (key = index).
function sl_readValue(src, key) {
    var type = src.getType(key);
    switch (type) {
    case DescValueType.INTEGERTYPE: return src.getInteger(key);
    case DescValueType.LARGEINTEGERTYPE: return src.getLargeInteger(key);
    case DescValueType.DOUBLETYPE: return sl_round(src.getDouble(key), 4);
    case DescValueType.UNITDOUBLE: return { value: sl_round(src.getUnitDoubleValue(key), 4), unit: sl_keyName(src.getUnitDoubleType(key)) };
    case DescValueType.STRINGTYPE: return src.getString(key);
    case DescValueType.BOOLEANTYPE: return src.getBoolean(key);
    case DescValueType.ENUMERATEDTYPE: return sl_keyName(src.getEnumerationValue(key));
    case DescValueType.CLASSTYPE: return sl_keyName(src.getClass(key));
    case DescValueType.LISTTYPE: return sl_listToArray(src.getList(key));
    case DescValueType.OBJECTTYPE:
        var className = sl_keyName(src.getObjectType(key));
        var obj = src.getObjectValue(key);
        if (className === "RGBColor") {
            var hex = sl_colorHex(obj);
            if (hex !== null) return hex;
        }
        return sl_descToObject(obj, className);
    }
    // Aliases (file paths), references and raw data are machine-specific or opaque: left out.
    return null;
}

function sl_descToObject(desc, className) {
    var o = {};
    var i, key;
    if (className) o._class = className;
    for (i = 0; i < desc.count; i++) {
        key = desc.getKey(i);
        o[sl_keyName(key)] = sl_readValue(desc, key);
    }
    return o;
}

function sl_listToArray(list) {
    var a = [];
    var i;
    for (i = 0; i < list.count; i++) a.push(sl_readValue(list, i));
    return a;
}

// Bounds descriptor (top/left/bottom/right) -> {x, y, width, height} in document pixels.
function sl_rect(desc) {
    var left = desc.getUnitDoubleValue(sl_s("left"));
    var top = desc.getUnitDoubleValue(sl_s("top"));
    var right = desc.getUnitDoubleValue(sl_s("right"));
    var bottom = desc.getUnitDoubleValue(sl_s("bottom"));
    return {
        x: Math.round(left),
        y: Math.round(top),
        width: Math.round(right) - Math.round(left),
        height: Math.round(bottom) - Math.round(top)
    };
}

// ---------- document and layers ----------

function sl_documentGet(key) {
    var ref = new ActionReference();
    ref.putProperty(sl_c("Prpr"), sl_s(key));
    ref.putEnumerated(sl_c("Dcmn"), sl_c("Ordn"), sl_c("Trgt"));
    return executeActionGet(ref);
}

function sl_layerCount() {
    return sl_documentGet("numberOfLayers").getInteger(sl_s("numberOfLayers"));
}

function sl_hasBackground() {
    try {
        var ref = new ActionReference();
        ref.putProperty(sl_c("Prpr"), sl_s("background"));
        ref.putIndex(sl_c("Lyr "), 0);
        return executeActionGet(ref).getBoolean(sl_s("background"));
    } catch (e) {
        return false;
    }
}

// Layer descriptor by Action Manager index (bottom = 1, or 0 for a background layer).
function sl_layerByIndex(index) {
    var ref = new ActionReference();
    ref.putIndex(sl_c("Lyr "), index);
    return executeActionGet(ref);
}

function sl_selectLayerById(id) {
    var ref = new ActionReference();
    ref.putIdentifier(sl_c("Lyr "), id);
    var desc = new ActionDescriptor();
    desc.putReference(sl_c("null"), ref);
    desc.putBoolean(sl_c("MkVs"), false);
    executeAction(sl_c("slct"), desc, DialogModes.NO);
}

// Layer > Duplicate Layer to a new document; the new document becomes active.
function sl_newDocumentFromSelectedLayer(name) {
    var desc = new ActionDescriptor();
    var docRef = new ActionReference();
    docRef.putClass(sl_c("Dcmn"));
    desc.putReference(sl_c("null"), docRef);
    desc.putString(sl_c("Nm  "), name);
    var layerRef = new ActionReference();
    layerRef.putEnumerated(sl_c("Lyr "), sl_c("Ordn"), sl_c("Trgt"));
    desc.putReference(sl_c("Usng"), layerRef);
    executeAction(sl_c("Mk  "), desc, DialogModes.NO);
    return app.activeDocument;
}

// Image > Image Size so the longest side is maxSide px, scaling layer styles too.
function sl_fitImage(doc, maxSide) {
    var w = doc.width.as("px");
    var h = doc.height.as("px");
    if (Math.max(w, h) <= maxSide) return;
    var desc = new ActionDescriptor();
    if (w >= h) desc.putUnitDouble(sl_c("Wdth"), sl_c("#Pxl"), maxSide);
    else desc.putUnitDouble(sl_c("Hght"), sl_c("#Pxl"), maxSide);
    desc.putBoolean(sl_s("scaleStyles"), true);
    desc.putBoolean(sl_c("CnsP"), true);
    desc.putEnumerated(sl_c("Intr"), sl_c("Intp"), sl_c("Bcbc"));
    executeAction(sl_c("ImgS"), desc, DialogModes.NO);
}

// Image > Image Size to exactly width x height px (not proportional), scaling layer styles too.
function sl_resizeImage(width, height) {
    var desc = new ActionDescriptor();
    desc.putUnitDouble(sl_c("Wdth"), sl_c("#Pxl"), width);
    desc.putUnitDouble(sl_c("Hght"), sl_c("#Pxl"), height);
    desc.putBoolean(sl_s("scaleStyles"), true);
    desc.putBoolean(sl_c("CnsP"), false);
    desc.putEnumerated(sl_c("Intr"), sl_c("Intp"), sl_c("Bcbc"));
    executeAction(sl_c("ImgS"), desc, DialogModes.NO);
}

// Opens a smart object's contents as their own (temporary) document, which becomes active
// The caller closes it with DONOTSAVECHANGES, so the smart object and the PSD copy are never changed.
function sl_openSmartObjectContents(layerId) {
    sl_selectLayerById(layerId);
    executeAction(sl_s("placedLayerEditContents"), new ActionDescriptor(), DialogModes.NO);
    return app.activeDocument;
}

// Source of a smart object layer: its embedded file name and whether it is linked to an outside file.
function sl_smartObjectSource(layerDesc) {
    var out = { file: "", linked: false };
    if (!sl_hasKey(layerDesc, "smartObject")) return out;
    var so = layerDesc.getObjectValue(sl_s("smartObject"));
    if (sl_hasKey(so, "fileReference")) out.file = so.getString(sl_s("fileReference"));
    if (sl_hasKey(so, "linked")) out.linked = so.getBoolean(sl_s("linked"));
    return out;
}

// True when a custom envelope warp's mesh points sit on the even uOrder x vOrder grid of its bounds.
function sl_identityWarp(warp) {
    try {
        var b = warp.getObjectValue(sl_s("bounds"));
        var left = b.getDouble(sl_s("left")), top = b.getDouble(sl_s("top"));
        var right = b.getDouble(sl_s("right")), bottom = b.getDouble(sl_s("bottom"));
        var u = warp.getInteger(sl_s("uOrder")), v = warp.getInteger(sl_s("vOrder"));
        var pts = warp.getObjectValue(sl_s("customEnvelopeWarp")).getList(sl_s("meshPoints"));
        if (pts.count !== u * v) return false;
        var i;
        for (i = 0; i < pts.count; i++) {
            var p = pts.getObjectValue(i);
            var x = p.getUnitDoubleValue(sl_s("horizontal")), y = p.getUnitDoubleValue(sl_s("vertical"));
            var ex = left + (right - left) * (i % u) / (u - 1);
            var ey = top + (bottom - top) * Math.floor(i / u) / (v - 1);
            if (Math.abs(x - ex) > 0.5 || Math.abs(y - ey) > 0.5) return false;
        }
        return true;
    } catch (e) {
        return false;
    }
}

// True when a smart object is warped or in perspective (its non-affine corners differ from the affine ones).
function sl_smartObjectNonAffine(layerDesc) {
    if (!sl_hasKey(layerDesc, "smartObjectMore")) return false;
    var so = layerDesc.getObjectValue(sl_s("smartObjectMore"));
    if (sl_hasKey(so, "warp")) {
        var warp = so.getObjectValue(sl_s("warp"));
        var style = sl_hasKey(warp, "warpStyle") ? sl_keyName(warp.getEnumerationValue(sl_s("warpStyle"))) : "warpNone";
        // A custom warp whose mesh is still the even grid (left behind after Free Transform) bends nothing.
        if (style === "warpCustom" && sl_identityWarp(warp)) style = "warpNone";
        if (style !== "warpNone") return true;
    }
    if (!sl_hasKey(so, "transform") || !sl_hasKey(so, "nonAffineTransform")) return false;
    var a = so.getList(sl_s("transform"));
    var b = so.getList(sl_s("nonAffineTransform"));
    var i;
    for (i = 0; i < a.count && i < b.count; i++) if (Math.abs(a.getDouble(i) - b.getDouble(i)) > 0.5) return true;
    return false;
}

// Live-shape origination of a shape layer: rect, roundedRect, ellipse or path.
// Read from the layer descriptor's "keyOriginType" list (one entry per live shape in the layer).
// Seen in Photoshop 2024: 1 rectangle, 2 rounded rectangle, 5 ellipse; anything else is a free path.
function sl_shapeInfo(layerDesc) {
    var out = { origination: "path", components: 0, originType: null, radii: null };
    if (!sl_hasKey(layerDesc, "keyOriginType")) return out;
    var origins = layerDesc.getList(sl_s("keyOriginType"));
    out.components = origins.count;
    if (origins.count !== 1) return out;

    var origin = origins.getObjectValue(0);
    if (!sl_hasKey(origin, "keyOriginType")) return out;
    out.originType = origin.getInteger(sl_s("keyOriginType"));
    if (out.originType === 1) out.origination = "rect";
    else if (out.originType === 2) out.origination = "roundedRect";
    else if (out.originType === 5) out.origination = "ellipse";

    if (sl_hasKey(origin, "keyOriginRRectRadii")) {
        var r = origin.getObjectValue(sl_s("keyOriginRRectRadii"));
        out.radii = {
            topLeft: sl_round(r.getUnitDoubleValue(sl_s("topLeft")), 2),
            topRight: sl_round(r.getUnitDoubleValue(sl_s("topRight")), 2),
            bottomRight: sl_round(r.getUnitDoubleValue(sl_s("bottomRight")), 2),
            bottomLeft: sl_round(r.getUnitDoubleValue(sl_s("bottomLeft")), 2)
        };
    }
    return out;
}

// Gradient of a gradient overlay / gradient stroke / gradient fill layer, normalised.
function sl_gradient(desc) {
    if (!sl_hasKey(desc, "gradient")) return null;
    var g = desc.getObjectValue(sl_s("gradient"));
    var out = {
        type: sl_hasKey(desc, "type") ? sl_keyName(desc.getEnumerationValue(sl_s("type"))) : "linear",
        angle: sl_hasKey(desc, "angle") ? sl_round(desc.getUnitDoubleValue(sl_s("angle")), 4) : 90,
        reverse: sl_hasKey(desc, "reverse") ? desc.getBoolean(sl_s("reverse")) : false,
        scale: sl_hasKey(desc, "scale") ? sl_round(desc.getUnitDoubleValue(sl_s("scale")), 4) : 100,
        alignWithLayer: sl_hasKey(desc, "align") ? desc.getBoolean(sl_s("align")) : true,
        opacity: sl_hasKey(desc, "opacity") ? sl_round(desc.getUnitDoubleValue(sl_s("opacity")), 4) : 100,
        blendMode: sl_hasKey(desc, "mode") ? sl_keyName(desc.getEnumerationValue(sl_s("mode"))) : "normal",
        form: sl_hasKey(g, "gradientForm") ? sl_keyName(g.getEnumerationValue(sl_s("gradientForm"))) : "customStops",
        colorStops: [],
        alphaStops: []
    };
    var i, stop;
    if (sl_hasKey(g, "colors")) {
        var colors = g.getList(sl_s("colors"));
        for (i = 0; i < colors.count; i++) {
            stop = colors.getObjectValue(i);
            out.colorStops.push({
                location: sl_round(stop.getInteger(sl_s("location")) / 4096, 4),
                midpoint: sl_round(stop.getInteger(sl_s("midpoint")) / 100, 4),
                color: sl_hasKey(stop, "color") ? sl_colorHex(stop.getObjectValue(sl_s("color"))) : null,
                stopType: sl_hasKey(stop, "type") ? sl_keyName(stop.getEnumerationValue(sl_s("type"))) : "userStop"
            });
        }
    }
    if (sl_hasKey(g, "transparency")) {
        var alphas = g.getList(sl_s("transparency"));
        for (i = 0; i < alphas.count; i++) {
            stop = alphas.getObjectValue(i);
            out.alphaStops.push({
                location: sl_round(stop.getInteger(sl_s("location")) / 4096, 4),
                midpoint: sl_round(stop.getInteger(sl_s("midpoint")) / 100, 4),
                opacity: sl_round(stop.getUnitDoubleValue(sl_s("opacity")), 4)
            });
        }
    }
    return out;
}

// ---------- export helpers ----------

// Selects the layers with these IDs: the first replaces the selection, the rest are added to it.
function sl_selectLayers(ids) {
    var i;
    for (i = 0; i < ids.length; i++) {
        var ref = new ActionReference();
        ref.putIdentifier(sl_c("Lyr "), ids[i]);
        var desc = new ActionDescriptor();
        desc.putReference(sl_c("null"), ref);
        if (i > 0) desc.putEnumerated(sl_s("selectionModifier"), sl_s("selectionModifierType"), sl_s("addToSelection"));
        desc.putBoolean(sl_c("MkVs"), false);
        executeAction(sl_c("slct"), desc, DialogModes.NO);
    }
}

// Layer fill opacity in percent (0 keeps the layer style and drops the fill).
function sl_setFillOpacity(layerId, percent) {
    var ref = new ActionReference();
    ref.putIdentifier(sl_c("Lyr "), layerId);
    var desc = new ActionDescriptor();
    desc.putReference(sl_c("null"), ref);
    var to = new ActionDescriptor();
    to.putUnitDouble(sl_s("fillOpacity"), sl_c("#Prc"), percent);
    desc.putObject(sl_c("T   "), sl_c("Lyr "), to);
    executeAction(sl_c("setd"), desc, DialogModes.NO);
}

// Shows or hides all layer effects of a layer (Layer > Layer Style > Hide All Effects).
function sl_setEffectsVisible(layerId, visible) {
    sl_selectLayerById(layerId);
    var ref = new ActionReference();
    ref.putClass(sl_c("Lefx"));
    ref.putEnumerated(sl_c("Lyr "), sl_c("Ordn"), sl_c("Trgt"));
    var list = new ActionList();
    list.putReference(ref);
    var desc = new ActionDescriptor();
    desc.putList(sl_c("null"), list);
    executeAction(sl_c(visible ? "Shw " : "Hd  "), desc, DialogModes.NO);
}

// Shows or hides one layer effect (the eye icon next to e.g. Gradient Overlay). effectKey is the
// layerEffects key: gradientFill, solidFill, frameFX, dropShadow ... The first effect of that kind.
function sl_setEffectVisible(layerId, effectKey, visible) {
    sl_selectLayerById(layerId);
    var ref = new ActionReference();
    ref.putIndex(sl_s(effectKey), 1);
    ref.putEnumerated(sl_c("Lyr "), sl_c("Ordn"), sl_c("Trgt"));
    var list = new ActionList();
    list.putReference(ref);
    var desc = new ActionDescriptor();
    desc.putList(sl_c("null"), list);
    executeAction(sl_c(visible ? "Shw " : "Hd  "), desc, DialogModes.NO);
}

// Shows or hides a layer (its eye icon).
function sl_setLayerVisible(layerId, visible) {
    var ref = new ActionReference();
    ref.putIdentifier(sl_c("Lyr "), layerId);
    var list = new ActionList();
    list.putReference(ref);
    var desc = new ActionDescriptor();
    desc.putList(sl_c("null"), list);
    executeAction(sl_c(visible ? "Shw " : "Hd  "), desc, DialogModes.NO);
}

// Bounds of the active layer's pixels in document coordinates.
function sl_activeLayerBounds() {
    var ref = new ActionReference();
    ref.putProperty(sl_c("Prpr"), sl_s("bounds"));
    ref.putEnumerated(sl_c("Lyr "), sl_c("Ordn"), sl_c("Trgt"));
    return sl_rect(executeActionGet(ref).getObjectValue(sl_s("bounds")));
}

// Rotates the selected layers by angle degrees (clockwise) around (cx, cy) in document pixels. A smart
// object re-renders from its source, so turning it back loses nothing; adjustment layers are not selected.
function sl_rotateSelected(angle, cx, cy) {
    var d = new ActionDescriptor();
    var r = new ActionReference();
    r.putEnumerated(sl_c("Lyr "), sl_c("Ordn"), sl_c("Trgt"));
    d.putReference(sl_c("null"), r);
    d.putEnumerated(sl_c("FTcs"), sl_c("QCSt"), sl_s("QCSIndependent"));
    var p = new ActionDescriptor();
    p.putUnitDouble(sl_c("Hrzn"), sl_c("#Pxl"), cx);
    p.putUnitDouble(sl_c("Vrtc"), sl_c("#Pxl"), cy);
    d.putObject(sl_c("Pstn"), sl_c("Pnt "), p);
    d.putUnitDouble(sl_c("Angl"), sl_c("#Ang"), angle);
    d.putEnumerated(sl_c("Intr"), sl_c("Intp"), sl_c("Bcbc"));
    executeAction(sl_c("Trnf"), d, DialogModes.NO);
}

// IDs of every layer in the document that can be transformed (pixel, shape, smart object, text, fill),
// skipping groups and adjustment layers.
function sl_transformableLayerIds(container) {
    var out = [];
    var i;
    for (i = 0; i < container.layers.length; i++) {
        var l = container.layers[i];
        if (l.typename === "LayerSet") {
            out = out.concat(sl_transformableLayerIds(l));
        } else if (l.kind !== LayerKind.BRIGHTNESSCONTRAST && l.kind !== LayerKind.LEVELS && l.kind !== LayerKind.CURVES &&
                   l.kind !== LayerKind.HUESATURATION && l.kind !== LayerKind.COLORBALANCE && l.kind !== LayerKind.BLACKANDWHITE &&
                   l.kind !== LayerKind.CHANNELMIXER && l.kind !== LayerKind.PHOTOFILTER && l.kind !== LayerKind.EXPOSURE &&
                   l.kind !== LayerKind.INVERSION && l.kind !== LayerKind.POSTERIZE && l.kind !== LayerKind.THRESHOLD &&
                   l.kind !== LayerKind.SELECTIVECOLOR && l.kind !== LayerKind.VIBRANCE && l.kind !== LayerKind.GRADIENTMAP) {
            out.push(l.id);
        }
    }
    return out;
}

// 8-bit RGB, so the PNG is PNG-24 with transparency.
function sl_preparePng(doc) {
    if (doc.bitsPerChannel !== BitsPerChannelType.EIGHT) doc.bitsPerChannel = BitsPerChannelType.EIGHT;
    if (doc.mode !== DocumentMode.RGB) doc.changeMode(ChangeMode.RGB);
}

function sl_savePng(doc, path) {
    var options = new PNGSaveOptions();
    options.compression = 6;
    options.interlaced = false;
    doc.saveAs(new File(path), options, true, Extension.LOWERCASE);
}
