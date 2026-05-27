// Voice selector, voice library, and digital-human uploads
// Split from state.js. Loaded as a classic script; shared symbols remain global.

function renderVoices() {
            const currentValue = $('voice-select').value;
            const options = state.voices.filter(v => (v.value || v.name || '') !== '使用参考音频').map(v => {
                const value = v.value || v.name || '';
                return `<option value="${escapeHtml(value)}">${escapeHtml(v.display_name || v.name || value)}</option>`;
            }).join('');
            $('voice-select').innerHTML = `<option value="">使用参考音频</option>${options}`;
            if (currentValue && state.voices.some(v => (v.value || v.name || '') === currentValue)) {
                $('voice-select').value = currentValue;
            }
            updateVoiceActions();
        }

        function selectedVoiceItem() {
            const selected = $('voice-select')?.value || '';
            if (!selected && state.uploadedVoice) return state.uploadedVoice;
            return state.voices.find(v => (v.value || v.name || '') === selected) || null;
        }

        function updateVoiceActions() {
            const item = selectedVoiceItem();
            const canPreview = !!mediaUrl(item);
            $('voice-preview-btn').disabled = !canPreview;
        }

        function renderVoicesLibrary() { /* 渲染声音库的主函数 */ /* 逻辑注释 */
            const el = $('voices-library'); /* 获取声音库DOM容器节点 */ /* DOM获取注释 */
            const voices = state.voices.filter(v => (v.value || v.name || '') !== '使用参考音频'); /* 过滤出可用声音列表数据 */ /* 数据过滤注释 */
            $('voices-summary').textContent = `共 ${voices.length} 个声音`; /* 界面文本更新声音总数 */ /* 赋值注释 */
            if (!voices.length) { /* 检测可用声音项数目 */ /* 判断注释 */
                el.innerHTML = '<div class="empty-state">还没有可管理音色，请上传音频保存为声音</div>'; /* 展现声音空状态模版 */ /* 模板输出注释 */
                return; /* 中断函数返回 */ /* 中止注释 */
            } /* 逻辑块结束 */ /* 块结束注释 */
            el.innerHTML = voices.map(voice => { /* 遍历生成每一个声音卡片 HTML */ /* 循环映射注释 */
                const value = voice.value || voice.name || ''; /* 提取该声音的唯一标识名 */ /* 获取标识注释 */
                const active = $('voice-select')?.value === value; /* 判断当前是否选中了此声音 */ /* 状态校验注释 */
                return `
                    <div class="voice-card ${active ? 'active' : ''}" data-voice-name="${escapeHtml(value)}"> <!-- 声音卡片容器 --> <!-- HTML注释 -->
                        <div class="voice-card-head"> <!-- 声音卡片头信息区 --> <!-- HTML注释 -->
                            <div class="voice-icon"> <!-- 科技渐变声波图标框 --> <!-- HTML注释 -->
                                <svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="22"></line><line x1="17" y1="5" x2="17" y2="19"></line><line x1="22" y1="10" x2="22" y2="14"></line><line x1="7" y1="5" x2="7" y2="19"></line><line x1="2" y1="10" x2="2" y2="14"></line></svg> <!-- 动态声波图形 --> <!-- HTML注释 -->
                            </div> <!-- 声波图标框结束 --> <!-- HTML注释 -->
                            <div class="library-meta"> <!-- 声音属性元信息 --> <!-- HTML注释 -->
                                <div class="asset-name">${escapeHtml(voice.display_name || voice.name || value)}</div> <!-- 声音显示名称 --> <!-- HTML注释 -->
                                <div class="asset-note">${escapeHtml(value)}</div> <!-- 音色英文名称/系统名称 --> <!-- HTML注释 -->
                                <div class="asset-note">${escapeHtml(voice.note || '暂无备注')}</div> <!-- 声音相关备注 --> <!-- HTML注释 -->
                            </div> <!-- 属性信息结束 --> <!-- HTML注释 -->
                        </div> <!-- 头信息区结束 --> <!-- HTML注释 -->
                        ${mediaUrl(voice) ? `<audio src="${escapeHtml(mediaUrl(voice))}" controls></audio>` : '<div class="status">暂无试听音频</div>'} <!-- 播放条或无音频提示 --> <!-- HTML注释 -->
                        <div class="voice-actions"> <!-- 声音卡片操作栏 --> <!-- HTML注释 -->
                            <button class="btn small" type="button" data-action="use-voice"> <!-- 设为当前音频操作按钮 --> <!-- HTML注释 -->
                                <svg class="icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"></path><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"></path></svg> <!-- 耳机播放SVG --> <!-- HTML注释 -->
                                设为当前 <!-- 按钮文字 --> <!-- HTML注释 -->
                            </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                            <button class="btn small" type="button" data-action="bind-current"> <!-- 绑定到当前人物按钮 --> <!-- HTML注释 -->
                                <svg class="icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg> <!-- 链条链接图标 --> <!-- HTML注释 -->
                                绑定当前 <!-- 按钮文字 --> <!-- HTML注释 -->
                            </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                            <button class="btn small" type="button" data-action="edit-voice"> <!-- 编辑音色信息按钮 --> <!-- HTML注释 -->
                                <svg class="icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"></path></svg> <!-- 编辑铅笔图标 --> <!-- HTML注释 -->
                                编辑 <!-- 按钮文字 --> <!-- HTML注释 -->
                            </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                            <button class="btn small" type="button" data-action="delete-voice"> <!-- 删除音色按钮 --> <!-- HTML注释 -->
                                <svg class="icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg> <!-- 垃圾箱图标 --> <!-- HTML注释 -->
                                删除 <!-- 按钮文字 --> <!-- HTML注释 -->
                            </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                        </div> <!-- 操作栏结束 --> <!-- HTML注释 -->
                    </div>`; /* 卡片模版内容拼接返回 */ /* 模版返回注释 */
            }).join(''); /* 归并拼接声音列表模版 */ /* 拼接注释 */
            el.querySelectorAll('[data-voice-name]').forEach(card => { /* 遍历挂接声音卡片动作事件 */ /* 循环注释 */
                const voiceName = card.dataset.voiceName; /* 取得此声音项标识名 */ /* 获取标识注释 */
                card.querySelectorAll('[data-action]').forEach(btn => { /* 遍历卡片底部的所有动作按钮 */ /* 循环注释 */
                    btn.onclick = () => handleVoiceAction(voiceName, btn.dataset.action).catch(err => log(err.message, 'err')); /* 挂接对应的动作处理事件 */ /* 事件挂接注释 */
                }); /* 动作按键遍历结束 */ /* 循环注释 */
            }); /* 卡片列表遍历挂接结束 */ /* 循环注释 */
        }

        async function handleVoiceAction(voiceName, action) {
            const voice = state.voices.find(v => (v.value || v.name || '') === voiceName);
            if (!voice) return;
            if (action === 'use-voice') {
                $('voice-select').value = voiceName;
                updateVoiceActions();
                renderVoicesLibrary();
                renderCurrentSelection();
                log(`已选择声音：${voice.display_name || voice.name || voiceName}`, 'ok');
                return;
            }
            if (action === 'bind-current') {
                const person = currentPerson();
                if (!person) throw new Error('请先选择人物');
                await createOrUpdatePerson({ ...person, default_voice_name: voiceName });
                log(`已绑定到人物：${person.name}`, 'ok');
                return;
            }
            if (action === 'edit-voice') {
                const result = await showFormDialog({
                    title: '编辑声音',
                    confirmText: '保存',
                    fields: [
                        { name: 'displayName', label: '声音显示名', value: voice.display_name || voice.name || voiceName, required: true, error: '请输入声音显示名' },
                        { name: 'note', label: '声音备注（可留空）', value: voice.note || '', type: 'textarea', rows: 3 },
                    ],
                });
                if (!result) return;
                const displayName = result.displayName.trim();
                if (!displayName) return;
                const note = result.note || '';
                const data = await postJson(`/api/digital-human/library/voices/${encodeURIComponent(voiceName)}`, { display_name: displayName, note }, 'PATCH');
                await refreshLibraryFromResponse(data);
                log(`声音已更新：${displayName}`, 'ok');
                return;
            }
            if (action === 'delete-voice') {
                if (!voice.deletable) throw new Error('该声音不可删除');
                await deleteVoiceByName(voiceName);
            }
        }

        function showVoiceAsset(item) {
            const el = $('voice-current');
            el.innerHTML = '';
            el.classList.toggle('show', !!item);
            if (!item) return;
            const url = mediaUrl(item);
            const name = item.name || item.path || item.url || '已上传音频';
            el.innerHTML = `
                <div class="chip-meta">
                    <div class="asset-name">${escapeHtml(name)}</div>
                    <button class="btn small" type="button">移除</button>
                </div>
                ${url ? `<audio src="${escapeHtml(url)}" controls></audio>` : '<div class="status">已保存参考音频</div>'}`;
            el.querySelector('button').onclick = () => {
                state.uploadedVoice = null;
                showVoiceAsset(null);
            };
        }

        function defaultVoiceName(file) {
            return (file?.name || '').replace(/\.[^.]+$/, '').trim();
        }

        async function uploadOne(file, kind, options = {}) {
            const form = new FormData();
            form.append('files', file);
            const uploadKind = kind === 'emotion' ? 'asset' : kind;
            const params = new URLSearchParams({ kind: uploadKind });
            if (kind === 'voice') {
                params.set('save_voice', 'true');
                params.set('voice_name', options.voiceName || defaultVoiceName(file));
                params.set('overwrite', options.overwrite ? 'true' : 'false');
            }
            let res;
            try {
                res = await fetch(`/api/digital-human/upload?${params.toString()}`, { method: 'POST', body: form });
            } catch {
                throw new Error(`数字人后台未就绪，${SERVICE_START_HINT}`);
            }
            const data = await readJsonBody(res);
            if (res.status === 409 && kind === 'voice') {
                const detail = data.detail || {};
                const voiceName = detail.voice_name || options.voiceName || defaultVoiceName(file);
                const shouldOverwrite = await showConfirmDialog({
                    title: '覆盖音色',
                    description: `音色“${voiceName}”已存在，要用当前文件覆盖吗？`,
                    confirmText: '覆盖',
                    danger: true,
                });
                if (shouldOverwrite) {
                    return uploadOne(file, kind, { ...options, voiceName, overwrite: true });
                }
                throw new Error('已取消保存音色');
            }
            if (!res.ok) {
                throw new Error(userFacingError(data?.detail ?? data?.message ?? data, '上传失败'));
            }
            if (data === null) throw new Error('后端返回格式异常，请刷新后重试。');
            return data.files?.[0];
        }

        async function uploadMany(files, kind) {
            const form = new FormData();
            Array.from(files || []).forEach(file => form.append('files', file));
            const uploadKind = kind === 'emotion' ? 'asset' : kind;
            const params = new URLSearchParams({ kind: uploadKind });
            let res;
            try {
                res = await fetch(`/api/digital-human/upload?${params.toString()}`, { method: 'POST', body: form });
            } catch {
                throw new Error(`数字人后端未就绪，${SERVICE_START_HINT}`);
            }
            const data = await readJsonBody(res);
            if (!res.ok) {
                throw new Error(userFacingError(data?.detail ?? data?.message ?? data, '上传失败'));
            }
            if (data === null) throw new Error('后端返回格式异常，请刷新后重试。');
            return Array.isArray(data.files) ? data.files : [];
        }

        function previewSelectedVoice() {
            const item = selectedVoiceItem();
            const url = mediaUrl(item);
            if (!url) {
                log('当前音色暂无试听音频', 'err');
                return;
            }
            const audio = $('voice-preview-audio');
            audio.src = url;
            audio.classList.remove('hidden');
            audio.play().catch(() => {
                log('浏览器阻止了自动播放，请点音频控件播放', 'err');
            });
            log(`正在试听：${item.name || item.value}`, 'ok');
        }

        async function deleteVoiceByName(name) {
            const item = state.voices.find(v => (v.value || v.name || '') === name);
            if (!item?.deletable) throw new Error('当前音色不可删除');
            const confirmed = await showConfirmDialog({
                title: '删除声音',
                description: `确定删除声音“${item.display_name || item.name || name}”吗？删除后不可恢复。`,
                confirmText: '删除',
                danger: true,
            });
            if (!confirmed) return;
            await requestJson(`/api/digital-human/voices/${encodeURIComponent(name)}`, { method: 'DELETE' }, '音色删除失败');
            state.uploadedVoice = null;
            showVoiceAsset(null);
            $('voice-preview-audio').pause();
            $('voice-preview-audio').removeAttribute('src');
            $('voice-preview-audio').classList.add('hidden');
            await loadConfig();
            if ($('voice-select').value === name) $('voice-select').value = '';
            log(`音色已删除：${name}`, 'ok');
        }

        async function deleteSelectedVoice() {
            const item = selectedVoiceItem();
            if (!item?.deletable) {
                log('当前音色不可删除', 'err');
                return;
            }
            await deleteVoiceByName(item.value || item.name);
        }

        function isVideoUploadFile(file) {
            if (!file) return false;
            const type = String(file.type || '').toLowerCase();
            const name = String(file.name || '').toLowerCase();
            return type.startsWith('video/') || /\.(mp4|mov|webm|m4v)$/.test(name);
        }

        function videoFilesFromList(files) {
            return Array.from(files || []).filter(isVideoUploadFile);
        }

        function setVideoUploadBusy(busy, text = '') {
            document.querySelectorAll('[data-video-upload-dropzone]').forEach(btn => {
                if (!btn) return;
                if (busy) {
                    if (!btn.dataset.idleHtml) btn.dataset.idleHtml = btn.innerHTML;
                    btn.disabled = true;
                    btn.classList.add('is-uploading');
                    btn.textContent = text || '正在上传';
                } else {
                    btn.disabled = false;
                    btn.classList.remove('is-uploading');
                    if (btn.dataset.idleHtml) {
                        btn.innerHTML = btn.dataset.idleHtml;
                        delete btn.dataset.idleHtml;
                    }
                }
            });
        }

        async function ensureVideoUploadPerson() {
            let person = currentPerson();
            if (!person && typeof ensureVideoSelection === 'function') {
                ensureVideoSelection();
                person = currentPerson();
            }
            if (person && state.selectedPersonId) return person;
            const result = await showFormDialog({
                title: '创建人物',
                description: '上传驱动视频前需要先创建或选择一个人物。',
                confirmText: '创建',
                fields: [
                    { name: 'name', label: '人物名称', value: '未命名人物', required: true, error: '请输入人物名称' },
                ],
            });
            const name = (result?.name || '').trim();
            if (!name) throw new Error('请先创建或选择人物');
            const data = await createOrUpdatePerson({ name });
            person = (data.people || []).find(p => p.name === name) || (data.people || [])[0];
            state.selectedPersonId = person?.id || '';
            if (!state.selectedPersonId) throw new Error('请先选择人物');
            return person;
        }

        async function handleVideoUploads(files) {
            const videoFiles = videoFilesFromList(files);
            if (!videoFiles.length) {
                log('请选择 MP4、MOV、WEBM 或 M4V 动作视频', 'err');
                return;
            }
            const person = await ensureVideoUploadPerson();
            const personId = person.id || state.selectedPersonId;
            if (!personId) throw new Error('请先选择人物');
            setVideoUploadBusy(true, videoFiles.length > 1 ? `正在上传 ${videoFiles.length} 个视频` : '正在上传视频');
            try {
                log(videoFiles.length > 1 ? `正在上传 ${videoFiles.length} 个动作视频...` : `正在上传动作视频：${videoFiles[0].name}`, 'ok');
                const uploaded = await uploadMany(videoFiles, 'video');
                if (!uploaded.length) throw new Error('上传失败，未收到视频文件');
                const data = await postJson(
                    `/api/digital-human/library/people/${encodeURIComponent(personId)}/videos/batch`,
                    { videos: uploaded, set_current: true }
                );
                await refreshLibraryFromResponse(data);
                state.selectedPersonId = personId;
                const refreshedPerson = state.people.find(p => p.id === personId) || currentPerson();
                const lastUploaded = uploaded[uploaded.length - 1];
                const lastVideo = (refreshedPerson?.videos || []).find(video =>
                    (lastUploaded.path && video.path === lastUploaded.path) ||
                    (lastUploaded.url && video.url === lastUploaded.url) ||
                    (lastUploaded.id && video.id === lastUploaded.id)
                );
                state.selectedVideoId = lastVideo?.id || refreshedPerson?.current_video_id || state.selectedVideoId;
                renderVideoLibrary();
                log(videoFiles.length > 1 ? `已加入 ${uploaded.length} 个动作视频` : '动作视频已加入', 'ok');
            } finally {
                setVideoUploadBusy(false);
            }
        }

        async function handleUpload(file, kind) {
            if (!file) return;
            if (kind === 'voice') {
                const suggested = defaultVoiceName(file);
                const result = await showFormDialog({
                    title: '保存音色',
                    description: '上传后会保存到声音库，可用于生成和绑定人物。',
                    confirmText: '保存',
                    fields: [
                        { name: 'voiceName', label: '音色名称', value: suggested, required: true, error: '请输入音色名称' },
                    ],
                });
                const voiceName = (result?.voiceName || '').trim();
                if (!voiceName) {
                    log('已取消保存音色', 'warn');
                    return;
                }
                const item = await uploadOne(file, kind, { voiceName });
                state.uploadedVoice = item;
                showVoiceAsset(item);
                if (Array.isArray(item?.voices)) {
                    state.voices = item.voices;
                } else {
                    await loadConfig();
                }
                if (item?.saved_voice || item?.voice_name) {
                    ensureVoiceInState(item.saved_voice || { name: item.voice_name, value: item.voice_name, preview_url: item.preview_url });
                }
                renderVoices();
                renderVideoLibrary();
                if (item?.voice_name && state.voices.some(v => (v.value || v.name || '') === item.voice_name)) {
                    $('voice-select').value = item.voice_name;
                }
                updateVoiceActions();
                await loadConfig();
                log(`音色已保存：${item.voice_name || item.name}`, 'ok');
            } else if (kind === 'emotion') {
                const item = await uploadOne(file, kind);
                state.uploadedEmotionAudio = item;
                updateEmotionPanels();
                log(`情感参考音频已上传：${item.name}`, 'ok');
            } else {
                await handleVideoUploads([file]);
            }
        }
