(function(){
    const panel = document.getElementById('canvasAssistantPanel');
    const toggle = document.getElementById('canvasAssistantToggle');
    const host = window.SynCanvasAssistantHost;
    if(!panel || !toggle || !host) return;

    const el = id => document.getElementById(id);
    const state = {
        open:false, sources:[], conversations:[], activeId:'', conversation:null,
        providers:[], providerId:'', model:'', refs:[], generating:false,
        abortController:null, sent:new Set(), draft:true, streamText:'', error:''
    };
    const text = (zh, en) => window.StudioI18n?.lang?.() === 'en' ? en : zh;
    const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, item => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[item]));
    const canvasId = () => String(host.getCanvasId?.() || '').trim();
    const conversationBase = () => `/api/canvases/${encodeURIComponent(canvasId())}/assistant/conversations`;

    function notify(message, error=false){
        state.error = error ? String(message || '') : '';
        const status = el('canvasAssistantStatus');
        if(status){
            status.textContent = String(message || '');
            status.classList.toggle('error', error);
        }
        if(message && !error) host.notify?.(message);
    }

    function applyLabels(){
        const heading = panel.querySelector('.sync-feature-head strong');
        const subtitle = panel.querySelector('.sync-feature-head span');
        const toggleLabel = toggle.querySelector('span');
        if(heading) heading.textContent = text('画布精灵','Canvas Assistant');
        if(subtitle) subtitle.textContent = text('多轮 API 对话 · 将单条回复发送为提示词节点','Multi-turn API chat · Send one reply to a prompt node');
        if(toggleLabel) toggleLabel.textContent = text('画布精灵','Canvas Assistant');
        toggle.title = text('画布精灵','Canvas Assistant');
        toggle.setAttribute('aria-label',toggle.title);
        const labels = {
            canvasAssistantClose:['关闭','Close'],
            canvasAssistantNew:['新建会话','New conversation'],
            canvasAssistantDelete:['删除会话','Delete conversation'],
            canvasAssistantUpload:['上传图片','Upload images'],
            canvasAssistantUseSelection:['引用画布选中图片','Use selected canvas images']
        };
        Object.entries(labels).forEach(([id,pair])=>{
            const target=el(id); if(!target) return;
            target.title=text(pair[0],pair[1]);
            target.setAttribute('aria-label',target.title);
        });
        const aria = {
            canvasAssistantConversation:['画布精灵会话','Canvas Assistant conversation'],
            canvasAssistantSource:['提示词来源','Prompt source'],
            canvasAssistantProvider:['API 平台','API provider'],
            canvasAssistantModel:['模型','Model']
        };
        Object.entries(aria).forEach(([id,pair])=>el(id)?.setAttribute('aria-label',text(pair[0],pair[1])));
        const input=el('canvasAssistantInput');
        if(input) input.placeholder=text('输入消息，Enter 发送…','Type a message, Enter to send…');
    }

    async function fetchJson(url, options={}){
        const response = await fetch(url, options);
        if(!response.ok){
            let detail = `${response.status}`;
            try { detail = (await response.json()).detail || detail; } catch(_) {}
            throw new Error(detail);
        }
        return response.json();
    }

    function currentSourceValue(){
        return el('canvasAssistantSource')?.value || 'general:';
    }

    function selectedSource(){
        const [kind, ...parts] = currentSourceValue().split(':');
        return {kind:kind || 'general', id:parts.join(':') || ''};
    }

    function sourceOptions(){
        const groups = [
            ['general', text('通用','General')],
            ['template', text('模板','Templates')],
            ['agent', text('智能体','Agents')],
        ];
        return groups.map(([kind, label]) => {
            const items = state.sources.filter(item => item.kind === kind);
            if(!items.length) return '';
            return `<optgroup label="${esc(label)}">${items.map(item => `<option value="${esc(`${item.kind}:${item.id || ''}`)}">${esc(item.name || item.id || label)}</option>`).join('')}</optgroup>`;
        }).join('');
    }

    function normalizeProviders(config){
        const configured = config.api_providers || [];
        const list = configured.filter(item => item.enabled !== false && (item.chat_models || []).length);
        if(config.ms_chat_models?.length && !configured.some(item => item.id === 'modelscope')){
            list.push({id:'modelscope', name:'ModelScope', chat_models:config.ms_chat_models});
        }
        return list;
    }

    function renderProviderControls(){
        const provider = el('canvasAssistantProvider');
        const model = el('canvasAssistantModel');
        if(!provider || !model) return;
        if(!state.providers.some(item => item.id === state.providerId)) state.providerId = state.providers[0]?.id || '';
        provider.innerHTML = state.providers.length
            ? state.providers.map(item => `<option value="${esc(item.id)}" ${item.id === state.providerId ? 'selected' : ''}>${esc(item.name || item.id)}</option>`).join('')
            : `<option value="">${esc(text('没有可用 API 平台','No API provider'))}</option>`;
        const active = state.providers.find(item => item.id === state.providerId);
        const models = [...new Set((active?.chat_models || []).map(String).filter(Boolean))];
        if(!models.includes(state.model)) state.model = models[0] || '';
        model.innerHTML = models.length
            ? models.map(item => `<option value="${esc(item)}" ${item === state.model ? 'selected' : ''}>${esc(item)}</option>`).join('')
            : `<option value="">${esc(text('没有聊天模型','No chat model'))}</option>`;
        provider.disabled = state.generating;
        model.disabled = state.generating;
    }

    function renderConversationControls(){
        const select = el('canvasAssistantConversation');
        const source = el('canvasAssistantSource');
        if(select){
            select.innerHTML = state.draft
                ? `<option value="">${esc(text('新对话','New conversation'))}</option>${state.conversations.map(item => `<option value="${esc(item.id)}">${esc(item.title || text('新对话','New conversation'))}</option>`).join('')}`
                : state.conversations.map(item => `<option value="${esc(item.id)}" ${item.id === state.activeId ? 'selected' : ''}>${esc(item.title || text('新对话','New conversation'))}</option>`).join('');
            select.disabled = state.generating;
        }
        if(source){
            const previous = state.conversation?.source ? `${state.conversation.source.kind}:${state.conversation.source.id || ''}` : currentSourceValue();
            source.innerHTML = sourceOptions();
            if(![...source.options].some(option => option.value === previous) && state.conversation?.source){
                const snapshot = document.createElement('option');
                snapshot.value = previous;
                snapshot.textContent = `${state.conversation.source.name || text('历史来源','Previous source')} · ${text('快照','snapshot')}`;
                source.prepend(snapshot);
            }
            if([...source.options].some(option => option.value === previous)) source.value = previous;
            source.disabled = Boolean(state.conversation) || state.generating;
        }
        const del = el('canvasAssistantDelete');
        if(del) del.disabled = !state.conversation || state.generating;
        const create = el('canvasAssistantNew');
        if(create) create.disabled = state.generating;
        renderProviderControls();
    }

    function safeMarkdown(value){
        const source = String(value || '');
        if(!window.marked || !window.DOMPurify) return esc(source).replace(/\n/g,'<br>');
        const parsed = marked.parse(source, {gfm:true, breaks:true});
        return DOMPurify.sanitize(parsed, {
            USE_PROFILES:{html:true},
            FORBID_TAGS:['style','iframe','object','embed','form','input','button','textarea','select','option'],
            FORBID_ATTR:['style','onerror','onclick','onload']
        });
    }

    function messageMarkup(message){
        const assistant = message.role === 'assistant';
        const origin = state.conversation ? {
            conversationId:state.conversation.id,
            messageId:message.id,
            sourceKind:state.conversation.source?.kind || 'general',
            sourceId:state.conversation.source?.id || '',
            sourceName:state.conversation.source?.name || text('画布精灵','Canvas Assistant')
        } : {};
        const originJson = esc(JSON.stringify(origin));
        return `<div class="canvas-assistant-message ${assistant ? 'assistant' : 'user'}" data-message-id="${esc(message.id || '')}">
            <div class="canvas-assistant-bubble ${assistant ? 'canvas-assistant-markdown' : ''}">${assistant ? safeMarkdown(message.content) : esc(message.content || '')}</div>
            ${assistant ? `<div class="canvas-assistant-message-actions"><button type="button" class="canvas-assistant-send-canvas ${state.sent.has(message.id) ? 'sent' : ''}" data-assistant-send="${esc(message.id || '')}" data-assistant-origin="${originJson}"><i data-lucide="text-cursor-input"></i><span>${esc(state.sent.has(message.id) ? text('已发送','Sent') : text('发送到画布','Send to canvas'))}</span></button></div>` : ''}
        </div>`;
    }

    function renderMessages(){
        const box = el('canvasAssistantMessages');
        if(!box) return;
        const messages = state.conversation?.messages || [];
        if(!messages.length && !state.generating){
            box.innerHTML = `<div class="canvas-assistant-empty"><i data-lucide="sparkles"></i>${esc(text('选择模板或智能体后点击“开始对话”，也可以直接输入问题。','Choose a template or Agent and start, or type a message directly.'))}</div>`;
        } else {
            box.innerHTML = messages.map(messageMarkup).join('') + (state.generating ? `<div class="canvas-assistant-message assistant" data-streaming><div class="canvas-assistant-bubble canvas-assistant-markdown">${safeMarkdown(state.streamText || text('正在思考…','Thinking…'))}</div></div>` : '');
        }
        box.querySelectorAll('[data-assistant-send]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                const message = (state.conversation?.messages || []).find(item => item.id === button.dataset.assistantSend);
                if(!message?.content) return;
                let origin = {};
                try { origin = JSON.parse(button.dataset.assistantOrigin || '{}'); } catch(_) {}
                const node = host.insertPrompt?.(message.content, origin);
                if(node){
                    state.sent.add(message.id);
                    notify(text('已创建提示词节点','Prompt node created'));
                    renderMessages();
                }
            });
        });
        window.lucide?.createIcons();
        requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
    }

    function renderRefs(){
        const strip = el('canvasAssistantAttachments');
        if(!strip) return;
        strip.classList.toggle('has-items', Boolean(state.refs.length));
        strip.innerHTML = state.refs.map((item,index) => `<div class="canvas-assistant-ref" title="${esc(item.name || item.url)}"><img src="${esc(item.url)}" alt=""><button type="button" data-remove-ref="${index}" aria-label="${esc(text('移除图片','Remove image'))}">×</button></div>`).join('');
        strip.querySelectorAll('[data-remove-ref]').forEach(button => button.onclick = () => {
            state.refs.splice(Number(button.dataset.removeRef),1);
            renderRefs();
        });
    }

    function renderComposer(){
        const primary = el('canvasAssistantSend');
        const input = el('canvasAssistantInput');
        if(primary){
            const canBootstrap = !state.conversation?.started && !(input?.value || '').trim() && !state.refs.length;
            primary.classList.toggle('stop', state.generating);
            primary.innerHTML = state.generating
                ? `<i data-lucide="square"></i><span>${esc(text('停止','Stop'))}</span>`
                : canBootstrap
                    ? `<i data-lucide="play"></i><span>${esc(text('开始对话','Start'))}</span>`
                    : `<i data-lucide="send"></i><span>${esc(text('发送','Send'))}</span>`;
            primary.disabled = !state.generating && (!state.providers.length || !state.model);
        }
        window.lucide?.createIcons();
    }

    function render(){
        applyLabels();
        renderConversationControls();
        renderMessages();
        renderRefs();
        renderComposer();
    }

    async function loadSources(){
        const data = await fetchJson('/api/canvas-assistant/sources', {cache:'no-store'});
        state.sources = data.sources || [];
    }

    async function loadProviders(){
        const config = await fetchJson('/api/config', {cache:'no-store'});
        state.providers = normalizeProviders(config);
        if(!state.providerId) state.providerId = state.providers[0]?.id || '';
        renderProviderControls();
    }

    async function loadConversation(id){
        if(!id){
            state.activeId = '';
            state.conversation = null;
            state.draft = true;
            render();
            return;
        }
        const data = await fetchJson(`${conversationBase()}/${encodeURIComponent(id)}`, {cache:'no-store'});
        state.activeId = id;
        state.conversation = data.conversation;
        state.providerId = state.conversation.provider_id || state.providerId;
        state.model = state.conversation.model || state.model;
        state.draft = false;
        render();
    }

    async function loadConversations(){
        if(!canvasId()) throw new Error(text('请先打开画布','Open a canvas first'));
        const data = await fetchJson(conversationBase(), {cache:'no-store'});
        state.conversations = data.conversations || [];
        const active = data.active_conversation_id || '';
        if(active) await loadConversation(active);
        else {
            state.activeId = '';
            state.conversation = null;
            state.draft = true;
            render();
        }
    }

    async function createConversation(){
        const data = await fetchJson(conversationBase(), {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({source:selectedSource(), provider_id:state.providerId, model:state.model})
        });
        state.conversation = data.conversation;
        state.activeId = data.active_conversation_id || data.conversation.id;
        state.draft = false;
        await refreshConversationList(false);
        return state.conversation;
    }

    async function refreshConversationList(loadActive=true){
        const data = await fetchJson(conversationBase(), {cache:'no-store'});
        state.conversations = data.conversations || [];
        if(loadActive && data.active_conversation_id && data.active_conversation_id !== state.activeId) await loadConversation(data.active_conversation_id);
        else renderConversationControls();
    }

    async function activateConversation(id){
        if(!id){ await loadConversation(''); return; }
        await fetchJson(`${conversationBase()}/${encodeURIComponent(id)}/activate`, {method:'POST'});
        await loadConversation(id);
    }

    async function deleteConversation(){
        if(!state.conversation) return;
        const ok = window.StudioDialog?.confirm
            ? await StudioDialog.confirm(text('删除当前画布精灵会话？','Delete this Canvas Assistant conversation?'), {title:text('删除会话','Delete conversation'), danger:true, confirmText:text('删除','Delete')})
            : window.confirm(text('删除当前画布精灵会话？','Delete this Canvas Assistant conversation?'));
        if(!ok) return;
        const data = await fetchJson(`${conversationBase()}/${encodeURIComponent(state.conversation.id)}`, {method:'DELETE'});
        state.activeId = '';
        state.conversation = null;
        await refreshConversationList(false);
        if(data.active_conversation_id) await loadConversation(data.active_conversation_id);
        else { state.draft = true; render(); }
    }

    async function patchConversation(){
        if(!state.conversation) return;
        const data = await fetchJson(`${conversationBase()}/${encodeURIComponent(state.conversation.id)}`, {
            method:'PATCH', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({provider_id:state.providerId, model:state.model})
        });
        state.conversation = data.conversation;
        await refreshConversationList(false);
    }

    function parseSseChunk(buffer, onEvent){
        const parts = buffer.split('\n\n');
        const rest = parts.pop() || '';
        for(const block of parts){
            const line = block.split('\n').find(item => item.startsWith('data:'));
            if(!line) continue;
            let event;
            try { event = JSON.parse(line.slice(5).trim()); } catch(_) { continue; }
            onEvent(event);
        }
        return rest;
    }

    async function sendMessage(){
        if(state.generating){ state.abortController?.abort(); return; }
        const input = el('canvasAssistantInput');
        const message = (input?.value || '').trim();
        const bootstrap = !message && !state.refs.length && !state.conversation?.started;
        if(!bootstrap && !message && !state.refs.length) return;
        if(!state.conversation) await createConversation();
        state.generating = true;
        state.streamText = '';
        state.abortController = new AbortController();
        state.error = '';
        render();
        try {
            const response = await fetch(`${conversationBase()}/${encodeURIComponent(state.conversation.id)}/messages/stream`, {
                method:'POST', headers:{'Content-Type':'application/json'}, signal:state.abortController.signal,
                body:JSON.stringify({message, reference_images:state.refs, bootstrap})
            });
            if(!response.ok){
                let detail = `${response.status}`;
                try { detail = (await response.json()).detail || detail; } catch(_) {}
                throw new Error(detail);
            }
            if(input){ input.value=''; input.style.height=''; }
            state.refs=[];
            renderRefs();
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer='';
            while(true){
                const {value,done}=await reader.read();
                if(done) break;
                buffer += decoder.decode(value,{stream:true});
                buffer = parseSseChunk(buffer, event => {
                    if(event.type === 'meta'){
                        state.conversation = event.conversation;
                        state.activeId = event.conversation?.id || state.activeId;
                        renderMessages();
                    } else if(event.type === 'delta'){
                        state.streamText += event.delta || '';
                        const bubble = panel.querySelector('[data-streaming] .canvas-assistant-bubble');
                        if(bubble) bubble.innerHTML = safeMarkdown(state.streamText);
                    } else if(event.type === 'done'){
                        state.conversation = event.conversation;
                        state.streamText = '';
                    } else if(event.type === 'error'){
                        throw new Error(event.detail || text('生成失败','Generation failed'));
                    }
                });
            }
            await refreshConversationList(false);
            notify(text('回复完成','Reply complete'));
        } catch(error){
            if(error.name === 'AbortError') notify(text('已停止生成','Generation stopped'));
            else notify(error.message || text('生成失败','Generation failed'), true);
        } finally {
            state.generating=false;
            state.abortController=null;
            state.streamText='';
            if(state.conversation?.id){
                try { await loadConversation(state.conversation.id); } catch(_) { render(); }
            } else render();
        }
    }

    function appendRefs(items){
        const seen = new Set(state.refs.map(item => item.url));
        for(const item of items || []){
            const url = String(item?.url || '').trim();
            if(!url || seen.has(url) || state.refs.length >= 8) continue;
            seen.add(url);
            state.refs.push({url, name:item.name || 'image', mime:item.mime || ''});
        }
        renderRefs();
        notify(text(`已添加 ${state.refs.length} 张参考图`,`Added ${state.refs.length} reference images`));
    }

    async function uploadFiles(files){
        const images = [...(files || [])].filter(file => file.type.startsWith('image/')).slice(0,8-state.refs.length);
        if(!images.length) return;
        const form = new FormData();
        images.forEach(file => form.append('files',file));
        const data = await fetchJson('/api/ai/upload',{method:'POST',body:form});
        appendRefs(data.files || []);
    }

    function closeOtherPanels(){
        document.querySelectorAll('.sync-feature-panel.open').forEach(item => {
            if(item !== panel){ item.classList.remove('open'); item.setAttribute('aria-hidden','true'); }
        });
        if(typeof window.toggleAssetLibrary === 'function') window.toggleAssetLibrary(false);
        document.getElementById('canvasAppearancePanel')?.classList.remove('open');
    }

    async function setOpen(open){
        state.open=Boolean(open);
        if(state.open) closeOtherPanels();
        panel.classList.toggle('open',state.open);
        panel.setAttribute('aria-hidden',state.open?'false':'true');
        toggle.setAttribute('aria-expanded',state.open?'true':'false');
        if(!state.open) return;
        notify(text('正在加载画布精灵…','Loading Canvas Assistant…'));
        try {
            await Promise.all([loadSources(),loadProviders()]);
            await loadConversations();
            notify('');
        } catch(error){ notify(error.message || text('加载失败','Load failed'),true); }
        render();
    }

    const panelStateObserver = new MutationObserver(()=>{
        const visible = panel.classList.contains('open');
        if(state.open === visible && toggle.getAttribute('aria-expanded') === String(visible)) return;
        state.open = visible;
        toggle.setAttribute('aria-expanded',visible?'true':'false');
    });
    panelStateObserver.observe(panel,{attributes:true,attributeFilter:['class']});

    toggle.addEventListener('click',event=>{ event.stopPropagation(); setOpen(!state.open); });
    el('canvasAssistantClose')?.addEventListener('click',()=>setOpen(false));
    el('canvasAssistantNew')?.addEventListener('click',()=>{
        state.activeId=''; state.conversation=null; state.draft=true; state.refs=[]; state.error=''; render();
    });
    el('canvasAssistantDelete')?.addEventListener('click',()=>deleteConversation().catch(error=>notify(error.message,true)));
    el('canvasAssistantConversation')?.addEventListener('change',event=>activateConversation(event.target.value).catch(error=>notify(error.message,true)));
    el('canvasAssistantProvider')?.addEventListener('change',event=>{
        state.providerId=event.target.value; state.model=''; renderProviderControls(); renderComposer();
        patchConversation().catch(error=>notify(error.message,true));
    });
    el('canvasAssistantModel')?.addEventListener('change',event=>{
        state.model=event.target.value; patchConversation().catch(error=>notify(error.message,true));
    });
    el('canvasAssistantSend')?.addEventListener('click',()=>sendMessage());
    el('canvasAssistantUpload')?.addEventListener('click',()=>el('canvasAssistantFile')?.click());
    el('canvasAssistantFile')?.addEventListener('change',event=>{
        uploadFiles(event.target.files).catch(error=>notify(error.message,true)); event.target.value='';
    });
    el('canvasAssistantUseSelection')?.addEventListener('click',()=>appendRefs(host.getSelectedImages?.() || []));
    const input=el('canvasAssistantInput');
    input?.addEventListener('input',()=>{
        input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,150)+'px'; renderComposer();
    });
    input?.addEventListener('keydown',event=>{
        if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){ event.preventDefault(); sendMessage(); }
    });
    const composer=el('canvasAssistantComposer');
    ['pointerdown','mousedown','click','dblclick','wheel'].forEach(type=>panel.addEventListener(type,event=>event.stopPropagation()));
    composer?.addEventListener('dragover',event=>{ event.preventDefault(); composer.classList.add('drag-over'); });
    composer?.addEventListener('dragleave',()=>composer.classList.remove('drag-over'));
    composer?.addEventListener('drop',event=>{
        event.preventDefault(); composer.classList.remove('drag-over');
        uploadFiles(event.dataTransfer?.files).catch(error=>notify(error.message,true));
    });
    window.addEventListener('studio-lang-change',()=>render());
    window.addEventListener('message',event=>{ if(event.data?.type==='studio-lang') render(); });
    applyLabels();
    window.lucide?.createIcons();
})();
