// Digital-human page startup
// Split from state.js. Loaded as a classic script; shared symbols remain global.

(async function bootstrapDigitalHumanPage() {
    const componentReady = typeof ensureDigitalHumanComponentReady === "function"
        ? await ensureDigitalHumanComponentReady()
        : true;
    if (!componentReady) return;

    bindTtsControls();
    loadConfig().catch(err => {
        setStatus("素材读取失败", "err");
        log(err.message, "err");
    });
})();
