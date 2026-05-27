// People library, selected video preview, and video poster handling
// Split from state.js. Loaded as a classic script; shared symbols remain global.

function currentPerson() {
            return state.people.find(p => p.id === state.selectedPersonId) || null;
        }

        function currentVideoItem() {
            const person = currentPerson();
            if (!person) return null;
            const videos = person.videos || [];
            return videos.find(v => v.id === state.selectedVideoId)
                || videos.find(v => v.id === person.current_video_id)
                || videos[0]
                || null;
        }

        const posterBackfillInFlight = new Set();
        const POSTER_BACKFILL_CONCURRENCY = 2;

        function ensureVideoSelection() {
            if (!state.selectedPersonId && state.people.length) {
                state.selectedPersonId = state.people[0].id;
            }
            const person = currentPerson() || state.people[0] || null;
            if (!person) {
                state.selectedPersonId = '';
                state.selectedVideoId = '';
                state.selectedVideoPath = '';
                state.selectedVideoSource = '';
                return;
            }
            state.selectedPersonId = person.id;
            const videos = person.videos || [];
            const video = videos.find(v => v.id === state.selectedVideoId)
                || videos.find(v => v.id === person.current_video_id)
                || videos[0]
                || null;
            state.selectedVideoId = video?.id || '';
            state.selectedVideoPath = video?.path || '';
            state.selectedVideoSource = video ? (video.source || 'library') : '';
        }

        function renderCurrentSelection() {
            const person = currentPerson();
            const video = currentVideoItem();
            const voice = selectedVoiceItem();
            $('current-person-name').textContent = person?.name || '未选择';
            $('current-person-note').textContent = person?.note || (person ? '已选择人物' : '请在人物库选择人物');
            $('current-video-name').textContent = video?.name || '未选择';
            $('current-video-note').textContent = video ? '已选择驱动视频' : '人物可包含多个驱动视频';
            $('current-voice-name').textContent = voice?.display_name || voice?.name || voice?.value || '使用参考音频';
            $('current-voice-note').textContent = voice?.note || (voice ? '已选择音色' : '可在声音库管理音色');
        }

        function renderSelectedVideoPreview() {
            const el = $('selected-video-preview');
            const summary = $('video-summary');
            const person = currentPerson();
            const video = currentVideoItem();
            if (!el || !summary) return;
            const url = mediaUrl(video);
            if (!video || !url) {
                summary.textContent = '';
                el.innerHTML = '';
                el.classList.add('hidden');
                return;
            }
            summary.textContent = video.name || '已选择驱动视频';
            el.classList.remove('hidden', 'empty');
            el.innerHTML = `
                <div class="selected-preview-media" data-video-preview-container>
                    ${videoPreviewSlot(video, '暂无封面', person?.id || '')}
                </div>
                <div class="selected-preview-meta">
                    <div class="asset-name">${escapeHtml(video.name || '驱动视频')}</div>
                    <div class="asset-note">当前生成使用的视频素材</div>
                </div>
            `;
        }
        function posterUrl(item) {
            return item?.poster_url || item?.poster || item?.thumbnail_url || '';
        }

        function videoPosterContent(video, label = '动作视频') {
            const poster = posterUrl(video);
            if (poster) {
                return `<img src="${escapeHtml(poster)}" alt="${escapeHtml(video?.name || label)}" loading="lazy">`;
            }
            return `<span class="video-poster-placeholder">${escapeHtml(label)}</span>`;
        }

        function videoPreviewSlot(video, label = '动作视频', personId = '') {
            const url = mediaUrl(video);
            const needsPoster = url && video?.id && !posterUrl(video) && !video?.poster_failed;
            const attrs = [
                url ? 'data-video-preview-slot' : '',
                url ? `data-video-src="${escapeHtml(url)}"` : '',
                needsPoster ? 'data-poster-missing="1"' : '',
                needsPoster && personId ? `data-poster-person-id="${escapeHtml(personId)}"` : '',
                needsPoster ? `data-poster-video-id="${escapeHtml(video.id)}"` : '',
            ].filter(Boolean).join(' ');
            return `<span class="video-poster-frame"${attrs ? ` ${attrs}` : ''}>${videoPosterContent(video, label)}</span>`;
        }

        function personCoverVideo(person) {
            const videos = person?.videos || [];
            const video = videos.find(v => v.id === person.current_video_id) || videos[0] || null;
            if (video) return videoPreviewSlot(video, '暂无封面', person?.id || '');
            return '<span class="status">无预览</span>';
        }

        function renderPeopleLibrary() {
            const el = $('people-library');
            $('people-summary').textContent = `共 ${state.people.length} 个人物`;
            if (!state.people.length) {
                el.innerHTML = `
                    <div class="empty-state">
                        <div>还没有人物，请先新建角色</div>
                        <button class="btn small" type="button" id="people-empty-add-btn">新建角色</button>
                    </div>`;
                $('people-empty-add-btn')?.addEventListener('click', () => createNewPerson().catch(err => log(err.message, 'err')));
                return;
            }
            const selectedPerson = currentPerson() || state.people[0];
            if (selectedPerson && selectedPerson.id !== state.selectedPersonId) {
                state.selectedPersonId = selectedPerson.id;
            }
            const currentVideo = selectedPerson
                ? ((selectedPerson.videos || []).find(v => v.id === selectedPerson.current_video_id) || (selectedPerson.videos || [])[0])
                : null;
            const totalVideos = state.people.reduce((sum, person) => sum + (person.videos || []).length, 0);
            const peopleList = state.people.map(person => {
                const active = person.id === selectedPerson?.id;
                const videoCount = (person.videos || []).length;
                return `
                    <div class="person-list-card ${active ? 'active' : ''}" role="button" tabindex="0" data-person-id="${escapeHtml(person.id)}">
                        <div class="person-list-thumb">${personCoverVideo(person)}</div>
                        <div class="person-list-meta">
                            <div class="asset-name">${escapeHtml(person.name)}</div>
                            <div class="asset-note">${videoCount} 个视频${person.default_voice_name ? ` · 默认 ${escapeHtml(person.default_voice_name)}` : ''}</div>
                        </div>
                    </div>`;
            }).join('');
            const selectedVoiceName = selectedPerson?.default_voice_name || '';
            const voiceOptions = state.voices.filter(v => (v.value || v.name || '') !== '使用参考音频').map(v => {
                const val = v.value || v.name || '';
                const selected = val === selectedVoiceName ? 'selected' : '';
                return `<option value="${escapeHtml(val)}" ${selected}>${escapeHtml(v.display_name || v.name || val)}</option>`;
            }).join('');
            const voiceSelectorHtml = `
                <div class="inline-voice-binder">
                    <select data-action="inline-bind-voice">
                        <option value="">未绑定默认声音</option>
                        ${voiceOptions}
                    </select>
                </div>`;
            const detail = selectedPerson ? `
                <div class="person-detail">
                    <div class="person-detail-hero">
                        <div class="person-detail-cover">${personCoverVideo(selectedPerson)}</div>
                        <div class="person-detail-main">
                            <div class="person-detail-title">
                                <div>
                                    <div class="section-label">当前角色</div>
                                    <div class="asset-name">${escapeHtml(selectedPerson.name)}</div>
                                    <div class="asset-note">${escapeHtml(selectedPerson.note || '暂无备注说明')}</div>
                                </div>
                                <button class="btn primary" type="button" data-action="select-person">
                                    <svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                                    使用此角色
                                </button>
                            </div>
                            <div class="person-stats">
                                <div class="person-stat">
                                    <div class="section-label">动作素材</div>
                                    <strong>
                                        <svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"></path><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>
                                        ${(selectedPerson.videos || []).length} 个
                                    </strong>
                                </div>
                                <div class="person-stat">
                                    <div class="section-label">当前动作</div>
                                    <strong>
                                        <svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                                        ${escapeHtml(currentVideo?.name || '未选择')}
                                    </strong>
                                </div>
                                <div class="person-stat">
                                    <div class="section-label">绑定音色 (直接修改)</div>
                                    <strong>
                                        <svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>
                                        ${voiceSelectorHtml}
                                    </strong>
                                </div>
                            </div>
                            <div class="library-actions">
                                <button class="btn small" type="button" data-action="edit-person">
                                    <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"></path></svg>
                                    编辑角色
                                </button>
                                <button class="btn small" type="button" data-action="delete-person">
                                    <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                                    删除角色
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="video-library-head">
                        <div>
                            <div class="asset-name">动作视频库</div>
                            <div class="asset-note">鼠标悬停卡片可自动静音预览动作，点击“使用动作”开始合成</div>
                        </div>
                        <button class="btn small" type="button" data-video-upload-dropzone>
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                            上传动作视频
                        </button>
                    </div>
                    <div class="video-card-grid">
                        ${(selectedPerson.videos || []).map(video => videoRowMarkup(selectedPerson, video)).join('') || '<div class="empty-state">这个人物还没有驱动视频</div>'}
                    </div>
                </div>` : '<div class="empty-state">请选择一个人物</div>';
            el.innerHTML = `
                <div class="people-workspace">
                    <div class="people-sidebar">
                        <div class="people-sidebar-head">
                            <div>
                                <div class="asset-name">角色库</div>
                                <div class="asset-note">${state.people.length} 角色 · ${totalVideos} 动作视频</div>
                            </div>
                            <button class="btn small" type="button" id="people-list-add-btn">
                                <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                                新建角色
                            </button>
                        </div>
                        <div class="people-list">${peopleList}</div>
                    </div>
                    ${detail}
                </div>`;
            $('people-list-add-btn')?.addEventListener('click', () => createNewPerson().catch(err => log(err.message, 'err')));
            el.querySelectorAll('[data-person-id]').forEach(card => {
                const personId = card.dataset.personId;
                card.onclick = (event) => {
                    if (event.target.closest('button[data-action], select[data-action], button[data-video-action]')) return;
                    state.selectedPersonId = personId;
                    const person = state.people.find(p => p.id === personId);
                    state.selectedVideoId = person?.current_video_id || person?.videos?.[0]?.id || '';
                    renderVideoLibrary();
                };
                card.onkeydown = (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        card.click();
                    }
                };
                card.querySelectorAll('[data-action]').forEach(btn => {
                    btn.onclick = () => handlePersonAction(personId, btn.dataset.action).catch(err => log(err.message, 'err'));
                });
            });
            el.querySelectorAll('.person-detail [data-action]').forEach(btn => {
                btn.onclick = () => handlePersonAction(selectedPerson.id, btn.dataset.action).catch(err => log(err.message, 'err'));
            });
            el.querySelectorAll('[data-video-upload-dropzone]').forEach(btn => {
                btn.onclick = () => {
                    state.selectedPersonId = selectedPerson.id;
                    $('video-upload').click();
                };
                btn.ondragover = (event) => {
                    event.preventDefault();
                    btn.classList.add('dragging');
                };
                btn.ondragleave = () => btn.classList.remove('dragging');
                btn.ondrop = (event) => {
                    event.preventDefault();
                    btn.classList.remove('dragging');
                    state.selectedPersonId = selectedPerson.id;
                    handleVideoUploads(event.dataTransfer.files).catch(err => log(err.message, 'err'));
                };
            });
            el.querySelectorAll('.person-detail [data-video-id]').forEach(card => {
                card.querySelectorAll('[data-video-action]').forEach(btn => {
                    btn.onclick = () => handlePersonVideoAction(selectedPerson.id, card.dataset.videoId, btn.dataset.videoAction).catch(err => log(err.message, 'err'));
                });
            });
            el.querySelectorAll('[data-action="inline-bind-voice"]').forEach(select => {
                select.onchange = async () => {
                    try {
                        const voiceName = select.value;
                        await createOrUpdatePerson({ ...selectedPerson, default_voice_name: voiceName });
                        log(`已将角色音色绑定为：${voiceName || '未绑定'}`, 'ok');
                    } catch (err) {
                        log(err.message, 'err');
                        renderPeopleLibrary();
                    }
                };
            });
            el.querySelectorAll('.person-list-card, .asset-video-card, .person-detail-cover').forEach(card => {
                const slot = card.querySelector('[data-video-preview-slot]');
                const src = slot?.dataset.videoSrc || '';
                if (!slot || !src) return;
                card.addEventListener('mouseenter', () => {
                    const video = ensureHoverPreviewVideo(slot);
                    if (!video) return;
                    slot.classList.add('previewing');
                    video.muted = true;
                    video.play().catch(() => {});
                });
                card.addEventListener('mouseleave', () => {
                    const video = slot.querySelector('video[data-hover-preview]');
                    if (!video) return;
                    video.pause();
                    video.currentTime = 0;
                    slot.classList.remove('previewing');
                });
            });
        }

        function ensureHoverPreviewVideo(slot) {
            const src = slot?.dataset.videoSrc || '';
            if (!src) return null;
            let video = slot.querySelector('video[data-hover-preview]');
            if (video) return video;
            video = document.createElement('video');
            video.dataset.hoverPreview = '1';
            video.src = src;
            video.muted = true;
            video.loop = true;
            video.playsInline = true;
            video.preload = 'metadata';
            video.addEventListener('loadedmetadata', () => {
                applyVideoOrientation(slot, video.videoWidth, video.videoHeight);
            }, { once: true });
            slot.appendChild(video);
            return video;
        }

        function updatePosterSlots(personId, videoId, video) {
            document.querySelectorAll('[data-poster-video-id]').forEach(slot => {
                if (slot.dataset.posterPersonId !== personId || slot.dataset.posterVideoId !== videoId) return;
                if (video?.poster_url) {
                    slot.innerHTML = videoPosterContent(video, '暂无封面');
                }
                slot.removeAttribute('data-poster-missing');
            });
        }

        function applyPosterBackfillResult(personId, videoId, data) {
            const posterUrl = data?.poster_url || data?.video?.poster_url || '';
            const person = state.people.find(p => p.id === personId);
            const video = person?.videos?.find(v => v.id === videoId);
            if (video && posterUrl) {
                video.poster_url = posterUrl;
                video.poster_path = data?.video?.poster_path || video.poster_path || '';
                video.poster_failed = false;
            } else if (video) {
                video.poster_failed = true;
            }
            updatePosterSlots(personId, videoId, video || data?.video || null);
        }

        function renderVideoLibrary() {
            ensureVideoSelection();
            renderCurrentSelection();
            renderSelectedVideoPreview();
            renderPeopleLibrary();
            renderVoicesLibrary();
        }

        function videoRowMarkup(person, video) {
            const url = mediaUrl(video);
            const active = person.id === state.selectedPersonId && video.id === state.selectedVideoId;
            return `
                <div class="asset-video-card ${active ? 'active' : ''}" data-video-id="${escapeHtml(video.id)}">
                    <div class="asset-video-thumb">
                        ${url ? videoPreviewSlot(video, '暂无封面', person.id) : '<span class="status">无预览</span>'}
                        ${active ? '<span class="video-badge">当前使用</span>' : ''}
                    </div>
                    <div class="asset-video-info">
                        <div class="asset-name">${escapeHtml(video.name || '驱动视频')}</div>
                        <div class="asset-note">${active ? '当前生成视频' : '可切换使用'}</div>
                    </div>
                    <div class="asset-video-actions">
                        <button class="btn small" type="button" data-video-action="use">
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                            使用
                        </button>
                        <button class="btn small" type="button" data-video-action="rename">
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"></path></svg>
                            改名
                        </button>
                        <button class="btn small" type="button" data-video-action="remove">
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                            移除
                        </button>
                    </div>
                </div>`;
        }

        function applyLibraryData(data) {
            state.library = data.library || null;
            state.people = data.people || data.library?.people || [];
            state.voices = data.voices || [];
            state.videos = data.videos || [];
            ensureVideoSelection();
            renderVoices();
            renderVideoLibrary();
        }

        async function refreshLibraryFromResponse(data) {
            applyLibraryData(data);
            prepareVideoPosters();
        }

        async function createOrUpdatePerson(person) {
            const data = await postJson('/api/digital-human/library/people', person || {});
            await refreshLibraryFromResponse(data);
            return data;
        }

        async function createNewPerson() {
            const result = await showFormDialog({
                title: '新增人物',
                description: '创建后可以继续添加驱动视频和绑定默认声音。',
                confirmText: '创建',
                fields: [
                    { name: 'name', label: '人物名称', value: '新人角色', required: true, error: '请输入人物名称' },
                    { name: 'note', label: '人物备注（可留空）', value: '', type: 'textarea', rows: 3 },
                ],
            });
            if (!result) return null;
            const name = result.name.trim();
            if (!name) return null;
            const note = result.note || '';
            const data = await createOrUpdatePerson({ name, note });
            const created = (data.people || []).find(p => p.name === name);
            if (created) state.selectedPersonId = created.id;
            renderVideoLibrary();
            log(`人物已创建：${name}`, 'ok');
            return created || null;
        }

        async function handlePersonAction(personId, action) {
            const person = state.people.find(p => p.id === personId);
            if (!person) return;
            if (action === 'select-person') {
                selectPerson(personId);
                return;
            }
            if (action === 'edit-person') {
                const result = await showFormDialog({
                    title: '编辑人物',
                    confirmText: '保存',
                    fields: [
                        { name: 'name', label: '人物名称', value: person.name || '', required: true, error: '请输入人物名称' },
                        { name: 'note', label: '人物备注（可留空）', value: person.note || '', type: 'textarea', rows: 3 },
                    ],
                });
                if (!result) return;
                const name = result.name.trim();
                if (!name) return;
                const note = result.note || '';
                await createOrUpdatePerson({ ...person, name, note });
                log(`人物已更新：${name}`, 'ok');
                return;
            }
            if (action === 'bind-voice') {
                const voiceName = $('voice-select').value;
                if (!voiceName) throw new Error('请先选择一个声音');
                await createOrUpdatePerson({ ...person, default_voice_name: voiceName });
                log(`已把声音绑定到人物：${person.name}`, 'ok');
                return;
            }
            if (action === 'delete-person') {
                const mode = await showConfirmDialog({
                    title: '删除人物',
                    description: `确定处理人物“${person.name}”吗？`,
                    confirmText: '删除',
                    danger: true,
                    choices: [
                        { value: '1', label: '仅移出库', description: '保留本地视频文件，之后仍可重新加入。' },
                        { value: '2', label: '同时删除视频文件', description: '会删除该人物关联的视频文件，不可恢复。' },
                    ],
                    defaultChoice: '1',
                });
                if (!mode) return;
                const deleteFiles = mode.trim() === '2';
                const data = await requestJson(
                    `/api/digital-human/library/people/${encodeURIComponent(personId)}?delete_files=${deleteFiles ? 'true' : 'false'}`,
                    { method: 'DELETE' },
                    '删除人物失败'
                );
                state.selectedPersonId = '';
                state.selectedVideoId = '';
                await refreshLibraryFromResponse(data);
                log(deleteFiles ? '人物和视频文件已删除' : '人物已移出库', 'ok');
            }
        }

        async function handlePersonVideoAction(personId, videoId, action) {
            const person = state.people.find(p => p.id === personId);
            const video = person?.videos?.find(v => v.id === videoId);
            if (!person || !video) return;
            if (action === 'use') {
                state.selectedPersonId = personId;
                state.selectedVideoId = videoId;
                await createOrUpdatePerson({ ...person, current_video_id: videoId });
                selectPerson(personId, videoId);
                return;
            }
            if (action === 'rename') {
                const result = await showFormDialog({
                    title: '编辑视频名称',
                    confirmText: '保存',
                    fields: [
                        { name: 'name', label: '视频名称', value: video.name || '', required: true, error: '请输入视频名称' },
                    ],
                });
                if (!result) return;
                const name = result.name.trim();
                if (!name) return;
                const data = await postJson(`/api/digital-human/library/people/${encodeURIComponent(personId)}/videos`, { video, name, set_current: videoId === state.selectedVideoId });
                await refreshLibraryFromResponse(data);
                log(`视频已改名：${name}`, 'ok');
                return;
            }
            if (action === 'remove') {
                const mode = await showConfirmDialog({
                    title: '移除视频',
                    description: `确定处理视频“${video.name}”吗？`,
                    confirmText: '移除',
                    danger: true,
                    choices: [
                        { value: '1', label: '仅移出库', description: '保留本地视频文件，之后仍可重新加入。' },
                        { value: '2', label: '同时删除文件', description: '会删除这个视频文件，不可恢复。' },
                    ],
                    defaultChoice: '1',
                });
                if (!mode) return;
                const deleteFile = mode.trim() === '2';
                const data = await requestJson(
                    `/api/digital-human/library/people/${encodeURIComponent(personId)}/videos/${encodeURIComponent(videoId)}?delete_file=${deleteFile ? 'true' : 'false'}`,
                    { method: 'DELETE' },
                    '移除视频失败'
                );
                await refreshLibraryFromResponse(data);
                log(deleteFile ? '视频文件已删除' : '视频已移出库', 'ok');
            }
        }

        function selectPerson(personId, videoId = '') {
            const person = state.people.find(p => p.id === personId);
            if (!person) return;
            state.selectedPersonId = personId;
            state.selectedVideoId = videoId || person.current_video_id || person.videos?.[0]?.id || '';
            state.selectedVideoSource = state.selectedVideoId ? 'library' : '';
            const hasDefaultVoice = person.default_voice_name && state.voices.some(v => (v.value || v.name || '') === person.default_voice_name);
            if (hasDefaultVoice) {
                $('voice-select').value = person.default_voice_name;
                updateVoiceActions();
            } else if (person.default_voice_name) {
                log(`人物默认声音不存在：${person.default_voice_name}，请重新选择声音`, 'warn');
            }
            renderVideoLibrary();
            switchTab('generate');
        }

        function prepareVideoPosters() {
            requestVisibleVideoPosters();
        }

        function requestVisibleVideoPosters() {
            const activePage = document.querySelector('.tab-page.active') || document;
            let started = 0;
            Array.from(activePage.querySelectorAll('[data-poster-missing][data-poster-person-id][data-poster-video-id]'))
                .forEach(slot => {
                    if (posterBackfillInFlight.size >= POSTER_BACKFILL_CONCURRENCY || started >= POSTER_BACKFILL_CONCURRENCY) return;
                    const personId = slot.dataset.posterPersonId || '';
                    const videoId = slot.dataset.posterVideoId || '';
                    const key = `${personId}:${videoId}`;
                    if (!personId || !videoId || posterBackfillInFlight.has(key)) return;
                    started += 1;
                    posterBackfillInFlight.add(key);
                    requestJson(
                        `/api/digital-human/library/people/${encodeURIComponent(personId)}/videos/${encodeURIComponent(videoId)}/poster`,
                        { method: 'POST' },
                        '封面生成失败'
                    ).then(data => {
                        applyPosterBackfillResult(personId, videoId, data);
                    }).catch(() => {
                        const person = state.people.find(p => p.id === personId);
                        const video = person?.videos?.find(v => v.id === videoId);
                        if (video) video.poster_failed = true;
                        updatePosterSlots(personId, videoId, null);
                    }).finally(() => {
                        posterBackfillInFlight.delete(key);
                        setTimeout(requestVisibleVideoPosters, 180);
                    });
                });
        }

        function applyVideoOrientation(target, width, height) {
            if (!width || !height) return;
            const isPortrait = height > width;
            target.closest('.selected-preview-media')?.classList.toggle('portrait', isPortrait);
            target.closest('.video-choice-thumb')?.classList.toggle('portrait', isPortrait);
            target.closest('.asset-video-thumb')?.classList.toggle('portrait', isPortrait);
        }
