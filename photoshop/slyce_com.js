// slyce_com.js - Windows Script Host (JScript) helper for SLYCE, not an ExtendScript file.
// Runs a JSX in Photoshop over COM through IDispatch, which needs no registered type library.
// Usage: cscript //nologo //E:jscript slyce_com.js <ProgID> <script.jsx>
// Exit codes: 0 script finished, 1 Photoshop could not be reached, 2 DoJavaScriptFile failed,
// 3 Photoshop stayed busy (a dialog open, text being edited, a tool in use) for the whole retry time.

var RETRY_LATER = 0x8001010A;   // RPC_E_SERVERCALL_RETRYLATER: Photoshop is busy
var CALL_REJECTED = 0x80010001; // RPC_E_CALL_REJECTED
var BUSY_RETRY_MS = 60000;
var BUSY_STEP_MS = 2000;

function hresult(e) {
    return (e.number < 0 ? e.number + 0x100000000 : e.number);
}

function describe(e) {
    var text = e.message || e.description || "";
    return "0x" + hresult(e).toString(16).toUpperCase() + (text ? " " + text : "");
}

var args = WScript.Arguments;
var ids = [args(0), "Photoshop.Application"];
var app = null;
var errors = [];
var i;
for (i = 0; i < ids.length && app === null; i++) {
    try {
        app = new ActiveXObject(ids[i]);
    } catch (e) {
        errors.push(ids[i] + ": " + describe(e));
    }
}
if (app === null) {
    WScript.StdErr.WriteLine("Cannot reach Photoshop over COM. " + errors.join(" / "));
    WScript.Quit(1);
}

var waited = 0;
while (true) {
    try {
        app.DoJavaScriptFile(args(1));
        WScript.Quit(0);
    } catch (e) {
        var code = hresult(e);
        if ((code === RETRY_LATER || code === CALL_REJECTED) && waited < BUSY_RETRY_MS) {
            WScript.Sleep(BUSY_STEP_MS);
            waited += BUSY_STEP_MS;
            continue;
        }
        if (code === RETRY_LATER || code === CALL_REJECTED) {
            WScript.StdErr.WriteLine("Photoshop stayed busy for " + (BUSY_RETRY_MS / 1000) + " s and did not accept the script. " +
                "Close any open dialog, finish or cancel text editing (Enter or Esc), and let the current tool finish, then run the command again.");
            WScript.Quit(3);
        }
        WScript.StdErr.WriteLine("DoJavaScriptFile failed: " + describe(e));
        WScript.Quit(2);
    }
}
