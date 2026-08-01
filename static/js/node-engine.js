(function(){
    const MODEL_CATEGORIES = [
        'checkpoints','clip','clip_vision','configs','controlnet','diffusion_models','embeddings','gligen',
        'hypernetworks','loras','photomaker','style_models','text_encoders','unet','upscale_models','vae','vae_approx'
    ];
    const TERMINAL = new Set(['succeeded','failed','cancelled','interrupted']);
    const state = {component:null, nodes:[], models:[], modelPaths:[], extensions:[], nodeTimer:0, modelTimer:0, noticeTimer:0};
    const byId = id => document.getElementById(id);

    function escapeHtml(value){
        return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }

    async function apiJson(path, options={}){
        const response = await fetch(path, {cache:'no-store', ...options});
        const body = await response.json().catch(() => ({}));
        if(!response.ok){
            const detail = body?.detail;
            const message = typeof detail === 'string' ? detail : detail?.message || body?.error || `请求失败 (${response.status})`;
            const error = new Error(message);
            error.status = response.status;
            throw error;
        }
        return body;
    }

    function jsonOptions(body){
        return {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
    }

    function delay(ms){ return new Promise(resolve => setTimeout(resolve, ms)); }

    function setBusy(button, busy){
        if(!button) return;
        button.disabled = Boolean(busy);
        button.classList.toggle('busy', Boolean(busy));
    }

    function notice(message, error=false){
        const element = byId('engineNotice');
        clearTimeout(state.noticeTimer);
        element.textContent = String(message || '');
        element.classList.toggle('error', error);
        element.classList.add('open');
        state.noticeTimer = setTimeout(() => element.classList.remove('open'), error ? 6000 : 3200);
    }

    function formatBytes(value){
        let size = Number(value) || 0;
        if(size < 1024) return `${size} B`;
        const units = ['KB','MB','GB','TB'];
        let index = -1;
        do { size /= 1024; index += 1; } while(size >= 1024 && index < units.length - 1);
        return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[index]}`;
    }

    function syncIcons(root=document){
        if(window.lucide) window.lucide.createIcons({nodes:[root]});
    }

    function setProgress(id, progress){
        const bar = byId(id)?.querySelector('span');
        if(bar) bar.style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;
    }

    function applyTheme(theme){
        const dark = theme === 'dark';
        document.documentElement.classList.toggle('studio-theme-dark', dark);
        document.documentElement.classList.toggle('theme-dark', dark);
    }

    async function loadEngineStatus(){
        const component = await apiJson('/api/components/node-engine/status');
        state.component = component;
        const process = component.process || {};
        const dot = byId('engineDot');
        dot.classList.toggle('ready', Boolean(process.ready));
        dot.classList.toggle('error', component.state === 'error');
        byId('engineState').textContent = component.supported === false ? '当前系统不支持' : process.ready ? '运行中' : component.ready ? '已安装，未运行' : component.state === 'error' ? '安装异常' : '未安装';
        const nodeCount = Number(component.catalog?.node_count) || 0;
        const utilityNodeCount = Number(component.catalog?.utility_node_count) || 0;
        byId('engineMeta').textContent = `${component.version || '未知版本'} · ${utilityNodeCount || nodeCount} 个实用节点 · ${nodeCount} 个全部节点 · ${component.license || 'GPL-3.0'}`;
        byId('engineStart').disabled = !component.ready || Boolean(process.ready);
        byId('engineStop').disabled = !process.running && !process.ready;
        byId('engineRescan').disabled = !component.ready;
        byId('engineInstallSection').hidden = Boolean(component.ready);
        byId('engineInstallMessage').textContent = component.supported === false
            ? `现有节点引擎运行时仅支持 Windows，暂不支持 ${component.platform || '当前系统'}`
            : component.error || component.message || '导入独立便携运行时';
        byId('engineInstall').disabled = component.supported === false;
        byId('engineSourceRoot').disabled = component.supported === false;
        setProgress('engineInstallProgress', component.progress_percent || 0);
        return component;
    }

    async function waitForComponent(){
        for(let attempt = 0; attempt < 1800; attempt += 1){
            const component = await loadEngineStatus();
            if(component.ready) return component;
            if(['error','cancelled','interrupted'].includes(component.state)) throw new Error(component.error || component.message || '节点引擎安装失败');
            await delay(1000);
        }
        throw new Error('节点引擎安装等待超时');
    }

    async function installEngine(){
        const button = byId('engineInstall');
        const sourceRoot = byId('engineSourceRoot').value.trim();
        if(!sourceRoot && !state.component?.can_install){
            notice('请输入本地便携运行时目录', true);
            byId('engineSourceRoot').focus();
            return;
        }
        setBusy(button, true);
        try {
            await apiJson('/api/components/node-engine/install', jsonOptions({source_root:sourceRoot || null}));
            await waitForComponent();
            await apiJson('/api/node-engine/start', jsonOptions({wait_seconds:90}));
            await apiJson('/api/runtime-nodes/rescan', {method:'POST'});
            await Promise.all([loadEngineStatus(), loadNodes()]);
            notice('节点引擎安装并启动完成');
        } catch(error){
            notice(error.message || String(error), true);
        } finally {
            setBusy(button, false);
        }
    }

    async function engineAction(action, button){
        setBusy(button, true);
        try {
            if(action === 'stop') await apiJson('/api/node-engine/stop', {method:'POST'});
            else if(action === 'rescan'){
                if(!state.component?.process?.ready) await apiJson('/api/node-engine/start', jsonOptions({wait_seconds:90}));
                await apiJson('/api/runtime-nodes/rescan', {method:'POST'});
            } else await apiJson('/api/node-engine/start', jsonOptions({wait_seconds:90}));
            await loadEngineStatus();
            if(action === 'rescan') await Promise.all([loadNodes(), loadExtensions()]);
            notice(action === 'stop' ? '节点引擎已停止' : action === 'rescan' ? '节点目录已重新扫描' : '节点引擎已启动');
        } catch(error){
            notice(error.message || String(error), true);
        } finally {
            setBusy(button, false);
        }
    }

    function populateCategories(){
        ['modelCategory','modelImportCategory','modelPathCategory'].forEach(id => {
            const select = byId(id);
            const current = select.value;
            const prefix = id === 'modelCategory' ? '<option value="">全部分类</option>' : '';
            select.innerHTML = prefix + MODEL_CATEGORIES.map(category => `<option value="${category}">${category}</option>`).join('');
            if([...select.options].some(option => option.value === current)) select.value = current;
        });
    }

    function renderNodes(payload){
        state.nodes = payload.items || [];
        byId('nodeCount').textContent = `${payload.total || 0} 个节点`;
        const rows = byId('nodeRows');
        rows.innerHTML = state.nodes.map(node => `
            <tr>
                <td><div class="model-name">${escapeHtml(node.display_name_zh || node.display_name)}</div><small>${escapeHtml(node.display_name_zh && node.display_name !== node.display_name_zh ? node.display_name + ' · ' : '')}${escapeHtml(node.class_type)}</small></td>
                <td><div>${escapeHtml(node.category_zh || node.category || '其他')}</div>${node.category_zh && node.category_zh !== node.category ? `<small>${escapeHtml(node.category)}</small>` : ''}</td>
                <td><span class="source-badge">${escapeHtml(node.package || '内置')}</span></td>
                <td><span class="status-badge ${escapeHtml(node.compatibility)}">${node.compatibility === 'limited' ? '有限兼容' : node.compatibility === 'blocked' ? '不可用' : '完整兼容'}</span></td>
            </tr>`).join('');
        byId('nodeEmpty').hidden = state.nodes.length > 0;
    }

    async function loadNodes(){
        const params = new URLSearchParams({
            query:byId('nodeSearch').value.trim(),
            scope:byId('nodeScope').value,
            compatibility:byId('nodeCompatibility').value,
            page:'1',
            page_size:'100'
        });
        try {
            renderNodes(await apiJson(`/api/runtime-nodes?${params}`));
        } catch(error){
            renderNodes({items:[], total:0});
            byId('nodeCount').textContent = '节点引擎未启动';
            if(state.component?.ready) notice(error.message || String(error), true);
        }
    }

    function renderModels(payload){
        state.models = payload.items || [];
        byId('modelCount').textContent = `${payload.total || 0} 个模型`;
        const rows = byId('modelRows');
        rows.innerHTML = state.models.map(model => `
            <tr>
                <td><div class="model-name">${escapeHtml(model.name)}</div><small>${escapeHtml(model.relative_path)}</small></td>
                <td>${escapeHtml(model.category)}</td>
                <td><span class="source-badge">${model.readonly ? '只读 · ' + escapeHtml(model.source_id) : 'SynCanvas'}</span></td>
                <td>${formatBytes(model.size)}</td>
            </tr>`).join('');
        byId('modelEmpty').hidden = state.models.length > 0;
    }

    async function loadModels(){
        const params = new URLSearchParams({
            query:byId('modelSearch').value.trim(),
            category:byId('modelCategory').value,
            page:'1',
            page_size:'100'
        });
        try {
            renderModels(await apiJson(`/api/node-engine/models?${params}`));
        } catch(error){
            notice(error.message || String(error), true);
        }
    }

    async function pollModelImport(taskId){
        for(;;){
            const record = await apiJson(`/api/node-engine/models/imports/${encodeURIComponent(taskId)}`);
            setProgress('modelImportProgress', (Number(record.progress) || 0) * 100);
            byId('modelImportStatus').textContent = `${record.phase || record.status} · ${record.processed_files || 0}/${record.total_files || 0} 个文件`;
            if(TERMINAL.has(record.status)){
                if(record.status !== 'succeeded') throw new Error(record.error || `模型导入 ${record.status}`);
                return record;
            }
            await delay(450);
        }
    }

    async function importModels(){
        const button = byId('modelImport');
        const sourcePath = byId('modelSourcePath').value.trim();
        if(!sourcePath){ notice('请输入模型文件或目录', true); return; }
        setBusy(button, true);
        setProgress('modelImportProgress', 0);
        try {
            const record = await apiJson('/api/node-engine/models/import', jsonOptions({
                source_path:sourcePath,
                category:byId('modelImportCategory').value,
                conflict:byId('modelConflict').value,
                recursive:byId('modelRecursive').checked
            }));
            const result = await pollModelImport(record.task_id);
            await loadModels();
            notice(`模型导入完成：${result.imported?.length || 0} 个新增，${result.duplicates?.length || 0} 个重复`);
        } catch(error){
            notice(error.message || String(error), true);
        } finally {
            setBusy(button, false);
        }
    }

    function renderModelPaths(){
        const list = byId('modelPathRows');
        list.innerHTML = state.modelPaths.map(source => {
            const paths = Object.entries(source.paths || {}).map(([category, relative]) => `${category}: ${relative}`).join(' · ');
            return `<div class="source-row" data-source-id="${escapeHtml(source.id)}">
                <div class="row-main"><strong>${escapeHtml(source.name || source.id)}</strong><small>${escapeHtml(source.id)}</small></div>
                <div class="row-detail"><span>${escapeHtml(source.base_path)}</span><span>${escapeHtml(paths)}</span></div>
                <label class="check-field"><input type="checkbox" data-source-toggle="${escapeHtml(source.id)}" ${source.enabled ? 'checked' : ''}><span>启用</span></label>
                <button class="icon-button" type="button" data-source-remove="${escapeHtml(source.id)}" title="移除" aria-label="移除"><i data-lucide="trash-2"></i></button>
            </div>`;
        }).join('');
        syncIcons(list);
    }

    async function loadModelPaths(){
        try {
            const payload = await apiJson('/api/node-engine/model-paths');
            state.modelPaths = payload.sources || [];
            renderModelPaths();
        } catch(error){ notice(error.message || String(error), true); }
    }

    async function saveModelPaths(){
        const payload = await apiJson('/api/node-engine/model-paths', {
            method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sources:state.modelPaths})
        });
        state.modelPaths = payload.sources || state.modelPaths;
        renderModelPaths();
        await loadEngineStatus();
        await loadModels();
    }

    async function addModelPath(){
        const id = byId('modelPathId').value.trim();
        const basePath = byId('modelPathBase').value.trim();
        const category = byId('modelPathCategory').value;
        const relative = byId('modelPathRelative').value.trim() || '.';
        if(!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(id)){ notice('来源 ID 只能包含字母、数字、点、下划线和连字符', true); return; }
        if(!basePath){ notice('请输入只读模型根目录', true); return; }
        const existing = state.modelPaths.find(source => source.id === id);
        if(existing){
            existing.name = byId('modelPathName').value.trim() || existing.name || id;
            existing.base_path = basePath;
            existing.paths = {...(existing.paths || {}), [category]:relative};
            existing.enabled = true;
        } else {
            state.modelPaths.push({id, name:byId('modelPathName').value.trim() || id, base_path:basePath, paths:{[category]:relative}, enabled:true});
        }
        try { await saveModelPaths(); notice('只读模型目录已保存'); }
        catch(error){ await loadModelPaths(); notice(error.message || String(error), true); }
    }

    function renderExtensions(payload){
        state.extensions = payload.items || [];
        byId('extensionCount').textContent = `${payload.total || 0} 个扩展`;
        const list = byId('extensionRows');
        list.innerHTML = state.extensions.map(item => `
            <div class="extension-row" data-extension-id="${escapeHtml(item.id)}">
                <div class="row-main"><strong>${escapeHtml(item.id)}</strong><small>${item.node_count || 0} 个节点</small></div>
                <div class="row-detail"><span>${escapeHtml(item.source || '本地扩展')}</span>${item.error ? `<span class="row-error">${escapeHtml(item.error)}</span>` : ''}</div>
                <span class="status-badge ${escapeHtml(item.status)}">${item.status === 'enabled' ? '已启用' : item.status === 'disabled' ? '已禁用' : item.status === 'load_error' ? '加载异常' : escapeHtml(item.status)}</span>
                <div class="extension-actions">
                    <button class="icon-button" type="button" data-extension-toggle="${escapeHtml(item.id)}" data-enabled="${item.enabled ? 'true' : 'false'}" title="${item.enabled ? '禁用' : '启用'}" aria-label="${item.enabled ? '禁用' : '启用'}"><i data-lucide="${item.enabled ? 'pause' : 'play'}"></i></button>
                    <button class="icon-button" type="button" data-extension-delete="${escapeHtml(item.id)}" title="删除" aria-label="删除"><i data-lucide="trash-2"></i></button>
                </div>
            </div>`).join('');
        byId('extensionEmpty').hidden = state.extensions.length > 0;
        syncIcons(list);
    }

    async function loadExtensions(){
        try { renderExtensions(await apiJson('/api/node-engine/extensions')); }
        catch(error){ notice(error.message || String(error), true); }
    }

    async function pollExtensionTask(taskId){
        for(;;){
            const record = await apiJson(`/api/node-engine/extensions/tasks/${encodeURIComponent(taskId)}`);
            setProgress('extensionInstallProgress', (Number(record.progress) || 0) * 100);
            byId('extensionInstallStatus').textContent = record.message || record.phase || record.status;
            if(TERMINAL.has(record.status)){
                if(record.status !== 'succeeded') throw new Error(record.error || `扩展安装 ${record.status}`);
                return record;
            }
            await delay(650);
        }
    }

    async function installExtension(){
        const button = byId('extensionInstall');
        const source = byId('extensionSource').value.trim();
        if(!source){ notice('请输入扩展目录、ZIP 或 HTTPS Git 地址', true); return; }
        const installDependencies = byId('extensionDependencies').checked;
        if(installDependencies && !window.confirm('扩展依赖将安装到节点引擎的独立 Python 环境。仅安装可信扩展，是否继续？')) return;
        setBusy(button, true);
        setProgress('extensionInstallProgress', 0);
        try {
            const record = await apiJson('/api/node-engine/extensions/install', jsonOptions({
                source,
                package_id:byId('extensionId').value.trim(),
                install_dependencies:installDependencies,
                replace:byId('extensionReplace').checked
            }));
            await pollExtensionTask(record.task_id);
            await Promise.all([loadExtensions(), loadEngineStatus()]);
            notice('扩展安装完成');
        } catch(error){
            notice(error.message || String(error), true);
        } finally {
            setBusy(button, false);
        }
    }

    async function toggleExtension(button){
        const id = button.dataset.extensionToggle;
        const enabled = button.dataset.enabled === 'true';
        setBusy(button, true);
        try {
            await apiJson(`/api/node-engine/extensions/${encodeURIComponent(id)}/${enabled ? 'disable' : 'enable'}`, jsonOptions({wait_seconds:90}));
            await Promise.all([loadExtensions(), loadEngineStatus()]);
            notice(`扩展已${enabled ? '禁用' : '启用'}：${id}`);
        } catch(error){ notice(error.message || String(error), true); setBusy(button, false); }
    }

    async function deleteExtension(button){
        const id = button.dataset.extensionDelete;
        if(!window.confirm(`删除节点引擎扩展“${id}”？`)) return;
        setBusy(button, true);
        try {
            await apiJson(`/api/node-engine/extensions/${encodeURIComponent(id)}?wait_seconds=90`, {method:'DELETE'});
            await Promise.all([loadExtensions(), loadEngineStatus()]);
            notice(`扩展已删除：${id}`);
        } catch(error){ notice(error.message || String(error), true); setBusy(button, false); }
    }

    function bindEvents(){
        document.querySelectorAll('[data-engine-tab]').forEach(button => button.addEventListener('click', () => {
            const tab = button.dataset.engineTab;
            document.querySelectorAll('[data-engine-tab]').forEach(item => {
                const active = item.dataset.engineTab === tab;
                item.classList.toggle('active', active);
                item.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            document.querySelectorAll('[data-engine-panel]').forEach(panel => {
                const active = panel.dataset.enginePanel === tab;
                panel.classList.toggle('active', active);
                panel.hidden = !active;
            });
            if(tab === 'extensions') loadExtensions();
            else if(tab === 'models') Promise.all([loadModels(), loadModelPaths()]);
            else if(tab === 'nodes') loadNodes();
        }));
        byId('engineInstall').addEventListener('click', installEngine);
        byId('engineStart').addEventListener('click', event => engineAction('start', event.currentTarget));
        byId('engineStop').addEventListener('click', event => engineAction('stop', event.currentTarget));
        byId('engineRescan').addEventListener('click', event => engineAction('rescan', event.currentTarget));
        byId('engineRefresh').addEventListener('click', () => Promise.all([loadEngineStatus(), loadNodes(), loadModels(), loadModelPaths(), loadExtensions()]));
        byId('nodeRefresh').addEventListener('click', loadNodes);
        byId('nodeScope').addEventListener('change', loadNodes);
        byId('nodeCompatibility').addEventListener('change', loadNodes);
        byId('nodeSearch').addEventListener('input', () => {
            clearTimeout(state.nodeTimer);
            state.nodeTimer = setTimeout(loadNodes, 220);
        });
        byId('modelRefresh').addEventListener('click', loadModels);
        byId('modelCategory').addEventListener('change', loadModels);
        byId('modelSearch').addEventListener('input', () => {
            clearTimeout(state.modelTimer);
            state.modelTimer = setTimeout(loadModels, 220);
        });
        byId('modelImport').addEventListener('click', importModels);
        byId('modelPathAdd').addEventListener('click', addModelPath);
        byId('modelPathRows').addEventListener('change', async event => {
            const id = event.target.dataset.sourceToggle;
            if(!id) return;
            const source = state.modelPaths.find(item => item.id === id);
            if(!source) return;
            source.enabled = event.target.checked;
            try { await saveModelPaths(); notice(`只读模型目录已${source.enabled ? '启用' : '禁用'}`); }
            catch(error){ await loadModelPaths(); notice(error.message || String(error), true); }
        });
        byId('modelPathRows').addEventListener('click', async event => {
            const button = event.target.closest('[data-source-remove]');
            if(!button) return;
            state.modelPaths = state.modelPaths.filter(item => item.id !== button.dataset.sourceRemove);
            try { await saveModelPaths(); notice('只读模型目录已移除'); }
            catch(error){ await loadModelPaths(); notice(error.message || String(error), true); }
        });
        byId('extensionRefresh').addEventListener('click', loadExtensions);
        byId('extensionInstall').addEventListener('click', installExtension);
        byId('extensionRows').addEventListener('click', event => {
            const toggle = event.target.closest('[data-extension-toggle]');
            const remove = event.target.closest('[data-extension-delete]');
            if(toggle) toggleExtension(toggle);
            else if(remove) deleteExtension(remove);
        });
        window.addEventListener('message', event => {
            if(event.data?.type === 'studio-theme') applyTheme(event.data.theme || 'light');
        });
        window.addEventListener('studio-theme-change', event => applyTheme(event.detail?.theme || 'light'));
    }

    document.addEventListener('DOMContentLoaded', async () => {
        populateCategories();
        bindEvents();
        syncIcons();
        const results = await Promise.allSettled([loadEngineStatus(), loadNodes(), loadModels(), loadModelPaths(), loadExtensions()]);
        const failure = results.find(result => result.status === 'rejected');
        if(failure) notice(failure.reason?.message || String(failure.reason), true);
    }, {once:true});
})();
