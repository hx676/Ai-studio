// Backend status checks, queue submit, and task polling.
// Split from state.js. Loaded as a classic script; shared symbols remain global.

const DIGITAL_HUMAN_ACTIVE_TASK_STATUSES = new Set(["queued", "pending", "running"]);

async function loadConfig() {
    const data = await requestJson("/api/digital-human/config", {}, "加载数字人配置失败");
    state.config = data.config || {};
    applyLibraryData(data);
    state.ttsStatus = data.tts_status || null;
    state.heygemStatus = await loadHeyGemStatus();
    const ttsReady = !state.ttsStatus || state.ttsStatus.connected;
    const heygemReady = !state.heygemStatus || state.heygemStatus.connected;
    const summary = `素材已刷新：人物 ${state.people.length} 个，声音 ${state.voices.length} 个`;
    const serviceText = serviceStatusText(ttsReady, heygemReady);
    $("voices-summary").textContent = summary;
    if (!state.selectedTaskId) setStatus("空闲");
    if (!ttsReady || !heygemReady) showToast(`${serviceText}，${SERVICE_START_HINT}`, "warn");
    await refreshTaskQueue({ silent: true }).catch(() => {});
    startTaskPolling();
}

async function loadHeyGemStatus(autoStart = false) {
    try {
        const suffix = autoStart ? "?auto_start=true" : "";
        return await requestJson(`/api/digital-human/heygem/status${suffix}`, {}, "HeyGem 状态读取失败");
    } catch (err) {
        return { connected: false, last_error: err.message };
    }
}

function serviceStatusText(ttsReady, heygemReady) {
    if (ttsReady && heygemReady) return "后台可用";
    if (!ttsReady && !heygemReady) return "TTS / HeyGem 未就绪";
    if (!ttsReady) return "TTS 未就绪";
    return "HeyGem 未就绪";
}

async function ensureTtsReady() {
    const data = await requestJson("/api/digital-human/tts/status?auto_start=true", {}, "TTS 状态读取失败");
    state.ttsStatus = data;
    if (!data.connected) throw new Error(`TTS 未就绪，${SERVICE_START_HINT}`);
    return data;
}

async function ensureHeyGemReady() {
    const data = await loadHeyGemStatus(true);
    state.heygemStatus = data;
    if (!data.connected) throw new Error(`HeyGem 未就绪，${SERVICE_START_HINT}`);
    return data;
}

async function generate() {
    const text = $("script-text").value.trim();
    if (!text) throw new Error("请输入文案");
    const selectedVoice = $("voice-select").value;
    const selectedVideo = currentVideoItem();
    const ttsOptions = collectTtsOptions(true);
    saveTtsOptions();
    const uploadedVoiceIsSelected =
        !!state.uploadedVoice && (!selectedVoice || selectedVoice === state.uploadedVoice.voice_name);
    const payload = {
        text,
        voice_name: selectedVoice || "",
        voice_url: uploadedVoiceIsSelected ? state.uploadedVoice?.url || "" : "",
        voice_path: uploadedVoiceIsSelected ? state.uploadedVoice?.path || "" : "",
        video_url: selectedVideo?.url || "",
        video_path: selectedVideo?.path || "",
        tts_options: ttsOptions,
    };
    if (!payload.video_url && !payload.video_path) throw new Error("请选择或上传驱动视频");

    const button = $("generate-btn");
    button.disabled = true;
    button.textContent = "提交中";
    setStatus("加入队列中");
    try {
        const data = await requestJson(
            "/api/digital-human/generate",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            },
            "提交失败"
        );
        state.taskId = data.task_id;
        state.selectedTaskId = data.task_id;
        state.lastBackendProgress = "";
        log(`已加入队列：${state.taskId}`, "ok");
        await refreshTaskQueue();
        startTaskPolling();
    } finally {
        button.disabled = false;
        button.textContent = "加入队列";
    }
}

function clearTaskPolling() {
    if (state.pollTimer) {
        clearTimeout(state.pollTimer);
        state.pollTimer = null;
    }
    state.pollToken += 1;
}

function startTaskPolling() {
    clearTaskPolling();
    const token = state.pollToken;
    pollTask(token).catch((err) => {
        if (token !== state.pollToken) return;
        log(err.message, "err");
    });
}

function hasActiveQueueTasks() {
    return (state.tasks || []).some((task) => DIGITAL_HUMAN_ACTIVE_TASK_STATUSES.has(task.status));
}

async function pollTask(token = state.pollToken) {
    if (token !== state.pollToken) return;
    await refreshTaskQueue({ silent: true });
    if (token !== state.pollToken) return;
    if (hasActiveQueueTasks()) {
        state.pollTimer = setTimeout(() => {
            pollTask(token).catch((err) => {
                if (token !== state.pollToken) return;
                log(err.message, "err");
            });
        }, 2200);
    }
}

async function refreshTaskQueue(options = {}) {
    const data = await requestJson("/api/digital-human/tasks", {}, "任务队列读取失败");
    state.tasks = Array.isArray(data.tasks) ? data.tasks : [];
    state.queueState = data.queue || null;
    const selectedExists = state.tasks.some((task) => task.task_id === state.selectedTaskId);
    if (!selectedExists) {
        const running = state.tasks.find((task) => task.status === "running");
        const queued = state.tasks.find((task) => task.status === "queued" || task.status === "pending");
        state.selectedTaskId = (running || queued || state.tasks[0] || {}).task_id || "";
    }
    state.taskId = state.selectedTaskId || "";
    renderTaskQueue();
    showSelectedTaskResult();
    if (!options.silent && hasActiveQueueTasks()) startTaskPolling();
    return data;
}

function taskStatusLabel(task) {
    const status = task?.status || "";
    if (status === "queued" || status === "pending") {
        return task.queue_position ? `排队 #${task.queue_position}` : "排队中";
    }
    if (status === "running") return stageText(task.stage || status);
    if (status === "succeeded") return "完成";
    if (status === "failed") return "失败";
    if (status === "canceled") return "已取消";
    return stageText(task?.stage || status);
}

function taskStatusKind(task) {
    const status = task?.status || "";
    if (status === "succeeded") return "ok";
    if (status === "failed" || status === "canceled") return "err";
    if (status === "queued" || status === "pending") return "warn";
    return "";
}

function taskEmptyText(task) {
    const status = task?.status || "";
    if (!task) return "暂无结果";
    if (status === "queued" || status === "pending") {
        return task.queue_position ? `等待执行，第 ${task.queue_position} 个` : "等待执行";
    }
    if (status === "running") return `正在${stageText(task.stage || status)}`;
    if (status === "failed") {
        const type = task.failure_type ? `（${task.failure_type}）` : "";
        return `${userFacingError(task.error, "生成失败，请确认后台运行正常。")}${type}`;
    }
    if (status === "canceled") return "任务已取消";
    return "暂无结果";
}

function taskMetaText(task) {
    const parts = [];
    if (task.voice_name) parts.push(task.voice_name);
    if (task.video_name) parts.push(task.video_name);
    if (!parts.length && task.task_id) parts.push(task.task_id);
    return parts.join(" · ");
}

function taskPrimaryOutputPath(task) {
    return task?.video?.path || task?.audio?.path || "";
}

function taskResultMetaLines(task) {
    const lines = [];
    if (task?.task_id) lines.push(`任务 ${task.task_id}`);
    if (task?.voice_name) lines.push(`音色 ${task.voice_name}`);
    if (task?.video_name) lines.push(`动作 ${task.video_name}`);
    const updatedAt = Number(task?.updated_at || task?.created_at || 0);
    if (updatedAt) {
        lines.push(`更新 ${new Date(updatedAt * 1000).toLocaleString("zh-CN", { hour12: false })}`);
    }
    return lines;
}

function taskStageSummary(task) {
    if (!task) return "";
    if (task.status === "queued" || task.status === "pending") {
        return task.queue_position ? `等待启动，前方 ${Math.max(0, Number(task.queue_position || 1) - 1)} 个` : "等待启动";
    }
    if (task.status === "running") {
        const progress = backendProgressText(task.heygem);
        return progress ? `${stageText(task.stage || task.status)} · ${progress}` : stageText(task.stage || task.status);
    }
    if (task.status === "succeeded") return "已生成成片，可播放、下载或打开目录";
    if (task.status === "failed") return userFacingError(task.error, "生成失败，请确认后台运行正常。");
    if (task.status === "canceled") return "任务已取消";
    return taskStatusLabel(task);
}

function taskStageTimeline(task) {
    const order = ["queued", "tts-submit", "gpu-handoff", "heygem-generate", "done"];
    const labels = {
        queued: "排队",
        "tts-submit": "TTS",
        "gpu-handoff": "释放显存",
        "heygem-generate": "HeyGem",
        done: "完成",
    };
    const current =
        ({
            queued: "queued",
            pending: "queued",
            tts: "tts-submit",
            "waiting-tts-light": "tts-submit",
            "tts-submit": "tts-submit",
            "gpu-handoff": "gpu-handoff",
            "waiting-heygem-light": "heygem-generate",
            "heygem-submit": "heygem-generate",
            "heygem-generate": "heygem-generate",
            "heygem-retry": "heygem-generate",
            done: "done",
            succeeded: "done",
            failed: "heygem-generate",
            canceled: "heygem-generate",
        }[task?.stage || task?.status || "queued"]) || "queued";
    const currentIndex = Math.max(0, order.indexOf(current));
    return order
        .map((step, index) => {
            const classes = [];
            if (task?.status === "succeeded" || index < currentIndex) classes.push("done");
            if ((task?.status === "failed" || task?.status === "canceled") && index === currentIndex) classes.push("error");
            else if ((task?.status === "running" || task?.status === "queued" || task?.status === "pending") && index === currentIndex) {
                classes.push("current");
            }
            if (task?.status === "succeeded" && step === "done") classes.push("current");
            return `<span class="result-stage-chip ${classes.join(" ")}">${labels[step]}</span>`;
        })
        .join("");
}

function queueResourceText(resource, release) {
    const active = resource?.active || "idle";
    const waiting = Number(resource?.waiting_tts || 0) + Number(resource?.waiting_heygem || 0);
    const suffix = waiting > 0 ? ` · 等绿灯 ${waiting}` : "";
    if (active === "tts") return `红灯：TTS 运行中${suffix}`;
    if (active === "heygem") return `红灯：HeyGem 合成中${suffix}`;
    if (release?.at) return "绿灯：空闲，显存已释放，生成时会自动启动";
    return "绿灯：空闲";
}

function renderTaskQueue() {
    const el = $("task-queue-list");
    const summary = $("queue-summary");
    if (!el || !summary) return;
    const tasks = state.tasks || [];
    const queueState = state.queueState || {};
    const resourceText = queueResourceText(queueState.resource || {}, queueState.gpu_release || null);
    const runningCount = tasks.filter((task) => task.status === "running").length;
    const queuedCount = tasks.filter((task) => task.status === "queued" || task.status === "pending").length;
    if (!tasks.length) {
        summary.textContent = resourceText;
        el.innerHTML = '<div class="status">暂无队列任务</div>';
        return;
    }
    summary.textContent = queueState.paused
        ? `已暂停 · 等待 ${queuedCount} · ${resourceText}`
        : runningCount
            ? `运行中 · 等待 ${queuedCount} · ${resourceText}`
            : queuedCount
                ? `等待 ${queuedCount} · ${resourceText}`
                : resourceText;
    const pausedHtml = queueState.paused
        ? `
        <div class="queue-pause-banner">
            <div>
                <strong>队列已暂停</strong>
                <span>${escapeHtml(queueState.pause_reason || "HeyGem 可能阻塞，请检查后台后继续。")}</span>
            </div>
            <button class="btn small primary" type="button" data-queue-continue>继续队列</button>
        </div>`
        : "";
    el.innerHTML =
        pausedHtml +
        tasks
            .map((task) => {
                const active = task.task_id === state.selectedTaskId;
                const canCancel = task.status === "queued" || task.status === "pending";
                const canRetry = task.status === "failed" && task.retryable !== false;
                const title = task.script_preview || task.task_id || "数字人任务";
                const meta = taskMetaText(task);
                return `
            <div class="task-queue-item ${active ? "active" : ""} ${escapeHtml(task.status || "")}" role="button" tabindex="0" data-task-id="${escapeHtml(task.task_id || "")}">
                <div class="task-queue-copy">
                    <div class="task-queue-title">${escapeHtml(title)}</div>
                    <div class="task-queue-meta">${escapeHtml(meta || taskStatusLabel(task))}</div>
                    <div class="task-queue-stage">${escapeHtml(taskStageSummary(task))}</div>
                </div>
                <div class="task-queue-side">
                    <span class="task-queue-status ${escapeHtml(taskStatusKind(task))}">${escapeHtml(taskStatusLabel(task))}</span>
                    ${canCancel ? '<button class="btn small" type="button" data-task-cancel>取消</button>' : ""}
                    ${canRetry ? '<button class="btn small" type="button" data-task-retry>重试</button>' : ""}
                </div>
            </div>`;
            })
            .join("");
    el.querySelector("[data-queue-continue]")?.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
            await continueQueue();
        } catch (err) {
            log(err.message, "err");
        }
    });
    el.querySelectorAll("[data-task-id]").forEach((item) => {
        const taskId = item.dataset.taskId || "";
        item.addEventListener("click", (event) => {
            if (event.target.closest("[data-task-cancel]")) return;
            selectTask(taskId);
        });
        item.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            selectTask(taskId);
        });
        item.querySelector("[data-task-cancel]")?.addEventListener("click", async (event) => {
            event.stopPropagation();
            try {
                await cancelTask(taskId);
            } catch (err) {
                log(err.message, "err");
            }
        });
        item.querySelector("[data-task-retry]")?.addEventListener("click", async (event) => {
            event.stopPropagation();
            try {
                await retryTask(taskId);
            } catch (err) {
                log(err.message, "err");
            }
        });
    });
}

function selectTask(taskId) {
    state.selectedTaskId = taskId;
    state.taskId = taskId;
    state.lastBackendProgress = "";
    renderTaskQueue();
    showSelectedTaskResult();
}

async function cancelTask(taskId) {
    if (!taskId) return;
    await requestJson(`/api/digital-human/task/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }, "取消任务失败");
    log("任务已取消", "ok");
    await refreshTaskQueue();
    startTaskPolling();
}

async function retryTask(taskId) {
    if (!taskId) return;
    const task = await requestJson(`/api/digital-human/task/${encodeURIComponent(taskId)}/retry`, { method: "POST" }, "重试任务失败");
    state.selectedTaskId = task.task_id || state.selectedTaskId;
    log("已重新加入队列", "ok");
    await refreshTaskQueue();
    startTaskPolling();
}

async function continueQueue() {
    await requestJson("/api/digital-human/queue/continue", { method: "POST" }, "继续队列失败");
    log("队列已继续", "ok");
    await refreshTaskQueue();
    startTaskPolling();
}

async function openSelectedTaskOutput(taskId) {
    if (!taskId) return;
    await requestJson(`/api/digital-human/task/${encodeURIComponent(taskId)}/open-output`, { method: "POST" }, "打开输出失败");
}

async function openDigitalHumanOutputDir() {
    await requestJson("/api/digital-human/output/open", { method: "POST" }, "打开成片目录失败");
}

function showSelectedTaskResult() {
    const task = (state.tasks || []).find((item) => item.task_id === state.selectedTaskId) || null;
    const audio = $("audio-preview");
    const video = $("result-video");
    const empty = $("empty-result");
    const download = $("download-link");
    const copyPathBtn = $("copy-result-path-btn");
    const openResultBtn = $("open-result-output-btn");
    const openOutputDirBtn = $("open-output-dir-btn");
    const taskMeta = $("result-task-meta");
    const stageStrip = $("result-stage-strip");
    if (!task) {
        setStatus("空闲");
        audio.pause();
        audio.removeAttribute("src");
        audio.classList.add("hidden");
        video.pause();
        video.removeAttribute("src");
        video.classList.add("hidden");
        download.classList.add("hidden");
        copyPathBtn?.classList.add("hidden");
        openResultBtn?.classList.add("hidden");
        if (taskMeta) {
            taskMeta.innerHTML = "";
            taskMeta.classList.add("hidden");
        }
        if (stageStrip) {
            stageStrip.innerHTML = "";
            stageStrip.classList.add("hidden");
        }
        empty.textContent = "暂无结果";
        empty.classList.remove("hidden");
        if (openOutputDirBtn) {
            openOutputDirBtn.onclick = () => openDigitalHumanOutputDir().catch((err) => log(err.message, "err"));
        }
        return;
    }

    setStatus(taskStatusLabel(task), taskStatusKind(task));
    if (taskMeta) {
        taskMeta.innerHTML = taskResultMetaLines(task)
            .map((line, index) => (index === 0 ? `<strong>${escapeHtml(line)}</strong>` : `<span>${escapeHtml(line)}</span>`))
            .join("");
        taskMeta.classList.remove("hidden");
    }
    if (stageStrip) {
        stageStrip.innerHTML = taskStageTimeline(task);
        stageStrip.classList.remove("hidden");
    }

    if (task.audio?.url) {
        audio.src = task.audio.url;
        audio.classList.remove("hidden");
    } else {
        audio.pause();
        audio.removeAttribute("src");
        audio.classList.add("hidden");
    }

    const downloadUrl = task.video?.url || task.audio?.url || "";
    if (task.video?.url) {
        video.src = task.video.url;
        video.classList.remove("hidden");
        empty.classList.add("hidden");
    } else {
        video.pause();
        video.removeAttribute("src");
        video.classList.add("hidden");
        empty.textContent = taskEmptyText(task);
        empty.classList.remove("hidden");
    }

    if (downloadUrl) {
        download.href = downloadUrl;
        download.classList.remove("hidden");
    } else {
        download.classList.add("hidden");
    }

    const outputPath = taskPrimaryOutputPath(task);
    if (copyPathBtn) {
        copyPathBtn.classList.toggle("hidden", !outputPath);
        copyPathBtn.onclick = async () => {
            if (!outputPath) return;
            try {
                await navigator.clipboard.writeText(outputPath);
                log("已复制输出路径", "ok");
            } catch (_) {
                log("复制输出路径失败", "err");
            }
        };
    }
    if (openResultBtn) {
        openResultBtn.classList.toggle("hidden", !task.task_id);
        openResultBtn.onclick = () => openSelectedTaskOutput(task.task_id).catch((err) => log(err.message, "err"));
    }
    if (openOutputDirBtn) {
        openOutputDirBtn.onclick = () => openDigitalHumanOutputDir().catch((err) => log(err.message, "err"));
    }

    const progressText = backendProgressText(task.heygem);
    const progressKey = `${task.task_id}:${progressText}`;
    if (task.status === "running" && progressText && progressKey !== state.lastBackendProgress) {
        state.lastBackendProgress = progressKey;
        log(`合成进度：${progressText}`);
    }
}

window.addEventListener("beforeunload", clearTaskPolling);
