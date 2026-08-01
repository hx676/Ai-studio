import './agent-skill-canvas.js?v=1.2.1';

const AGENT_TYPE = 'syncanvas.agent-skill/agent';
let metadata = null;
let metadataError = '';
let metadataPromise = null;
const isEnglish = () => (globalThis.StudioI18n?.lang?.() || document.documentElement.lang || '').toLowerCase().startsWith('en');
const localeText = (zh, en) => isEnglish() ? en : zh;

async function fetchJson(url) {
    const response = await fetch(url, {cache: 'no-store'});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = body?.detail;
        throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
    }
    return body;
}

async function loadMetadata(force = false) {
    if (metadata && !force) return metadata;
    if (metadataPromise && !force) return metadataPromise;
    metadataPromise = Promise.all([
        fetchJson('/api/agents'),
        fetchJson('/api/providers'),
        fetchJson('/api/ai-runtime/settings'),
    ]).then(([agents, providers, settings]) => {
        metadata = {
            agents: agents.agents || [],
            providers: (providers.providers || []).filter(item => item.enabled !== false && (item.chat_models || []).length),
            settings: settings || {},
        };
        metadataError = '';
        return metadata;
    }).catch(error => {
        metadataError = error.message || String(error);
        throw error;
    }).finally(() => {
        metadataPromise = null;
    });
    return metadataPromise;
}

function refreshBroadcastMetadata() {
    metadata = null;
    loadMetadata(true).then(() => {
        globalThis.dispatchEvent(new CustomEvent('syncanvas-agent-skills-metadata'));
    }).catch(() => {});
}

globalThis.addEventListener('message', event => {
    if (event.data?.type === 'studio-agent-skills-updated') refreshBroadcastMetadata();
});
try {
    const metadataChannel = new BroadcastChannel('studio-agent-skills');
    metadataChannel.onmessage = event => {
        if (event.data?.type === 'studio-agent-skills-updated') refreshBroadcastMetadata();
    };
} catch (_) {}

function firstValue(...values) {
    return values.find(value => value !== undefined && value !== null && value !== '') ?? '';
}

function migrateAgentV1(data, node) {
    return {
        agentId: String(firstValue(data.agentId, data.agent_id, node.agentId)),
        providerId: String(firstValue(data.providerId, data.provider_id, data.aiProvider, node.aiProvider)),
        textModel: String(firstValue(data.textModel, data.text_model, node.textModel)),
        visionModel: String(firstValue(data.visionModel, data.vision_model, node.visionModel)),
        message: String(firstValue(data.message, data.userInput, node.userInput)),
        expectJson: Boolean(firstValue(data.expectJson, data.expect_json, node.expectJson, false)),
    };
}

function migrateAgent({node, definition}) {
    const wasLegacy = node.type !== definition.type;
    let version = Number(node.nodeVersion) || 1;
    let data = node.data && typeof node.data === 'object' && !Array.isArray(node.data) ? {...node.data} : {};
    if (wasLegacy) version = 1;
    while (version < Number(definition.version || 1)) {
        if (version === 1) data = migrateAgentV1(data, node);
        else throw new Error(`Missing Agent state migration from version ${version}`);
        version += 1;
    }
    if (wasLegacy && version >= 2) data = migrateAgentV1(data, node);
    node.type = definition.type;
    node.extensionType = definition.type;
    node.nodeVersion = version;
    node.data = data;
    ['agentId', 'aiProvider', 'textModel', 'visionModel', 'userInput', 'expectJson', 'inputBindings'].forEach(key => delete node[key]);
    return node;
}

function selectedProvider(meta, providerId) {
    return meta.providers.find(item => item.id === providerId) || meta.providers[0] || null;
}

function ensureDefaults(node, meta) {
    const data = node.data || (node.data = {});
    const provider = selectedProvider(meta, data.providerId || meta.settings.provider_id);
    const models = provider?.chat_models || [];
    const agent = meta.agents.find(item => item.id === data.agentId) || meta.agents[0] || null;
    const next = {
        agentId: agent?.id || '',
        providerId: provider?.id || '',
        textModel: data.textModel || meta.settings.text_model || models[0] || '',
        visionModel: data.visionModel || meta.settings.vision_model || models[0] || '',
        message: data.message || '',
        expectJson: Boolean(data.expectJson),
    };
    const changed = Object.keys(next).some(key => data[key] !== next[key]);
    node.data = {...data, ...next};
    return changed;
}

function serializeAgent({node}) {
    ['promptDraftHtml', 'promptDraftText', 'runSettings'].forEach(key => delete node[key]);
    if (Array.isArray(node.images) && !node.images.length) delete node.images;
    return node;
}

function optionHtml(items, selected, escapeHtml, valueKey = 'id', labelKey = 'name') {
    return items.map(item => {
        const value = String(item[valueKey] || '');
        return `<option value="${escapeHtml(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(item[labelKey] || value)}</option>`;
    }).join('');
}

function modelOptions(meta, providerId, selected, escapeHtml) {
    const models = selectedProvider(meta, providerId)?.chat_models || [];
    const values = selected && !models.includes(selected) ? [selected, ...models] : models;
    return values.map(model => `<option value="${escapeHtml(model)}" ${model === selected ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('') || `<option value="">${localeText('未配置模型', 'No model configured')}</option>`;
}

function statusView(node, escapeHtml) {
    const label = (isEnglish() ? {
        queued: 'Queued', running: 'Running', succeeded: 'Completed', done: 'Completed',
        failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted',
    } : {
        queued: '排队中', running: '运行中', succeeded: '已完成', done: '已完成',
        failed: '运行失败', cancelled: '已取消', interrupted: '已中断',
    })[node.runStatus] || localeText('未运行', 'Not run');
    const statusClass = ['failed', 'cancelled', 'interrupted'].includes(node.runStatus)
        ? 'error'
        : ['succeeded', 'done'].includes(node.runStatus) ? 'success' : '';
    let output = node.extensionError || node.runError || node.outputText || '';
    if (!output && node.structuredOutput != null) {
        try { output = JSON.stringify(node.structuredOutput, null, 2); } catch (_) { output = String(node.structuredOutput); }
    }
    return `<div class="ai-node-status ${statusClass}"><span class="ai-node-dot"></span><span>${escapeHtml(label)}</span></div><div class="ai-node-output">${escapeHtml(output || localeText('运行结果会显示在这里', 'Run output will appear here'))}</div>`;
}

function requestMetadata(node, context, force = false) {
    loadMetadata(force).then(() => {
        context.update?.(node);
        context.save?.();
    }).catch(() => context.update?.(node));
}

function renderAgent({node, escapeHtml, context}) {
    if (!metadata) {
        if (!metadataPromise) requestMetadata(node, context);
        const message = metadataError || localeText('正在加载智能体配置...', 'Loading Agent configuration...');
        return `<div class="ai-node-body agent-extension-body"><div class="ai-node-empty">${escapeHtml(message)}</div>${metadataError ? `<button class="ai-node-run" type="button" data-agent-retry><i data-lucide="refresh-cw"></i><span>${localeText('重试', 'Retry')}</span></button>` : ''}</div>`;
    }
    if (ensureDefaults(node, metadata)) queueMicrotask(() => context.save?.());
    const data = node.data;
    const agent = metadata.agents.find(item => item.id === data.agentId);
    const activeModel = agent?.modelKind === 'vision' ? data.visionModel : data.textModel;
    return `<div class="ai-node-body agent-extension-body ${node.running ? 'is-running' : ''}">
        <label><span class="ai-node-label">${localeText('智能体', 'Agent')}</span><select class="ai-node-select" data-agent-id>${optionHtml(metadata.agents, data.agentId, escapeHtml)}</select></label>
        <div class="ai-node-row">
            <label><span class="ai-node-label">Provider</span><select class="ai-node-select" data-agent-provider>${optionHtml(metadata.providers, data.providerId, escapeHtml)}</select></label>
            <label><span class="ai-node-label">${agent?.modelKind === 'vision' ? localeText('视觉模型', 'Vision model') : localeText('文本模型', 'Text model')}</span><select class="ai-node-select" data-agent-model>${modelOptions(metadata, data.providerId, activeModel, escapeHtml)}</select></label>
        </div>
        <label><span class="ai-node-label">${localeText('输入', 'Input')}</span><textarea class="ai-node-textarea" data-agent-message placeholder="${localeText('输入任务，也可以连接上游文本或图片', 'Enter a task or connect upstream text/images')}">${escapeHtml(data.message)}</textarea></label>
        <label class="ai-node-status"><input data-agent-json type="checkbox" ${data.expectJson ? 'checked' : ''}><span>${localeText('按 JSON 解析输出', 'Parse output as JSON')}</span></label>
        ${statusView(node, escapeHtml)}
        <div class="ai-node-actions">
            <button class="ai-node-run" type="button" data-agent-run ${node.running ? 'disabled' : ''}><i data-lucide="play"></i><span>${node.running ? localeText('运行中', 'Running') : localeText('运行', 'Run')}</span></button>
            <button class="ai-node-cancel" type="button" data-agent-cancel title="${localeText('取消运行', 'Cancel run')}" ${node.running ? '' : 'disabled'}><i data-lucide="square"></i></button>
        </div>
    </div>`;
}

function bindAgent({root, node, update, save, run, cancel, context}) {
    const cleanups = [];
    const listen = (element, eventName, listener) => {
        if (!element) return;
        element.addEventListener(eventName, listener);
        cleanups.push(() => element.removeEventListener(eventName, listener));
    };
    const setData = (key, value, rerender = false) => {
        node.data = {...(node.data || {}), [key]: value};
        save?.();
        if (rerender) update?.(node);
    };
    root.querySelectorAll('textarea,input,select,button').forEach(control => {
        listen(control, 'mousedown', event => event.stopPropagation());
        listen(control, 'click', event => event.stopPropagation());
    });
    listen(root.querySelector('[data-agent-id]'), 'change', event => setData('agentId', event.target.value, true));
    listen(root.querySelector('[data-agent-provider]'), 'change', event => {
        const providerId = event.target.value;
        const models = selectedProvider(metadata, providerId)?.chat_models || [];
        node.data = {...node.data, providerId, textModel: models[0] || '', visionModel: models[0] || ''};
        save?.();
        update?.(node);
    });
    listen(root.querySelector('[data-agent-model]'), 'change', event => {
        const agent = metadata?.agents.find(item => item.id === node.data?.agentId);
        setData(agent?.modelKind === 'vision' ? 'visionModel' : 'textModel', event.target.value);
    });
    listen(root.querySelector('[data-agent-message]'), 'input', event => setData('message', event.target.value));
    listen(root.querySelector('[data-agent-json]'), 'change', event => setData('expectJson', event.target.checked));
    listen(root.querySelector('[data-agent-run]'), 'click', () => run().catch(error => context.error?.(error.message || String(error))));
    listen(root.querySelector('[data-agent-cancel]'), 'click', () => cancel().catch(error => context.error?.(error.message || String(error))));
    listen(root.querySelector('[data-agent-retry]'), 'click', () => requestMetadata(node, context, true));
    return () => cleanups.splice(0).forEach(cleanup => cleanup());
}

function renderLegacySkill(node, context) {
    const bridge = globalThis.SynCanvasAgentSkills;
    if (!bridge) return '';
    const rendered = bridge.render(node, context || {});
    return rendered?.outerHTML || '';
}

export function register(api) {
    api.registerNode('agent', {
        migrate: migrateAgent,
        serialize: serializeAgent,
        render: renderAgent,
        bind: bindAgent,
    });
    api.registerNode('skill', {legacy: true, render: ({node, context}) => renderLegacySkill(node, context)});
}

export const agentNodeType = AGENT_TYPE;
