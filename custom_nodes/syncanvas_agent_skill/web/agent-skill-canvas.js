(function(global){
    'use strict';

    let metadata = null;
    let metadataPromise = null;

    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
    const escapeAttr = escapeHtml;
    const isEnglish = () => (global.StudioI18n?.lang?.() || document.documentElement.lang || '').toLowerCase().startsWith('en');
    const localeText = (zh, en) => isEnglish() ? en : zh;

    async function fetchJson(url, options={}){
        const response = await fetch(url, options);
        if(!response.ok){
            let message = `${localeText('请求失败', 'Request failed')} (${response.status})`;
            try {
                const data = await response.json();
                message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
            } catch(error) {
                message = (await response.text()) || message;
            }
            throw new Error(message);
        }
        return response.json();
    }

    async function loadMetadata(force=false){
        if(metadata && !force) return metadata;
        if(metadataPromise && !force) return metadataPromise;
        metadataPromise = Promise.all([
            fetchJson('/api/agents'), fetchJson('/api/skills'), fetchJson('/api/providers'), fetchJson('/api/ai-runtime/settings')
        ]).then(([agents, skills, providers, settings]) => {
            metadata = {
                agents: agents.agents || [],
                skills: skills.skills || [],
                providers: (providers.providers || []).filter(item => item.enabled !== false && ['openai','apimart'].includes(item.protocol || 'openai') && (item.chat_models || []).length),
                settings: settings || {},
            };
            return metadata;
        }).finally(() => { metadataPromise = null; });
        return metadataPromise;
    }

    function refreshBroadcastMetadata(){
        metadata = null;
        loadMetadata(true).then(() => {
            global.dispatchEvent(new CustomEvent('syncanvas-agent-skills-metadata'));
        }).catch(() => {});
    }

    global.addEventListener('message', event => {
        if(event.data?.type === 'studio-agent-skills-updated') refreshBroadcastMetadata();
    });
    try {
        const metadataChannel = new BroadcastChannel('studio-agent-skills');
        metadataChannel.onmessage = event => {
            if(event.data?.type === 'studio-agent-skills-updated') refreshBroadcastMetadata();
        };
    } catch(error) {}

    function provider(meta, id){
        return meta.providers.find(item => item.id === id) || meta.providers[0] || null;
    }

    function ensureDefaults(node, meta){
        const selectedProvider = provider(meta, node.aiProvider || meta.settings.provider_id);
        const models = selectedProvider?.chat_models || [];
        node.aiProvider = selectedProvider?.id || '';
        node.textModel = node.textModel || meta.settings.text_model || models[0] || '';
        node.visionModel = node.visionModel || meta.settings.vision_model || models[0] || '';
        node.outputText = node.outputText || '';
        node.structuredOutput = node.structuredOutput || null;
        node.inputBindings = node.inputBindings || {};
        if(node.type === 'agent' || node.type === 'smart-agent'){
            node.agentId = node.agentId || meta.agents[0]?.id || '';
            node.userInput = node.userInput || '';
            node.expectJson = Boolean(node.expectJson);
        } else {
            node.skillId = node.skillId || meta.skills[0]?.id || '';
            node.skillInput = node.skillInput || {};
        }
    }

    function options(items, selected, valueKey='id', labelKey='name'){
        return items.map(item => {
            const value = item[valueKey];
            const label = isEnglish() ? (item.nameEn || item[labelKey] || value) : (item[labelKey] || value);
            return `<option value="${escapeAttr(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`;
        }).join('');
    }

    function modelOptions(meta, providerId, selected){
        const models = provider(meta, providerId)?.chat_models || [];
        const values = selected && !models.includes(selected) ? [selected, ...models] : models;
        return values.map(model => `<option value="${escapeAttr(model)}" ${model === selected ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('') || `<option value="">${localeText('未配置模型', 'No model configured')}</option>`;
    }

    function fieldSchemaType(schema){
        if(!schema) return 'string';
        if(schema.type) return schema.type;
        if(schema.anyOf){
            const concrete = schema.anyOf.find(item => item.type && item.type !== 'null');
            return concrete?.type || 'object';
        }
        if(schema.$ref) return 'object';
        return 'string';
    }

    function fieldDefault(schema){
        if(schema && Object.prototype.hasOwnProperty.call(schema, 'default')) return schema.default;
        const type = fieldSchemaType(schema);
        if(type === 'array') return [];
        if(type === 'object') return {};
        if(type === 'boolean') return false;
        if(type === 'number' || type === 'integer') return 0;
        return '';
    }

    function fieldControl(name, schema, value){
        const type = fieldSchemaType(schema);
        const enumValues = schema?.enum || schema?.anyOf?.find(item => item.enum)?.enum;
        if(enumValues){
            return `<select class="ai-node-select" data-ai-field="${escapeAttr(name)}">${enumValues.map(item => `<option value="${escapeAttr(item)}" ${String(item) === String(value) ? 'selected' : ''}>${escapeHtml(item)}</option>`).join('')}</select>`;
        }
        if(type === 'boolean') return `<select class="ai-node-select" data-ai-field="${escapeAttr(name)}"><option value="false" ${!value ? 'selected' : ''}>${localeText('否', 'No')}</option><option value="true" ${value ? 'selected' : ''}>${localeText('是', 'Yes')}</option></select>`;
        if(type === 'number' || type === 'integer') return `<input class="ai-node-input" data-ai-field="${escapeAttr(name)}" type="number" value="${escapeAttr(value)}">`;
        if(type === 'array'){
            const text = Array.isArray(value) ? value.join('\n') : String(value || '');
            return `<textarea class="ai-node-textarea" data-ai-field="${escapeAttr(name)}" data-ai-type="array" placeholder="${localeText('每行一项', 'One item per line')}">${escapeHtml(text)}</textarea>`;
        }
        if(type === 'object'){
            const text = value && typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value || '');
            return `<textarea class="ai-node-textarea ai-node-json" data-ai-field="${escapeAttr(name)}" data-ai-type="object" placeholder="${localeText('JSON 对象', 'JSON object')}">${escapeHtml(text)}</textarea>`;
        }
        return `<textarea class="ai-node-textarea" data-ai-field="${escapeAttr(name)}">${escapeHtml(value ?? '')}</textarea>`;
    }

    function sourceOptions(sources, binding){
        return `<option value="">${localeText('手动填写', 'Manual input')}</option>${sources.map(source => `<option value="${escapeAttr(source.id)}" ${binding?.sourceNodeId === source.id ? 'selected' : ''}>${localeText('上游', 'Upstream')}: ${escapeHtml(source.label || source.id)}</option>`).join('')}`;
    }

    function sourceFieldOptions(sources, binding){
        const source = sources.find(item => item.id === binding?.sourceNodeId);
        if(!source) return '';
        const fields = new Set();
        if(source.skillId){
            const skill = metadata?.skills.find(item => item.id === source.skillId);
            Object.keys(skill?.outputSchema?.properties || {}).forEach(field => fields.add(field));
        }
        if(source.output && typeof source.output === 'object' && !Array.isArray(source.output)){
            Object.keys(source.output).forEach(field => fields.add(field));
        }
        const selected = binding?.sourceField || 'auto';
        const choices = [
            ['auto', localeText('自动选择', 'Auto select')],
            ['output_text', localeText('文本输出', 'Text output')],
            ['$json', localeText('完整 JSON', 'Full JSON')],
            ['images', localeText('图片列表', 'Image list')],
            ...[...fields].map(field => [field, `${localeText('字段', 'Field')}: ${field}`]),
        ];
        return choices.map(([value, label]) => `<option value="${escapeAttr(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');
    }

    function renderAgent(node, meta, context){
        const agent = meta.agents.find(item => item.id === node.agentId);
        const wrap = document.createElement('div');
        wrap.className = 'ai-node-body';
        wrap.classList.toggle('is-running', Boolean(node.running));
        wrap.innerHTML = `
            <div><span class="ai-node-label">${localeText('智能体', 'Agent')}</span><select class="ai-node-select" data-ai-agent>${options(meta.agents, node.agentId)}</select></div>
            <div class="ai-node-row">
                <div><span class="ai-node-label">Provider</span><select class="ai-node-select" data-ai-provider>${options(meta.providers, node.aiProvider)}</select></div>
                <div><span class="ai-node-label">${agent?.modelKind === 'vision' ? localeText('视觉模型', 'Vision model') : localeText('文本模型', 'Text model')}</span><select class="ai-node-select" data-ai-active-model>${modelOptions(meta, node.aiProvider, agent?.modelKind === 'vision' ? node.visionModel : node.textModel)}</select></div>
            </div>
            <div><span class="ai-node-label">${localeText('输入', 'Input')}</span><textarea class="ai-node-textarea" data-ai-user-input placeholder="${localeText('输入任务，也可以连接上游文本或图片', 'Enter a task or connect upstream text/images')}">${escapeHtml(node.userInput || '')}</textarea></div>
            <label class="ai-node-status"><input data-ai-json type="checkbox" ${node.expectJson ? 'checked' : ''} style="width:14px;height:14px">${localeText('按 JSON 解析输出', 'Parse output as JSON')}</label>
            ${resultHtml(node)}
            ${actionsHtml(node)}
        `;
        bindCommon(wrap, node, meta, context);
        wrap.querySelector('[data-ai-agent]').onchange = event => { node.agentId = event.target.value; context.changed(true); };
        wrap.querySelector('[data-ai-user-input]').oninput = event => { node.userInput = event.target.value; context.changed(false); };
        wrap.querySelector('[data-ai-json]').onchange = event => { node.expectJson = event.target.checked; context.changed(false); };
        return wrap;
    }

    function renderSkill(node, meta, context){
        const skill = meta.skills.find(item => item.id === node.skillId);
        const selectableSkills = meta.skills.filter(item => !item.hidden || item.id === node.skillId);
        const properties = skill?.inputSchema?.properties || {};
        Object.entries(properties).forEach(([name, schema]) => {
            if(!Object.prototype.hasOwnProperty.call(node.skillInput, name)) node.skillInput[name] = fieldDefault(schema);
        });
        const wrap = document.createElement('div');
        wrap.className = 'ai-node-body';
        wrap.classList.toggle('is-running', Boolean(node.running));
        wrap.innerHTML = `
            <div><span class="ai-node-label">${localeText('AI 工作流', 'AI Workflow')}</span><select class="ai-node-select" data-ai-skill>${options(selectableSkills, node.skillId)}</select></div>
            <div class="ai-node-row">
                <div><span class="ai-node-label">Provider</span><select class="ai-node-select" data-ai-provider>${options(meta.providers, node.aiProvider)}</select></div>
                <div><span class="ai-node-label">${localeText('模型组', 'Model group')}</span><select class="ai-node-select" disabled><option>${localeText('文本 + 视觉', 'Text + vision')}</option></select></div>
            </div>
            <div class="ai-skill-fields">${Object.entries(properties).map(([name, schema]) => `
                <div class="ai-skill-field">
                    <div class="ai-skill-field-main"><span class="ai-node-label">${escapeHtml(schema.title || name)}</span>${fieldControl(name, schema, node.skillInput[name])}</div>
                    <div class="ai-skill-binding-stack">
                        <select class="ai-skill-binding" data-ai-binding="${escapeAttr(name)}">${sourceOptions(context.sources || [], node.inputBindings[name])}</select>
                        ${node.inputBindings[name]?.sourceNodeId ? `<select class="ai-skill-source-field" data-ai-source-field="${escapeAttr(name)}">${sourceFieldOptions(context.sources || [], node.inputBindings[name])}</select>` : ''}
                    </div>
                </div>`).join('') || `<div class="ai-node-empty">${localeText('此 AI 工作流没有输入字段', 'This AI Workflow has no input fields')}</div>`}</div>
            ${resultHtml(node)}
            ${actionsHtml(node)}
        `;
        bindCommon(wrap, node, meta, context);
        wrap.querySelector('[data-ai-skill]').onchange = event => {
            node.skillId = event.target.value;
            node.skillInput = {};
            node.inputBindings = {};
            context.changed(true);
        };
        wrap.querySelectorAll('[data-ai-field]').forEach(control => {
            control.oninput = () => {
                const name = control.dataset.aiField;
                const schema = properties[name];
                const type = control.dataset.aiType || fieldSchemaType(schema);
                if(type === 'array') node.skillInput[name] = control.value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
                else if(type === 'object'){
                    try { node.skillInput[name] = control.value.trim() ? JSON.parse(control.value) : {}; control.setCustomValidity(''); }
                    catch(error) { control.setCustomValidity(localeText('请输入有效 JSON', 'Enter valid JSON')); }
                } else if(type === 'boolean') node.skillInput[name] = control.value === 'true';
                else if(type === 'number' || type === 'integer') node.skillInput[name] = Number(control.value || 0);
                else node.skillInput[name] = control.value;
                context.changed(false);
            };
        });
        wrap.querySelectorAll('[data-ai-binding]').forEach(select => {
            select.onchange = () => {
                const field = select.dataset.aiBinding;
                if(select.value) node.inputBindings[field] = {mode:'connection', sourceNodeId:select.value, sourceField:'auto'};
                else delete node.inputBindings[field];
                if(context.bindField) context.bindField(field, select.value, 'auto');
                context.changed(true);
            };
        });
        wrap.querySelectorAll('[data-ai-source-field]').forEach(select => {
            select.onchange = () => {
                const field = select.dataset.aiSourceField;
                const binding = node.inputBindings[field];
                if(!binding) return;
                binding.sourceField = select.value || 'auto';
                if(context.bindField) context.bindField(field, binding.sourceNodeId, binding.sourceField);
                context.changed(false);
            };
        });
        return wrap;
    }

    function resultHtml(node){
        const status = node.runStatus || '';
        const statusText = (isEnglish()
            ? {queued:'Queued',running:'Running',succeeded:'Completed',done:'Completed',failed:'Failed',cancelled:'Cancelled',interrupted:'Interrupted'}
            : {queued:'排队中',running:'运行中',succeeded:'已完成',done:'已完成',failed:'运行失败',cancelled:'已取消',interrupted:'已中断'})[status] || localeText('未运行', 'Not run');
        const statusClass = ['failed','cancelled','interrupted'].includes(status) ? 'error' : ['succeeded','done'].includes(status) ? 'success' : '';
        const output = node.runError || node.outputText || (node.structuredOutput ? JSON.stringify(node.structuredOutput, null, 2) : localeText('运行结果会显示在这里', 'Run output will appear here'));
        return `<div class="ai-node-status ${statusClass}"><span class="ai-node-dot"></span><span>${escapeHtml(statusText)}</span>${node.runId ? `<span title="${escapeAttr(node.runId)}">· ${escapeHtml(node.runId.slice(0,8))}</span>` : ''}</div><div class="ai-node-output">${escapeHtml(output)}</div>`;
    }

    function actionsHtml(node){
        return `<div class="ai-node-actions"><button class="ai-node-run" type="button" data-ai-run ${node.running ? 'disabled' : ''}><i data-lucide="play"></i><span>${node.running ? localeText('运行中', 'Running') : localeText('运行', 'Run')}</span></button><button class="ai-node-cancel" type="button" data-ai-cancel title="${localeText('取消运行', 'Cancel run')}" ${node.running ? '' : 'disabled'}><i data-lucide="square"></i></button></div>`;
    }

    function bindCommon(wrap, node, meta, context){
        const providerSelect = wrap.querySelector('[data-ai-provider]');
        providerSelect.onchange = event => {
            node.aiProvider = event.target.value;
            const models = provider(meta, node.aiProvider)?.chat_models || [];
            node.textModel = models[0] || '';
            node.visionModel = models[0] || '';
            context.changed(true);
        };
        const modelSelect = wrap.querySelector('[data-ai-active-model]');
        if(modelSelect) modelSelect.onchange = event => {
            const agent = meta.agents.find(item => item.id === node.agentId);
            if(agent?.modelKind === 'vision') node.visionModel = event.target.value;
            else node.textModel = event.target.value;
            context.changed(false);
        };
        wrap.querySelectorAll('textarea,input,select,button').forEach(control => {
            control.addEventListener('mousedown', event => event.stopPropagation());
            control.addEventListener('click', event => event.stopPropagation());
        });
        wrap.querySelector('[data-ai-run]').onclick = event => { event.stopPropagation(); context.run(); };
        wrap.querySelector('[data-ai-cancel]').onclick = event => { event.stopPropagation(); context.cancel(); };
        if(global.lucide) global.lucide.createIcons({nodes:[wrap]});
    }

    function render(node, context){
        if(!metadata){
            const placeholder = document.createElement('div');
            placeholder.className = 'ai-node-empty';
            placeholder.textContent = localeText('正在加载智能体/AI 工作流配置...', 'Loading Agent / AI Workflow configuration...');
            loadMetadata().then(() => context.changed(true)).catch(error => { placeholder.textContent = error.message; });
            return placeholder;
        }
        ensureDefaults(node, metadata);
        return (node.type === 'agent' || node.type === 'smart-agent') ? renderAgent(node, metadata, context) : renderSkill(node, metadata, context);
    }

    function exactSourceValue(source, sourceField){
        if(!sourceField || sourceField === 'auto') return undefined;
        if(sourceField === 'output_text') return source.text || '';
        if(sourceField === '$json') return source.output ?? null;
        if(sourceField === 'images') return source.images || [];
        return String(sourceField).split('.').reduce((value, key) => value == null ? undefined : value[key], source.output);
    }

    function sourceValue(source, fieldName, schema, binding={}){
        const exact = exactSourceValue(source, binding.sourceField);
        if(exact !== undefined) return exact;
        const type = fieldSchemaType(schema);
        const lower = fieldName.toLowerCase();
        if(type === 'array'){
            if(lower.includes('image')) return source.images || [];
            if(Array.isArray(source.output)) return source.output;
            if(source.output && Array.isArray(source.output[fieldName])) return source.output[fieldName];
            if(source.output && Array.isArray(source.output.lines)) return source.output.lines;
            if(source.output && Array.isArray(source.output.outline)) return source.output.outline;
            return source.text ? source.text.split(/\r?\n/).filter(Boolean) : [];
        }
        if(type === 'object') return source.output && typeof source.output === 'object' ? source.output : {};
        if(type === 'number' || type === 'integer') return Number(source.text || 0);
        if(type === 'boolean') return Boolean(source.text);
        if(lower.includes('image') && source.images?.length) return source.images[0];
        return source.text || '';
    }

    function buildSkillInput(node, sources){
        const skill = metadata?.skills.find(item => item.id === node.skillId);
        const properties = skill?.inputSchema?.properties || {};
        const result = {...(node.skillInput || {})};
        Object.entries(node.inputBindings || {}).forEach(([field, binding]) => {
            const source = sources.find(item => item.id === binding.sourceNodeId);
            if(source) result[field] = sourceValue(source, field, properties[field], binding);
        });
        Object.entries(properties).forEach(([field, schema]) => {
            if(node.inputBindings?.[field]) return;
            const current = result[field];
            const empty = current == null || current === '' || (Array.isArray(current) && !current.length) || (typeof current === 'object' && !Array.isArray(current) && !Object.keys(current).length);
            if(!empty) return;
            const candidates = sources.map(source => sourceValue(source, field, schema));
            const candidate = candidates.find(value => value != null && value !== '' && (!Array.isArray(value) || value.length) && (typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length));
            if(candidate !== undefined) result[field] = candidate;
        });
        return result;
    }

    function combinedInputText(node, sources){
        return [node.userInput || '', ...sources.map(source => source.text || '')].map(value => String(value).trim()).filter(Boolean).join('\n\n');
    }

    async function runNode(node, context){
        await loadMetadata();
        ensureDefaults(node, metadata);
        const sources = context.sources || [];
        const isAgent = node.type === 'agent' || node.type === 'smart-agent';
        const endpoint = isAgent ? `/api/agents/${encodeURIComponent(node.agentId)}/runs` : `/api/skills/${encodeURIComponent(node.skillId)}/runs`;
        const input = isAgent ? {
            message: combinedInputText(node, sources),
            images: sources.flatMap(source => source.images || []).filter(Boolean).slice(0,8),
        } : buildSkillInput(node, sources);
        node.running = true;
        node.runStatus = 'queued';
        node.runError = '';
        context.updated();
        try {
            const created = await fetchJson(endpoint, {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
                    input,
                    provider_id:node.aiProvider || '',
                    text_model:node.textModel || '',
                    vision_model:node.visionModel || '',
                    canvas_id:context.canvasId || '',
                    node_id:node.id,
                    ...(isAgent ? {expect_json:Boolean(node.expectJson)} : {}),
                })
            });
            node.runId = created.run_id;
            node.runStatus = created.status;
            context.updated();
            while(true){
                await new Promise(resolve => setTimeout(resolve, 850));
                const record = await fetchJson(`/api/ai-runs/${encodeURIComponent(node.runId)}`);
                node.runStatus = record.status;
                if(['succeeded','failed','cancelled','interrupted'].includes(record.status)){
                    node.running = false;
                    node.outputText = record.output_text || '';
                    node.structuredOutput = record.output ?? null;
                    node.runError = record.error || '';
                    node.runWarnings = record.warnings || [];
                    node.fallbackUsed = Boolean(record.fallback_used);
                    context.updated();
                    if(record.status !== 'succeeded') throw new Error(record.error || `${localeText('运行', 'Run ')}${record.status}`);
                    return record;
                }
                context.updated(false);
            }
        } catch(error) {
            node.running = false;
            if(!['cancelled','interrupted'].includes(node.runStatus)) node.runStatus = 'failed';
            node.runError = error.message || String(error);
            context.updated();
            throw error;
        }
    }

    async function cancelNode(node, context){
        if(!node.runId) return;
        try {
            const record = await fetchJson(`/api/ai-runs/${encodeURIComponent(node.runId)}`, {method:'DELETE'});
            node.runStatus = record.status || 'cancelled';
            node.running = false;
            node.runError = record.error || localeText('运行已取消', 'Run cancelled');
            context.updated();
        } catch(error) {
            node.runError = error.message;
            context.updated();
        }
    }

    global.SynCanvasAgentSkills = {
        loadMetadata,
        render,
        runNode,
        cancelNode,
        buildSkillInput,
        get metadata(){ return metadata; },
    };
})(window);
