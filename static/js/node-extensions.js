(function(){
    const state = {
        revision:'', packages:[], nodes:[], aliases:{classic:{}, smart:{}},
        rawPackages:[], rawNodes:[], definitions:new Map(), knownDefinitions:new Map(), knownAliases:{classic:{}, smart:{}}, adapters:new Map(), cleanup:new WeakMap(), activeEditor:null, error:''
    };
    const listeners = new Set();

    function escapeHtml(value){
        return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
    function clone(value){
        try { return JSON.parse(JSON.stringify(value)); } catch(e) { return {}; }
    }
    function languageIsChinese(){
        if(window.StudioI18n?.lang) return window.StudioI18n.lang() !== 'en';
        try { return localStorage.getItem('studio_lang') !== 'en'; } catch(e) { return true; }
    }
    function localizedField(item, key){
        if(languageIsChinese()){
            const translated = String(item?.[`${key}_zh`] || '').trim();
            if(translated) return translated;
        }
        return item?.[key] || '';
    }
    function localizedPort(port){
        return {...port, name:localizedField(port, 'name')};
    }
    function localizedNode(definition){
        return {
            ...definition,
            display_name:localizedField(definition, 'display_name'),
            description:localizedField(definition, 'description'),
            package_name:localizedField(definition, 'package_name'),
            inputs:(definition.inputs || []).map(localizedPort),
            outputs:(definition.outputs || []).map(localizedPort)
        };
    }
    function rebuildLocalizedRegistry(){
        state.packages = state.rawPackages.map(packageInfo => ({
            ...packageInfo,
            name:localizedField(packageInfo, 'name'),
            description:localizedField(packageInfo, 'description'),
            nodes:(packageInfo.nodes || []).map(localizedNode)
        }));
        state.nodes = state.rawNodes.map(localizedNode);
        state.definitions = new Map(state.nodes.map(def => [def.type, def]));
        const knownNodes = state.packages.flatMap(packageInfo => packageInfo.nodes || []);
        state.knownDefinitions = new Map(knownNodes.map(def => [def.type, def]));
        state.knownAliases = {classic:{}, smart:{}};
        knownNodes.forEach(def => (def.surfaces || []).forEach(surface => {
            state.knownAliases[surface][def.type] = def.type;
            (def.legacy_types?.[surface] || []).forEach(alias => { state.knownAliases[surface][alias] = def.type; });
        }));
    }
    function surfaceName(value){
        if(value === 'classic' || value === 'smart') return value;
        return location.pathname.includes('smart-canvas') ? 'smart' : 'classic';
    }
    function resolveType(type, surface){
        const name = String(type || '');
        return state.aliases[surfaceName(surface)]?.[name] || name;
    }
    function definition(type, surface){
        return state.definitions.get(resolveType(type, surface)) || null;
    }
    function definitionForNode(node, surface){
        const base = definition(node?.extensionType || node?.type, surface);
        const snapshot = node?.data?.definitionSnapshot;
        if(base && snapshot?.runtime_node && Array.isArray(snapshot.inputs) && Array.isArray(snapshot.outputs)){
            const inputModes = node?.data?.inputModes || {};
            return {
                ...base,
                display_name:localizedField(snapshot, 'display_name') || base.display_name,
                description:localizedField(snapshot, 'description') || base.description,
                category:snapshot.category || base.category,
                inputs:snapshot.inputs.filter(port => !port?.widget?.enabled || inputModes[port.id] === 'port').map(localizedPort),
                outputs:snapshot.outputs.map(localizedPort),
                size:snapshot.size || base.size,
                runtime_node:true,
                compatibility:snapshot.compatibility || '',
                compatibility_reasons:snapshot.compatibility_reasons || [],
                fingerprint:snapshot.fingerprint || ''
            };
        }
        return base;
    }
    function adapterFor(node, surface){
        const def = definitionForNode(node, surface);
        return def ? state.adapters.get(def.type) || null : null;
    }
    function sizeFor(def, surface){
        const size = def?.size?.[surfaceName(surface)] || {};
        return {width:Number(size.width) || 360, height:Number(size.height) || 320};
    }
    function decorateNode(node, surface){
        if(!node || typeof node !== 'object') return node;
        const def = definitionForNode(node, surface);
        if(def){
            node.extensionType = def.type;
            if(!node.data || typeof node.data !== 'object' || Array.isArray(node.data)) node.data = {};
            const adapter = state.adapters.get(def.type);
            if(adapter?.migrate){
                try {
                    const migrated = adapter.migrate({node, definition:def, surface:surfaceName(surface)});
                    if(migrated && migrated !== node) Object.assign(node, migrated);
                } catch(error) {
                    node.extensionError = error.message || String(error);
                }
            }
            node.extensionType = def.type;
            node.nodeVersion = Number(node.nodeVersion) || Number(def.version) || 1;
            node.extensionMissing = false;
        } else {
            const target = surfaceName(surface);
            const knownType = node.extensionType || state.knownAliases[target]?.[node.type] || (String(node.type || '').includes('/') ? node.type : '');
            const knownDefinition = state.knownDefinitions.get(knownType);
            if(knownType) node.extensionType = knownType;
            if(knownDefinition) node.nodeVersion = Number(node.nodeVersion) || Number(knownDefinition.version) || 1;
            if(knownType) node.extensionMissing = true;
        }
        return node;
    }
    function serializeNode(node, surface){
        let copy = clone(node || {});
        decorateNode(copy, surface);
        const def = definitionForNode(copy, surface);
        const adapter = def ? state.adapters.get(def.type) : null;
        if(adapter?.serialize){
            try {
                const serialized = adapter.serialize({node:copy, definition:def, surface:surfaceName(surface)});
                if(serialized && typeof serialized === 'object') copy = serialized;
            } catch(error) {
                copy.extensionError = error.message || String(error);
            }
        }
        delete copy.extensionMissing;
        delete copy.extensionError;
        delete copy._extensionRunId;
        copy.running = false;
        return copy;
    }
    function createData(type, surface){
        const def = definition(type, surface);
        if(!def) return null;
        const size = sizeFor(def, surface);
        return {
            type:def.type,
            extensionType:def.type,
            nodeVersion:Number(def.version) || 1,
            title:def.display_name,
            w:size.width,
            h:size.height,
            data:clone(def.defaults || {}),
            runStatus:'',
            running:false,
            outputText:'',
            structuredOutput:null,
            created_at:Date.now()
        };
    }
    function definitionsFor(surface, options={}){
        const target = surfaceName(surface);
        return state.nodes.filter(def => {
            if(!(def.surfaces || []).includes(target)) return false;
            if(options.onlyNew && (def.legacy_types?.[target] || []).length) return false;
            return true;
        });
    }
    function portTypes(def, direction, portId){
        const ports = def?.[direction] || [];
        const exact = ports.find(port => String(port.id) === String(portId || ''));
        return (exact ? [exact] : ports).flatMap(port => port.types || ['any']);
    }
    function canConnect(fromNode, toNode, surface, fromPort='out', toPort='in'){
        const from = definitionForNode(fromNode, surface);
        const to = definitionForNode(toNode, surface);
        if(!from && !to) return null;
        if(from?.runtime_node && !to){
            return !portTypes(from, 'outputs', fromPort).some(type => String(type).startsWith('comfy:'));
        }
        if(!from && to?.runtime_node){
            return !portTypes(to, 'inputs', toPort).some(type => String(type).startsWith('comfy:'));
        }
        if(!from || !to) return true;
        const outputs = new Set(portTypes(from, 'outputs', fromPort));
        const inputs = new Set(portTypes(to, 'inputs', toPort));
        if(!outputs.size || !inputs.size) return false;
        if(outputs.has('any') || inputs.has('any')) return true;
        return [...outputs].some(type => inputs.has(type));
    }
    function missingBody(node){
        const type = node?.extensionType || node?.type || 'unknown';
        return `<div class="extension-missing" role="alert"><strong>扩展节点不可用</strong><span>${escapeHtml(type)}</span><small>扩展可能已禁用、删除或加载失败。原始节点数据和连接仍会保留。</small></div>`;
    }
    function genericBody(node, def){
        const fields = Object.entries(node.data || {}).map(([key, value]) => {
            if(typeof value === 'boolean'){
                return `<label class="extension-check"><input type="checkbox" data-extension-state="${escapeHtml(key)}" ${value ? 'checked' : ''}><span>${escapeHtml(key)}</span></label>`;
            }
            const text = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? '');
            return `<label class="extension-field"><span>${escapeHtml(key)}</span><textarea data-extension-state="${escapeHtml(key)}">${escapeHtml(text)}</textarea></label>`;
        }).join('');
        return `<div class="extension-generic"><div class="extension-node-kind">${escapeHtml(def?.package_name || 'Extension')}</div>${def?.description ? `<p>${escapeHtml(def.description)}</p>` : ''}${fields}<button type="button" class="extension-run" data-extension-run>${node.running ? '运行中' : '运行'}</button><pre class="extension-output">${escapeHtml(node.extensionError || node.outputText || '')}</pre></div>`;
    }
    function renderBody(node, surface, context={}){
        decorateNode(node, surface);
        const def = definitionForNode(node, surface);
        if(!def) return missingBody(node);
        const adapter = adapterFor(node, surface);
        if(adapter?.render){
            try {
                const rendered = adapter.render({node, definition:def, surface:surfaceName(surface), escapeHtml, context:publicContext(context)});
                if(typeof rendered === 'string' && rendered.trim()) return rendered;
            } catch(error) {
                console.error('Extension renderer failed', def.type, error);
                node.extensionError = error.message || String(error);
            }
        }
        return genericBody(node, def);
    }
    function collectInputs(node, context){
        if(typeof context?.collectInputs === 'function'){
            const values = context.collectInputs(node);
            if(values && typeof values === 'object') return values;
        }
        return clone(node.data || {});
    }
    function applyResult(node, result){
        const payload = result?.result || {};
        node.outputText = payload.output_text || '';
        node.structuredOutput = payload.structured_output ?? null;
        if(Array.isArray(payload.images) && payload.images.length){
            node.images = payload.images.map((url, index) => ({url, name:`output-${index + 1}`}));
        }
        node.extensionOutputs = payload.outputs || {};
    }
    async function responseJson(response){
        const data = await response.json().catch(() => ({}));
        if(!response.ok){
            const detail = data?.detail;
            throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
        }
        return data;
    }
    async function uploadAsset(blob, options={}){
        if(!(blob instanceof Blob)) throw new Error('Asset upload requires a Blob or File');
        const filename = String(options.filename || (blob.type === 'image/jpeg' ? 'node-output.jpg' : 'node-output.png')).replace(/[\\/:*?"<>|]+/g, '-');
        const file = blob instanceof File ? blob : new File([blob], filename, {type:blob.type || 'application/octet-stream'});
        const form = new FormData();
        const kind = String(options.kind || 'image');
        let payload;
        let uploaded;
        if(kind === 'model'){
            form.append('file', file, file.name || filename);
            form.append('kind', 'model');
            form.append('extension_id', String(options.extensionId || '3d-director'));
            payload = await fetch('/api/node-extension-assets', {method:'POST', body:form}).then(responseJson);
            uploaded = payload;
        }else{
            form.append('files', file, file.name || filename);
            payload = await fetch('/api/ai/upload', {method:'POST', body:form}).then(responseJson);
            uploaded = payload?.files?.[0];
        }
        if(!uploaded?.url) throw new Error('Uploaded asset did not return a local URL');
        return {url:String(uploaded.url), name:String(uploaded.name || file.name || filename), kind:String(uploaded.kind || kind), size:Number(uploaded.size || file.size || 0)};
    }
    function closeEditor(reason='close'){
        const active = state.activeEditor;
        if(!active) return;
        state.activeEditor = null;
        document.removeEventListener('keydown', active.keydown, true);
        try { active.cleanup?.(); } catch(error) { console.error('Extension editor cleanup failed', error); }
        try { active.options?.onClose?.({reason}); } catch(error) { console.error('Extension editor close handler failed', error); }
        active.backdrop.remove();
        document.documentElement.classList.remove('extension-editor-open');
        document.body.classList.remove('extension-editor-open');
        if(active.restoreFocus?.isConnected) active.restoreFocus.focus({preventScroll:true});
    }
    function openEditor(options={}){
        if(typeof options.mount !== 'function') throw new Error('Extension editor requires a mount(container) function');
        closeEditor('replace');
        const restoreFocus = document.activeElement;
        const backdrop = document.createElement('div');
        backdrop.className = `extension-editor-backdrop ${String(options.className || '').trim()}`.trim();
        backdrop.setAttribute('role', 'presentation');
        const shell = document.createElement('section');
        shell.className = 'extension-editor-shell';
        shell.setAttribute('role', 'dialog');
        shell.setAttribute('aria-modal', 'true');
        shell.setAttribute('aria-label', String(options.title || 'Extension editor'));
        const header = document.createElement('header');
        header.className = 'extension-editor-header';
        const title = document.createElement('strong');
        title.className = 'extension-editor-title';
        title.textContent = String(options.title || 'Extension editor');
        const actions = document.createElement('div');
        actions.className = 'extension-editor-actions';
        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'extension-editor-close';
        closeButton.setAttribute('aria-label', languageIsChinese() ? '关闭编辑器' : 'Close editor');
        closeButton.title = languageIsChinese() ? '关闭' : 'Close';
        closeButton.innerHTML = '<span aria-hidden="true">&times;</span>';
        actions.appendChild(closeButton);
        header.append(title, actions);
        const content = document.createElement('div');
        content.className = 'extension-editor-content';
        shell.append(header, content);
        backdrop.appendChild(shell);
        document.body.appendChild(backdrop);
        document.documentElement.classList.add('extension-editor-open');
        document.body.classList.add('extension-editor-open');
        const keydown = event => {
            if(event.key === 'Escape' && options.closeOnEscape !== false){
                event.preventDefault(); event.stopPropagation(); closeEditor('escape');
            }
        };
        document.addEventListener('keydown', keydown, true);
        closeButton.addEventListener('click', () => closeEditor('button'));
        if(options.closeOnBackdrop){
            backdrop.addEventListener('mousedown', event => { if(event.target === backdrop) closeEditor('backdrop'); });
        }
        const editorApi = {
            close:reason => closeEditor(reason || 'extension'),
            setTitle:value => { title.textContent = String(value || options.title || 'Extension editor'); },
            actions,
            uploadAsset,
        };
        const active = {backdrop, shell, content, keydown, options, cleanup:null, restoreFocus};
        state.activeEditor = active;
        try {
            const mounted = options.mount(content, editorApi);
            if(mounted && typeof mounted.then === 'function'){
                mounted.then(cleanup => {
                    if(state.activeEditor === active && typeof cleanup === 'function') active.cleanup = cleanup;
                    else if(typeof cleanup === 'function') cleanup();
                }).catch(error => {
                    console.error('Extension editor failed to mount', error);
                    content.innerHTML = `<div class="extension-editor-error">${escapeHtml(error.message || String(error))}</div>`;
                });
            } else if(typeof mounted === 'function') active.cleanup = mounted;
        } catch(error) {
            console.error('Extension editor failed to mount', error);
            content.innerHTML = `<div class="extension-editor-error">${escapeHtml(error.message || String(error))}</div>`;
        }
        requestAnimationFrame(() => closeButton.focus({preventScroll:true}));
        return editorApi;
    }
    function publicContext(context={}){
        return {...context, openEditor, closeEditor, uploadAsset};
    }
    async function runNode(node, surface, context={}){
        const def = definitionForNode(node, surface);
        if(!def) throw new Error('扩展节点不可用');
        if(def.execution !== 'python'){
            const adapter = adapterFor(node, surface);
            if(!adapter?.run) throw new Error('此节点没有可执行的后端');
            return adapter.run({node, definition:def, context:publicContext(context)});
        }
        if(node.running) return null;
        node.running = true;
        node.runStatus = 'queued';
        node.extensionError = '';
        context.update?.(node);
        try {
            let record = await fetch('/api/node-runs', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({
                    node_type:def.type,
                    node_version:Number(node.nodeVersion) || Number(def.version) || 1,
                    state:clone(node.data || {}),
                    inputs:collectInputs(node, context),
                    context:clone(context.runContext || {}),
                    canvas_id:String(context.canvasId || ''),
                    node_id:String(node.id || '')
                })
            }).then(responseJson);
            node._extensionRunId = record.run_id;
            while(!['succeeded','failed','cancelled','interrupted'].includes(record.status)){
                await new Promise(resolve => setTimeout(resolve, 400));
                record = await fetch(`/api/node-runs/${encodeURIComponent(record.run_id)}`).then(responseJson);
                node.runStatus = record.status;
                node.runProgress = record.progress;
                context.update?.(node, {quiet:true});
            }
            node.runStatus = record.status;
            if(record.status !== 'succeeded') throw new Error(record.error || `Node run ${record.status}`);
            applyResult(node, record);
            return record.result;
        } catch(error) {
            if(!['cancelled','interrupted'].includes(node.runStatus)) node.runStatus = 'failed';
            node.extensionError = error.message || String(error);
            throw error;
        } finally {
            node.running = false;
            delete node._extensionRunId;
            context.update?.(node);
            context.save?.();
        }
    }
    async function cancelNode(node){
        if(!node?._extensionRunId) return;
        const record = await fetch(`/api/node-runs/${encodeURIComponent(node._extensionRunId)}`, {method:'DELETE'}).then(responseJson);
        node.runStatus = record.status || 'cancelled';
        node.extensionError = record.error || '';
        return record;
    }
    function bindNode(root, node, surface, context={}){
        if(!root || !node) return;
        const oldCleanup = state.cleanup.get(root);
        if(typeof oldCleanup === 'function') oldCleanup();
        const cleanups = [];
        root.querySelectorAll('[data-extension-state]').forEach(control => {
            const eventName = control.type === 'checkbox' ? 'change' : 'input';
            const listener = () => {
                const key = control.dataset.extensionState;
                let value = control.type === 'checkbox' ? control.checked : control.value;
                const previous = node.data?.[key];
                if(previous && typeof previous === 'object'){
                    try { value = JSON.parse(value); } catch(e) {}
                }
                node.data = {...(node.data || {}), [key]:value};
                context.save?.();
            };
            control.addEventListener(eventName, listener);
            cleanups.push(() => control.removeEventListener(eventName, listener));
        });
        root.querySelectorAll('[data-extension-run]').forEach(button => {
            const listener = event => {
                event.preventDefault(); event.stopPropagation();
                runNode(node, surface, context).catch(error => context.error?.(error.message || String(error)));
            };
            button.addEventListener('click', listener);
            cleanups.push(() => button.removeEventListener('click', listener));
        });
        const adapter = adapterFor(node, surface);
        if(adapter?.bind){
            try {
                const extensionContext = publicContext(context);
                const cleanup = adapter.bind({root, node, definition:definitionForNode(node, surface), surface:surfaceName(surface), update:context.update, save:context.save, run:() => runNode(node, surface, context), cancel:() => cancelNode(node), escapeHtml, context:extensionContext});
                if(typeof cleanup === 'function') cleanups.push(cleanup);
            } catch(error) {
                console.error('Extension binder failed', node.extensionType || node.type, error);
            }
        }
        const cleanup = () => cleanups.splice(0).forEach(fn => { try { fn(); } catch(e) {} });
        state.cleanup.set(root, cleanup);
    }
    function scopedApi(packageInfo){
        return {
            escapeHtml,
            registerNode(localId, adapter){
                const type = `${packageInfo.id}/${localId}`;
                if(!state.definitions.has(type)) throw new Error(`Unknown node in manifest: ${type}`);
                state.adapters.set(type, adapter || {});
            }
        };
    }
    async function loadPackage(packageInfo){
        if(!packageInfo.enabled || !packageInfo.loaded) return;
        (packageInfo.styles || []).forEach(url => {
            if(document.querySelector(`link[data-node-extension-style="${CSS.escape(url)}"]`)) return;
            const link = document.createElement('link');
            link.rel = 'stylesheet'; link.href = `${url}?v=${encodeURIComponent(state.revision)}`;
            link.dataset.nodeExtensionStyle = url;
            document.head.appendChild(link);
        });
        if(!packageInfo.web_module) return;
        try {
            const module = await import(`${packageInfo.web_module}?v=${encodeURIComponent(state.revision)}`);
            if(typeof module.register === 'function') await module.register(scopedApi(packageInfo));
        } catch(error) {
            console.error('Failed to load node extension frontend', packageInfo.id, error);
        }
    }
    async function load(){
        try {
            const response = await fetch('/api/node-extensions', {cache:'no-store'});
            const payload = await responseJson(response);
            state.revision = payload.revision || '';
            state.rawPackages = payload.packages || [];
            state.rawNodes = payload.nodes || [];
            state.aliases = payload.aliases || {classic:{}, smart:{}};
            rebuildLocalizedRegistry();
            for(const packageInfo of state.packages) await loadPackage(packageInfo);
            state.error = '';
            listeners.forEach(listener => listener(api));
            window.dispatchEvent(new CustomEvent('syncanvas-node-extensions-ready', {detail:api}));
            return api;
        } catch(error) {
            state.error = error.message || String(error);
            console.error('Failed to load node extensions', error);
            return api;
        }
    }
    function onReady(listener){
        listeners.add(listener);
        if(state.nodes.length) listener(api);
        return () => listeners.delete(listener);
    }
    const api = {
        state, escapeHtml, ready:null, load, onReady, resolveType, definition, definitionForNode,
        definitionsFor, decorateNode, serializeNode, createData, sizeFor, canConnect,
        renderBody, bindNode, runNode, cancelNode, openEditor, closeEditor, uploadAsset
    };
    window.SynCanvasNodeExtensions = api;
    window.addEventListener('studio-lang-change', () => {
        if(!state.rawNodes.length) return;
        rebuildLocalizedRegistry();
        listeners.forEach(listener => listener(api));
        window.dispatchEvent(new CustomEvent('syncanvas-node-extensions-ready', {detail:api}));
    });
    api.ready = load();
})();
