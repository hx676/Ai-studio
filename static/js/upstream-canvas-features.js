(function(){
    'use strict';

    const isSmart = Boolean(document.getElementById('smartWorkflowPanel'));
    const prefix = isSmart ? 'smart' : 'canvas';
    const panelIds = isSmart
        ? ['smartPromptTemplatePanel', 'smartWorkflowPanel', 'canvasAssistantPanel']
        : ['canvasPromptTemplatePanel', 'canvasWorkflowPanel', 'canvasAssetPanel', 'canvasAssistantPanel'];
    const promptState = {libraries:[], libraryId:'', category:'all', query:'', selectedId:''};
    const assetState = {libraries:[], libraryId:'', categoryId:''};
    let initialized = false;

    function byId(id){ return document.getElementById(id); }
    function esc(value){
        return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
    function clone(value){
        try { return window.SynCanvasGraph?.deepClone(value) ?? (typeof structuredClone === 'function' ? structuredClone(value) : JSON.parse(JSON.stringify(value))); }
        catch(_) { return JSON.parse(JSON.stringify(value)); }
    }
    async function apiError(response, fallback){
        const data = await response.json().catch(() => null);
        return data?.detail || data?.error || fallback;
    }
    function notify(message){
        if(isSmart && typeof toast === 'function') toast(message);
        else if(typeof setStatus === 'function') setStatus(message);
    }
    function fail(message){
        if(isSmart && typeof toast === 'function') toast(message);
        else if(typeof showErrorModal === 'function') showErrorModal(message, '画布功能');
    }
    function stopCanvasInteractionLeak(event){
        event.stopPropagation();
    }
    function refreshFeatureIcons(){ if(window.lucide) window.lucide.createIcons(); }
    function setPanelOpen(id, open=true){
        if(isSmart && typeof toggleAssetLibrary === 'function' && open) toggleAssetLibrary(false);
        panelIds.forEach(panelId => {
            const panel = byId(panelId);
            const active = panelId === id && open;
            panel?.classList.toggle('open', active);
            panel?.setAttribute('aria-hidden', active ? 'false' : 'true');
        });
        refreshFeatureIcons();
    }
    function downloadBlob(blob, filename){
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
    }
    function safeFilename(ext){
        const title = String(canvas?.title || (isSmart ? 'smart-canvas' : 'canvas-workflow'))
            .replace(/[\\/:*?"<>|]+/g, '-')
            .replace(/\s+/g, '-')
            .slice(0, 60) || 'canvas-workflow';
        return `${title}-${Date.now()}.${ext}`;
    }
    function cleanWorkflowNode(source){
        const node = clone(source || {});
        ['running','runStatus','runError','_pending','_cascadeIdx','_cascadeFailed','_activeLoopCtx'].forEach(key => delete node[key]);
        return node;
    }
    function selectedWorkflowPayload(){
        const ids = isSmart
            ? (typeof selectedNodeIds === 'function' ? selectedNodeIds() : [])
            : [...(selected || [])];
        const idSet = new Set(ids);
        const pickedNodes = (nodes || []).filter(node => idSet.has(node.id)).map(cleanWorkflowNode);
        const pickedIds = new Set(pickedNodes.map(node => node.id));
        const allConnections = isSmart ? (canvas?.connections || []) : (connections || []);
        const pickedConnections = allConnections
            .filter(conn => pickedIds.has(conn.from) && pickedIds.has(conn.to))
            .map(clone);
        return {
            format:isSmart ? 'syncanvas-smart-workflow' : 'syncanvas-canvas-workflow',
            version:1,
            canvas_type:isSmart ? 'smart' : 'classic',
            exported_at:Date.now(),
            nodes:pickedNodes,
            connections:pickedConnections
        };
    }
    function updateWorkflowMeta(){
        const payload = selectedWorkflowPayload();
        const meta = byId(`${prefix}WorkflowMeta`);
        if(meta) meta.textContent = payload.nodes.length
            ? `已选择 ${payload.nodes.length} 个节点，${payload.connections.length} 条连线`
            : '请先选择要导出的节点';
        return payload;
    }
    async function exportWorkflow(includeResources=false){
        const payload = updateWorkflowMeta();
        if(!payload.nodes.length){ notify('请先选择要导出的节点'); return; }
        try {
            if(!includeResources){
                downloadBlob(new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'}), safeFilename('json'));
            } else {
                const filename = safeFilename('zip');
                const response = await fetch('/api/canvas-workflows/export', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({...payload, filename, include_resources:true})
                });
                if(!response.ok) throw new Error(await apiError(response, '导出工作流失败'));
                downloadBlob(await response.blob(), filename);
            }
            notify(`已导出 ${payload.nodes.length} 个节点`);
        } catch(error){ fail(error.message || '导出工作流失败'); }
    }
    function normalizeImportedWorkflow(data){
        if(window.SynCanvasGraph) return window.SynCanvasGraph.normalizeWorkflow(data);
        if(Array.isArray(data)) return {nodes:data, connections:[]};
        if(Array.isArray(data?.nodes)) return {nodes:data.nodes, connections:Array.isArray(data.connections) ? data.connections : []};
        if(Array.isArray(data?.workflow?.nodes)) return {nodes:data.workflow.nodes, connections:Array.isArray(data.workflow.connections) ? data.workflow.connections : []};
        return {nodes:[], connections:[]};
    }
    function remapNodeReferences(node, idMap){
        if(window.SynCanvasGraph) return window.SynCanvasGraph.remapNodeReferences(node, idMap);
        ['items','children','nodeIds'].forEach(key => {
            if(Array.isArray(node[key])) node[key] = node[key].map(id => idMap.get(id) || id);
        });
        if(node.inputBindings && typeof node.inputBindings === 'object'){
            Object.values(node.inputBindings).forEach(binding => {
                if(binding?.sourceNodeId) binding.sourceNodeId = idMap.get(binding.sourceNodeId) || binding.sourceNodeId;
            });
        }
        return node;
    }
    function insertWorkflow(imported){
        const sourceNodes = (imported.nodes || []).filter(node => node && typeof node === 'object');
        if(window.SynCanvasGraph) window.SynCanvasGraph.assertLimits(sourceNodes, imported.connections || []);
        if(!canvas || !sourceNodes.length) throw new Error('工作流中没有可导入的节点');
        if(typeof pushUndo === 'function') pushUndo();
        const sourceConnections = (imported.connections || []).filter(conn => conn && typeof conn === 'object');
        const minX = Math.min(...sourceNodes.map(node => Number(node.x || 0)));
        const minY = Math.min(...sourceNodes.map(node => Number(node.y || 0)));
        const target = isSmart
            ? (typeof viewportCenter === 'function' ? viewportCenter() : {x:0,y:0})
            : (typeof defaultPoint === 'function' ? defaultPoint(0, 0) : {x:0,y:0});
        const idMap = new Map();
        const added = sourceNodes.map(source => {
            const copy = cleanWorkflowNode(source);
            const oldId = String(copy.id || '');
            copy.id = typeof uid === 'function' ? uid(copy.type || 'node') : `node_${Date.now()}_${Math.random()}`;
            copy.x = Number(copy.x || 0) - minX + target.x;
            copy.y = Number(copy.y || 0) - minY + target.y;
            if(oldId) idMap.set(oldId, copy.id);
            return copy;
        });
        added.forEach(node => remapNodeReferences(node, idMap));
        const addedConnections = sourceConnections.map(source => {
            const copy = clone(source);
            copy.from = idMap.get(source.from);
            copy.to = idMap.get(source.to);
            if(copy.id) copy.id = typeof uid === 'function' ? uid('c') : `c_${Date.now()}_${Math.random()}`;
            return copy;
        }).filter(conn => conn.from && conn.to);
        nodes.push(...added);
        if(isSmart){
            canvas.connections = [...(canvas.connections || []), ...addedConnections];
            selectedIds = added.length > 1 ? added.map(node => node.id) : [];
            selectedId = added.length === 1 ? added[0].id : '';
            selectedImage = {nodeId:'', index:-1};
        } else {
            connections = [...connections, ...addedConnections];
            selected.clear();
            added.forEach(node => selected.add(node.id));
        }
        if(typeof render === 'function') render();
        if(typeof scheduleSave === 'function') scheduleSave();
        notify(`已导入 ${added.length} 个节点`);
    }
    async function importWorkflowFile(file){
        if(!file || !canvas) return;
        try {
            const form = new FormData();
            form.append('file', file, file.name || 'workflow.json');
            const response = await fetch('/api/canvas-workflows/import', {method:'POST', body:form});
            if(!response.ok) throw new Error(await apiError(response, '导入工作流失败'));
            insertWorkflow(normalizeImportedWorkflow(await response.json()));
            setPanelOpen('', false);
        } catch(error){ fail(error.message || '导入工作流失败'); }
    }
    async function importWorkflowAsset(url, name='workflow.zip'){
        if(!url) return;
        try {
            const response = await fetch(url);
            if(!response.ok) throw new Error('工作流文件读取失败');
            const file = new File([await response.blob()], name || 'workflow.zip');
            await importWorkflowFile(file);
        } catch(error){ fail(error.message || '工作流导入失败'); }
    }
    async function insertBuiltInWorkflow(){
        if(!canvas){ notify('请先打开画布'); return; }
        const url = isSmart
            ? '/static/workflows/reference-style-prompt.smart.json'
            : '/static/workflows/reference-style-prompt.classic.json';
        try {
            const response = await fetch(url, {cache:'no-store'});
            if(!response.ok) throw new Error('内置示例读取失败');
            insertWorkflow(normalizeImportedWorkflow(await response.json()));
            setPanelOpen('', false);
        } catch(error){ fail(error.message || '内置示例插入失败'); }
    }

    function activePromptLibrary(){
        return promptState.libraries.find(lib => lib.id === promptState.libraryId) || promptState.libraries[0] || {items:[]};
    }
    function promptItems(){ return (activePromptLibrary().items || []).filter(item => item?.id && (item.positive || item.prompt)); }
    function selectedPromptTemplate(){ return promptItems().find(item => item.id === promptState.selectedId) || promptItems()[0] || null; }
    async function loadPromptLibraries(){
        try {
            const response = await fetch('/api/prompt-libraries', {cache:'no-store'});
            if(!response.ok) throw new Error('提示词模板加载失败');
            const data = await response.json();
            promptState.libraries = Array.isArray(data.library?.libraries) ? data.library.libraries : [];
            promptState.libraryId = promptState.libraries.some(lib => lib.id === promptState.libraryId)
                ? promptState.libraryId
                : (promptState.libraries.find(lib => lib.id === 'system')?.id || promptState.libraries[0]?.id || '');
            promptState.selectedId = promptItems()[0]?.id || '';
            renderPromptPanel();
        } catch(error){ fail(error.message || '提示词模板加载失败'); }
    }
    function renderPromptPanel(){
        const librarySelect = byId(`${prefix}PromptLibrarySelect`);
        const categoriesEl = byId(`${prefix}PromptTemplateCategories`);
        const listEl = byId(`${prefix}PromptTemplateList`);
        const detailEl = byId(`${prefix}PromptTemplateDetail`);
        if(!librarySelect || !categoriesEl || !listEl || !detailEl) return;
        librarySelect.innerHTML = promptState.libraries.map(lib => `<option value="${esc(lib.id)}" ${lib.id === promptState.libraryId ? 'selected' : ''}>${esc(lib.name || '提示词库')}</option>`).join('');
        const allItems = promptItems();
        const categories = [...new Set(allItems.map(item => item.category || 'other'))];
        if(promptState.category !== 'all' && !categories.includes(promptState.category)) promptState.category = 'all';
        categoriesEl.innerHTML = [{id:'all', name:'全部'}, ...categories.map(id => ({id, name:id}))]
            .map(item => `<button type="button" data-prompt-category="${esc(item.id)}" class="${item.id === promptState.category ? 'active' : ''}">${esc(item.name)}</button>`).join('');
        const query = promptState.query.toLowerCase();
        const visible = allItems.filter(item => (promptState.category === 'all' || (item.category || 'other') === promptState.category)
            && (!query || `${item.name || ''} ${item.positive || item.prompt || ''}`.toLowerCase().includes(query)));
        if(!visible.some(item => item.id === promptState.selectedId)) promptState.selectedId = visible[0]?.id || '';
        listEl.innerHTML = visible.length ? visible.map(item => `<button type="button" class="sync-template-item ${item.id === promptState.selectedId ? 'active' : ''}" data-prompt-template="${esc(item.id)}"><strong>${esc(item.name || '未命名模板')}</strong><span>${esc(item.positive || item.prompt || '')}</span></button>`).join('') : '<div class="sync-feature-empty">没有匹配的模板</div>';
        const selectedTemplate = selectedPromptTemplate();
        detailEl.innerHTML = selectedTemplate ? `<p>${esc(selectedTemplate.positive || selectedTemplate.prompt || '')}</p><div class="sync-template-actions"><button type="button" data-apply-template="positive"><i data-lucide="text-cursor-input"></i><span>应用正向提示词</span></button><button type="button" class="primary" data-apply-template="full"><i data-lucide="check"></i><span>完整应用</span></button></div>` : '';
        categoriesEl.querySelectorAll('[data-prompt-category]').forEach(button => button.onclick = () => { promptState.category = button.dataset.promptCategory || 'all'; renderPromptPanel(); });
        listEl.querySelectorAll('[data-prompt-template]').forEach(button => button.onclick = () => { promptState.selectedId = button.dataset.promptTemplate || ''; renderPromptPanel(); });
        detailEl.querySelectorAll('[data-apply-template]').forEach(button => button.onclick = () => applyPromptTemplate(button.dataset.applyTemplate || 'positive'));
        refreshFeatureIcons();
    }
    function promptTemplateText(template, mode){
        const positive = String(template?.positive || template?.prompt || '').trim();
        const negative = String(template?.negative || template?.negative_prompt || '').trim();
        return mode === 'full' && negative ? `${positive}\n\nNegative prompt: ${negative}` : positive;
    }
    function smartPromptTemplateHtml(template, text, mode){
        const title = String(template?.name || '未命名模板').trim() || '未命名模板';
        const templateId = String(template?.id || '').trim();
        return `<span class="prompt-template-token" contenteditable="false" data-template-id="${esc(templateId)}" data-template-mode="${esc(mode)}" data-template-prompt="${esc(text)}" title="已应用提示词模板：${esc(title)}">${esc(title)}</span>`;
    }
    function applyPromptTemplate(mode='positive'){
        const template = selectedPromptTemplate();
        const text = promptTemplateText(template, mode);
        if(!text) return;
        if(isSmart){
            const input = byId('promptInput');
            if(!input) return;
            input.innerHTML = smartPromptTemplateHtml(template, text, mode);
            input.dispatchEvent(new Event('input', {bubbles:true}));
        } else {
            let node = [...selected].map(id => nodes.find(item => item.id === id)).find(item => item?.type === 'prompt');
            if(!node) node = addPromptNode(typeof defaultPoint === 'function' ? defaultPoint(0, 0) : undefined);
            if(!node) return;
            node.text = text;
            selected.clear();
            selected.add(node.id);
            render();
            scheduleSave();
            if(typeof syncGeneratorInputs === 'function') syncGeneratorInputs();
            if(typeof refreshGeneratorInputViews === 'function') refreshGeneratorInputViews();
        }
        setPanelOpen('', false);
        notify(`已应用模板：${template.name || '未命名模板'}`);
    }

    function assetLibrariesFrom(data){
        const library = data?.library || {};
        if(Array.isArray(library.libraries) && library.libraries.length) return library.libraries;
        return [{id:'default', name:'素材库', categories:Array.isArray(library.categories) ? library.categories : []}];
    }
    function activeAssetLibrary(){ return assetState.libraries.find(lib => lib.id === assetState.libraryId) || assetState.libraries[0] || {categories:[]}; }
    function activeAssetCategory(){
        const categories = activeAssetLibrary().categories || [];
        return categories.find(cat => cat.id === assetState.categoryId) || categories[0] || null;
    }
    function assetKind(item){
        const kind = String(item?.kind || '').toLowerCase();
        if(kind) return kind;
        const url = String(item?.url || '').split('?')[0].toLowerCase();
        if(/\.(mp4|webm|mov|m4v)$/.test(url)) return 'video';
        if(/\.(mp3|wav|m4a|ogg)$/.test(url)) return 'audio';
        if(/\.(zip|json)$/.test(url)) return 'workflow';
        return 'image';
    }
    function renderCanvasAssets(){
        if(isSmart) return;
        const librarySelect = byId('canvasAssetLibrarySelect');
        const categorySelect = byId('canvasAssetCategorySelect');
        const grid = byId('canvasAssetGrid');
        if(!librarySelect || !categorySelect || !grid) return;
        librarySelect.innerHTML = assetState.libraries.map(lib => `<option value="${esc(lib.id)}" ${lib.id === assetState.libraryId ? 'selected' : ''}>${esc(lib.name || '素材库')}</option>`).join('');
        const categories = activeAssetLibrary().categories || [];
        if(!categories.some(cat => cat.id === assetState.categoryId)) assetState.categoryId = categories[0]?.id || '';
        categorySelect.innerHTML = categories.map(cat => `<option value="${esc(cat.id)}" ${cat.id === assetState.categoryId ? 'selected' : ''}>${esc(cat.name || '默认分组')}</option>`).join('');
        const items = activeAssetCategory()?.items || [];
        grid.innerHTML = items.length ? items.map(item => {
            const kind = assetKind(item);
            const preview = kind === 'image' ? `<img src="${esc(item.url)}" alt="">` : kind === 'video' ? `<video src="${esc(item.url)}" muted preload="metadata"></video>` : `<i data-lucide="${kind === 'audio' ? 'audio-lines' : 'package-open'}"></i>`;
            return `<button type="button" class="sync-asset-item" data-asset-url="${esc(item.url)}" data-asset-name="${esc(item.name || 'asset')}" data-asset-kind="${esc(kind)}"><div class="sync-asset-preview">${preview}</div><span>${esc(item.name || 'asset')}</span></button>`;
        }).join('') : '<div class="sync-feature-empty">当前分组没有素材</div>';
        grid.querySelectorAll('[data-asset-url]').forEach(button => button.onclick = () => addCanvasAsset(button.dataset.assetUrl, button.dataset.assetName, button.dataset.assetKind));
        refreshFeatureIcons();
    }
    async function loadCanvasAssets(){
        if(isSmart) return;
        try {
            const response = await fetch('/api/asset-library', {cache:'no-store'});
            if(!response.ok) throw new Error('素材库加载失败');
            assetState.libraries = assetLibrariesFrom(await response.json());
            if(!assetState.libraries.some(lib => lib.id === assetState.libraryId)) assetState.libraryId = assetState.libraries[0]?.id || '';
            renderCanvasAssets();
        } catch(error){ fail(error.message || '素材库加载失败'); }
    }
    function addCanvasAsset(url, name, kind){
        if(!url || !canvas) return;
        if(kind === 'workflow'){ importWorkflowAsset(url, name); return; }
        const point = typeof defaultPoint === 'function' ? defaultPoint(0, 0) : undefined;
        if(kind === 'video' && typeof createVideoCardFromUrl === 'function') createVideoCardFromUrl(url, point, name);
        else if(kind === 'audio' && typeof createAudioCardFromUrl === 'function') createAudioCardFromUrl(url, point, name);
        else if(typeof createImageCardFromUrl === 'function') createImageCardFromUrl(url, point, name);
        setPanelOpen('', false);
    }

    function bindDropZone(label, input){
        if(!label || !input) return;
        input.onchange = async () => {
            const file = input.files?.[0];
            input.value = '';
            if(file) await importWorkflowFile(file);
        };
        ['dragenter','dragover'].forEach(type => label.addEventListener(type, event => { event.preventDefault(); label.classList.add('drag-over'); }));
        ['dragleave','drop'].forEach(type => label.addEventListener(type, event => { event.preventDefault(); label.classList.remove('drag-over'); }));
        label.addEventListener('drop', async event => {
            const file = event.dataTransfer?.files?.[0];
            if(file) await importWorkflowFile(file);
        });
    }
    async function handleFeatureToggle(event){
        const toggle = event.target?.closest?.(`#${prefix}PromptTemplateToggle, #${prefix}WorkflowToggle`);
        if(!toggle) return;
        event.preventDefault();
        if(toggle.id === `${prefix}PromptTemplateToggle`){
            setPanelOpen(`${prefix}PromptTemplatePanel`, true);
            await loadPromptLibraries();
            return;
        }
        updateWorkflowMeta();
        setPanelOpen(`${prefix}WorkflowPanel`, true);
    }
    function init(){
        if(initialized) return;
        initialized = true;
        document.querySelectorAll('[data-sync-close]').forEach(button => button.onclick = () => setPanelOpen('', false));
        panelIds.forEach(panelId => {
            const panel = byId(panelId);
            ['pointerdown','mousedown','click','dblclick','wheel'].forEach(type => panel?.addEventListener(type, stopCanvasInteractionLeak));
        });
        document.addEventListener('click', handleFeatureToggle, true);
        byId(`${prefix}WorkflowExportJson`)?.addEventListener('click', () => exportWorkflow(false));
        byId(`${prefix}WorkflowExportZip`)?.addEventListener('click', () => exportWorkflow(true));
        byId(`${prefix}WorkflowInsertExample`)?.addEventListener('click', insertBuiltInWorkflow);
        bindDropZone(byId(`${prefix}WorkflowPanel`)?.querySelector('.sync-drop-zone'), byId(`${prefix}WorkflowImportInput`));
        const search = byId(`${prefix}PromptTemplateSearch`);
        if(search) search.oninput = () => { promptState.query = search.value || ''; renderPromptPanel(); };
        const promptLibrarySelect = byId(`${prefix}PromptLibrarySelect`);
        if(promptLibrarySelect) promptLibrarySelect.onchange = () => { promptState.libraryId = promptLibrarySelect.value; promptState.category = 'all'; promptState.selectedId = promptItems()[0]?.id || ''; renderPromptPanel(); };
        if(!isSmart){
            byId('canvasAssetToggle')?.addEventListener('click', async () => { setPanelOpen('canvasAssetPanel', true); await loadCanvasAssets(); });
            byId('canvasAssetLibrarySelect')?.addEventListener('change', event => { assetState.libraryId = event.target.value; assetState.categoryId = ''; renderCanvasAssets(); });
            byId('canvasAssetCategorySelect')?.addEventListener('change', event => { assetState.categoryId = event.target.value; renderCanvasAssets(); });
        }
        refreshFeatureIcons();
    }

    window.importSmartWorkflowAsset = importWorkflowAsset;
    window.SynCanvasUpstreamFeatures = {exportWorkflow, importWorkflowFile, importWorkflowAsset, loadPromptLibraries, loadCanvasAssets};
    if(document.readyState === 'complete') init();
    else window.addEventListener('load', init, {once:true});
    try {
        const channel = new BroadcastChannel('studio-api');
        channel.onmessage = event => {
            if(event.data?.type === 'asset_library_updated'){
                if(isSmart && typeof loadAssetLibrary === 'function') loadAssetLibrary();
                else loadCanvasAssets();
            }
        };
    } catch(_) {}
})();
