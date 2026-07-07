        const state = {
            config: null,
            library: null,
            people: [],
            voices: [],
            videos: [],
            uploadedVoice: null,
            uploadedEmotionAudio: null,
            uploadedVideo: null,
            selectedPersonId: '',
            selectedVideoId: '',
            selectedVideoPath: '',
            selectedVideoSource: '',
            activeTab: 'generate',
            taskId: '',
            tasks: [],
            queueState: null,
            selectedTaskId: '',
            lastBackendProgress: '',
            ttsStatus: null,
            heygemStatus: null,
            pollTimer: null,
            pollToken: 0
        };
        const $ = (id) => document.getElementById(id);
        function tr(key){ return window.StudioI18n ? StudioI18n.t(key) : key; }
        const SERVICE_START_HINT_KEY = 'dh.serviceStartHint';
        const SERVICE_START_HINT = tr(SERVICE_START_HINT_KEY);
        // Listen for language changes
        window.addEventListener('studio-lang-change', () => {
            // Re-apply any dynamic text that needs updating
        });
const STAGE_TEXT = {
            queued: '排队中',
            tts: '准备口播',
            'waiting-tts-light': '等待 TTS 绿灯',
            'waiting-heygem-light': '等待 HeyGem 绿灯',
            'tts-submit': '生成口播',
            'gpu-handoff': '释放 TTS 显存',
            'heygem-submit': '载入素材',
            'heygem-generate': '合成视频',
            'heygem-retry': '重试合成',
            done: '完成',
            failed: '失败',
            running: '处理中',
            pending: '排队中',
        };

        function escapeHtml(value) {
            return String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[s]));
        }
        function mediaUrl(item) {
            return item?.preview_url || item?.url || '';
        }
        function responseDetailText(detail) {
            if (!detail) return '';
            if (typeof detail === 'string') return detail;
            if (typeof detail === 'object') {
                const direct = detail.message || detail.detail || detail.error || detail.last_error || detail.reason;
                if (direct) return responseDetailText(direct);
                try {
                    return JSON.stringify(detail);
                } catch {
                    return '';
                }
            }
            return String(detail);
        }
        function userFacingError(detail, fallback = '操作失败') {
            if (detail && typeof detail === 'object' && detail.failure_type === 'tts_timeout') {
                return 'TTS 生成耗时超过等待窗口，服务通常仍在继续生成音频；请稍后查看队列或缩短文案重试。';
            }
            if (detail && typeof detail === 'object' && detail.failure_type === 'heygem_queue_blocked') {
                return 'HeyGem 内部队列阻塞，通常是显存资源冲突、残留进程或驱动视频过重导致。请停止残留 HeyGem/TTS 后重试。';
            }
            if (detail && typeof detail === 'object' && detail.failure_type === 'heygem_stall_at_20') {
                return 'HeyGem 卡在 20%，视频驱动队列没有继续推进。请清理残留进程或换一个更短、分辨率更低的动作视频后重试。';
            }
            if (detail && typeof detail === 'object' && detail.failure_type === 'heygem_task_not_found') {
                return 'HeyGem 任务提交后丢失，可能是 HeyGem 内部队列被旧任务干扰。请清理/重启 HeyGem 后重试。';
            }
            if (detail && typeof detail === 'object' && detail.failure_type === 'heygem_gpu_conflict') {
                return '检测到 HeyGem/TTS 显存资源冲突或残留进程，已暂停队列。请停止残留进程或重启启动器后重试。';
            }
            if (detail && typeof detail === 'object' && detail.failure_type === 'tts_output_write_failed') {
                return responseDetailText(detail).trim() || 'TTS 音频已生成，但保存到本地输出目录失败。请检查文件名和输出目录权限后重试。';
            }
            if (detail && typeof detail === 'object' && detail.failure_type === 'tts_failed') {
                return responseDetailText(detail).trim() || 'TTS 生成失败，请检查文案和参考音频。';
            }
            const raw = responseDetailText(detail).trim();
            if (!raw) return fallback;
            const lower = raw.toLowerCase();
            if (lower.includes('timeout') || lower.includes('timed out') || raw.includes('超时')) {
                return '生成等待超时，请确认后台仍在运行。';
            }
            if (
                lower.includes('connection refused') ||
                lower.includes('connecterror') ||
                lower.includes('connection failed') ||
                lower.includes('failed to connect') ||
                lower.includes('max retries') ||
                lower.includes('httpconnectionpool') ||
                lower.includes('networkerror') ||
                lower.includes('fetch failed') ||
                lower.includes('bad gateway') ||
                lower.includes('502') ||
                lower.includes('503') ||
                lower.includes('proxy') ||
                lower.includes('not ready') ||
                lower.includes('did not become ready')
            ) {
                return `数字人后台未就绪，${SERVICE_START_HINT}`;
            }
            if (lower.includes('heygem service is not ready')) {
                return `HeyGem 未就绪，${SERVICE_START_HINT}`;
            }
            if (lower.includes('tts service is not ready')) {
                return `TTS 未就绪，${SERVICE_START_HINT}`;
            }
            if (
                lower.includes('traceback') ||
                lower.includes('exception') ||
                lower.includes('errno') ||
                lower.includes('winerror') ||
                lower.includes('readtimedout') ||
                lower.includes('httpx') ||
                lower.includes('requests.') ||
                lower.includes('gradio_client')
            ) {
                return fallback;
            }
            return raw.length > 140 ? `${raw.slice(0, 140)}...` : raw;
        }
        async function readJsonBody(res) {
            return await res.json().catch(() => null);
        }
        async function readJsonResponse(res, fallback = '操作失败') {
            const data = await readJsonBody(res);
            if (!res.ok) {
                throw new Error(userFacingError(data?.detail ?? data?.message ?? data ?? res.statusText, fallback));
            }
            if (data === null) throw new Error('后端返回格式异常，请刷新后重试。');
            return data;
        }
        async function requestJson(url, options = {}, fallback = '操作失败') {
            try {
                const res = await fetch(url, options);
                return await readJsonResponse(res, fallback);
            } catch (err) {
                if (err instanceof TypeError) throw new Error(`数字人后台未就绪，${SERVICE_START_HINT}`);
                throw new Error(userFacingError(err.message, fallback));
            }
        }
        function postJson(url, payload, method = 'POST') {
            return requestJson(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload || {}),
            });
        }
        function stageText(stage) {
            return STAGE_TEXT[stage] || stage || '处理中';
        }
        function log(message, kind = '') {
            showToast(message, kind);
        }
        function setStatus(text, kind = '') {
            $('task-status').textContent = text;
            $('task-status').className = `status ${kind}`.trim();
        }
        function ensureToast() {
            let el = $('dh-toast');
            if (!el) {
                el = document.createElement('div');
                el.id = 'dh-toast';
                el.className = 'toast';
                document.body.appendChild(el);
            }
            return el;
        }
        function showToast(message, kind = '') {
            const text = String(message || '').trim();
            if (!text) return;
            const el = ensureToast();
            clearTimeout(el._timer);
            el.textContent = text;
            el.className = `toast show ${kind}`.trim();
            el._timer = setTimeout(() => {
                el.classList.remove('show');
            }, kind === 'err' ? 4200 : 2600);
        }
        function showFormDialog(options = {}) {
            return showDigitalHumanDialog({ ...options, type: 'form' });
        }
        function showConfirmDialog(options = {}) {
            return showDigitalHumanDialog({ ...options, type: 'confirm' });
        }
        function showDigitalHumanDialog(options = {}) {
            const {
                title = '提示',
                description = '',
                fields = [],
                choices = [],
                defaultChoice = choices[0]?.value || '',
                confirmText = '确定',
                cancelText = '取消',
                danger = false,
                width = 'normal',
                type = 'confirm',
            } = options;
            const restoreFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            return new Promise(resolve => {
                const existing = document.querySelector('.dh-runtime-dialog');
                if (existing) existing.remove();

                const modal = document.createElement('div');
                modal.className = `dh-dialog-modal dh-runtime-dialog ${width === 'wide' ? 'wide' : ''}`.trim();
                modal.setAttribute('role', 'dialog');
                modal.setAttribute('aria-modal', 'true');

                const backdrop = document.createElement('div');
                backdrop.className = 'dh-dialog-backdrop';
                modal.appendChild(backdrop);

                const dialog = document.createElement('form');
                dialog.className = 'dh-dialog';
                dialog.noValidate = true;
                modal.appendChild(dialog);

                const head = document.createElement('div');
                head.className = 'dh-dialog-head';
                const heading = document.createElement('h2');
                heading.textContent = title;
                const closeBtn = document.createElement('button');
                closeBtn.className = 'dh-dialog-close';
                closeBtn.type = 'button';
                closeBtn.setAttribute('aria-label', '关闭');
                closeBtn.innerHTML = '&times;';
                head.append(heading, closeBtn);

                const body = document.createElement('div');
                body.className = 'dh-dialog-body';
                if (description) {
                    const desc = document.createElement('p');
                    desc.className = 'dh-dialog-description';
                    desc.textContent = description;
                    body.appendChild(desc);
                }

                const controls = [];
                fields.forEach(field => {
                    const wrap = document.createElement('label');
                    wrap.className = 'dh-dialog-field';
                    const label = document.createElement('span');
                    label.className = 'dh-dialog-label';
                    label.textContent = field.label || field.name || '';
                    const control = field.type === 'textarea' ? document.createElement('textarea') : document.createElement('input');
                    control.name = field.name || '';
                    control.value = field.value ?? '';
                    control.placeholder = field.placeholder || '';
                    if (field.type && field.type !== 'textarea') control.type = field.type;
                    if (field.rows && control.tagName === 'TEXTAREA') control.rows = field.rows;
                    if (field.required) control.dataset.required = 'true';
                    const error = document.createElement('div');
                    error.className = 'dh-dialog-error';
                    error.textContent = field.error || '请填写此项';
                    wrap.append(label, control, error);
                    body.appendChild(wrap);
                    controls.push({ field, control, wrap });
                });

                let choiceInputs = [];
                if (choices.length) {
                    const list = document.createElement('div');
                    list.className = 'dh-dialog-choice-list';
                    list.setAttribute('role', 'radiogroup');
                    choices.forEach((choice, index) => {
                        const item = document.createElement('label');
                        item.className = 'dh-dialog-choice';
                        const input = document.createElement('input');
                        input.type = 'radio';
                        input.name = 'dh-dialog-choice';
                        input.value = choice.value;
                        input.checked = choice.value === defaultChoice || (!defaultChoice && index === 0);
                        const copy = document.createElement('span');
                        const titleEl = document.createElement('strong');
                        titleEl.textContent = choice.label || choice.value;
                        copy.appendChild(titleEl);
                        if (choice.description) {
                            const note = document.createElement('small');
                            note.textContent = choice.description;
                            copy.appendChild(note);
                        }
                        item.append(input, copy);
                        list.appendChild(item);
                        choiceInputs.push(input);
                    });
                    body.appendChild(list);
                }

                const foot = document.createElement('div');
                foot.className = 'dh-dialog-foot';
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'btn';
                cancelBtn.type = 'button';
                cancelBtn.textContent = cancelText;
                const confirmBtn = document.createElement('button');
                confirmBtn.className = `btn ${danger ? 'danger' : 'primary'}`.trim();
                confirmBtn.type = 'submit';
                confirmBtn.textContent = confirmText;
                foot.append(cancelBtn, confirmBtn);

                dialog.append(head, body, foot);

                let settled = false;
                const cleanup = (result) => {
                    if (settled) return;
                    settled = true;
                    document.removeEventListener('keydown', onKeyDown);
                    modal.remove();
                    if (restoreFocus?.isConnected) restoreFocus.focus();
                    resolve(result);
                };
                const cancel = () => cleanup(type === 'form' ? null : false);
                const onKeyDown = (event) => {
                    if (event.key === 'Escape') {
                        event.preventDefault();
                        cancel();
                    }
                };
                backdrop.addEventListener('click', cancel);
                closeBtn.addEventListener('click', cancel);
                cancelBtn.addEventListener('click', cancel);
                document.addEventListener('keydown', onKeyDown);

                dialog.addEventListener('submit', (event) => {
                    event.preventDefault();
                    let invalid = null;
                    controls.forEach(({ control, wrap }) => {
                        const isInvalid = control.dataset.required === 'true' && !control.value.trim();
                        wrap.classList.toggle('invalid', isInvalid);
                        if (isInvalid && !invalid) invalid = control;
                    });
                    if (invalid) {
                        invalid.focus();
                        return;
                    }
                    if (type === 'form') {
                        const values = {};
                        controls.forEach(({ field, control }) => {
                            values[field.name] = control.value;
                        });
                        cleanup(values);
                        return;
                    }
                    if (choiceInputs.length) {
                        cleanup(choiceInputs.find(input => input.checked)?.value || '');
                        return;
                    }
                    cleanup(true);
                });

                document.body.appendChild(modal);
                const focusTarget = controls[0]?.control || choiceInputs.find(input => input.checked) || confirmBtn;
                setTimeout(() => {
                    focusTarget?.focus();
                    if (focusTarget?.select && focusTarget.tagName !== 'TEXTAREA') focusTarget.select();
                }, 0);
            });
        }
        function switchTab(name) {
            const target = name || 'generate';
            if (!['generate', 'people', 'voices'].includes(target)) return;
            state.activeTab = target;
            document.body.classList.toggle('library-mode', target !== 'generate');
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === target);
            });
            document.querySelectorAll('.tab-page').forEach(page => {
                page.classList.toggle('active', page.id === `tab-${target}`);
            });
            prepareVideoPosters();
        }
        function backendProgressText(progress) {
            if (!progress) return '';
            const parts = [];
            if (progress.progress !== null && progress.progress !== undefined && progress.progress !== '') {
                parts.push(`${progress.progress}%`);
            }
            if (progress.message) parts.push(progress.message);
            if (!parts.length && progress.poll_status) parts.push(`状态 ${progress.poll_status}`);
            return parts.join(' · ');
        }

        function clampNumber(value, fallback, min, max) {
            const num = Number(value);
            if (!Number.isFinite(num)) return fallback;
            return Math.max(min, Math.min(max, num));
        }
