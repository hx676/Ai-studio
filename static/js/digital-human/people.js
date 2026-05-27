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

        function renderPeopleLibrary() { /* 渲染人物库角色工坊版主函数 */ /* 逻辑注释 */
            const el = $('people-library'); /* 获取人物库容器DOM节点 */ /* DOM获取注释 */
            $('people-summary').textContent = `共 ${state.people.length} 个人物`; /* 顶部状态显示总人数 */ /* 赋值注释 */
            if (!state.people.length) { /* 判断是否没有任何人物数据 */ /* 判断注释 */
                el.innerHTML = `
                    <div class="empty-state">
                        <div>还没有人物，请先新建角色</div>
                        <button class="btn small" type="button" id="people-empty-add-btn">新建角色</button>
                    </div>`;
                $('people-empty-add-btn')?.addEventListener('click', () => createNewPerson().catch(err => log(err.message, 'err')));
                return; /* 函数中止返回 */ /* 中止注释 */
            } /* 判断结束 */ /* 逻辑块注释 */
            const selectedPerson = currentPerson() || state.people[0]; /* 精确检索当前被选中的角色 */ /* 选取注释 */
            if (selectedPerson && selectedPerson.id !== state.selectedPersonId) { /* 如果当前人物与全局变量ID不一致 */ /* 判断注释 */
                state.selectedPersonId = selectedPerson.id; /* 自动同步全局选中角色ID */ /* 赋值注释 */
            } /* 同步结束 */ /* 逻辑块注释 */
            const currentVideo = selectedPerson /* 基于选中人物检索当前视频 */ /* 检索注释 */
                ? ((selectedPerson.videos || []).find(v => v.id === selectedPerson.current_video_id) || (selectedPerson.videos || [])[0]) /* 拉取其当前驱动视频 */ /* 检索视频注释 */
                : null; /* 否则为空 */ /* 空值注释 */
            const totalVideos = state.people.reduce((sum, person) => sum + (person.videos || []).length, 0); /* 统计整个库中的驱动视频总数 */ /* 计算注释 */
            const peopleList = state.people.map(person => { /* 生成左侧精简人物头像轨道 */ /* 循环映射注释 */
                const active = person.id === selectedPerson?.id; /* 校验是否为选中高亮状态 */ /* 状态校验注释 */
                const videoCount = (person.videos || []).length; /* 该角色的视频积累数 */ /* 统计注释 */
                return `
                    <div class="person-list-card ${active ? 'active' : ''}" role="button" tabindex="0" data-person-id="${escapeHtml(person.id)}"> <!-- 人物胶囊名片 --> <!-- HTML注释 -->
                        <div class="person-list-thumb">${personCoverVideo(person)}</div> <!-- 极简圆形头像壳 --> <!-- HTML注释 -->
                        <div class="person-list-meta"> <!-- 文本区域 --> <!-- HTML注释 -->
                            <div class="asset-name">${escapeHtml(person.name)}</div> <!-- 人物名字 --> <!-- HTML注释 -->
                            <div class="asset-note">${videoCount} 个视频${person.default_voice_name ? ` · 默认 ${escapeHtml(person.default_voice_name)}` : ''}</div> <!-- 子段信息说明 --> <!-- HTML注释 -->
                        </div> <!-- 文本结束 --> <!-- HTML注释 -->
                    </div>`; /* 卡片模版拼接 */ /* 模版返回注释 */
            }).join(''); /* 归并单字符串 */ /* 拼接注释 */
            const selectedVoiceName = selectedPerson?.default_voice_name || ''; /* 预备此人物当前绑定的默认音色名称 */ /* 取值注释 */
            const voiceOptions = state.voices.filter(v => (v.value || v.name || '') !== '使用参考音频').map(v => { /* 循环声音列表生成绑定下拉选项 */ /* 循环注释 */
                const val = v.value || v.name || ''; /* 选项值 */ /* 取值注释 */
                const selected = val === selectedVoiceName ? 'selected' : ''; /* 校验是否当前正选中绑定 */ /* 状态判断注释 */
                return `<option value="${escapeHtml(val)}" ${selected}>${escapeHtml(v.display_name || v.name || val)}</option>`; /* 生成选项标签 */ /* 标签返回注释 */
            }).join(''); /* 拼接所有下拉选项 */ /* 拼接注释 */
            const voiceSelectorHtml = `
                <div class="inline-voice-binder"> <!-- 下拉选择绑定容器 --> <!-- HTML注释 -->
                    <select data-action="inline-bind-voice"> <!-- 声音选择下拉框 --> <!-- HTML注释 -->
                        <option value="">未绑定默认声音</option> <!-- 未绑定占位 --> <!-- HTML注释 -->
                        ${voiceOptions} <!-- 声音选项流 --> <!-- HTML注释 -->
                    </select> <!-- 选择框结束 --> <!-- HTML注释 -->
                </div>`; /* 下拉选择绑定器模版 */ /* 模版返回注释 */
            const detail = selectedPerson ? `
                <div class="person-detail"> <!-- 一体化大详情画壁 --> <!-- HTML注释 -->
                    <div class="person-detail-hero"> <!-- 顶层双栏工坊控制台 --> <!-- HTML注释 -->
                        <div class="person-detail-cover">${personCoverVideo(selectedPerson)}</div> <!-- 左大视频展示监视器 --> <!-- HTML注释 -->
                        <div class="person-detail-main"> <!-- 右侧控制卡片 --> <!-- HTML注释 -->
                            <div class="person-detail-title"> <!-- 第一行名字与主使用按钮 --> <!-- HTML注释 -->
                                <div> <!-- 文字组 --> <!-- HTML注释 -->
                                    <div class="section-label">当前角色</div> <!-- 单一标签 --> <!-- HTML注释 -->
                                    <div class="asset-name">${escapeHtml(selectedPerson.name)}</div> <!-- 名字标题 --> <!-- HTML注释 -->
                                    <div class="asset-note">${escapeHtml(selectedPerson.note || '暂无备注说明')}</div> <!-- 备注说明 --> <!-- HTML注释 -->
                                </div> <!-- 文字组结束 --> <!-- HTML注释 -->
                                <button class="btn primary" type="button" data-action="select-person"> <!-- 确定选定此角色生成按钮 --> <!-- HTML注释 -->
                                    <svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> <!-- 打勾SVG --> <!-- HTML注释 -->
                                    使用此角色 <!-- 按钮文字 --> <!-- HTML注释 -->
                                </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                            </div> <!-- 第一行结束 --> <!-- HTML注释 -->
                            <div class="person-stats"> <!-- 三维数据小名牌 --> <!-- HTML注释 -->
                                <div class="person-stat"> <!-- 第一个名牌：素材动作视频量 --> <!-- HTML注释 -->
                                    <div class="section-label">动作素材</div> <!-- 标签 --> <!-- HTML注释 -->
                                    <strong> <!-- 粗体 --> <!-- HTML注释 -->
                                        <svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 7l-7 5 7 5V7z"></path><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg> <!-- 视频机 --> <!-- HTML注释 -->
                                        ${(selectedPerson.videos || []).length} 个 <!-- 视频数量 --> <!-- HTML注释 -->
                                    </strong> <!-- 粗体结束 --> <!-- HTML注释 -->
                                </div> <!-- 名牌结束 --> <!-- HTML注释 -->
                                <div class="person-stat"> <!-- 第二个名牌：当前所选的视频 --> <!-- HTML注释 -->
                                    <div class="section-label">当前动作</div> <!-- 标签 --> <!-- HTML注释 -->
                                    <strong> <!-- 粗体 --> <!-- HTML注释 -->
                                        <svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> <!-- 播放图标 --> <!-- HTML注释 -->
                                        ${escapeHtml(currentVideo?.name || '未选择')} <!-- 当前驱动名 --> <!-- HTML注释 -->
                                    </strong> <!-- 粗体结束 --> <!-- HTML注释 -->
                                </div> <!-- 名牌结束 --> <!-- HTML注释 -->
                                <div class="person-stat"> <!-- 第三个名牌：直接内嵌声音快速下拉绑定器 --> <!-- HTML注释 -->
                                    <div class="section-label">绑定音色 (直接修改)</div> <!-- 绑定标签 --> <!-- HTML注释 -->
                                    <strong> <!-- 绑定下拉容器 --> <!-- HTML注释 -->
                                        <svg class="icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg> <!-- 麦克风 --> <!-- HTML注释 -->
                                        ${voiceSelectorHtml} <!-- 注入绑定器 HTML --> <!-- HTML注释 -->
                                    </strong> <!-- 容器结束 --> <!-- HTML注释 -->
                                </div> <!-- 名牌结束 --> <!-- HTML注释 -->
                            </div> <!-- 数据名牌区结束 --> <!-- HTML注释 -->
                            <div class="library-actions"> <!-- 编辑动作按钮栏 --> <!-- HTML注释 -->
                                <button class="btn small" type="button" data-action="edit-person"> <!-- 修改角色基本信息 --> <!-- HTML注释 -->
                                    <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"></path></svg> <!-- 画笔 --> <!-- HTML注释 -->
                                    编辑角色 <!-- 文字 --> <!-- HTML注释 -->
                                </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                                <button class="btn small" type="button" data-action="delete-person"> <!-- 销毁删除整个角色 --> <!-- HTML注释 -->
                                    <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg> <!-- 垃圾桶 --> <!-- HTML注释 -->
                                    删除角色 <!-- 文字 --> <!-- HTML注释 -->
                                </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                            </div> <!-- 工具栏结束 --> <!-- HTML注释 -->
                        </div> <!-- 右侧结束 --> <!-- HTML注释 -->
                    </div> <!-- 控制台结束 --> <!-- HTML注释 -->
                    <div class="video-library-head"> <!-- 下半部分：动作素材库网格头 --> <!-- HTML注释 -->
                        <div> <!-- 栏目标签 --> <!-- HTML注释 -->
                            <div class="asset-name">动作视频库</div> <!-- 素材库名称 --> <!-- HTML注释 -->
                            <div class="asset-note">鼠标悬停卡片可自动静音预览动作，点击“使用动作”开始合成</div> <!-- 悬停自动播放说明 --> <!-- HTML注释 -->
                        </div> <!-- 标签结束 --> <!-- HTML注释 -->
                        <button class="btn small" type="button" data-action="add-video" data-video-upload-dropzone> <!-- 快捷上传动作视频 --> <!-- HTML注释 -->
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg> <!-- 加号 --> <!-- HTML注释 -->
                            上传动作视频 <!-- 文字 --> <!-- HTML注释 -->
                        </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                    </div> <!-- 网格头结束 --> <!-- HTML注释 -->
                    <div class="video-card-grid"> <!-- 卡片画廊网格 --> <!-- HTML注释 -->
                        ${(selectedPerson.videos || []).map(video => videoRowMarkup(selectedPerson, video)).join('') || '<div class="empty-state">这个人物还没有驱动视频</div>'} <!-- 批量映射输出视频卡片 --> <!-- HTML注释 -->
                    </div> <!-- 网格结束 --> <!-- HTML注释 -->
                </div>` : '<div class="empty-state">请选择一个人物</div>'; /* 拼装完工 */ /* 详情注释 */
            el.innerHTML = `
                <div class="people-workspace"> <!-- 一体化媒体工坊大框架 --> <!-- HTML注释 -->
                    <div class="people-sidebar"> <!-- 左侧精美名片墙 --> <!-- HTML注释 -->
                        <div class="people-sidebar-head"> <!-- 头栏 --> <!-- HTML注释 -->
                            <div> <!-- 数据统计 --> <!-- HTML注释 -->
                                <div class="asset-name">角色库</div> <!-- 库名字 --> <!-- HTML注释 -->
                                <div class="asset-note">${state.people.length} 角色 · ${totalVideos} 动作视频</div> <!-- 统计说明 --> <!-- HTML注释 -->
                            </div> <!-- 统计结束 --> <!-- HTML注释 -->
                            <button class="btn small" type="button" id="people-list-add-btn"> <!-- 创建角色按钮 --> <!-- HTML注释 -->
                                <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg> <!-- 胶囊加号 --> <!-- HTML注释 -->
                                新建角色 <!-- 按钮文字 --> <!-- HTML注释 -->
                            </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                        </div> <!-- 头栏结束 --> <!-- HTML注释 -->
                        <div class="people-list">${peopleList}</div> <!-- 头像列表流 --> <!-- HTML注释 -->
                    </div> <!-- 侧栏结束 --> <!-- HTML注释 -->
                    ${detail} <!-- 注入角色大详情工坊板 --> <!-- HTML注释 -->
                </div>`; /* 大框架拼接完成 */ /* 整体拼接注释 */
            $('people-list-add-btn')?.addEventListener('click', () => createNewPerson().catch(err => log(err.message, 'err'))); /* 新增角色快捷响应 */ /* 事件绑定注释 */
            el.querySelectorAll('[data-person-id]').forEach(card => { /* 遍历挂接左边头像卡事件 */ /* 循环注释 */
                const personId = card.dataset.personId; /* 得到当前卡的角色ID */ /* 取值注释 */
                card.onclick = (event) => { /* 设置单击选择动作 */ /* 事件绑定注释 */
                    if (event.target.closest('button[data-action], select[data-action], button[data-video-action]')) return; /* 过滤小按钮事件触发 */ /* 过滤注释 */
                    state.selectedPersonId = personId; /* 同步当前的全局选中角色 */ /* 赋值注释 */
                    const person = state.people.find(p => p.id === personId); /* 拉取对应人物数据 */ /* 获取注释 */
                    state.selectedVideoId = person?.current_video_id || person?.videos?.[0]?.id || ''; /* 同步视频选中状态 */ /* 赋值注释 */
                    renderVideoLibrary(); /* 重渲染链条 */ /* 联动渲染注释 */
                }; /* 挂载结束 */ /* 回调注释 */
                card.onkeydown = (event) => { /* 键盘辅助选择 */ /* 键盘注释 */
                    if (event.key === 'Enter' || event.key === ' ') { /* 按下空格或回车 */ /* 键位检测注释 */
                        event.preventDefault(); /* 屏蔽默认 */ /* 阻止行为注释 */
                        card.click(); /* 触发点击 */ /* 触发点击注释 */
                    } /* 判断块结束 */ /* 逻辑块注释 */
                }; /* 回调结束 */ /* 回调注释 */
                card.querySelectorAll('[data-action]').forEach(btn => { /* 原装动作绑定 */ /* 循环注释 */
                    btn.onclick = () => handlePersonAction(personId, btn.dataset.action).catch(err => log(err.message, 'err')); /* 执行 */ /* 事件绑定注释 */
                }); /* 动作遍历结束 */ /* 循环注释 */
            }); /* 左边头像卡事件绑定完成 */ /* 循环注释 */
            el.querySelectorAll('.person-detail [data-action]').forEach(btn => { /* 详情控制面板事件绑定 */ /* 循环注释 */
                btn.onclick = () => handlePersonAction(selectedPerson.id, btn.dataset.action).catch(err => log(err.message, 'err')); /* 执行人物级动作 */ /* 事件绑定注释 */
            }); /* 绑定完成 */ /* 循环注释 */
            el.querySelectorAll('[data-video-upload-dropzone]').forEach(btn => {
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
            el.querySelectorAll('.person-detail [data-video-id]').forEach(card => { /* 驱动视频卡片事件注册 */ /* 循环注释 */
                card.querySelectorAll('[data-video-action]').forEach(btn => { /* 卡片操作覆盖层内功能按键 */ /* 循环注释 */
                    btn.onclick = () => handlePersonVideoAction(selectedPerson.id, card.dataset.videoId, btn.dataset.videoAction).catch(err => log(err.message, 'err')); /* 执行改名、移除、使用事件 */ /* 事件绑定注释 */
                }); /* 遍历完成 */ /* 循环注释 */
            }); /* 驱动卡事件绑定完成 */ /* 循环注释 */
            el.querySelectorAll('[data-action="inline-bind-voice"]').forEach(select => { /* 声音下拉绑定器事件绑定 */ /* 循环注释 */
                select.onchange = async () => { /* 选择项改变逻辑 */ /* 事件绑定注释 */
                    try { /* 尝试绑定 */ /* 异常捕获注释 */
                        const voiceName = select.value; /* 取得选中的音色名字 */ /* 取值注释 */
                        await createOrUpdatePerson({ ...selectedPerson, default_voice_name: voiceName }); /* 静默递交后端绑定 */ /* 递交后端注释 */
                        log(`已将角色音色绑定为：${voiceName || '未绑定'}`, 'ok'); /* 日志通知成功 */ /* 日志注释 */
                    } catch (err) { /* 捕获异常 */ /* 异常处理注释 */
                        log(err.message, 'err'); /* 汇报错误 */ /* 日志注释 */
                        renderPeopleLibrary(); /* 失败后还原前端下拉框为正常绑定态 */ /* 联动渲染注释 */
                    } /* 捕获块结束 */ /* 逻辑注释 */
                }; /* 回调结束 */ /* 回调注释 */
            }); /* 下拉框监听挂接完成 */ /* 监听挂接注释 */
            el.querySelectorAll('.person-list-card, .asset-video-card, .person-detail-cover').forEach(card => { /* 挂载悬停懒加载视频预览 */ /* 循环注释 */
                const slot = card.querySelector('[data-video-preview-slot]'); /* 检索封面槽 */ /* DOM查询注释 */
                const src = slot?.dataset.videoSrc || ''; /* 获取真实视频地址 */ /* 数据注释 */
                if (!slot || !src) return; /* 没有视频则跳过 */ /* 判断注释 */
                card.addEventListener('mouseenter', () => { /* 鼠标滑过时事件 */ /* 监听挂载注释 */
                    const video = ensureHoverPreviewVideo(slot); /* 首次悬停才创建 video */ /* 懒加载注释 */
                    if (!video) return; /* 创建失败则跳过 */ /* 判断注释 */
                    slot.classList.add('previewing'); /* 显示懒加载视频 */ /* 状态注释 */
                    video.muted = true; /* 强制设为静音以规避浏览器策略限制 */ /* 强制静音注释 */
                    video.play().catch(() => {}); /* 触发自动播放 */ /* 自动播放注释 */
                }); /* 移入事件完成 */ /* 监听挂接注释 */
                card.addEventListener('mouseleave', () => { /* 鼠标移出事件 */ /* 监听挂载注释 */
                    const video = slot.querySelector('video[data-hover-preview]'); /* 查找已创建的视频 */ /* DOM查询注释 */
                    if (!video) return; /* 没有视频则跳过 */ /* 判断注释 */
                    video.pause(); /* 暂停播放 */ /* 暂停播放注释 */
                    video.currentTime = 0; /* 播放进度复位重置到首帧 */ /* 进度重置注释 */
                    slot.classList.remove('previewing'); /* 恢复封面显示 */ /* 状态注释 */
                }); /* 移出事件完成 */ /* 监听挂接注释 */
            }); /* 悬浮播放监听挂载完成 */ /* 监听挂接注释 */
        } /* 人物库渲染函数结束 */ /* 函数注释 */

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
            prepareVisibleVideoPosters();
        }

        function videoRowMarkup(person, video) { /* 生成每个视频卡片的 HTML 标记 */ /* 函数注释 */
            const url = mediaUrl(video); /* 获取预览视频链接地址 */ /* 链接注释 */
            const active = person.id === state.selectedPersonId && video.id === state.selectedVideoId; /* 校验是否正在使用当前视频生成 */ /* 状态校验注释 */
            return `
                <div class="asset-video-card ${active ? 'active' : ''}" data-video-id="${escapeHtml(video.id)}"> <!-- 视频卡片盒子 --> <!-- HTML注释 -->
                    <div class="asset-video-thumb"> <!-- 视频预览屏幕区 --> <!-- HTML注释 -->
                        ${url ? videoPreviewSlot(video, '暂无封面', person.id) : '<span class="status">无预览</span>'} <!-- 渲染封面图，悬停时再加载视频 --> <!-- HTML注释 -->
                        ${active ? '<span class="video-badge">当前使用</span>' : ''} <!-- 如果是活动视频则印章高亮徽章 --> <!-- HTML注释 -->
                    </div> <!-- 预览屏结束 --> <!-- HTML注释 -->
                    <div class="asset-video-info"> <!-- 卡片信息文本带 --> <!-- HTML注释 -->
                        <div class="asset-name">${escapeHtml(video.name || '驱动视频')}</div> <!-- 视频文件名 --> <!-- HTML注释 -->
                        <div class="asset-note">${active ? '当前生成视频' : '可切换使用'}</div> <!-- 指示说明语 --> <!-- HTML注释 -->
                    </div> <!-- 文本带结束 --> <!-- HTML注释 -->
                    <div class="asset-video-actions"> <!-- 视频操作按钮底槽 --> <!-- HTML注释 -->
                        <button class="btn small" type="button" data-video-action="use"> <!-- 使用本资源按钮 --> <!-- HTML注释 -->
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> <!-- 勾选小SVG --> <!-- HTML注释 -->
                            使用 <!-- 文字 --> <!-- HTML注释 -->
                        </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                        <button class="btn small" type="button" data-video-action="rename"> <!-- 修改视频名字按钮 --> <!-- HTML注释 -->
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4z"></path></svg> <!-- 修改画笔SVG --> <!-- HTML注释 -->
                            改名 <!-- 文字 --> <!-- HTML注释 -->
                        </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                        <button class="btn small" type="button" data-video-action="remove"> <!-- 移除该视频按钮 --> <!-- HTML注释 -->
                            <svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg> <!-- 垃圾桶SVG --> <!-- HTML注释 -->
                            移除 <!-- 文字 --> <!-- HTML注释 -->
                        </button> <!-- 按钮结束 --> <!-- HTML注释 -->
                    </div> <!-- 按钮槽结束 --> <!-- HTML注释 -->
                </div>`; /* 返回构建好的一整个视频卡片 */ /* 返回模版注释 */
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
            if (action === 'add-video') {
                state.selectedPersonId = personId;
                $('video-upload').click();
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
            prepareVisibleVideoPosters();
        }

        function prepareVisibleVideoPosters() {
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
                        setTimeout(prepareVisibleVideoPosters, 180);
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

        function waitForVideoEvent(video, names, timeout = 8000) {
            return new Promise(resolve => {
                let done = false;
                const cleanup = () => {
                    clearTimeout(timer);
                    names.forEach(name => {
                        video.removeEventListener(name, onEvent);
                    });
                };
                const finish = () => {
                    if (done) return;
                    done = true;
                    cleanup();
                    resolve();
                };
                const onEvent = () => finish();
                const timer = setTimeout(finish, timeout);
                names.forEach(name => {
                    video.addEventListener(name, onEvent, { once: true });
                });
            });
        }

        function waitForDecodedFrame(video) {
            return new Promise(resolve => {
                const finish = () => resolve();
                if (typeof video.requestVideoFrameCallback === 'function') {
                    video.requestVideoFrameCallback(finish);
                    setTimeout(finish, 1200);
                } else {
                    setTimeout(finish, 180);
                }
            });
        }

        function drawVideoPosterFrame(source) {
            const canvas = document.createElement('canvas');
            const maxSide = 720;
            const scale = Math.min(1, maxSide / Math.max(source.videoWidth, source.videoHeight));
            canvas.width = Math.max(1, Math.round(source.videoWidth * scale));
            canvas.height = Math.max(1, Math.round(source.videoHeight * scale));
            const ctx = canvas.getContext('2d');
            ctx.drawImage(source, 0, 0, canvas.width, canvas.height);

            const sample = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            let total = 0;
            let count = 0;
            const step = Math.max(4, Math.floor(sample.length / 1200 / 4) * 4);
            for (let i = 0; i < sample.length; i += step) {
                total += (sample[i] + sample[i + 1] + sample[i + 2]) / 3;
                count++;
            }
            const brightness = total / Math.max(1, count);
            return { dataUrl: canvas.toDataURL('image/jpeg', 0.78), brightness };
        }

        async function captureVideoPoster(video) {
            if (!video?.src) return;
            const source = document.createElement('video');
            Object.assign(source.style, {
                position: 'fixed',
                left: '-9999px',
                top: '-9999px',
                width: '1px',
                height: '1px',
                opacity: '0',
                pointerEvents: 'none',
            });
            source.crossOrigin = video.crossOrigin || '';
            source.muted = true;
            source.playsInline = true;
            source.preload = 'metadata';
            source.src = video.currentSrc || video.src;
            document.body.appendChild(source);
            try {
                source.load();
                await waitForVideoEvent(source, ['loadedmetadata', 'error']);
                if (!source.videoWidth || !source.videoHeight) return;
                applyVideoOrientation(video, source.videoWidth, source.videoHeight);
                const duration = Number.isFinite(source.duration) ? source.duration : 0;
                const candidates = [0.3, 1, 2].map(time => Math.min(time, Math.max(0, duration - 0.05)));
                let best = null;
                for (const time of candidates) {
                    if (time > 0.03) {
                        source.currentTime = time;
                        await waitForVideoEvent(source, ['seeked', 'canplay', 'error']);
                    } else {
                        await waitForVideoEvent(source, ['loadeddata', 'canplay', 'error'], 3000);
                    }
                    await waitForDecodedFrame(source);
                    const frame = drawVideoPosterFrame(source);
                    if (!best || frame.brightness > best.brightness) best = frame;
                    if (frame.brightness > 12) break;
                }
                if (best?.dataUrl) video.poster = best.dataUrl;
            } catch {
                // 同源截帧失败时保留浏览器 metadata 首帧。
            } finally {
                source.remove();
                source.removeAttribute('src');
                source.load();
            }
        }
