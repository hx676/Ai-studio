(function(){
    const list = document.getElementById('extensionList');
    const status = document.getElementById('status');
    const summary = document.getElementById('summary');
    const count = document.getElementById('packageCount');
    const rescanBtn = document.getElementById('rescanBtn');
    const applyBtn = document.getElementById('applyBtn');
    let registry = null;
    let managerLanguage = localStorage.getItem('studio_lang') || 'zh';

    function escapeHtml(value){
        return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
    function localizedField(item, key){
        if(managerLanguage !== 'en'){
            const translated = String(item?.[`${key}_zh`] || '').trim();
            if(translated) return translated;
        }
        return item?.[key] || '';
    }
    function setStatus(message, kind=''){
        status.textContent = message || '';
        status.className = `extension-status ${kind}`;
    }
    async function jsonRequest(url, options={}){
        const response = await fetch(url, options);
        const body = await response.json().catch(() => ({}));
        if(!response.ok){
            const error = new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || `请求失败 (${response.status})`);
            error.status = response.status;
            error.detail = body.detail;
            throw error;
        }
        return body;
    }
    function statusLabel(item){
        return {
            loaded:'已加载', disabled:'已禁用', pending_restart:'等待重启',
            missing_dependencies:'缺少依赖', error:'加载失败', invalid:'配置无效'
        }[item.status] || item.status;
    }
    function packageRow(item){
        const requirements = (item.requirements || []).join(', ');
        const missing = (item.missing_dependencies || []).join(', ');
        const nodes = (item.nodes || []).map(node => localizedField(node, 'display_name')).join('、');
        return `<article class="extension-row" data-package-id="${escapeHtml(item.id)}">
            <div class="extension-main">
                <div class="extension-title-line">
                    <strong>${escapeHtml(localizedField(item, 'name') || item.id)}</strong>
                    <code>${escapeHtml(item.id)}</code>
                    <span class="status-chip status-${escapeHtml(item.status)}">${escapeHtml(statusLabel(item))}</span>
                </div>
                <p>${escapeHtml(localizedField(item, 'description') || '无描述')}</p>
                <div class="extension-meta">
                    <span>版本 ${escapeHtml(item.version || '-')}</span>
                    <span>${(item.nodes || []).length} 个节点</span>
                    <span>${escapeHtml(nodes || '未声明节点')}</span>
                </div>
                ${requirements ? `<div class="dependency-line"><i data-lucide="package"></i><span>${escapeHtml(requirements)}</span></div>` : ''}
                ${missing ? `<div class="extension-error">缺少依赖：${escapeHtml(missing)}</div>` : ''}
                ${item.error ? `<div class="extension-error">${escapeHtml(item.error)}</div>` : ''}
            </div>
            <div class="extension-row-actions">
                ${(item.missing_dependencies || []).length ? `<button type="button" data-install title="安装依赖"><i data-lucide="download"></i><span>安装依赖</span></button>` : ''}
                ${item.status !== 'invalid' ? `<label class="enable-toggle"><input type="checkbox" data-enabled ${item.enabled ? 'checked' : ''}><span></span><b>${item.enabled ? '启用' : '禁用'}</b></label>` : ''}
            </div>
        </article>`;
    }
    function render(payload){
        registry = payload;
        const packages = payload.packages || [];
        count.textContent = String(packages.length);
        summary.innerHTML = `<span><b>${packages.filter(item => item.loaded).length}</b> 已加载</span><span><b>${payload.nodes?.length || 0}</b> 个节点</span><span><b>${packages.filter(item => item.error).length}</b> 个错误</span>${payload.restart_required ? '<span class="restart-note"><i data-lucide="rotate-cw"></i>有变更等待应用</span>' : ''}`;
        list.innerHTML = packages.length ? packages.map(packageRow).join('') : '<div class="empty-state">custom_nodes 中没有可用扩展</div>';
        applyBtn.disabled = !payload.restart_required;
        bindRows();
        if(window.lucide) lucide.createIcons();
    }
    async function load(rescan=false){
        setStatus(rescan ? '正在扫描扩展目录...' : '正在读取扩展...');
        rescanBtn.disabled = true;
        try {
            const payload = await jsonRequest('/api/node-extensions' + (rescan ? '/rescan' : ''), rescan ? {method:'POST'} : {});
            render(payload);
            setStatus(rescan ? '扫描完成' : '');
        } catch(error) {
            setStatus(error.message, 'error');
        } finally {
            rescanBtn.disabled = false;
        }
    }
    function bindRows(){
        list.querySelectorAll('.extension-row').forEach(row => {
            const packageId = row.dataset.packageId;
            const toggle = row.querySelector('[data-enabled]');
            toggle?.addEventListener('change', async () => {
                toggle.disabled = true;
                try {
                    render(await jsonRequest(`/api/node-extensions/${encodeURIComponent(packageId)}`, {
                        method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:toggle.checked})
                    }));
                    setStatus('启停状态已保存，点击“应用并重启”后生效。', 'notice');
                } catch(error) {
                    setStatus(error.message, 'error');
                    toggle.checked = !toggle.checked;
                    toggle.disabled = false;
                }
            });
            row.querySelector('[data-install]')?.addEventListener('click', async event => {
                if(!window.confirm('依赖将安装到 SynCanvas 共享 Python 环境。仅应安装可信扩展，是否继续？')) return;
                const button = event.currentTarget;
                button.disabled = true;
                setStatus(`正在安装 ${packageId} 的依赖，这可能需要几分钟...`);
                try {
                    const result = await jsonRequest(`/api/node-extensions/${encodeURIComponent(packageId)}/dependencies/install`, {
                        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirmed:true})
                    });
                    setStatus(result.log ? result.log.split('\n').slice(-1)[0] : '依赖安装完成', 'notice');
                    await load(true);
                } catch(error) {
                    setStatus(error.message, 'error');
                    button.disabled = false;
                }
            });
        });
    }
    async function waitForRestart(){
        let sawOffline = false;
        const started = Date.now();
        while(Date.now() - started < 90000){
            await new Promise(resolve => setTimeout(resolve, 1000));
            try {
                const response = await fetch('/api/app-info', {cache:'no-store'});
                if(response.ok && sawOffline){
                    window.top.location.reload();
                    return;
                }
            } catch(error) {
                sawOffline = true;
                setStatus('后端正在重启，请稍候...', 'notice');
            }
        }
        setStatus('后端重启超时，请检查启动器日志。', 'error');
        applyBtn.disabled = false;
    }
    async function applyChanges(retry=false){
        applyBtn.disabled = true;
        setStatus('正在应用扩展变更...');
        try {
            window.parent.postMessage({type:'syncanvas-save-before-restart'}, '*');
            await new Promise(resolve => setTimeout(resolve, 500));
            const result = await jsonRequest('/api/node-extensions/apply', {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({restart_delay:3})
            });
            if(!result.restart_scheduled){
                setStatus('没有需要重启的变更。');
                await load();
                return;
            }
            setStatus('已安排后端重启...', 'notice');
            await waitForRestart();
        } catch(error) {
            const runs = error.detail?.runs || [];
            if(error.status === 409 && runs.length && !retry && window.confirm(`仍有 ${runs.length} 个扩展节点在运行。是否取消这些任务并继续？`)){
                await Promise.all(runs.map(run => fetch(`/api/node-runs/${encodeURIComponent(run.run_id)}`, {method:'DELETE'})));
                return applyChanges(true);
            }
            setStatus(error.message, 'error');
            applyBtn.disabled = false;
        }
    }
    rescanBtn.addEventListener('click', () => load(true));
    applyBtn.addEventListener('click', () => applyChanges());
    window.addEventListener('message', event => {
        if(event.data?.type === 'studio-theme') document.documentElement.classList.toggle('theme-dark', event.data.theme === 'dark');
        if(event.data?.type === 'studio-lang'){
            managerLanguage = event.data.lang === 'en' ? 'en' : 'zh';
            if(registry) render(registry);
        }
    });
    load();
})();
