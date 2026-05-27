// Backend status checks, queue submit, and task polling.
// Split from state.js. Loaded as a classic script; shared symbols remain global.

const DIGITAL_HUMAN_ACTIVE_TASK_STATUSES = new Set(['queued', 'pending', 'running']);

async function loadConfig() {
    const data = await requestJson('/api/digital-human/config', {}, '加载数字人配置失败');
    state.config = data.config || {};
    applyLibraryData(data);
    state.ttsStatus = data.tts_status || null;
    state.heygemStatus = await loadHeyGemStatus();
    const ttsReady = !state.ttsStatus || state.ttsStatus.connected;
    const heygemReady = !state.heygemStatus || state.heygemStatus.connected;
    const summary = `素材已刷新：人物 ${state.people.length} 个，声音 ${state.voices.length} 个`;
    const serviceText = serviceStatusText(ttsReady, heygemReady);
    $('voices-summary').textContent = summary;
    if (!state.selectedTaskId) setStatus('空闲');
    if (!ttsReady || !heygemReady) showToast(`${serviceText}，${SERVICE_START_HINT}`, 'warn');
    await refreshTaskQueue({ silent: true }).catch(() => {});
    startTaskPolling();
}

async function loadHeyGemStatus() {
    try {
        return await requestJson('/api/digital-human/heygem/status', {}, 'HeyGem 状态读取失败');
    } catch (err) {
        return { connected: false, last_error: err.message };
    }
}

function serviceStatusText(ttsReady, heygemReady) {
    if (ttsReady && heygemReady) return '后台可用';
    if (!ttsReady && !heygemReady) return 'TTS / HeyGem 未就绪';
    if (!ttsReady) return 'TTS 未就绪';
    return 'HeyGem 未就绪';
}

async function ensureTtsReady() {
    const data = await requestJson('/api/digital-human/tts/status?auto_start=true', {}, 'TTS 状态读取失败');
    state.ttsStatus = data;
    if (!data.connected) throw new Error(`TTS 未就绪，${SERVICE_START_HINT}`);
    return data;
}

async function ensureHeyGemReady() {
    const data = await loadHeyGemStatus();
    state.heygemStatus = data;
    if (!data.connected) throw new Error(`HeyGem 未就绪，${SERVICE_START_HINT}`);
    return data;
}

async function generate() {
    const text = $('script-text').value.trim();
    if (!text) throw new Error('请输入文案');
    const selectedVoice = $('voice-select').value;
    const selectedVideo = currentVideoItem();
    const ttsOptions = collectTtsOptions(true);
    saveTtsOptions();
    const uploadedVoiceIsSelected = !!state.uploadedVoice
        && (!selectedVoice || selectedVoice === state.uploadedVoice.voice_name);
    const payload = {
        text,
        voice_name: selectedVoice || '',
        voice_url: uploadedVoiceIsSelected ? (state.uploadedVoice?.url || '') : '',
        voice_path: uploadedVoiceIsSelected ? (state.uploadedVoice?.path || '') : '',
        video_url: selectedVideo?.url || '',
        video_path: selectedVideo?.path || '',
        tts_options: ttsOptions,
    };
    if (!payload.video_url && !payload.video_path) throw new Error('请选择或上传驱动视频');

    const button = $('generate-btn');
    button.disabled = true;
    button.textContent = '提交中';
    setStatus('加入队列中');
    try {
        const data = await requestJson('/api/digital-human/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }, '提交失败');
        state.taskId = data.task_id;
        state.selectedTaskId = data.task_id;
        state.lastBackendProgress = '';
        log(`已加入队列：${state.taskId}`, 'ok');
        await refreshTaskQueue();
        startTaskPolling();
    } finally {
        button.disabled = false;
        button.textContent = '加入队列';
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
    pollTask(token).catch(err => {
        if (token !== state.pollToken) return;
        log(err.message, 'err');
    });
}

function hasActiveQueueTasks() {
    return (state.tasks || []).some(task => DIGITAL_HUMAN_ACTIVE_TASK_STATUSES.has(task.status));
}

async function pollTask(token = state.pollToken) {
    if (token !== state.pollToken) return;
    await refreshTaskQueue({ silent: true });
    if (token !== state.pollToken) return;
    if (hasActiveQueueTasks()) {
        state.pollTimer = setTimeout(() => pollTask(token).catch(err => {
            if (token !== state.pollToken) return;
            log(err.message, 'err');
        }), 2200);
    }
}

async function refreshTaskQueue(options = {}) {
    const data = await requestJson('/api/digital-human/tasks', {}, '任务队列读取失败');
    state.tasks = Array.isArray(data.tasks) ? data.tasks : [];
    const selectedExists = state.tasks.some(task => task.task_id === state.selectedTaskId);
    if (!selectedExists) {
        const running = state.tasks.find(task => task.status === 'running');
        const queued = state.tasks.find(task => task.status === 'queued' || task.status === 'pending');
        state.selectedTaskId = (running || queued || state.tasks[0] || {}).task_id || '';
    }
    state.taskId = state.selectedTaskId || '';
    renderTaskQueue();
    showSelectedTaskResult();
    if (!options.silent && hasActiveQueueTasks()) startTaskPolling();
    return data;
}

function taskStatusLabel(task) {
    const status = task?.status || '';
    if (status === 'queued' || status === 'pending') {
        return task.queue_position ? `排队 #${task.queue_position}` : '排队中';
    }
    if (status === 'running') return stageText(task.stage || status);
    if (status === 'succeeded') return '完成';
    if (status === 'failed') return '失败';
    if (status === 'canceled') return '已取消';
    return stageText(task?.stage || status);
}

function taskStatusKind(task) {
    const status = task?.status || '';
    if (status === 'succeeded') return 'ok';
    if (status === 'failed' || status === 'canceled') return 'err';
    if (status === 'queued' || status === 'pending') return 'warn';
    return '';
}

function taskEmptyText(task) {
    const status = task?.status || '';
    if (!task) return '暂无结果';
    if (status === 'queued' || status === 'pending') return task.queue_position ? `等待执行，第 ${task.queue_position} 个` : '等待执行';
    if (status === 'running') return `正在${stageText(task.stage || status)}`;
    if (status === 'failed') return userFacingError(task.error, '生成失败，请确认后台运行正常。');
    if (status === 'canceled') return '任务已取消';
    return '暂无结果';
}

function taskMetaText(task) {
    const parts = [];
    if (task.voice_name) parts.push(task.voice_name);
    if (task.video_name) parts.push(task.video_name);
    if (!parts.length && task.task_id) parts.push(task.task_id);
    return parts.join(' · ');
}

function renderTaskQueue() {
    const el = $('task-queue-list');
    const summary = $('queue-summary');
    if (!el || !summary) return;
    const tasks = state.tasks || [];
    const runningCount = tasks.filter(task => task.status === 'running').length;
    const queuedCount = tasks.filter(task => task.status === 'queued' || task.status === 'pending').length;
    if (!tasks.length) {
        summary.textContent = '暂无任务';
        el.innerHTML = '<div class="status">暂无队列任务</div>';
        return;
    }
    summary.textContent = runningCount ? `运行中 · 等待 ${queuedCount}` : (queuedCount ? `等待 ${queuedCount}` : '无等待');
    el.innerHTML = tasks.map(task => {
        const active = task.task_id === state.selectedTaskId;
        const canCancel = task.status === 'queued' || task.status === 'pending';
        const title = task.script_preview || task.task_id || '数字人任务';
        const meta = taskMetaText(task);
        return `
            <div class="task-queue-item ${active ? 'active' : ''} ${escapeHtml(task.status || '')}" role="button" tabindex="0" data-task-id="${escapeHtml(task.task_id || '')}">
                <div class="task-queue-copy">
                    <div class="task-queue-title">${escapeHtml(title)}</div>
                    <div class="task-queue-meta">${escapeHtml(meta || taskStatusLabel(task))}</div>
                </div>
                <div class="task-queue-side">
                    <span class="task-queue-status ${escapeHtml(taskStatusKind(task))}">${escapeHtml(taskStatusLabel(task))}</span>
                    ${canCancel ? '<button class="btn small" type="button" data-task-cancel>取消</button>' : ''}
                </div>
            </div>`;
    }).join('');
    el.querySelectorAll('[data-task-id]').forEach(item => {
        const taskId = item.dataset.taskId || '';
        item.addEventListener('click', event => {
            if (event.target.closest('[data-task-cancel]')) return;
            selectTask(taskId);
        });
        item.addEventListener('keydown', event => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            selectTask(taskId);
        });
        item.querySelector('[data-task-cancel]')?.addEventListener('click', async event => {
            event.stopPropagation();
            try {
                await cancelTask(taskId);
            } catch (err) {
                log(err.message, 'err');
            }
        });
    });
}

function selectTask(taskId) {
    state.selectedTaskId = taskId;
    state.taskId = taskId;
    state.lastBackendProgress = '';
    renderTaskQueue();
    showSelectedTaskResult();
}

async function cancelTask(taskId) {
    if (!taskId) return;
    await requestJson(`/api/digital-human/task/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' }, '取消任务失败');
    log('任务已取消', 'ok');
    await refreshTaskQueue();
    startTaskPolling();
}

function showSelectedTaskResult() {
    const task = (state.tasks || []).find(item => item.task_id === state.selectedTaskId) || null;
    const audio = $('audio-preview');
    const video = $('result-video');
    const empty = $('empty-result');
    const download = $('download-link');
    if (!task) {
        setStatus('空闲');
        audio.pause();
        audio.removeAttribute('src');
        audio.classList.add('hidden');
        video.pause();
        video.removeAttribute('src');
        video.classList.add('hidden');
        download.classList.add('hidden');
        empty.textContent = '暂无结果';
        empty.classList.remove('hidden');
        return;
    }
    setStatus(taskStatusLabel(task), taskStatusKind(task));
    if (task.audio?.url) {
        audio.src = task.audio.url;
        audio.classList.remove('hidden');
    } else {
        audio.pause();
        audio.removeAttribute('src');
        audio.classList.add('hidden');
    }
    if (task.video?.url) {
        video.src = task.video.url;
        video.classList.remove('hidden');
        empty.classList.add('hidden');
        download.href = task.video.url;
        download.classList.remove('hidden');
    } else {
        video.pause();
        video.removeAttribute('src');
        video.classList.add('hidden');
        download.classList.add('hidden');
        empty.textContent = taskEmptyText(task);
        empty.classList.remove('hidden');
    }
    const progressText = backendProgressText(task.heygem);
    const progressKey = `${task.task_id}:${progressText}`;
    if (task.status === 'running' && progressText && progressKey !== state.lastBackendProgress) {
        state.lastBackendProgress = progressKey;
        log(`合成进度：${progressText}`);
    }
}

window.addEventListener('beforeunload', clearTaskPolling);
