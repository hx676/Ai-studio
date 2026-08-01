// Optional digital-human component install gate.

const DIGITAL_HUMAN_COMPONENT_ACTIVE_STATES = new Set([
    "queued",
    "downloading",
    "verifying",
    "installing",
    "cancelling",
]);

let digitalHumanComponentPollTimer = null;
let digitalHumanComponentEventsBound = false;

function componentElement(id) {
    return document.getElementById(id);
}

function formatComponentBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "--";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let number = bytes;
    let unit = 0;
    while (number >= 1024 && unit < units.length - 1) {
        number /= 1024;
        unit += 1;
    }
    const digits = unit >= 3 ? 1 : 0;
    return `${number.toFixed(digits)} ${units[unit]}`;
}

function formatComponentDuration(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    if (seconds < 60) return `${Math.ceil(seconds)} 秒`;
    if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.ceil((seconds % 3600) / 60);
    return `${hours} 小时 ${minutes} 分钟`;
}

function componentStateText(state) {
    return {
        not_installed: "未安装",
        partial: "安装不完整",
        queued: "等待安装",
        downloading: "下载中",
        verifying: "校验中",
        installing: "安装中",
        cancelling: "正在取消",
        cancelled: "已取消",
        interrupted: "可以继续",
        error: "安装失败",
        unsupported: "当前系统不支持",
        ready: "已安装",
    }[state] || "正在检查";
}

function componentManualProviderName(provider) {
    return {
        "baidu-pan": "百度网盘",
    }[String(provider || "").toLowerCase()] || "外部下载";
}

async function copyComponentText(value, button) {
    const text = String(value || "");
    if (!text) return;
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            const input = document.createElement("textarea");
            input.value = text;
            input.setAttribute("readonly", "");
            input.style.position = "fixed";
            input.style.opacity = "0";
            document.body.appendChild(input);
            input.select();
            document.execCommand("copy");
            input.remove();
        }
        const original = button.textContent;
        button.textContent = "已复制";
        setTimeout(() => {
            button.textContent = original;
        }, 1200);
    } catch {
        button.textContent = "复制失败";
    }
}

function renderDigitalHumanManualDownloads(status) {
    const panel = componentElement("component-manual-downloads");
    const list = componentElement("component-manual-list");
    const summary = componentElement("component-manual-summary");
    if (!panel || !list || !summary) return;

    const artifacts = Array.isArray(status.artifacts)
        ? status.artifacts.filter((artifact) => artifact?.platform_supported !== false && artifact?.manual_download?.share_url)
        : [];
    panel.hidden = artifacts.length === 0;
    list.replaceChildren();
    if (!artifacts.length) return;

    const pendingCount = artifacts.filter((artifact) => !artifact.local_source_available).length;
    summary.textContent = pendingCount
        ? `还需下载 ${pendingCount} 个文件，完成后点击“重新检查”`
        : "已找到全部组件包，可以开始校验并安装";

    artifacts.forEach((artifact) => {
        const manual = artifact.manual_download;
        const card = document.createElement("article");
        card.className = "component-manual-card";

        const info = document.createElement("div");
        info.className = "component-manual-info";
        const titleLine = document.createElement("div");
        titleLine.className = "component-manual-title";
        const title = document.createElement("strong");
        title.textContent = artifact.display_name || artifact.id || "组件包";
        const badge = document.createElement("span");
        badge.className = artifact.local_source_available
            ? "component-manual-badge is-ready"
            : "component-manual-badge";
        badge.textContent = artifact.local_source_available ? "已找到" : "待下载";
        titleLine.append(title, badge);

        const filename = document.createElement("div");
        filename.className = "component-manual-filename";
        filename.textContent = manual.filename || artifact.filename || "";
        const detail = document.createElement("div");
        detail.className = "component-manual-detail";
        detail.textContent = `${componentManualProviderName(manual.provider)} · ${formatComponentBytes(artifact.download_size)}`;
        info.append(titleLine, filename, detail);

        const actions = document.createElement("div");
        actions.className = "component-manual-actions";
        const openLink = document.createElement("a");
        openLink.className = "btn component-manual-open";
        openLink.href = manual.share_url;
        openLink.target = "_blank";
        openLink.rel = "noopener noreferrer";
        openLink.referrerPolicy = "no-referrer";
        openLink.textContent = artifact.local_source_available ? "重新打开网盘" : "打开网盘";
        actions.appendChild(openLink);

        if (manual.extraction_code) {
            const code = document.createElement("code");
            code.textContent = `提取码 ${manual.extraction_code}`;
            const copyButton = document.createElement("button");
            copyButton.type = "button";
            copyButton.className = "btn component-manual-copy";
            copyButton.textContent = "复制提取码";
            copyButton.addEventListener("click", () => copyComponentText(manual.extraction_code, copyButton));
            actions.append(code, copyButton);
        }

        card.append(info, actions);
        list.appendChild(card);
    });
}

async function componentRequest(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...(options.headers || {}),
        },
    });
    let payload = {};
    try {
        payload = await response.json();
    } catch {
        payload = {};
    }
    if (!response.ok) {
        throw new Error(payload.detail || payload.error || `请求失败（HTTP ${response.status}）`);
    }
    return payload;
}

function stopDigitalHumanComponentPolling() {
    if (digitalHumanComponentPollTimer) {
        clearTimeout(digitalHumanComponentPollTimer);
        digitalHumanComponentPollTimer = null;
    }
}

function scheduleDigitalHumanComponentPolling() {
    stopDigitalHumanComponentPolling();
    digitalHumanComponentPollTimer = setTimeout(async () => {
        try {
            const status = await componentRequest("/api/components/digital-human/status");
            renderDigitalHumanComponentStatus(status);
            if (status.ready) {
                window.location.reload();
                return;
            }
            if (DIGITAL_HUMAN_COMPONENT_ACTIVE_STATES.has(status.state)) {
                scheduleDigitalHumanComponentPolling();
            }
        } catch (error) {
            renderDigitalHumanComponentFailure(error);
            scheduleDigitalHumanComponentPolling();
        }
    }, 1000);
}

function renderDigitalHumanComponentFailure(error) {
    const gate = componentElement("digital-human-component-gate");
    if (!gate) return;
    gate.hidden = false;
    document.body.classList.add("component-install-required");
    const errorBox = componentElement("component-install-error");
    errorBox.hidden = false;
    errorBox.textContent = error?.message || "数字人组件状态读取失败";
    componentElement("component-install-state").textContent = "检查失败";
}

function renderDigitalHumanComponentStatus(status) {
    const gate = componentElement("digital-human-component-gate");
    if (!gate) return;
    if (status.ready) {
        gate.hidden = true;
        document.body.classList.remove("component-install-required");
        stopDigitalHumanComponentPolling();
        return;
    }

    gate.hidden = false;
    document.body.classList.add("component-install-required");
    const state = status.state || "not_installed";
    const active = DIGITAL_HUMAN_COMPONENT_ACTIVE_STATES.has(state);
    const progress = Math.max(0, Math.min(100, Number(status.progress_percent || 0)));
    const task = status.task || {};
    const errorText = status.error || task.error || "";

    componentElement("component-install-state").textContent = componentStateText(state);
    componentElement("component-download-size").textContent = formatComponentBytes(status.download_size);
    componentElement("component-installed-size").textContent = formatComponentBytes(status.installed_size);
    componentElement("component-free-space").textContent = formatComponentBytes(status.free_bytes);
    componentElement("component-free-space").classList.toggle("space-low", status.enough_space === false);
    componentElement("component-install-location").textContent = status.install_root
        ? `默认安装位置：${status.install_root}`
        : "";

    const progressPanel = componentElement("component-install-progress");
    progressPanel.hidden = !(active || progress > 0);
    componentElement("component-progress-label").textContent =
        status.message || task.message || componentStateText(state);
    componentElement("component-progress-percent").textContent = `${progress.toFixed(progress >= 10 ? 0 : 1)}%`;
    componentElement("component-progress-value").style.width = `${progress}%`;

    const details = [];
    if (task.current_artifact_name) details.push(task.current_artifact_name);
    if (task.speed_bytes_per_second) details.push(`${formatComponentBytes(task.speed_bytes_per_second)}/s`);
    if (task.eta_seconds != null) details.push(`预计还需 ${formatComponentDuration(task.eta_seconds)}`);
    componentElement("component-progress-detail").textContent = details.join(" · ");

    const errorBox = componentElement("component-install-error");
    errorBox.hidden = !errorText;
    errorBox.textContent = errorText;
    renderDigitalHumanManualDownloads(status);

    const installButton = componentElement("component-install-btn");
    const repairButton = componentElement("component-repair-btn");
    const cancelButton = componentElement("component-cancel-btn");
    installButton.hidden = active;
    installButton.disabled = status.supported === false || !status.can_install || status.enough_space === false;
    const allSourcesLocal = Array.isArray(status.artifacts)
        && status.artifacts.length > 0
        && status.artifacts.every((artifact) => artifact.local_source_available);
    installButton.textContent = state === "interrupted" || state === "cancelled"
        ? "继续下载并安装"
        : (allSourcesLocal ? "校验并安装" : "下载并安装");
    repairButton.hidden = active || !["partial", "error"].includes(state);
    repairButton.disabled = status.supported === false || !status.can_install || status.enough_space === false;
    cancelButton.hidden = !active || state === "cancelling";

    const description = componentElement("component-install-description");
    if (status.supported === false) {
        description.textContent = `数字人 TTS 与 HeyGem 当前仅提供 Windows 运行包，暂不支持 ${status.platform || "当前系统"}。画布和在线生成功能不受影响。`;
    } else if (status.enough_space === false) {
        description.textContent = `磁盘空间不足，至少需要 ${formatComponentBytes(status.minimum_free_bytes)} 可用空间。`;
    } else if (status.manual_download_required) {
        description.textContent = "请先通过下方百度网盘下载 TTS 与 HeyGem，保持文件名不变并保存到系统“下载”目录，然后点击“重新检查”。";
    } else if (!status.can_install) {
        description.textContent = "未找到数字人组件包。请把 TTS 与 HeyGem 压缩包放在 SynCanvas 核心目录、核心目录的上一级，或 packages/components 中。";
    } else if (allSourcesLocal) {
        description.textContent = "已找到全部数字人组件包。点击“校验并安装”，系统会核对文件大小与 SHA-256 后自动解压。";
    } else {
        description.textContent = "画布功能已经可以正常使用。数字人组件会分两段下载，完成校验后自动安装。";
    }

    if (active) scheduleDigitalHumanComponentPolling();
    else stopDigitalHumanComponentPolling();
}

async function refreshDigitalHumanComponentStatus() {
    try {
        const status = await componentRequest("/api/components/digital-human/status");
        renderDigitalHumanComponentStatus(status);
        return status;
    } catch (error) {
        renderDigitalHumanComponentFailure(error);
        return { ready: false, state: "error", error: error.message };
    }
}

async function startDigitalHumanComponentInstall(force = false) {
    const endpoint = force
        ? "/api/components/digital-human/repair"
        : "/api/components/digital-human/install";
    try {
        const status = await componentRequest(endpoint, {
            method: "POST",
            body: JSON.stringify({ force }),
        });
        renderDigitalHumanComponentStatus(status);
        scheduleDigitalHumanComponentPolling();
    } catch (error) {
        renderDigitalHumanComponentFailure(error);
    }
}

function bindDigitalHumanComponentEvents() {
    if (digitalHumanComponentEventsBound) return;
    digitalHumanComponentEventsBound = true;
    componentElement("component-install-btn").onclick = () => startDigitalHumanComponentInstall(false);
    componentElement("component-repair-btn").onclick = () => startDigitalHumanComponentInstall(true);
    componentElement("component-cancel-btn").onclick = async () => {
        try {
            const status = await componentRequest("/api/components/digital-human/cancel", { method: "POST" });
            renderDigitalHumanComponentStatus(status);
            scheduleDigitalHumanComponentPolling();
        } catch (error) {
            renderDigitalHumanComponentFailure(error);
        }
    };
    componentElement("component-refresh-btn").onclick = () => refreshDigitalHumanComponentStatus();
}

async function ensureDigitalHumanComponentReady() {
    bindDigitalHumanComponentEvents();
    const status = await refreshDigitalHumanComponentStatus();
    return status.ready === true;
}
