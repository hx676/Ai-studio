(function(){
    'use strict';

    const state = {
        agents: [],
        skills: [],
        providers: [],
        settings: {},
        selectedAgentId: '',
        selectedSkillId: '',
        activeTab: 'agents',
        dirty: false,
    };

    const $ = selector => document.querySelector(selector);
    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
    const escapeAttr = escapeHtml;

    async function fetchJson(url, options={}){
        const response = await fetch(url, options);
        if(!response.ok){
            let message = `请求失败 (${response.status})`;
            try {
                const data = await response.json();
                message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
            } catch(error) {
                message = (await response.text()) || message;
            }
            throw new Error(message);
        }
        return response.status === 204 ? null : response.json();
    }

    function toast(message, error=false){
        const el = $('#toast');
        el.textContent = message;
        el.classList.toggle('error', error);
        el.classList.add('show');
        clearTimeout(toast.timer);
        toast.timer = setTimeout(() => el.classList.remove('show'), 2600);
    }

    function setStatus(message, kind=''){
        const el = $('#pageStatus');
        el.textContent = message;
        el.className = `status ${kind}`.trim();
    }

    async function confirmAction(message, options={}){
        if(window.StudioDialog) return StudioDialog.confirm(message, options);
        return window.confirm(message);
    }

    async function promptName(title, value=''){
        if(window.StudioDialog){
            return StudioDialog.formPrompt({
                title,
                label:'名称',
                value,
                placeholder:'输入名称',
                confirmText:'创建',
                validate:name => name.trim() ? true : '名称不能为空',
            });
        }
        return window.prompt(title, value);
    }

    async function confirmDiscard(){
        if(!state.dirty) return true;
        return confirmAction('当前修改尚未保存，确定放弃吗？', {title:'放弃修改', danger:true, confirmText:'放弃'});
    }

    function notifyDefinitionsChanged(){
        const message = {type:'studio-agent-skills-updated', at:Date.now()};
        try { window.parent.postMessage(message, '*'); } catch(error) {}
        try {
            const channel = new BroadcastChannel('studio-agent-skills');
            channel.postMessage(message);
            channel.close();
        } catch(error) {}
    }

    function updateCounts(){
        $('#agentCount').textContent = state.agents.length;
        $('#skillCount').textContent = state.skills.length;
    }

    async function reloadCatalog(selection={}){
        const [agentData, skillData] = await Promise.all([fetchJson('/api/agents'), fetchJson('/api/skills')]);
        state.agents = agentData.agents || [];
        state.skills = (skillData.skills || []).filter(item => !item.hidden);
        const nextAgentId = selection.agentId || state.selectedAgentId;
        const nextSkillId = selection.skillId || state.selectedSkillId;
        state.selectedAgentId = state.agents.some(item => item.id === nextAgentId) ? nextAgentId : state.agents[0]?.id || '';
        state.selectedSkillId = state.skills.some(item => item.id === nextSkillId) ? nextSkillId : state.skills[0]?.id || '';
        state.dirty = false;
        updateCounts();
        renderAgentList();
        renderAgentEditor();
        renderSkillList();
        renderSkillEditor();
    }

    function modelOptions(selected=''){
        const provider = state.providers.find(item => item.id === $('#runtimeProvider').value);
        const models = provider?.chat_models || [];
        const values = selected && !models.includes(selected) ? [selected, ...models] : models;
        return values.length
            ? values.map(model => `<option value="${escapeAttr(model)}" ${model === selected ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('')
            : '<option value="">暂无聊天模型</option>';
    }

    function renderRuntime(){
        const usable = state.providers.filter(item => item.enabled !== false && ['openai','apimart'].includes(item.protocol || 'openai') && (item.chat_models || []).length);
        const currentId = state.settings.provider_id || usable[0]?.id || '';
        $('#runtimeProvider').innerHTML = usable.length
            ? usable.map(item => `<option value="${escapeAttr(item.id)}" ${item.id === currentId ? 'selected' : ''}>${escapeHtml(item.name || item.id)}</option>`).join('')
            : '<option value="">没有可用 Provider</option>';
        $('#runtimeTextModel').innerHTML = modelOptions(state.settings.text_model || '');
        $('#runtimeVisionModel').innerHTML = modelOptions(state.settings.vision_model || '');
    }

    function filteredAgents(){
        const query = $('#agentSearch').value.trim().toLowerCase();
        const kind = $('#agentKindFilter').value;
        return state.agents.filter(agent => {
            if(kind === 'unbound' && !agent.unbound) return false;
            if(kind !== 'all' && kind !== 'unbound' && agent.modelKind !== kind) return false;
            return !query || `${agent.id} ${agent.name} ${agent.description}`.toLowerCase().includes(query);
        });
    }

    function renderAgentList(){
        const list = filteredAgents();
        $('#agentList').innerHTML = list.length ? list.map(agent => `
            <button class="agent-row ${agent.id === state.selectedAgentId ? 'active' : ''}" type="button" data-agent-id="${escapeAttr(agent.id)}">
                <span>
                    <span class="agent-name">${escapeHtml(agent.name)}</span>
                    <span class="agent-id">${escapeHtml(agent.id)} · ${agent.builtIn ? '内置' : '自定义'}</span>
                </span>
                <span class="kind-badge ${agent.modelKind === 'vision' ? 'vision' : ''}">${agent.modelKind === 'vision' ? '视觉' : '文本'}</span>
            </button>
        `).join('') : '<div class="agent-list-empty">没有符合条件的智能体</div>';
        $('#agentList').querySelectorAll('[data-agent-id]').forEach(button => {
            button.onclick = () => selectAgent(button.dataset.agentId);
        });
    }

    function currentAgent(){
        return state.agents.find(item => item.id === state.selectedAgentId) || null;
    }

    async function selectAgent(agentId){
        if(agentId === state.selectedAgentId) return;
        if(!await confirmDiscard()) return;
        state.selectedAgentId = agentId;
        state.dirty = false;
        renderAgentList();
        renderAgentEditor();
    }

    function renderAgentEditor(){
        const agent = currentAgent();
        if(!agent){
            $('#agentEditor').innerHTML = '<div class="empty-editor">选择一个智能体开始编辑</div>';
            return;
        }
        const usedBy = (agent.usedBy || []).map(id => state.skills.find(skill => skill.id === id) || {id, name:id});
        $('#agentEditor').innerHTML = `
            <div class="editor-head">
                <div>
                    <div class="editor-title">${escapeHtml(agent.name)} <span class="origin-badge ${agent.builtIn ? '' : 'custom'}">${agent.builtIn ? '内置' : '自定义'}</span></div>
                    <div class="agent-id">${escapeHtml(agent.id)}${agent.unbound ? ' · 可在 Agent 节点中直接运行' : ''}</div>
                </div>
                <div class="editor-actions">
                    <button class="btn" type="button" id="duplicateAgentBtn"><i data-lucide="copy-plus"></i><span>复制</span></button>
                    <button class="btn" type="button" id="exportAgentsBtn"><i data-lucide="download"></i><span>导出</span></button>
                    <button class="btn" type="button" id="importAgentsBtn"><i data-lucide="upload"></i><span>导入</span></button>
                    ${agent.builtIn
                        ? '<button class="btn danger" type="button" id="resetAgentBtn"><i data-lucide="rotate-ccw"></i><span>恢复默认</span></button>'
                        : '<button class="btn danger" type="button" id="deleteAgentBtn"><i data-lucide="trash-2"></i><span>删除</span></button>'}
                    <button class="btn primary" type="button" id="saveAgentBtn"><i data-lucide="save"></i><span>保存</span></button>
                </div>
            </div>
            <form id="agentForm" class="editor-form">
                <div class="form-grid">
                    <div class="field"><label for="agentName">名称</label><input id="agentName" maxlength="120" value="${escapeAttr(agent.name)}"></div>
                    <div class="field"><label for="agentKind">模型类型</label><select id="agentKind"><option value="text" ${agent.modelKind === 'text' ? 'selected' : ''}>文本</option><option value="vision" ${agent.modelKind === 'vision' ? 'selected' : ''}>视觉</option></select></div>
                    <div class="field"><label for="agentTemperature">温度</label><input id="agentTemperature" type="number" min="0" max="2" step="0.05" value="${escapeAttr(agent.temperature)}"></div>
                </div>
                <div class="field"><label for="agentDescription">说明</label><input id="agentDescription" maxlength="500" value="${escapeAttr(agent.description)}"></div>
                <div class="used-by"><span>被 AI 工作流使用：</span>${usedBy.length ? usedBy.map(skill => `<span class="tag">${escapeHtml(skill.name)}</span>`).join('') : '<span class="tag">无</span>'}</div>
                <div class="field"><label for="agentPrompt">System Prompt</label><textarea id="agentPrompt" class="prompt-area" maxlength="50000">${escapeHtml(agent.systemPrompt)}</textarea></div>
            </form>`;
        $('#agentForm').querySelectorAll('input,select,textarea').forEach(control => control.addEventListener('input', () => { state.dirty = true; }));
        $('#saveAgentBtn').onclick = saveAgent;
        $('#duplicateAgentBtn').onclick = duplicateAgent;
        $('#resetAgentBtn')?.addEventListener('click', resetAgent);
        $('#deleteAgentBtn')?.addEventListener('click', deleteAgent);
        $('#exportAgentsBtn').onclick = exportAgents;
        $('#importAgentsBtn').onclick = () => $('#agentImportInput').click();
        if(window.lucide) lucide.createIcons();
    }

    function agentFormPayload(){
        return {
            name:$('#agentName').value.trim(),
            description:$('#agentDescription').value.trim(),
            modelKind:$('#agentKind').value,
            temperature:Number($('#agentTemperature').value),
            systemPrompt:$('#agentPrompt').value,
        };
    }

    async function createAgent(){
        const name = await promptName('新建智能体');
        if(name == null) return;
        try {
            const data = await fetchJson('/api/agents', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
                id:'', name:name.trim(), description:'', modelKind:'text', temperature:0.5,
                systemPrompt:'你是一个专业的 AI 助手。请准确理解用户任务，给出清晰、可执行的结果。',
            })});
            await reloadCatalog({agentId:data.agent.id});
            notifyDefinitionsChanged();
            toast('智能体已创建');
        } catch(error) { toast(error.message, true); }
    }

    async function saveAgent(){
        const agent = currentAgent();
        if(!agent) return;
        try {
            await fetchJson(`/api/agents/${encodeURIComponent(agent.id)}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(agentFormPayload())});
            await reloadCatalog({agentId:agent.id});
            notifyDefinitionsChanged();
            toast('智能体已保存');
        } catch(error) { toast(error.message, true); }
    }

    async function duplicateAgent(){
        const agent = currentAgent();
        if(!agent) return;
        const name = await promptName('复制智能体', `${agent.name} 副本`);
        if(name == null) return;
        try {
            const data = await fetchJson('/api/agents', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:'', ...agentFormPayload(), name:name.trim()})});
            await reloadCatalog({agentId:data.agent.id});
            notifyDefinitionsChanged();
            toast('已创建智能体副本');
        } catch(error) { toast(error.message, true); }
    }

    async function resetAgent(){
        const agent = currentAgent();
        if(!agent || !await confirmAction(`恢复“${agent.name}”的内置默认 Prompt？`, {title:'恢复默认', danger:true, confirmText:'恢复'})) return;
        try {
            await fetchJson(`/api/agents/${encodeURIComponent(agent.id)}/reset`, {method:'POST'});
            await reloadCatalog({agentId:agent.id});
            notifyDefinitionsChanged();
            toast('已恢复内置默认值');
        } catch(error) { toast(error.message, true); }
    }

    async function deleteAgent(){
        const agent = currentAgent();
        if(!agent || !await confirmAction(`删除自定义智能体“${agent.name}”？`, {title:'删除智能体', danger:true, confirmText:'删除'})) return;
        try {
            await fetchJson(`/api/agents/${encodeURIComponent(agent.id)}`, {method:'DELETE'});
            state.selectedAgentId = '';
            await reloadCatalog();
            notifyDefinitionsChanged();
            toast('智能体已删除');
        } catch(error) { toast(error.message, true); }
    }

    async function exportAgents(){
        try {
            const response = await fetch('/api/agents/export');
            if(!response.ok) throw new Error('导出失败');
            const blob = await response.blob();
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'syncanvas-agents.json';
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(link.href);
        } catch(error) { toast(error.message, true); }
    }

    async function importAgents(file){
        if(!file) return;
        try {
            const raw = JSON.parse(await file.text());
            const agents = Array.isArray(raw) ? raw : raw.agents;
            const data = await fetchJson('/api/agents/import', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({agents})});
            await reloadCatalog();
            notifyDefinitionsChanged();
            toast(`已导入 ${data.imported} 个智能体`);
        } catch(error) { toast(`导入失败：${error.message}`, true); }
        finally { $('#agentImportInput').value = ''; }
    }

    function schemaProperties(schema){ return schema?.properties || {}; }
    function schemaType(value){
        if(value?.enum) return value.enum.join(' | ');
        if(value?.type === 'array') return `${value.items?.type || 'any'}[]`;
        if(value?.anyOf) return value.anyOf.map(item => item.type || item.$ref?.split('/').pop() || 'object').join(' | ');
        if(value?.$ref) return value.$ref.split('/').pop();
        return value?.type || 'object';
    }

    function filteredSkills(){
        const query = $('#skillSearch').value.trim().toLowerCase();
        return state.skills.filter(skill => !query || `${skill.id} ${skill.name} ${skill.description} ${(skill.agents || []).join(' ')}`.toLowerCase().includes(query));
    }

    function renderSkillList(){
        const skills = filteredSkills();
        $('#skillList').innerHTML = skills.length ? skills.map(skill => `
            <button class="skill-row ${skill.id === state.selectedSkillId ? 'active' : ''}" type="button" data-skill-id="${escapeAttr(skill.id)}">
                <span class="skill-row-main"><span class="agent-name">${escapeHtml(skill.name)}</span><span class="agent-id">${escapeHtml(skill.id)}</span></span>
                <span class="origin-badge">内置</span>
            </button>`).join('') : '<div class="agent-list-empty">没有符合条件的 AI 工作流</div>';
        $('#skillList').querySelectorAll('[data-skill-id]').forEach(button => button.onclick = () => selectSkill(button.dataset.skillId));
    }

    function currentSkill(){ return state.skills.find(item => item.id === state.selectedSkillId) || null; }

    async function selectSkill(skillId){
        if(skillId === state.selectedSkillId) return;
        if(!await confirmDiscard()) return;
        state.selectedSkillId = skillId;
        state.dirty = false;
        renderSkillList();
        renderSkillEditor();
    }

    function skillSchemaHtml(skill){
        const inputs = schemaProperties(skill.inputSchema);
        const outputs = schemaProperties(skill.outputSchema);
        return `
            <div class="schema-block"><div class="schema-title">输入字段</div><div class="schema-fields">${Object.entries(inputs).map(([name, value]) => `<span class="schema-field">${escapeHtml(name)}: ${escapeHtml(schemaType(value))}</span>`).join('') || '<span class="tag">无</span>'}</div></div>
            <div class="schema-block"><div class="schema-title">输出字段</div><div class="schema-fields">${Object.entries(outputs).map(([name, value]) => `<span class="schema-field">${escapeHtml(name)}: ${escapeHtml(schemaType(value))}</span>`).join('') || '<span class="tag">动态 JSON</span>'}</div></div>`;
    }

    function renderSkillEditor(){
        const skill = currentSkill();
        if(!skill){
            $('#skillEditor').innerHTML = '<div class="empty-editor">选择一个 AI 工作流查看定义</div>';
            return;
        }
        $('#skillEditor').innerHTML = `
            <div class="editor-head">
                <div><div class="editor-title">${escapeHtml(skill.name)} <span class="origin-badge">内置</span></div><div class="agent-id">${escapeHtml(skill.id)}</div></div>
            </div>
            <p class="readonly-note">内置 AI 工作流定义只读。</p>
            <div class="skill-item">
                <div class="skill-desc">${escapeHtml(skill.description)}</div>
                <div class="skill-agents">${(skill.agents || []).map(id => `<span class="tag">${escapeHtml(id)}</span>`).join('')}</div>
                ${skillSchemaHtml(skill)}
            </div>`;
        if(window.lucide) lucide.createIcons();
    }

    async function activateTab(name){
        if(name === state.activeTab) return;
        if(!await confirmDiscard()) return;
        state.activeTab = name;
        state.dirty = false;
        document.querySelectorAll('.tab').forEach(button => button.classList.toggle('active', button.dataset.tab === name));
        $('#agentsView').hidden = name !== 'agents';
        $('#skillsView').hidden = name !== 'skills';
    }

    async function saveRuntime(){
        const payload = {provider_id:$('#runtimeProvider').value, text_model:$('#runtimeTextModel').value, vision_model:$('#runtimeVisionModel').value};
        try {
            state.settings = await fetchJson('/api/ai-runtime/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
            notifyDefinitionsChanged();
            toast('默认运行模型已保存');
        } catch(error) { toast(error.message, true); }
    }

    function bindEvents(){
        document.querySelectorAll('.tab').forEach(button => button.onclick = () => activateTab(button.dataset.tab));
        $('#agentSearch').oninput = renderAgentList;
        $('#agentKindFilter').onchange = renderAgentList;
        $('#skillSearch').oninput = renderSkillList;
        $('#newAgentBtn').onclick = createAgent;
        $('#runtimeProvider').onchange = () => {
            const provider = state.providers.find(item => item.id === $('#runtimeProvider').value);
            const first = provider?.chat_models?.[0] || '';
            $('#runtimeTextModel').innerHTML = modelOptions(first);
            $('#runtimeVisionModel').innerHTML = modelOptions(first);
        };
        $('#saveRuntimeBtn').onclick = saveRuntime;
        $('#agentImportInput').onchange = event => importAgents(event.target.files?.[0]);
        window.addEventListener('message', event => {
            if(event.data?.type === 'studio-theme') document.documentElement.classList.toggle('studio-theme-dark', event.data.theme === 'dark');
        });
        window.addEventListener('beforeunload', event => {
            if(!state.dirty) return;
            event.preventDefault();
            event.returnValue = '';
        });
    }

    async function init(){
        bindEvents();
        try {
            const [providerData, settings] = await Promise.all([fetchJson('/api/providers'), fetchJson('/api/ai-runtime/settings')]);
            state.providers = providerData.providers || [];
            state.settings = settings || {};
            renderRuntime();
            await reloadCatalog();
            setStatus('配置已就绪', 'success');
            if(window.lucide) lucide.createIcons();
        } catch(error) {
            setStatus(error.message, 'error');
            toast(error.message, true);
        }
    }

    init();
})();
