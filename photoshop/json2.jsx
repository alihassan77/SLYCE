// json2.jsx - JSON.stringify / JSON.parse for ExtendScript (ES3), which has no JSON object. Part of SLYCE.
// Follows Douglas Crockford's public-domain json2.js and its validate-then-eval parse.
// Object keys keep insertion order, so output is deterministic.

if (typeof JSON !== "object") {
    JSON = {};
}

(function () {
    // Only ASCII control characters, quotes and backslashes are escaped (ExtendScript cannot parse
    // non-ASCII ranges in regex classes). Files are written as UTF-8, so other characters stay as they are.
    var escapable = /[\\\"\x00-\x1f\x7f]/g;
    var meta = { "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", "\"": "\\\"", "\\": "\\\\" };

    function hex4(ch) {
        return "\\u" + ("0000" + ch.charCodeAt(0).toString(16)).slice(-4);
    }

    function quote(string) {
        escapable.lastIndex = 0;
        if (!escapable.test(string)) return "\"" + string + "\"";
        return "\"" + string.replace(escapable, function (a) {
            var c = meta[a];
            return typeof c === "string" ? c : hex4(a);
        }) + "\"";
    }

    function str(value, indent, gap) {
        var i, k, v, parts, inner;
        if (value === null || value === undefined) return "null";
        switch (typeof value) {
        case "string":
            return quote(value);
        case "number":
            return isFinite(value) ? String(value) : "null";
        case "boolean":
            return String(value);
        case "object":
            inner = indent + gap;
            parts = [];
            if (value instanceof Array) {
                for (i = 0; i < value.length; i++) parts.push(str(value[i], inner, gap));
                if (parts.length === 0) return "[]";
                return gap ? "[\n" + inner + parts.join(",\n" + inner) + "\n" + indent + "]" : "[" + parts.join(",") + "]";
            }
            for (k in value) {
                if (Object.prototype.hasOwnProperty.call(value, k)) {
                    v = value[k];
                    if (v === undefined || typeof v === "function") continue;
                    parts.push(quote(k) + (gap ? ": " : ":") + str(v, inner, gap));
                }
            }
            if (parts.length === 0) return "{}";
            return gap ? "{\n" + inner + parts.join(",\n" + inner) + "\n" + indent + "}" : "{" + parts.join(",") + "}";
        }
        return "null";
    }

    if (typeof JSON.stringify !== "function") {
        JSON.stringify = function (value, replacer, space) {
            var gap = "";
            if (typeof space === "number") gap = new Array(space + 1).join(" ");
            else if (typeof space === "string") gap = space;
            return str(value, "", gap);
        };
    }

    if (typeof JSON.parse !== "function") {
        JSON.parse = function (text) {
            text = String(text);
            if (/^[\],:{}\s]*$/.test(text
                    .replace(/\\(?:["\\\/bfnrt]|u[0-9a-fA-F]{4})/g, "@")
                    .replace(/"[^"\\\n\r]*"|true|false|null|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?/g, "]")
                    .replace(/(?:^|:|,)(?:\s*\[)+/g, ""))) {
                return eval("(" + text + ")");
            }
            throw new SyntaxError("JSON.parse: invalid JSON");
        };
    }
}());
