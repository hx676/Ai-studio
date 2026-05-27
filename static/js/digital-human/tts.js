// TTS controls and option persistence
// Split from state.js. Loaded as a classic script; shared symbols remain global.

const TTS_OPTIONS_STORAGE_KEY = 'digital_human_tts_options_v1';
        const TTS_DEFAULT_OPTIONS = {
            speed: 1,
            emo_control_method: '与音色参考音频相同',
            emo_ref_url: '',
            emo_ref_path: '',
            emo_weight: 0.8,
            emo_text: '',
            emo_random: false,
            max_tokens: 120,
            vec1: 0,
            vec2: 0,
            vec3: 0,
            vec4: 0,
            vec5: 0,
            vec6: 0,
            vec7: 0,
            vec8: 0,
            do_sample: true,
            top_p: 0.8,
            top_k: 30,
            temperature: 0.8,
            length_penalty: 0,
            num_beams: 3,
            repetition_penalty: 10,
            max_mel: 1500,
        };
        const EMOTION_VECTORS = [
            ['vec1', '喜'],
            ['vec2', '怒'],
            ['vec3', '哀'],
            ['vec4', '惧'],
            ['vec5', '厌恶'],
            ['vec6', '低落'],
            ['vec7', '惊喜'],
            ['vec8', '平静'],
        ];
        const ADVANCED_FIELDS = [
            ['max_tokens', '分句最大Token数', 20, 600, 1],
            ['top_p', 'top_p', 0, 1, 0.05],
            ['top_k', 'top_k', 0, 100, 1],
            ['temperature', 'temperature', 0.1, 2, 0.1],
            ['length_penalty', 'length_penalty', -10, 10, 0.1],
            ['num_beams', 'num_beams', 1, 10, 1],
            ['repetition_penalty', 'repetition_penalty', 0, 20, 0.1],
            ['max_mel', 'max_mel_tokens', 50, 1815, 1],
        ];
        function loadTtsOptions() {
            try {
                return { ...TTS_DEFAULT_OPTIONS, ...(JSON.parse(localStorage.getItem(TTS_OPTIONS_STORAGE_KEY) || '{}') || {}) };
            } catch {
                return { ...TTS_DEFAULT_OPTIONS };
            }
        }

        function saveTtsOptions() {
            localStorage.setItem(TTS_OPTIONS_STORAGE_KEY, JSON.stringify(collectTtsOptions(false)));
        }

        function sliderNumberPair(rangeId, numberId, fallback, min, max) {
            const range = $(rangeId);
            const number = $(numberId);
            const sync = (fromRange) => {
                const value = clampNumber(fromRange ? range.value : number.value, fallback, min, max);
                range.value = String(value);
                number.value = String(value);
                saveTtsOptions();
            };
            range.oninput = () => sync(true);
            number.oninput = () => sync(false);
        }

        function renderTtsOptionControls() {
            $('emotion-vector-grid').innerHTML = EMOTION_VECTORS.map(([id, label]) => `
                <div class="field">
                    <label for="tts-${id}">${label}</label>
                    <input id="tts-${id}" type="range" min="0" max="1.4" step="0.1" value="0">
                </div>
            `).join('');
            $('tts-advanced-grid').innerHTML = ADVANCED_FIELDS.map(([id, label, min, max, step]) => `
                <div class="field">
                    <label for="tts-${id}">${label}</label>
                    <input id="tts-${id}" type="number" min="${min}" max="${max}" step="${step}" value="${TTS_DEFAULT_OPTIONS[id]}">
                </div>
            `).join('');
        }

        function applyTtsOptionsToUI(options) {
            const opts = { ...TTS_DEFAULT_OPTIONS, ...(options || {}) };
            $('tts-speed').value = opts.speed;
            $('tts-speed-value').value = opts.speed;
            $('tts-emo-method').value = opts.emo_control_method;
            $('tts-emo-weight').value = opts.emo_weight;
            $('tts-emo-weight-value').value = opts.emo_weight;
            $('tts-emo-text').value = opts.emo_text || '';
            $('tts-emo-random').checked = !!opts.emo_random;
            $('tts-do-sample').checked = opts.do_sample !== false;
            EMOTION_VECTORS.forEach(([id]) => {
                const el = $(`tts-${id}`);
                if (el) el.value = opts[id] ?? 0;
            });
            ADVANCED_FIELDS.forEach(([id]) => {
                const el = $(`tts-${id}`);
                if (el) el.value = opts[id] ?? TTS_DEFAULT_OPTIONS[id];
            });
            updateEmotionPanels();
        }

        function collectTtsOptions(validate = true) {
            const method = $('tts-emo-method').value || TTS_DEFAULT_OPTIONS.emo_control_method;
            const opts = {
                speed: clampNumber($('tts-speed-value').value, 1, 0.1, 2.5),
                emo_control_method: method,
                emo_ref_url: state.uploadedEmotionAudio?.url || '',
                emo_ref_path: state.uploadedEmotionAudio?.path || '',
                emo_weight: clampNumber($('tts-emo-weight-value').value, 0.8, 0, 1.6),
                emo_text: $('tts-emo-text').value.trim(),
                emo_random: $('tts-emo-random').checked,
                do_sample: $('tts-do-sample').checked,
            };
            EMOTION_VECTORS.forEach(([id]) => {
                opts[id] = clampNumber($(`tts-${id}`)?.value, 0, 0, 1.4);
            });
            ADVANCED_FIELDS.forEach(([id, , min, max]) => {
                opts[id] = clampNumber($(`tts-${id}`)?.value, TTS_DEFAULT_OPTIONS[id], min, max);
            });
            if (validate && method === '使用情感参考音频' && !opts.emo_ref_url && !opts.emo_ref_path) {
                throw new Error('请先上传情感参考音频');
            }
            if (validate && method === '使用情感描述文本控制' && !opts.emo_text) {
                throw new Error('请填写情感描述文本');
            }
            return opts;
        }
        window.collectDigitalHumanTtsOptions = collectTtsOptions;

        function updateEmotionPanels() {
            const method = $('tts-emo-method')?.value || '';
            $('emo-ref-panel').classList.toggle('show', method === '使用情感参考音频');
            $('emo-text-panel').classList.toggle('show', method === '使用情感描述文本控制');
            $('emo-vector-panel').classList.toggle('show', method === '使用情感向量控制');
            $('emo-ref-summary').textContent = state.uploadedEmotionAudio?.name || '未选择';
        }

        function bindTtsControls() {
            renderTtsOptionControls();
            applyTtsOptionsToUI(loadTtsOptions());
            sliderNumberPair('tts-speed', 'tts-speed-value', 1, 0.1, 2.5);
            sliderNumberPair('tts-emo-weight', 'tts-emo-weight-value', 0.8, 0, 1.6);
            [
                'tts-emo-method', 'tts-emo-text', 'tts-emo-random', 'tts-do-sample',
                ...EMOTION_VECTORS.map(([id]) => `tts-${id}`),
                ...ADVANCED_FIELDS.map(([id]) => `tts-${id}`),
            ].forEach(id => {
                const el = $(id);
                if (!el) return;
                el.oninput = () => {
                    updateEmotionPanels();
                    saveTtsOptions();
                };
                el.onchange = el.oninput;
            });
            $('tts-advanced-toggle').onclick = () => {
                const panel = $('tts-advanced');
                panel.classList.toggle('open');
                $('tts-advanced-indicator').textContent = panel.classList.contains('open') ? '收起' : '展开';
            };
            $('tts-reset-btn').onclick = () => {
                state.uploadedEmotionAudio = null;
                applyTtsOptionsToUI(TTS_DEFAULT_OPTIONS);
                saveTtsOptions();
                log('语音参数已恢复默认', 'ok');
            };
            $('emo-ref-upload-btn').onclick = () => $('emo-ref-upload').click();
            $('emo-ref-upload').onchange = e => handleUpload(e.target.files?.[0], 'emotion')
                .catch(err => log(err.message, 'err'))
                .finally(() => { e.target.value = ''; });
        }

