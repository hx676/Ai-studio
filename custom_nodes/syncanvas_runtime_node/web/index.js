const RUNTIME_TYPE = 'syncanvas.node-engine/runtime-node';
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

function clone(value){
    try { return JSON.parse(JSON.stringify(value)); } catch(_) { return {}; }
}

async function responseJson(response){
    const body = await response.json().catch(() => ({}));
    if(!response.ok){
        const detail = body?.detail;
        throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
    }
    return body;
}

function widgetDefaults(definition){
    const values = {};
    (definition?.inputs || []).forEach(input => {
        const widget = input.widget || {};
        if(widget.enabled && Object.prototype.hasOwnProperty.call(widget, 'default')) values[input.id] = clone(widget.default);
    });
    return values;
}

function inputModeDefaults(definition){
    const modes = {};
    (definition?.inputs || []).forEach(input => {
        if(input.widget?.enabled) modes[input.id] = 'widget';
    });
    return modes;
}

function definitionSnapshot(definition){
    return {
        runtime_node:true,
        display_name:definition.display_name_zh || definition.display_name || definition.class_type,
        display_name_en:definition.display_name || definition.class_type,
        description:definition.description_zh || definition.description || '',
        category:definition.category_zh || definition.category || '其他',
        category_raw:definition.category || 'other',
        compatibility:definition.compatibility || 'blocked',
        compatibility_reasons:clone(definition.compatibility_reasons || []),
        canvas_ready:definition.canvas_ready !== false,
        fingerprint:definition.fingerprint || '',
        inputs:clone(definition.inputs || []).map(input => ({...input, name_en:input.name, name:input.name_zh || input.name})),
        outputs:clone(definition.outputs || []).map(output => ({...output, name_en:output.name, name:output.name_zh || output.name})),
        size:{classic:{width:400,height:540},smart:{width:400,height:540}}
    };
}

function selectedDefinition(node){
    return node?.data?.definitionSnapshot || null;
}

function widgetControl(input, value, mode, escapeHtml){
    const widget = input.widget || {};
    const id = escapeHtml(input.id);
    const label = escapeHtml(input.name || input.id);
    const tooltip = widget.tooltip ? ` title="${escapeHtml(widget.tooltip)}"` : '';
    const switcher = `<button type="button" class="runtime-mode-btn ${mode === 'port' ? 'active' : ''}" data-runtime-input-mode="${id}" title="${mode === 'port' ? '改为控件输入' : '改为端口输入'}"><i data-lucide="${mode === 'port' ? 'sliders-horizontal' : 'plug'}"></i></button>`;
    if(mode === 'port'){
        return `<div class="runtime-widget runtime-widget-port"${tooltip}><div class="runtime-widget-label"><span>${label}</span>${switcher}</div><small>由节点端口输入</small></div>`;
    }
    if(widget.type === 'enum'){
        const options = (widget.options || []).map(option => `<option value="${escapeHtml(String(option))}" ${String(option) === String(value ?? '') ? 'selected' : ''}>${escapeHtml(String(option))}</option>`).join('');
        return `<div class="runtime-widget"${tooltip}><div class="runtime-widget-label"><span>${label}</span>${switcher}</div><select data-runtime-widget="${id}">${options}</select></div>`;
    }
    if(widget.type === 'boolean'){
        return `<div class="runtime-widget"${tooltip}><div class="runtime-widget-label"><span>${label}</span>${switcher}</div><label class="runtime-widget-check"><span>启用</span><input type="checkbox" data-runtime-widget="${id}" ${value ? 'checked' : ''}></label></div>`;
    }
    if(widget.type === 'string' && widget.multiline){
        return `<div class="runtime-widget"${tooltip}><div class="runtime-widget-label"><span>${label}</span>${switcher}</div><textarea data-runtime-widget="${id}" rows="3">${escapeHtml(String(value ?? ''))}</textarea></div>`;
    }
    const numeric = ['int','float','number'].includes(widget.type);
    const attrs = numeric ? ` type="number"${widget.min != null ? ` min="${escapeHtml(widget.min)}"` : ''}${widget.max != null ? ` max="${escapeHtml(widget.max)}"` : ''}${widget.step != null ? ` step="${escapeHtml(widget.step)}"` : ''}` : ' type="text"';
    return `<div class="runtime-widget"${tooltip}><div class="runtime-widget-label"><span>${label}</span>${switcher}</div><input${attrs} data-runtime-widget="${id}" value="${escapeHtml(String(value ?? ''))}"></div>`;
}

function renderSearch(node, escapeHtml){
    const error = escapeHtml(node.extensionError || '');
    return `<div class="runtime-node-panel runtime-node-picker">
        <div class="runtime-engine-row"><span class="runtime-dot"></span><span data-runtime-engine-label>正在检查内置节点引擎</span><button type="button" class="runtime-icon-btn" data-runtime-rescan title="重新扫描"><i data-lucide="refresh-cw"></i></button></div>
        <label class="runtime-search"><i data-lucide="search"></i><input data-runtime-search placeholder="搜索节点名称或类型" autocomplete="off"></label>
        <div class="runtime-filter-row">
            <div class="runtime-scope-toggle" role="group" aria-label="节点范围">
                <button type="button" class="active" data-runtime-scope="utility">画布实用</button>
                <button type="button" data-runtime-scope="all">全部节点</button>
            </div>
            <select data-runtime-compatibility aria-label="兼容等级"><option value="">全部兼容等级</option><option value="supported">完整兼容</option><option value="limited">有限兼容</option></select>
        </div>
        <div class="runtime-search-status">输入关键词选择节点</div>
        <div class="runtime-search-results" role="listbox"></div>
        ${error ? `<div class="runtime-error">${error}</div>` : ''}
    </div>`;
}

function renderSelected(node, definition, escapeHtml){
    const widgets = node.data?.widgets || {};
    const inputModes = node.data?.inputModes || {};
    const controls = (definition.inputs || []).filter(input => input.widget?.enabled).map(input => widgetControl(input, widgets[input.id], inputModes[input.id] || 'widget', escapeHtml)).join('');
    const compatibility = definition.compatibility === 'limited' ? '有限兼容' : '完整兼容';
    const images = (node.images || []).map(image => `<img src="${escapeHtml(typeof image === 'string' ? image : image?.url || '')}" alt="">`).join('');
    const audio = (node.audio || []).map(url => `<audio controls preload="metadata" src="${escapeHtml(typeof url === 'string' ? url : url?.url || '')}"></audio>`).join('');
    const videos = (node.videos || []).map(url => `<video controls preload="metadata" src="${escapeHtml(typeof url === 'string' ? url : url?.url || '')}"></video>`).join('');
    const status = node.running ? `${escapeHtml(node.runStatus || 'running')} ${Math.round((Number(node.runProgress) || 0) * 100)}%` : escapeHtml(node.extensionError || node.outputText || '');
    const migrationWarning = node.data?.migrationIssues?.length ? `<div class="runtime-warning">节点定义已更新：${escapeHtml(node.data.migrationIssues.join('；'))}。原连线已保留，请检查待修复端口。</div>` : '';
    return `<div class="runtime-node-panel">
        <div class="runtime-definition-head"><div><strong>${escapeHtml(definition.display_name || node.data.classType)}</strong><span>${escapeHtml(node.data.classType)}</span></div><button type="button" class="runtime-icon-btn" data-runtime-change title="更换节点"><i data-lucide="replace"></i></button></div>
        <div class="runtime-meta"><span>${escapeHtml(definition.category || 'other')}</span><span class="runtime-compat ${definition.compatibility || ''}">${compatibility}</span></div>
        ${definition.compatibility_reasons?.length ? `<div class="runtime-warning">${escapeHtml(definition.compatibility_reasons.join('；'))}</div>` : ''}
        ${migrationWarning}
        <div class="runtime-widgets">${controls || '<div class="runtime-empty">此节点没有可编辑参数</div>'}</div>
        <div class="runtime-actions"><button type="button" class="runtime-run" data-extension-run ${node.running ? 'disabled' : ''}><i data-lucide="play"></i><span>运行节点图</span></button>${node.running ? '<button type="button" class="runtime-stop" data-runtime-cancel title="取消"><i data-lucide="square"></i></button>' : ''}</div>
        ${images ? `<div class="runtime-output-images">${images}</div>` : ''}
        ${audio || videos ? `<div class="runtime-output-media">${audio}${videos}</div>` : ''}
        <div class="runtime-run-status ${node.extensionError ? 'error' : ''}">${status}</div>
    </div>`;
}

function parseWidgetValue(control, input){
    if(control.type === 'checkbox') return control.checked;
    const value = control.value;
    const type = input?.widget?.type;
    if(type === 'int') return Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : value;
    if(type === 'float' || type === 'number') return Number.isFinite(Number(value)) ? Number(value) : value;
    return value;
}

async function runRuntimeNode(node, context){
    if(node.running) return null;
    const graph = context?.buildRuntimeGraph?.(node);
    if(!graph?.nodes?.length) throw new Error('无法从画布构建运行时节点图');
    node.running = true;
    node.runStatus = 'queued';
    node.runProgress = 0;
    node.extensionError = '';
    context.update?.(node);
    let record = null;
    try {
        record = await fetch('/api/runtime-graphs/runs', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(graph)
        }).then(responseJson);
        node._runtimeRunId = record.run_id;
        context.applyRuntimeProgress?.(record, graph);
        while(!TERMINAL.has(record.status)){
            await new Promise(resolve => setTimeout(resolve, 450));
            record = await fetch(`/api/runtime-graphs/runs/${encodeURIComponent(record.run_id)}`).then(responseJson);
            node.runStatus = record.status;
            node.runProgress = Number(record.progress) || 0;
            context.applyRuntimeProgress?.(record, graph);
            context.update?.(node, {quiet:true});
        }
        context.applyRuntimeProgress?.(record, graph);
        node.runStatus = record.status;
        if(record.status !== 'succeeded') throw new Error(record.error || `运行时任务 ${record.status}`);
        const result = record.result || {};
        node.images = (result.images || []).map((url, index) => ({url, name:`runtime-${index + 1}.png`}));
        node.audio = result.audio || [];
        node.videos = result.videos || [];
        node.outputText = result.output_text || '';
        node.structuredOutput = result.structured_output ?? null;
        node.extensionOutputs = {};
        Object.entries(result.outputs || {}).forEach(([key, values]) => {
            const [sourceId, portId] = key.split(':');
            if(sourceId === node.id) node.extensionOutputs[portId] = values;
        });
        return result;
    } catch(error) {
        node.runStatus = node.runStatus === 'cancelled' ? 'cancelled' : 'failed';
        node.extensionError = error.message || String(error);
        throw error;
    } finally {
        node.running = false;
        delete node._runtimeRunId;
        context.applyRuntimeProgress?.(record || {status:node.runStatus || 'failed', progress:node.runProgress || 0}, graph);
        context.update?.(node);
        context.save?.();
    }
}

export function register(api){
    api.registerNode('runtime-node', {
        migrate({node}){
            node.data = {...(node.data || {})};
            node.data.widgets = node.data.widgets && typeof node.data.widgets === 'object' ? node.data.widgets : {};
            node.data.inputModes = node.data.inputModes && typeof node.data.inputModes === 'object' ? node.data.inputModes : {};
            if(node.data.definitionSnapshot) node.data.definitionSnapshot.runtime_node = true;
            return node;
        },
        serialize({node}){
            const copy = clone(node);
            delete copy._runtimeRunId;
            delete copy._runtimeDefinitionChecked;
            return copy;
        },
        render({node, escapeHtml}){
            const definition = selectedDefinition(node);
            return definition && node.data?.classType ? renderSelected(node, definition, escapeHtml) : renderSearch(node, escapeHtml);
        },
        bind({root, node, update, save, context}){
            const cleanups = [];
            const search = root.querySelector('[data-runtime-search]');
            const results = root.querySelector('.runtime-search-results');
            const status = root.querySelector('.runtime-search-status');
            const compatibility = root.querySelector('[data-runtime-compatibility]');
            const scopeButtons = [...root.querySelectorAll('[data-runtime-scope]')];
            let nodeScope = node.data?.pickerScope === 'all' ? 'all' : 'utility';
            scopeButtons.forEach(button => button.classList.toggle('active', button.dataset.runtimeScope === nodeScope));
            let timer = 0;
            let requestNumber = 0;
            const engineLabel = root.querySelector('[data-runtime-engine-label]');
            const engineDot = root.querySelector('.runtime-dot');
            const refreshDefinition = async () => {
                if(!node.data?.classType || !node.data?.definitionSnapshot) return;
                const currentFingerprint = node.data.definitionSnapshot.fingerprint || '';
                if(node._runtimeDefinitionChecked === currentFingerprint) return;
                node._runtimeDefinitionChecked = currentFingerprint;
                try {
                    const latest = await fetch(`/api/runtime-nodes/definition?class_type=${encodeURIComponent(node.data.classType)}`, {cache:'no-store'}).then(responseJson);
                    if(latest.fingerprint === currentFingerprint) return;
                    const oldDefinition = node.data.definitionSnapshot;
                    const newInputs = new Map((latest.inputs || []).map(input => [input.id, input]));
                    const oldWidgets = node.data.widgets || {};
                    const migratedWidgets = widgetDefaults(latest);
                    Object.keys(oldWidgets).forEach(key => { if(newInputs.has(key)) migratedWidgets[key] = oldWidgets[key]; });
                    const issues = [];
                    (oldDefinition.inputs || []).forEach(input => { if(!newInputs.has(input.id)) issues.push(`输入 ${input.id} 已不存在`); });
                    const outputCount = (latest.outputs || []).length;
                    (oldDefinition.outputs || []).forEach(output => { if(Number(output.index) >= outputCount) issues.push(`输出 ${output.id} 已不存在`); });
                    node.data = {
                        ...node.data,
                        widgets:migratedWidgets,
                        inputModes:{...inputModeDefaults(latest), ...(node.data.inputModes || {})},
                        definitionSnapshot:definitionSnapshot(latest),
                        migrationIssues:issues
                    };
                    node._runtimeDefinitionChecked = latest.fingerprint || '';
                    node.title = latest.display_name_zh || latest.display_name || latest.class_type;
                    update?.(node); save?.();
                } catch(error) {
                    node.extensionError = error.message || String(error);
                    update?.(node);
                }
            };
            const waitForInstall = async () => {
                for(let attempt = 0; attempt < 720; attempt++){
                    const component = await fetch('/api/components/node-engine/status', {cache:'no-store'}).then(responseJson);
                    status.textContent = component.message || `正在安装 ${Math.round(Number(component.progress_percent) || 0)}%`;
                    if(component.ready) return component;
                    if(['error','cancelled'].includes(component.state)) throw new Error(component.error || component.message || '节点引擎安装失败');
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }
                throw new Error('节点引擎安装等待超时');
            };
            const renderInstaller = component => {
                if(!results) return;
                status.textContent = component.error || '节点引擎尚未安装';
                results.innerHTML = `<div class="runtime-installer"><strong>安装独立节点引擎</strong><span>可选择已经下载好的便携运行时目录进行独立导入；模型和扩展不会复制。</span><input type="text" data-runtime-source-root placeholder="例如 E:\\ComfyUI_portable"><button type="button" data-runtime-install>${component.can_install ? '安装官方组件' : '从本地目录导入'}</button></div>`;
                const button = results.querySelector('[data-runtime-install]');
                const source = results.querySelector('[data-runtime-source-root]');
                const listener = async event => {
                    event.preventDefault(); event.stopPropagation();
                    const sourceRoot = source?.value?.trim() || '';
                    if(!component.can_install && !sourceRoot){ status.textContent = '请输入便携运行时目录'; source?.focus(); return; }
                    button.disabled = true;
                    try {
                        await fetch('/api/components/node-engine/install', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source_root:sourceRoot})}).then(responseJson);
                        await waitForInstall();
                        await fetch('/api/node-engine/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({wait_seconds:90})}).then(responseJson);
                        await fetch('/api/runtime-nodes/rescan', {method:'POST'}).then(responseJson);
                        engineLabel.textContent = '内置节点引擎已就绪';
                        engineDot?.classList.add('ready');
                        await searchNodes();
                    } catch(error){ status.textContent = error.message || String(error); button.disabled = false; }
                };
                button.addEventListener('click', listener);
                cleanups.push(() => button.removeEventListener('click', listener));
            };
            const initializePicker = async () => {
                try {
                    const component = await fetch('/api/components/node-engine/status', {cache:'no-store'}).then(responseJson);
                    if(!component.ready){
                        engineLabel.textContent = '内置节点引擎未安装';
                        renderInstaller(component);
                        return;
                    }
                    engineLabel.textContent = component.process?.ready ? '内置节点引擎运行中' : '内置节点引擎已安装';
                    if(component.process?.ready) engineDot?.classList.add('ready');
                    if(!component.catalog?.node_count){
                        status.textContent = '正在启动并扫描节点引擎...';
                        await fetch('/api/node-engine/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({wait_seconds:90})}).then(responseJson);
                        await fetch('/api/runtime-nodes/rescan', {method:'POST'}).then(responseJson);
                    }
                    await searchNodes();
                } catch(error){ status.textContent = error.message || String(error); }
            };
            const searchNodes = async () => {
                if(!search || !results) return;
                const current = ++requestNumber;
                const query = search.value.trim();
                status.textContent = '正在读取节点目录...';
                try {
                    const params = new URLSearchParams({query, scope:nodeScope, compatibility:compatibility?.value || '', page:'1', page_size:'30'});
                    const payload = await fetch(`/api/runtime-nodes?${params}`, {cache:'no-store'}).then(responseJson);
                    if(current !== requestNumber) return;
                    status.textContent = `找到 ${payload.total} 个节点`;
                    results.innerHTML = (payload.items || []).map(item => `<button type="button" class="runtime-result" data-runtime-class="${api.escapeHtml(item.class_type)}" ${item.compatibility === 'blocked' ? 'disabled' : ''}><span><strong>${api.escapeHtml(item.display_name_zh || item.display_name)}</strong><small>${api.escapeHtml(item.display_name_zh && item.display_name !== item.display_name_zh ? item.display_name + ' · ' : '')}${api.escapeHtml(item.class_type)}</small></span><em class="${item.compatibility}">${item.compatibility === 'limited' ? '有限' : item.compatibility === 'blocked' ? '阻止' : '兼容'}</em></button>`).join('');
                    results.querySelectorAll('[data-runtime-class]').forEach(button => {
                        const listener = async event => {
                            event.preventDefault(); event.stopPropagation();
                            try {
                                const definition = await fetch(`/api/runtime-nodes/definition?class_type=${encodeURIComponent(button.dataset.runtimeClass)}`, {cache:'no-store'}).then(responseJson);
                                node.data = {
                                    ...(node.data || {}), classType:definition.class_type,
                                    widgets:widgetDefaults(definition), inputModes:inputModeDefaults(definition),
                                    definitionSnapshot:definitionSnapshot(definition), migrationIssues:[]
                                };
                                node.title = definition.display_name_zh || definition.display_name || definition.class_type;
                                node.extensionError = '';
                                update?.(node); save?.();
                            } catch(error) {
                                node.extensionError = error.message || String(error); update?.(node);
                            }
                        };
                        button.addEventListener('click', listener);
                        cleanups.push(() => button.removeEventListener('click', listener));
                    });
                } catch(error) {
                    if(current !== requestNumber) return;
                    status.textContent = error.message || String(error);
                    results.innerHTML = '<div class="runtime-empty">请先安装并启动节点引擎</div>';
                }
            };
            if(search){
                const listener = () => { clearTimeout(timer); timer = setTimeout(searchNodes, 250); };
                search.addEventListener('input', listener);
                cleanups.push(() => search.removeEventListener('input', listener));
                initializePicker();
            }
            if(compatibility){
                const listener = searchNodes;
                compatibility.addEventListener('change', listener);
                cleanups.push(() => compatibility.removeEventListener('change', listener));
            }
            scopeButtons.forEach(button => {
                const listener = event => {
                    event.preventDefault(); event.stopPropagation();
                    nodeScope = button.dataset.runtimeScope === 'all' ? 'all' : 'utility';
                    node.data = {...(node.data || {}), pickerScope:nodeScope};
                    scopeButtons.forEach(item => item.classList.toggle('active', item.dataset.runtimeScope === nodeScope));
                    save?.();
                    searchNodes();
                };
                button.addEventListener('click', listener);
                cleanups.push(() => button.removeEventListener('click', listener));
            });
            const rescan = root.querySelector('[data-runtime-rescan]');
            if(rescan){
                const listener = async event => {
                    event.preventDefault(); event.stopPropagation();
                    status.textContent = '正在重新扫描节点...';
                    try {
                        await fetch('/api/node-engine/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({wait_seconds:90})}).then(responseJson);
                        await fetch('/api/runtime-nodes/rescan', {method:'POST'}).then(responseJson);
                        engineLabel.textContent = '内置节点引擎运行中'; engineDot?.classList.add('ready');
                        await searchNodes();
                    }
                    catch(error){ status.textContent = error.message || String(error); }
                };
                rescan.addEventListener('click', listener);
                cleanups.push(() => rescan.removeEventListener('click', listener));
            }
            root.querySelectorAll('[data-runtime-widget]').forEach(control => {
                const listener = () => {
                    const definition = selectedDefinition(node);
                    const input = (definition?.inputs || []).find(item => item.id === control.dataset.runtimeWidget);
                    node.data.widgets = {...(node.data.widgets || {}), [control.dataset.runtimeWidget]:parseWidgetValue(control, input)};
                    save?.();
                };
                const eventName = control.tagName === 'SELECT' || control.type === 'checkbox' ? 'change' : 'input';
                control.addEventListener(eventName, listener);
                cleanups.push(() => control.removeEventListener(eventName, listener));
            });
            root.querySelectorAll('[data-runtime-input-mode]').forEach(button => {
                const listener = event => {
                    event.preventDefault(); event.stopPropagation();
                    const inputId = button.dataset.runtimeInputMode;
                    const current = node.data?.inputModes?.[inputId] || 'widget';
                    node.data.inputModes = {...(node.data.inputModes || {}), [inputId]:current === 'port' ? 'widget' : 'port'};
                    update?.(node); save?.();
                };
                button.addEventListener('click', listener);
                cleanups.push(() => button.removeEventListener('click', listener));
            });
            const change = root.querySelector('[data-runtime-change]');
            if(change){
                const listener = event => {
                    event.preventDefault(); event.stopPropagation();
                    node.data = {...(node.data || {}), classType:'', definitionSnapshot:null, widgets:{}, inputModes:{}, migrationIssues:[]};
                    node.title = '运行时节点'; update?.(node); save?.();
                };
                change.addEventListener('click', listener);
                cleanups.push(() => change.removeEventListener('click', listener));
            }
            const cancel = root.querySelector('[data-runtime-cancel]');
            if(cancel){
                const listener = async event => {
                    event.preventDefault(); event.stopPropagation();
                    if(node._runtimeRunId) await fetch(`/api/runtime-graphs/runs/${encodeURIComponent(node._runtimeRunId)}`, {method:'DELETE'}).then(responseJson).catch(() => null);
                };
                cancel.addEventListener('click', listener);
                cleanups.push(() => cancel.removeEventListener('click', listener));
            }
            refreshDefinition();
            if(window.lucide) window.lucide.createIcons({nodes:[root]});
            return () => { clearTimeout(timer); cleanups.splice(0).forEach(cleanup => cleanup()); };
        },
        run({node, context}){
            return runRuntimeNode(node, context);
        }
    });
}
