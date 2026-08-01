let providers = [];
let selectedId = '';
let providerToggleSavingId = '';
let lastSaveError = '';
const providerList = document.getElementById('providerList');
const editorTitle = document.getElementById('editorTitle');
const statusEl = document.getElementById('status');
const nameInput = document.getElementById('nameInput');
const idInput = document.getElementById('idInput');
const baseInput = document.getElementById('baseInput');
const logoInput = document.getElementById('logoInput');
const logoPreviewCard = document.getElementById('logoPreviewCard');
const logoPreviewImg = document.getElementById('logoPreviewImg');
const removeLogoBtn = document.getElementById('removeLogoBtn');
const protocolInput = document.getElementById('protocolInput');
const advancedEndpoints = document.getElementById('advancedEndpoints');
const imageGenerationEndpointInput = document.getElementById('imageGenerationEndpointInput');
const imageEditEndpointInput = document.getElementById('imageEditEndpointInput');
const keyInput = document.getElementById('keyInput');
const keyHint = document.getElementById('keyHint');
const jimengCliPanel = document.getElementById('jimengCliPanel');
const jimengCliStatus = document.getElementById('jimengCliStatus');
const jimengInstallBtn = document.getElementById('jimengInstallBtn');
const jimengLoginBtn = document.getElementById('jimengLoginBtn');
const jimengCreditBtn = document.getElementById('jimengCreditBtn');
const jimengLogoutBtn = document.getElementById('jimengLogoutBtn');
const jimengCredit = document.getElementById('jimengCredit');
const jimengLoginBox = document.getElementById('jimengLoginBox');
const imageModelList = document.getElementById('imageModelList');
const chatModelList = document.getElementById('chatModelList');
const videoModelList = document.getElementById('videoModelList');
const msLoraBlock = document.getElementById('msLoraBlock');
const msLoraList = document.getElementById('msLoraList');
const MS_BUILTIN_IMAGE_MODELS = [
    'Tongyi-MAI/Z-Image-Turbo',
    'Qwen/Qwen-Image-2512',
    'Qwen/Qwen-Image-Edit-2511',
    'black-forest-labs/FLUX.2-klein-9B'
];

function refreshIcons(){ if(window.lucide) lucide.createIcons(); }
function tr(key){ return window.StudioI18n ? window.StudioI18n.t(key) : key; }
function setStatus(text){ statusEl.textContent = text || ''; }
function isBuiltinProvider(item){
    return item?.id === 'modelscope' || item?.id === 'runninghub';
}
function normalizeId(value){
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/^-+|-+$/g, '').replace(/-+/g, '-').slice(0, 40);
}
// 平台 Key 按 ID 写入 API/.env；ID 一旦创建就保持稳定，避免改名或中文名称导致 Key 看起来丢失。
function deriveIdFromName(name, existingId){
    if(existingId) return existingId;
    let id = normalizeId(name);
    if(!id){
        id = 'api-' + Math.random().toString(36).slice(2, 8);
    }
    let candidate = id, i = 2;
    while(providers.some(p => p.id === candidate)){
        candidate = `${id}-${i++}`;
    }
    return candidate;
}
function updateIdPreview(){
    const item = provider();
    if(!item) return;
    const isBuiltin = item.id === 'comfly' || item.id === 'modelscope' || item.id === 'runninghub';
    const idPreview = document.getElementById('idPreview');
    if(!idPreview) return;
    if(isBuiltin){
        idPreview.textContent = item.id;
        return;
    }
    idPreview.textContent = deriveIdFromName(nameInput.value, item.id);
}
function provider(){
    return providers.find(item => item.id === selectedId) || providers[0];
}
function unique(values){
    const seen = new Set();
    return values.map(v => String(v || '').trim()).filter(v => v && !seen.has(v) && seen.add(v));
}
function endpointValue(input){
    return String(input?.value || '').trim();
}
function logoRatioMessage(width, height){
    const ratio = height ? width / height : 0;
    return `${tr('api.logoRatioAlert')} ${tr('api.logoCurrentRatio')} ${ratio.toFixed(2)}:1 (${width}x${height})`;
}
function readLogoImage(file){
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const result = { width:img.naturalWidth, height:img.naturalHeight };
            URL.revokeObjectURL(url);
            resolve(result);
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error(tr('api.logoInvalidImage')));
        };
        img.src = url;
    });
}
function openLogoPicker(){
    const item = provider();
    if(!item || isBuiltinProvider(item)) return;
    logoInput?.click();
}
function renderLogoPreview(item){
    if(!logoPreviewImg || !logoPreviewCard) return;
    logoPreviewImg.src = item?.logo_url || '';
    logoPreviewImg.alt = item?.logo_url ? (item.name || item.id || 'Logo') : '';
    logoPreviewCard.classList.toggle('has-logo', !!item?.logo_url);
    if(removeLogoBtn) removeLogoBtn.style.display = item?.logo_url ? 'inline-flex' : 'none';
}
async function handleLogoUpload(file){
    syncEditor();
    const item = provider();
    if(!item || isBuiltinProvider(item) || !file) return;
    const fileType = String(file.type || '').toLowerCase();
    const fileName = String(file.name || '').toLowerCase();
    const looksLikeImage = ['image/png', 'image/jpeg', 'image/webp'].includes(fileType)
        || /\.(png|jpe?g|webp)$/.test(fileName);
    if(!looksLikeImage){
        StudioDialog.alert(tr('api.logoInvalidImage'));
        return;
    }
    if(file.size > 512 * 1024){
        StudioDialog.alert(tr('api.logoSizeAlert'));
        return;
    }
    let dimensions;
    try {
        dimensions = await readLogoImage(file);
    } catch(err) {
        StudioDialog.alert(err.message || tr('api.logoInvalidImage'));
        return;
    }
    const ratio = dimensions.height ? dimensions.width / dimensions.height : 0;
    if(ratio < 4 || ratio > 6){
        StudioDialog.alert(logoRatioMessage(dimensions.width, dimensions.height));
        return;
    }
    const form = new FormData();
    form.append('file', file);
    setStatus(tr('api.logoUploading'));
    try {
        const res = await fetch('/api/providers/logo', { method:'POST', body:form });
        const data = await res.json();
        if(!res.ok) throw new Error(data.detail || tr('api.logoUploadFailed'));
        item.logo_url = data.url || '';
        renderLogoPreview(item);
        renderProviderList();
        setStatus(tr('api.logoUploaded'));
    } catch(err) {
        setStatus(err.message || tr('api.logoUploadFailed'));
    }
}
function removeProviderLogo(){
    syncEditor();
    const item = provider();
    if(!item || isBuiltinProvider(item)) return;
    item.logo_url = '';
    renderLogoPreview(item);
    renderProviderList();
    setStatus(tr('api.logoRemoved'));
}
function toggleAdvancedEndpoints(force){
    if(!advancedEndpoints) return;
    const open = typeof force === 'boolean' ? force : !advancedEndpoints.classList.contains('open');
    advancedEndpoints.classList.toggle('open', open);
    refreshIcons();
}
function normalizeEndpointSetting(value){
    const endpoint = String(value || '').trim();
    if(!endpoint) return '';
    if(/^https?:\/\//i.test(endpoint)) return endpoint.replace(/\/+$/, '');
    return endpoint;
}
function validateEndpointSetting(endpoint, label){
    if(!endpoint) return true;
    if(/\s/.test(endpoint)) throw new Error(`${label} 不能包含空格`);
    if(/^https?:\/\//i.test(endpoint)) return true;
    if(!endpoint.startsWith('/')) throw new Error(`${label} 请填写 /v1/... 格式，例如 /v1/images/edits`);
    return true;
}
function syncEditor(){
    const item = provider();
    if(!item) return;
    const oldId = item.id;
    const isBuiltin = item.id === 'comfly' || isBuiltinProvider(item);
    // 内置和自定义平台的 ID 都保持稳定；新建时若没有 ID 才生成一次。
    const nextId = isBuiltin ? item.id : deriveIdFromName(nameInput.value, item.id);
    item.id = nextId;
    if(oldId !== item.id) selectedId = item.id;
    item.name = nameInput.value.trim() || item.id;
    item.base_url = baseInput.value.trim();
    // MS 固定使用 OpenAI 协议，不从下拉读取
    item.protocol = (item.id === 'modelscope') ? 'openai' : item.id === 'runninghub' ? 'runninghub' : (protocolInput?.value || 'openai');
    item.image_generation_endpoint = normalizeEndpointSetting(endpointValue(imageGenerationEndpointInput));
    item.image_edit_endpoint = normalizeEndpointSetting(endpointValue(imageEditEndpointInput));
    const key = keyInput.value.trim();
    if(key) item.api_key = key;
}
function updateProtocolFromInput(){
    const item = provider();
    if(!item || !protocolInput || item.id === 'modelscope' || item.id === 'runninghub') return;
    const value = String(protocolInput.value || 'openai').toLowerCase();
    const supported = ['openai', 'apimart', 'gemini', 'grok', 'volcengine', 'jimeng', 'codex', 'gemini-cli'];
    item.protocol = supported.includes(value) ? value : 'openai';
    if(value === 'jimeng'){
        item.base_url = '';
        item.image_models = unique([...(item.image_models || []), '5.0Pro', '5.0', '4.7', '4.6', '4.5', '4.1', '4.0', '3.1', '3.0']);
        item.video_models = unique([...(item.video_models || []), 'seedance2.0fast_vip', 'seedance2.0_vip', 'seedance2.0', 'seedance2.0fast', 'seedance2.0mini']);
    } else if(value === 'codex'){
        item.base_url = '';
        item.image_models = unique([...(item.image_models || []), 'gpt-image-2']);
        item.chat_models = unique([...(item.chat_models || []), 'gpt-5.5']);
        item.video_models = [];
    } else if(value === 'gemini-cli'){
        item.base_url = '';
        item.image_models = unique([...(item.image_models || []), 'auto']);
        item.chat_models = unique([...(item.chat_models || []), 'auto']);
        item.video_models = [];
    }
    baseInput.value = item.base_url || '';
    applyJimengMode(value === 'jimeng');
    renderModels('image');
    renderModels('chat');
    renderModels('video');
    clearVerifyResult();
}
let jimengLoginTimer = null;
function applyJimengMode(active){
    document.body.classList.toggle('show-jimeng', active);
    if(jimengCliPanel){
        jimengCliPanel.hidden = !active;
        jimengCliPanel.style.display = active ? 'flex' : 'none';
    }
    if(!active){
        const item = provider();
        keyInput.placeholder = item?.has_key ? `${tr('api.keepCurrentKey')} ${item.key_preview || ''}` : tr('api.enterKey');
        keyHint.textContent = item?.has_key ? `${tr('api.keySaved')}${item.key_env || 'API/.env'}` : tr('api.noKey');
        return;
    }
    baseInput.value = '';
    keyInput.value = '';
    keyInput.placeholder = '即梦 CLI 使用本机 Dreamina 登录态，无需 API Key';
    keyHint.textContent = '安装并登录 Dreamina CLI 后即可在画布中选择即梦模型。';
    refreshJimengStatus(false);
}
function setJimengStatus(text, state=''){
    if(!jimengCliStatus) return;
    jimengCliStatus.textContent = text || '未检测';
    jimengCliStatus.classList.toggle('ok', state === 'ok');
    jimengCliStatus.classList.toggle('bad', state === 'bad');
}
function setJimengControls(installed, loggedIn){
    if(jimengInstallBtn){
        jimengInstallBtn.hidden = installed;
        jimengInstallBtn.style.display = installed ? 'none' : 'inline-flex';
    }
    if(jimengLoginBtn) jimengLoginBtn.disabled = !installed || loggedIn;
    if(jimengCreditBtn) jimengCreditBtn.disabled = !loggedIn;
    if(jimengLogoutBtn) jimengLogoutBtn.disabled = !loggedIn;
}
function jimengCreditText(raw){
    if(!raw) return '';
    const parts = [];
    const seen = new Set();
    const visit = value => {
        if(!value || typeof value !== 'object') return;
        Object.entries(value).forEach(([key, item]) => {
            const low = key.toLowerCase();
            if(/credit|balance|quota|point|coin|积分|余额/.test(low) && item !== null && typeof item !== 'object'){
                const label = `${key}: ${item}`;
                if(!seen.has(label)){ seen.add(label); parts.push(label); }
            }
            if(item && typeof item === 'object') visit(item);
        });
    };
    visit(raw);
    if(parts.length) return parts.join(' · ');
    try { return JSON.stringify(raw, null, 2); } catch(_) { return String(raw); }
}
async function refreshJimengStatus(showCredit=true){
    if(!jimengCliPanel || jimengCliPanel.hidden) return;
    setJimengStatus('检测中');
    try {
        const response = await fetch('/api/jimeng/status');
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '检测失败');
        const installed = data.installed === true;
        const loggedIn = data.logged_in === true;
        setJimengControls(installed, loggedIn);
        if(loggedIn){
            setJimengStatus('已登录', 'ok');
            if(showCredit && data.raw && jimengCredit) jimengCredit.textContent = jimengCreditText(data.raw);
        } else if(installed){
            setJimengStatus('未登录', 'bad');
            if(jimengCredit) jimengCredit.textContent = data.message || `Dreamina CLI ${data.cli_version || ''} 已安装，请扫码登录。`;
        } else {
            setJimengStatus('未安装', 'bad');
            if(jimengCredit) jimengCredit.textContent = '本机尚未安装 Dreamina CLI。点击“安装 CLI”，在打开的窗口中按提示完成 WSL 和即梦 CLI 安装。';
        }
        if(installed && data.version_ok === false && jimengCredit){
            jimengCredit.textContent = `当前版本 ${data.cli_version || '未知'} 低于推荐版本 ${data.min_version || '1.4.2'}，请重新运行安装器升级。`;
        }
    } catch(error){
        setJimengStatus('检测失败', 'bad');
        setJimengControls(false, false);
        if(jimengCredit) jimengCredit.textContent = error.message || String(error);
    }
}
async function startJimengInstall(){
    if(jimengInstallBtn) jimengInstallBtn.disabled = true;
    setJimengStatus('启动安装器');
    try {
        const response = await fetch('/api/jimeng/install/start', {method:'POST'});
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '启动安装器失败');
        setJimengStatus('安装中');
        if(jimengCredit) jimengCredit.textContent = `${data.message || '安装窗口已打开。'} 完成后点击“检测状态”。`;
    } catch(error){
        setJimengStatus('启动失败', 'bad');
        if(jimengCredit) jimengCredit.textContent = error.message || String(error);
    } finally {
        if(jimengInstallBtn) jimengInstallBtn.disabled = false;
    }
}
function renderJimengLoginBox(data){
    if(!jimengLoginBox) return;
    if(data?.logged_in){
        jimengLoginBox.hidden = true;
        jimengLoginBox.innerHTML = '';
        return;
    }
    const text = data?.text || '';
    const qrImageUrl = data?.qr_image_url || data?.qr_url || '';
    const verificationUrl = data?.verification_url || '';
    const userCode = data?.user_code || '';
    const qr = qrImageUrl
        ? `<img class="jimeng-qr-img" src="${escapeAttr(qrImageUrl)}" alt="即梦登录二维码">`
        : '';
    const openLink = /^https?:\/\//i.test(verificationUrl)
        ? `<a class="jimeng-login-link" href="${escapeAttr(verificationUrl)}" target="_blank" rel="noopener noreferrer">浏览器打开登录页</a>`
        : '';
    const code = userCode
        ? `<div class="jimeng-user-code"><span>备用用户码</span><code>${escapeHtml(userCode)}</code></div>`
        : '';
    const raw = text
        ? `<details class="jimeng-login-raw"><summary>查看 CLI 登录信息</summary><pre>${escapeHtml(text)}</pre></details>`
        : '';
    const detail = (openLink || code || raw)
        ? `<div class="jimeng-login-detail">${openLink}${code}${raw}</div>`
        : '';
    jimengLoginBox.hidden = false;
    jimengLoginBox.innerHTML = data?.expired
        ? `<div class="jimeng-login-expired">登录二维码已过期，请重新点击“扫码登录”。</div>${raw}`
        : `${qr}${detail || '<div class="jimeng-login-detail">等待 Dreamina CLI 返回登录信息...</div>'}`;
}
async function startJimengLogin(){
    setJimengStatus('等待扫码');
    if(jimengCredit) jimengCredit.textContent = '';
    try {
        const response = await fetch('/api/jimeng/login/start', {method:'POST'});
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '启动登录失败');
        renderJimengLoginBox(data);
        clearInterval(jimengLoginTimer);
        jimengLoginTimer = setInterval(pollJimengLogin, 2500);
    } catch(error){
        setJimengStatus('登录失败', 'bad');
        if(jimengCredit) jimengCredit.textContent = error.message || String(error);
    }
}
async function pollJimengLogin(){
    try {
        const response = await fetch('/api/jimeng/login/status');
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '登录检测失败');
        renderJimengLoginBox(data);
        if(data.logged_in){
            clearInterval(jimengLoginTimer);
            setJimengStatus('已登录', 'ok');
            setJimengControls(true, true);
            if(jimengCredit) jimengCredit.textContent = jimengCreditText(data.raw);
        } else if(data.running){
            setJimengStatus('等待扫码');
        } else {
            clearInterval(jimengLoginTimer);
            setJimengStatus('未登录', 'bad');
        }
    } catch(error){
        clearInterval(jimengLoginTimer);
        setJimengStatus('登录检测失败', 'bad');
        if(jimengCredit) jimengCredit.textContent = error.message || String(error);
    }
}
async function refreshJimengCredit(){
    setJimengStatus('查询积分');
    try {
        const response = await fetch('/api/jimeng/credit');
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '查询积分失败');
        setJimengStatus('已登录', 'ok');
        if(jimengCredit) jimengCredit.textContent = jimengCreditText(data.raw);
    } catch(error){
        setJimengStatus('查询失败', 'bad');
        if(jimengCredit) jimengCredit.textContent = error.message || String(error);
    }
}
async function logoutJimeng(){
    if(!confirm('确认退出即梦 CLI 登录？')) return;
    try {
        const response = await fetch('/api/jimeng/logout', {method:'POST'});
        const data = await response.json();
        if(!response.ok) throw new Error(data.detail || '退出登录失败');
        setJimengStatus('未登录', 'bad');
        setJimengControls(true, false);
        if(jimengCredit) jimengCredit.textContent = '即梦 CLI 已退出登录。';
        if(jimengLoginBox) jimengLoginBox.hidden = true;
    } catch(error){
        setJimengStatus('退出失败', 'bad');
        if(jimengCredit) jimengCredit.textContent = error.message || String(error);
    }
}
function sortedProviders(){
    const order = ['modelscope', 'runninghub', 'comfly'];
    return [...providers].sort((a, b) => {
        const ai = order.indexOf(a.id);
        const bi = order.indexOf(b.id);
        if(ai === -1 && bi === -1) return 0;
        if(ai === -1) return 1;
        if(bi === -1) return -1;
        return ai - bi;
    });
}
function providerToggleMarkup(item){
    const enabled = item.enabled !== false;
    const label = enabled ? '已启用' : '已关闭';
    const saving = providerToggleSavingId === item.id;
    return `
        <span class="provider-card-side" onclick="event.stopPropagation()" onkeydown="event.stopPropagation()">
            ${providerCapabilitiesMarkup(item)}
            <span class="provider-state ${enabled ? 'is-enabled' : ''}">${label}</span>
            <button class="provider-switch ${enabled ? 'is-on' : ''}" type="button" role="switch" aria-checked="${enabled ? 'true' : 'false'}" aria-label="${escapeAttr(`${item.name || item.id} ${label}`)}" onclick="toggleProviderEnabled('${escapeAttr(item.id)}', event)" ${saving ? 'disabled' : ''}>
                <span class="provider-switch-knob"></span>
            </button>
        </span>
    `;
}
function providerCapabilitiesMarkup(item){
    const badges = [];
    if((item.image_models || []).length) badges.push('<span class="provider-capability image">生图可用</span>');
    if((item.video_models || []).length) badges.push('<span class="provider-capability video">视频可用</span>');
    if((item.chat_models || []).length) badges.push('<span class="provider-capability chat">对话可用</span>');
    if(!badges.length) badges.push('<span class="provider-capability empty">仅基础配置</span>');
    return `<span class="provider-capabilities">${badges.join('')}</span>`;
}
function renderProviderList(){
    providerList.innerHTML = sortedProviders().map(item => {
        const active = item.id === selectedId ? 'active' : '';
        const disabled = item.enabled === false ? 'is-disabled' : '';
        const isJimeng = String(item.protocol || '').toLowerCase() === 'jimeng';
        const providerIcon = isJimeng ? 'terminal' : (item.has_key ? 'key-round' : 'key');
        const providerMeta = isJimeng ? '本机 Dreamina CLI' : (item.base_url || '未配置地址');
        const toggle = providerToggleMarkup(item);
        if(item.id === 'modelscope'){
            return `
                <div class="provider-card provider-card-banner ${active} ${disabled}" role="button" tabindex="0" onclick="selectProvider('${escapeHtml(item.id)}')" onkeydown="handleProviderCardKey(event, '${escapeHtml(item.id)}')">
                    <span class="provider-card-main">
                        <img src="/static/images/modelscope.gif" alt="ModelScope" class="ms-icon-light">
                        <img src="/static/images/modelscope-1.gif" alt="ModelScope" class="ms-icon-dark">
                    </span>
                    ${toggle}
                </div>
            `;
        }
        if(item.id === 'runninghub'){
            return `
                <div class="provider-card provider-card-banner ${active} ${disabled}" role="button" tabindex="0" onclick="selectProvider('${escapeHtml(item.id)}')" onkeydown="handleProviderCardKey(event, '${escapeHtml(item.id)}')">
                    <span class="provider-card-main">
                        <img src="/static/images/RunningHub-B.png" alt="RunningHub" class="runninghub-icon ms-icon-light">
                        <img src="/static/images/RunningHub-W.png" alt="RunningHub" class="runninghub-icon ms-icon-dark">
                    </span>
                    ${toggle}
                </div>
            `;
        }
        if(item.logo_url){
            return `
                <div class="provider-card provider-card-banner ${active} ${disabled}" role="button" tabindex="0" onclick="selectProvider('${escapeHtml(item.id)}')" onkeydown="handleProviderCardKey(event, '${escapeHtml(item.id)}')">
                    <span class="provider-card-main">
                        <img src="${escapeAttr(item.logo_url)}" alt="${escapeAttr(item.name || item.id)}" class="custom-provider-logo">
                    </span>
                    ${toggle}
                </div>
            `;
        }
        return `
            <div class="provider-card ${active} ${disabled}" role="button" tabindex="0" onclick="selectProvider('${escapeHtml(item.id)}')" onkeydown="handleProviderCardKey(event, '${escapeHtml(item.id)}')">
                <span class="provider-card-main">
                    <span class="provider-mark"><i data-lucide="${providerIcon}" class="w-4 h-4"></i></span>
                    <span class="min-w-0">
                        <div class="provider-name">${escapeHtml(item.name || item.id)}</div>
                        <div class="provider-meta">${escapeHtml(providerMeta)}</div>
                    </span>
                </span>
                ${toggle}
            </div>
        `;
    }).join('');
    refreshIcons();
}
function renderEditor(){
    const item = provider();
    if(!item) return;
    editorTitle.textContent = item.name || item.id;
    nameInput.value = item.name || '';
    idInput.value = item.id || '';
    updateIdPreview();
    clearVerifyResult();
    baseInput.value = item.base_url || '';
    if(protocolInput) protocolInput.value = item.id === 'runninghub' ? 'openai' : (item.protocol || 'openai');
    if(imageGenerationEndpointInput) imageGenerationEndpointInput.value = item.image_generation_endpoint || '';
    if(imageEditEndpointInput) imageEditEndpointInput.value = item.image_edit_endpoint || '';
    toggleAdvancedEndpoints(false);
    keyInput.value = '';
    keyInput.placeholder = item.has_key ? `${tr('api.keepCurrentKey')} ${item.key_preview || ''}` : tr('api.enterKey');
    keyHint.textContent = item.has_key ? `${tr('api.keySaved')}${item.key_env || 'API/.env'}` : tr('api.noKey');
    const isModelScope = item.id === 'modelscope';
    const isRunningHub = item.id === 'runninghub';
    const isJimeng = String(item.protocol || '').toLowerCase() === 'jimeng';
    document.body.classList.toggle('show-ms', isModelScope);
    document.body.classList.toggle('show-runninghub', isRunningHub);
    applyJimengMode(isJimeng);
    renderLogoPreview(item);
    if(msLoraBlock) msLoraBlock.style.display = isModelScope ? 'flex' : 'none';
    const deleteBtn = document.getElementById('deleteBtn');
    if(deleteBtn) deleteBtn.style.display = (item.id === 'modelscope' || item.id === 'runninghub') ? 'none' : 'inline-flex';
    renderModels('image');
    renderModels('chat');
    renderModels('video');
    if(isModelScope) renderMsLoras();
    else if(msLoraList) msLoraList.innerHTML = '';
    renderProviderList();
}
function showVerifyResult(html){ const el = document.getElementById('verifyResult'); if(el){ el.style.display = 'block'; el.innerHTML = html; } }
function clearVerifyResult(){ const el = document.getElementById('verifyResult'); if(el){ el.style.display = 'none'; el.innerHTML = ''; } }

async function probeAsync(){
    const item = provider();
    if(!item) return;
    const btn = document.getElementById('probeAsyncBtn');
    const baseUrl = baseInput.value.trim();
    if(!baseUrl){ StudioDialog.alert('请先填写请求地址'); return; }
    if(btn){ btn.disabled = true; btn.querySelector('span').textContent = '检测中...'; }
    showVerifyResult(`<span style="color:var(--muted);font-size:11px;font-weight:700">正在检测协议类型...</span>`);
    try {
        const apiKey = keyInput.value.trim();
        const data = await fetch('/api/providers/probe-async', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, provider_id: item.id })
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json()).detail || '请求失败');
            return r.json();
        });
        const isAsync = data.ok === true;
        // 自动设置协议下拉
        if(protocolInput && !['gemini', 'volcengine'].includes(protocolInput.value)){
            protocolInput.value = isAsync ? 'apimart' : 'openai';
            // 触发 change 以便其他地方同步
            protocolInput.dispatchEvent(new Event('change'));
        }
        const rawJson = JSON.stringify(data.raw, null, 2);
        const color = isAsync ? '#15803d' : data.ok === null ? '#b45309' : '#64748b';
        const icon = isAsync ? '✓' : '⚠';
        const proto = isAsync ? 'APIMart 异步' : 'OpenAI 兼容';
        showVerifyResult(`
            <div style="font-size:11px;font-weight:800;color:${color}">${icon} ${escapeHtml(data.message)}</div>
            <div style="font-size:11px;color:var(--muted);font-weight:700;margin-top:2px">协议已自动设置为：<strong style="color:var(--text)">${proto}</strong></div>
            <details style="margin-top:6px">
                <summary style="font-size:10.5px;color:var(--muted);cursor:pointer;font-weight:700;user-select:none">▸ 查看原始响应 (HTTP ${data.status_code})</summary>
                <pre style="margin-top:6px;padding:10px 12px;border-radius:10px;background:var(--soft);border:1px solid var(--line-2);font-size:10.5px;font-family:ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-all;color:var(--text);max-height:200px;overflow:auto">${escapeHtml(rawJson)}</pre>
            </details>`);
    } catch(e){
        const keepManualProtocol = ['gemini', 'volcengine'].includes(protocolInput?.value || '');
        if(protocolInput && !keepManualProtocol){ protocolInput.value = 'openai'; protocolInput.dispatchEvent(new Event('change')); }
        const suffix = keepManualProtocol ? '，已保留当前手动选择的协议' : '，协议已设为 OpenAI 兼容';
        showVerifyResult(`<div style="font-size:11px;font-weight:800;color:#b45309">⚠ ${escapeHtml(e.message || String(e))}${suffix}</div>`);
    } finally {
        if(btn){ btn.disabled = false; btn.querySelector('span').textContent = '验证协议'; refreshIcons(); }
    }
}

async function testConnection(){
    const item = provider();
    if(!item) return;
    const btn = document.getElementById('testUrlBtn');
    const baseUrl = baseInput.value.trim();
    if(!baseUrl){ StudioDialog.alert('请先填写请求地址'); return; }
    if(btn){ btn.disabled = true; btn.querySelector('span').textContent = tr('api.testingUrl') || '验证中...'; }
    showVerifyResult(`<span style="color:var(--muted);font-size:11px;font-weight:700">验证中...</span>`);
    try {
        const apiKey = keyInput.value.trim();
        const data = await fetch('/api/providers/test-connection', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, provider_id: item.id, protocol: protocolInput?.value || 'openai' })
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json()).detail || (tr('api.urlInvalid') || '验证失败'));
            return r.json();
        });
        if(data.ok){
            // 存入 picker 状态并启用「选择模型」按钮，但不自动弹出
            lastFetchedAll = data.all || [];
            lastFetchedSuggestion = {
                image: new Set(data.image_models || []),
                chat: new Set(data.chat_models || []),
                video: new Set(data.video_models || []),
            };
            const openBtn = document.getElementById('openPickerBtn');
            if(openBtn){ openBtn.disabled = false; openBtn.style.opacity = '1'; }
            showVerifyResult(`<span style="color:#15803d;font-size:11px;font-weight:800">✓ 地址验证通过 · 找到 ${data.model_count} 个模型</span>`);
        } else {
            showVerifyResult(`
                <div style="font-size:11px;font-weight:800;color:#b45309">⚠ 地址验证未通过 (HTTP ${data.status})</div>
                <div style="font-size:11px;color:var(--muted);font-weight:600;margin-top:3px">${escapeHtml((data.message || '').slice(0,200))}</div>`);
        }
    } catch(e){
        showVerifyResult(`<div style="font-size:11px;font-weight:800;color:#b45309">⚠ ${escapeHtml(e.message || String(e))}</div>`);
    } finally {
        if(btn){ btn.disabled = false; btn.querySelector('span').textContent = tr('api.testUrl') || '验证地址'; }
    }
}
let lastFetchedAll = [];          // 全部模型 id 列表
let lastFetchedSuggestion = null; // 后端自动分类建议

async function fetchModels(){
    const item = provider();
    if(!item) return;
    syncEditor();
    const btn = document.getElementById('fetchModelsBtn');
    const baseUrl = baseInput.value.trim();
    const apiKey = keyInput.value.trim();
    if(!baseUrl){ StudioDialog.alert('请先填写请求地址'); return; }
    if(btn){ btn.disabled = true; btn.querySelector('span').textContent = tr('api.fetchingModels') || '拉取中...'; }
    setStatus(tr('api.fetchingModels') || '正在从上游拉取模型列表...');
    try {
        const data = await fetch('/api/providers/fetch-models', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({base_url:baseUrl, api_key:apiKey, provider_id:item.id, protocol:protocolInput?.value || 'openai'})
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json()).detail || (tr('api.urlInvalid') || '拉取失败'));
            return r.json();
        });
        lastFetchedAll = data.all || [];
        lastFetchedSuggestion = {
            image: new Set(data.image_models || []),
            chat: new Set(data.chat_models || []),
            video: new Set(data.video_models || []),
        };
        // 启用「选择模型」按钮，并 statusbar 显示已拉取数量
        const openBtn = document.getElementById('openPickerBtn');
        if(openBtn){ openBtn.disabled = false; openBtn.style.opacity = '1'; }
        setStatus(`已拉取 ${data.total} 个模型 · 点「选择模型」勾选要导入的`);
        openModelPicker();
    } catch(e){
        StudioDialog.alert('拉取失败：' + (e.message || e));
        setStatus('拉取失败');
    } finally {
        if(btn){ btn.disabled = false; btn.querySelector('span').textContent = tr('api.fetchModels') || '拉取模型'; }
    }
}

// —— 模型选择器浮层 ——
// 每个模型只归一类（根据用户已配置 或 关键字猜测）；勾选 = 纳入该分类
let pickerState = { category: {}, selected: {} };
let pickerVisibleIds = [];
function openModelPicker(){
    const item = provider();
    if(!item || !lastFetchedAll.length){ StudioDialog.alert('没有拉取到模型'); return; }
    const existing = { image: new Set(item.image_models||[]), chat: new Set(item.chat_models||[]), video: new Set(item.video_models||[]) };
    const allIds = new Set([...lastFetchedAll, ...(item.image_models||[]), ...(item.chat_models||[]), ...(item.video_models||[])]);
    pickerState = { category: {}, selected: {} };
    allIds.forEach(id => {
        // 类别归属：用户已配置 > 关键字建议 > 默认 chat
        let cat;
        if(existing.image.has(id)) cat = 'image';
        else if(existing.video.has(id)) cat = 'video';
        else if(existing.chat.has(id)) cat = 'chat';
        else if(lastFetchedSuggestion?.image?.has(id)) cat = 'image';
        else if(lastFetchedSuggestion?.video?.has(id)) cat = 'video';
        else cat = 'chat';
        pickerState.category[id] = cat;
        // 默认勾选状态：已在用户配置里的 = 勾选；新拉的 = 不勾选（让用户主动选）
        pickerState.selected[id] = existing.image.has(id) || existing.chat.has(id) || existing.video.has(id);
    });
    // 默认 tab 切回「全部」
    document.querySelectorAll('.picker-cat-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.cat === 'all');
    });
    document.getElementById('modelPickerOverlay').style.display = 'flex';
    renderModelPicker();
}
function closeModelPicker(){ document.getElementById('modelPickerOverlay').style.display = 'none'; }
function renderModelPicker(){
    const filter = (document.getElementById('pickerFilter')?.value || '').toLowerCase();
    const currentTab = document.querySelector('.picker-cat-tab.active')?.dataset.cat || 'all';
    const ids = Object.keys(pickerState.category).sort();
    // 各分类总数 / 已选数
    const totals = { all: ids.length, image:0, chat:0, video:0 };
    const selecteds = { all:0, image:0, chat:0, video:0 };
    ids.forEach(id => {
        const cat = pickerState.category[id];
        totals[cat]++;
        if(pickerState.selected[id]){ selecteds[cat]++; selecteds.all++; }
    });
    // 过滤显示
    const list = ids.filter(id => {
        if(filter && !id.toLowerCase().includes(filter)) return false;
        if(currentTab === 'all') return true;
        return pickerState.category[id] === currentTab;
    });
    pickerVisibleIds = list;
    document.getElementById('pickerCount').textContent = `共 ${totals.all} 个模型 · 当前显示 ${list.length} 个`;
    document.querySelectorAll('.picker-cat-tab').forEach(tab => {
        const cat = tab.dataset.cat;
        tab.querySelector('.cat-count').textContent = `${selecteds[cat]}/${totals[cat]}`;
    });
    // 列表
    const html = list.map((id, index) => {
        const checked = pickerState.selected[id];
        return `
            <div class="picker-row ${checked?'has-sel':''}" onclick="togglePickerRowByIndex(${index})">
                <div class="picker-checkbox ${checked?'checked':''}">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                </div>
                <div class="picker-model-name" title="${escapeAttr(id)}">${escapeHtml(id)}</div>
            </div>
        `;
    }).join('');
    document.getElementById('pickerList').innerHTML = html || `<div style="padding:32px;text-align:center;color:var(--faint);font-size:12px">无匹配</div>`;
    // 底部汇总
    const sumImage = document.getElementById('sumImage');
    const sumChat = document.getElementById('sumChat');
    const sumVideo = document.getElementById('sumVideo');
    const sumUnsel = document.getElementById('sumUnsel');
    if(sumImage){ sumImage.textContent = `生图 ${selecteds.image}`; sumImage.classList.toggle('picker-sum-chip-empty', selecteds.image === 0); }
    if(sumChat){ sumChat.textContent = `LLM ${selecteds.chat}`; sumChat.classList.toggle('picker-sum-chip-empty', selecteds.chat === 0); }
    if(sumVideo){ sumVideo.textContent = `视频 ${selecteds.video}`; sumVideo.classList.toggle('picker-sum-chip-empty', selecteds.video === 0); }
    if(sumUnsel){ sumUnsel.textContent = `未选 ${totals.all - selecteds.all}`; }
}
function togglePickerRow(id){
    pickerState.selected[id] = !pickerState.selected[id];
    renderModelPicker();
}
function togglePickerRowByIndex(index){
    const id = pickerVisibleIds[index];
    if(typeof id !== 'string') return;
    togglePickerRow(id);
}
function selectPickerCat(cat){
    document.querySelectorAll('.picker-cat-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.cat === cat);
    });
    renderModelPicker();
}
function applyModelPicker(){
    const item = provider(); if(!item) return;
    const image = [], chat = [], video = [];
    Object.entries(pickerState.selected).forEach(([id, sel]) => {
        if(!sel) return;
        const cat = pickerState.category[id];
        if(cat === 'image') image.push(id);
        else if(cat === 'video') video.push(id);
        else chat.push(id);
    });
    item.image_models = image;
    item.chat_models = chat;
    item.video_models = video;
    renderModels('image'); renderModels('chat'); renderModels('video');
    renderMsLoras();
    setStatus(`已应用 · 生图 ${image.length} / LLM ${chat.length} / 视频 ${video.length}，点保存生效`);
    closeModelPicker();
}
async function saveKeyOnly(){
    const item = provider();
    if(!item) return;
    const key = keyInput.value.trim();
    if(!key){ StudioDialog.alert(tr('api.enterKeyAlert') || '请输入 Key'); return; }
    item.api_key = key;
    const ok = await saveProviders();
    if(ok) keyInput.value = '';
}
async function clearKeyOnly(){
    const item = provider();
    if(!item) return;
    if(!item.has_key && !keyInput.value){ return; }
    if(!await StudioDialog.confirm(tr('api.confirmClearKey') || '确认清除当前 Key？', {title:'清除 Key', danger:true, confirmText:'清除'})) return;
    item._clearKey = true;
    const ok = await saveProviders();
    if(ok) keyInput.value = '';
}
function renderModels(kind){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    const list = kind === 'image' ? imageModelList : kind === 'video' ? videoModelList : chatModelList;
    const models = item?.[key] || [];
    if(!models.length){
        list.innerHTML = `<div class="empty">${tr('api.noModels')}</div>`;
        return;
    }
    list.innerHTML = models.map((model, index) => `
        <div class="model-row">
            <input value="${escapeAttr(model)}" oninput="updateModel('${kind}', ${index}, this.value)">
            <button class="icon-btn" type="button" onclick="removeModel('${kind}', ${index})" title="删除"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        </div>
    `).join('');
    refreshIcons();
}
function msLoraTargetOptions(selected){
    const item = provider();
    const models = unique([selected, ...MS_BUILTIN_IMAGE_MODELS, ...((item?.image_models) || [])]);
    return models.filter(Boolean).map(model => `<option value="${escapeAttr(model)}" ${model === selected ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('');
}
function normalizeLoraStrength(value){
    const n = Number(value);
    if(!Number.isFinite(n)) return 0.8;
    return Math.max(0, Math.min(2, n));
}
function renderMsLoras(){
    const item = provider();
    if(!msLoraList || !item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    if(!item.ms_loras.length){
        msLoraList.innerHTML = `<div class="lora-empty">${tr('api.loraEmpty')}</div>`;
        return;
    }
    msLoraList.innerHTML = item.ms_loras.map((lora, index) => {
        const target = lora.target_model || lora.model || MS_BUILTIN_IMAGE_MODELS[0];
        const strength = normalizeLoraStrength(lora.strength ?? lora.default_strength ?? 0.8);
        return `
            <div class="lora-row">
                <label class="lora-field">
                    <span>${tr('api.loraId')}</span>
                    <input value="${escapeAttr(lora.id || '')}" placeholder="${escapeAttr(tr('api.loraIdPlaceholder'))}" oninput="updateMsLora(${index}, 'id', this.value)">
                </label>
                <label class="lora-field">
                    <span>${tr('api.loraTargetModel')}</span>
                    <select onchange="updateMsLora(${index}, 'target_model', this.value)">${msLoraTargetOptions(target)}</select>
                </label>
                <label class="lora-field">
                    <span>${tr('api.loraDefaultStrength')}</span>
                    <input type="number" min="0" max="2" step="0.05" value="${strength}" oninput="updateMsLora(${index}, 'strength', this.value)">
                </label>
                <button class="icon-btn" type="button" onclick="removeMsLora(${index})" title="${escapeAttr(tr('common.delete'))}"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
            </div>
        `;
    }).join('');
    refreshIcons();
}
function addMsLora(){
    const item = provider();
    if(!item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    item.ms_loras.push({
        id:'',
        name:'',
        target_model: (item.image_models || [])[0] || MS_BUILTIN_IMAGE_MODELS[0],
        strength:0.8,
        enabled:true,
        note:''
    });
    renderMsLoras();
}
function updateMsLora(index, field, value){
    const item = provider();
    if(!item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    const lora = item.ms_loras[index];
    if(!lora) return;
    if(field === 'strength') lora.strength = normalizeLoraStrength(value);
    else lora[field] = value;
}
function removeMsLora(index){
    const item = provider();
    if(!item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    item.ms_loras.splice(index, 1);
    renderMsLoras();
}
function selectProvider(id){
    syncEditor();
    selectedId = id;
    renderEditor();
}
function handleProviderCardKey(event, id){
    if(event.target?.closest?.('.provider-card-side')) return;
    if(event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    selectProvider(id);
}
async function toggleProviderEnabled(id, event){
    if(event){
        event.preventDefault();
        event.stopPropagation();
    }
    if(providerToggleSavingId) return;
    syncEditor();
    const item = providers.find(provider => provider.id === id);
    if(!item) return;
    const oldEnabled = item.enabled !== false;
    const nextEnabled = !oldEnabled;
    if(!nextEnabled && providers.filter(provider => provider.enabled !== false).length <= 1){
        setStatus('至少保留一个启用的服务商');
        return;
    }
    item.enabled = nextEnabled;
    providerToggleSavingId = id;
    renderProviderList();
    setStatus(nextEnabled ? '正在启用服务商...' : '正在关闭服务商...');
    const saved = await saveProviders();
    providerToggleSavingId = '';
    if(!saved){
        const rollbackItem = providers.find(provider => provider.id === id);
        if(rollbackItem) rollbackItem.enabled = oldEnabled;
        setStatus(lastSaveError ? `服务商状态保存失败：${lastSaveError}，已恢复原状态` : '服务商状态保存失败，已恢复原状态');
        renderProviderList();
        return;
    }
    renderProviderList();
    setStatus(nextEnabled ? '服务商已启用' : '服务商已关闭');
}
function addProvider(){
    syncEditor();
    let id = 'custom-api';
    let index = 2;
    while(providers.some(item => item.id === id)) id = `custom-api-${index++}`;
    providers.push({id, name:'API', base_url:'https://', protocol:'openai', image_generation_endpoint:'', image_edit_endpoint:'', logo_url:'', enabled:true, primary:false, image_models:[], chat_models:[], video_models:[], has_key:false, key_preview:''});
    selectedId = id;
    renderEditor();
}
async function addJimengProvider(){
    syncEditor();
    let item = providers.find(candidate => String(candidate.protocol || '').toLowerCase() === 'jimeng');
    if(!item){
        item = {
            id:'jimeng', name:'即梦 CLI', base_url:'', protocol:'jimeng',
            image_generation_endpoint:'', image_edit_endpoint:'', logo_url:'',
            enabled:true, primary:false,
            image_models:['5.0Pro','5.0','4.7','4.6','4.5','4.1','4.0','3.1','3.0'],
            chat_models:[],
            video_models:['seedance2.0fast_vip','seedance2.0_vip','seedance2.0','seedance2.0fast','seedance2.0mini'],
            has_key:false, key_preview:''
        };
        providers.push(item);
    }
    selectedId = item.id;
    renderEditor();
    await saveProviders();
}
function deleteProvider(){
    const item = provider();
    if(!item) return;
    if(isBuiltinProvider(item)){ StudioDialog.alert(tr('api.modelscopeOnlyDelete') || '内置服务商不可删除'); return; }
    if(providers.length <= 1){ StudioDialog.alert(tr('api.keepOne')); return; }
    providers = providers.filter(p => p.id !== item.id);
    selectedId = providers[0]?.id || '';
    renderEditor();
    saveProviders();
}
function addModel(kind){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    item[key] = [...(item[key] || []), ''];
    renderModels(kind);
    if(kind === 'image') renderMsLoras();
}
function updateModel(kind, index, value){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    item[key][index] = value;
    if(kind === 'image') renderMsLoras();
}
function removeModel(kind, index){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    item[key].splice(index, 1);
    renderModels(kind);
    if(kind === 'image') renderMsLoras();
}
async function loadProviders(){
    setStatus(tr('api.loading'));
    try {
        const data = await fetch('/api/providers').then(r => r.json());
        providers = data.providers || [];
        selectedId = sortedProviders()[0]?.id || '';
        renderEditor();
        setStatus('');
    } catch(err) {
        setStatus(tr('api.loadFailed'));
    }
}
async function saveProviders(){
    syncEditor();
    lastSaveError = '';
    try {
        providers.forEach(item => {
            item.id = normalizeId(item.id);
            item.protocol = item.id === 'runninghub'
                ? 'runninghub'
                : ['openai', 'apimart', 'gemini', 'grok', 'volcengine', 'jimeng', 'codex', 'gemini-cli'].includes(String(item.protocol || '').toLowerCase()) ? String(item.protocol).toLowerCase() : 'openai';
            item.image_generation_endpoint = normalizeEndpointSetting(item.image_generation_endpoint);
            item.image_edit_endpoint = normalizeEndpointSetting(item.image_edit_endpoint);
            item.logo_url = String(item.logo_url || '').trim();
            validateEndpointSetting(item.image_generation_endpoint, '文生图端口');
            validateEndpointSetting(item.image_edit_endpoint, '图生图/编辑端口');
            item.image_models = unique(item.image_models || []);
            item.chat_models = unique(item.chat_models || []);
            item.video_models = unique(item.video_models || []);
            item.ms_loras = (Array.isArray(item.ms_loras) ? item.ms_loras : []).map(lora => ({
                id:String(lora.id || '').trim(),
                name:String(lora.name || lora.id || '').trim(),
                target_model:String(lora.target_model || '').trim(),
                strength:normalizeLoraStrength(lora.strength ?? 0.8),
                enabled:lora.enabled !== false,
                note:String(lora.note || '').trim()
            })).filter(lora => lora.id && lora.target_model);
        });
    } catch(err) {
        lastSaveError = err.message || '高级端口设置不合法';
        setStatus(lastSaveError);
        toggleAdvancedEndpoints(true);
        return false;
    }
    if(new Set(providers.map(item => item.id)).size !== providers.length){
        StudioDialog.alert(tr('api.duplicateId'));
        lastSaveError = tr('api.duplicateId');
        return false;
    }
    setStatus(tr('api.saving'));
    try {
        const res = await fetch('/api/providers', {
            method:'PUT',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(providers.map(item => ({
                id:item.id,
                name:item.name,
                base_url:item.base_url,
                protocol:(item.id === 'modelscope') ? 'openai' : item.id === 'runninghub' ? 'runninghub' : (item.protocol || 'openai'),
                image_generation_endpoint:item.image_generation_endpoint || '',
                image_edit_endpoint:item.image_edit_endpoint || '',
                logo_url:item.logo_url || '',
                enabled:item.enabled !== false,
                primary:false,
                image_models:item.image_models || [],
                chat_models:item.chat_models || [],
                video_models:item.video_models || [],
                ms_loras:item.id === 'modelscope' ? (item.ms_loras || []) : [],
                ms_defaults_version:item.id === 'modelscope' ? (item.ms_defaults_version || 1) : 0,
                api_key:item.api_key || undefined,
                clear_key:item._clearKey === true
            })))
        });
        if(!res.ok) throw new Error((await res.json()).detail || tr('api.saveFailed'));
        const data = await res.json();
        providers = data.providers || providers;
        selectedId = provider()?.id || providers[0]?.id || '';
        renderEditor();
        setStatus(`${tr('api.saved')}，已同步到 AI 生图 / 无限画布 / 智能画布`);
        // 广播变更，画布等其他 iframe 立即重新拉取最新平台/模型列表
        try { new BroadcastChannel('studio-api').postMessage({ type:'providers-changed' }); } catch(e) {}
        return true;
    } catch(err) {
        lastSaveError = err.message || tr('api.saveFailed');
        setStatus(lastSaveError);
        return false;
    }
}
function escapeHtml(str){
    return String(str || '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
}
function escapeAttr(str){ return escapeHtml(str).replace(/`/g, '&#96;'); }
window.addEventListener('message', event => {
    if(event.data?.type === 'studio-theme' && window.StudioTheme) window.StudioTheme.set(event.data.theme);
    if(event.data?.type === 'studio-lang' && window.StudioI18n) {
        window.StudioI18n.set(event.data.lang);
        renderEditor();
    }
});
window.addEventListener('studio-lang-change', () => {
    renderEditor();
});
window.onload = () => {
    if(window.StudioTheme) window.StudioTheme.apply();
    if(window.StudioI18n) window.StudioI18n.apply();
    loadProviders();
    // 平台名输入时实时预览生成的 ID
    if(nameInput) nameInput.addEventListener('input', updateIdPreview);
    if(logoInput) logoInput.addEventListener('change', event => {
        const file = event.target.files?.[0];
        if(file) handleLogoUpload(file);
        event.target.value = '';
    });
    if(protocolInput) protocolInput.addEventListener('change', updateProtocolFromInput);
};
