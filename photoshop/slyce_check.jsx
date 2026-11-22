// slyce_check.jsx - SLYCE check: reports the Photoshop version and the number of open documents.
$.evalFile(new File(SL_SCRIPTS + "/json2.jsx"));
$.evalFile(new File(SL_SCRIPTS + "/slyce_common.jsx"));
(function () {
    var job = JSON.parse(sl_readText(SL_JOB_PATH));
    sl_writeAtomic(job.donePath, JSON.stringify({ status: "ok", version: app.version, documents: app.documents.length }));
}());
