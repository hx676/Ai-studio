(function(){
    const KEY = 'studio_theme';
    const LEGACY_KEY = 'canvas_theme';

    function currentTheme(){
        return localStorage.getItem(KEY) || localStorage.getItem(LEGACY_KEY) || 'light';
    }

    function applyTheme(theme){
        const next = theme === 'dark' ? 'dark' : 'light';
        const dark = next === 'dark';
        document.documentElement.classList.toggle('studio-theme-dark', dark);
        document.documentElement.classList.toggle('theme-dark', dark);
        if(document.body){
            document.body.classList.toggle('studio-theme-dark', dark);
            document.body.classList.toggle('theme-dark', dark);
        }
        window.dispatchEvent(new CustomEvent('studio-theme-change', { detail: { theme: next } }));
    }

    window.StudioTheme = {
        key: KEY,
        get: currentTheme,
        apply: applyTheme,
        set(theme){
            const next = theme === 'dark' ? 'dark' : 'light';
            localStorage.setItem(KEY, next);
            localStorage.setItem(LEGACY_KEY, next);
            applyTheme(next);
        }
    };

    applyTheme(currentTheme());

    document.addEventListener('DOMContentLoaded', () => applyTheme(currentTheme()));
    window.addEventListener('message', event => {
        if(event.data?.type === 'studio-theme' || event.data?.type === 'theme-change') {
            applyTheme(event.data.theme);
        }
    });
    window.addEventListener('storage', event => {
        if(event.key === KEY || event.key === LEGACY_KEY) applyTheme(currentTheme());
    });

    const FOCUSABLE_SELECTOR = 'a[href],area[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),iframe,object,embed,[tabindex]:not([tabindex="-1"]),[contenteditable="true"]';
    let dialogId = 0;

    function ensureFeedbackStyles(){
        if(document.getElementById('studio-feedback-style-fallback')) return;
        const hasThemeStylesheet = Array.from(document.styleSheets).some(sheet => String(sheet.href || '').includes('/static/css/theme.css'));
        if(hasThemeStylesheet) return;
        const style = document.createElement('style');
        style.id = 'studio-feedback-style-fallback';
        style.textContent = '.studio-dialog-modal{position:fixed;inset:0;z-index:99999;display:grid;place-items:center;padding:24px;font-family:inherit}.studio-dialog-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.42);backdrop-filter:blur(10px)}.studio-dialog{position:relative;width:min(440px,calc(100vw - 32px));max-height:calc(100vh - 48px);overflow:hidden;display:flex;flex-direction:column;background:var(--panel,#fff);color:var(--text,var(--text-main,#0f172a));border:1px solid var(--line,rgba(148,163,184,.28));border-radius:16px;box-shadow:0 24px 80px rgba(15,23,42,.28)}.studio-theme-dark .studio-dialog,.theme-dark .studio-dialog{background:#111216;color:#e8e8ea;border-color:#25262b}.studio-dialog-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:20px 20px 8px}.studio-dialog-title{margin:0;font-size:16px;font-weight:900;line-height:1.35}.studio-dialog-close{border:0;background:transparent;color:inherit;font-size:22px;line-height:1;cursor:pointer;opacity:.62;padding:0 2px}.studio-dialog-body{padding:6px 20px 18px;overflow:auto;color:var(--muted,#475569);font-size:14px;line-height:1.7}.studio-theme-dark .studio-dialog-body,.theme-dark .studio-dialog-body{color:#9aa6b8}.studio-dialog-description{margin:0;white-space:pre-wrap}.studio-dialog-field{display:grid;gap:8px;margin-top:12px}.studio-dialog-label{color:var(--text,var(--text-main,#0f172a));font-size:11px;font-weight:900;letter-spacing:.08em;text-transform:uppercase}.studio-dialog-input{width:100%;min-height:42px;border:1px solid var(--line,rgba(148,163,184,.35));border-radius:10px;background:var(--soft,rgba(241,245,249,.72));color:var(--text,var(--text-main,#0f172a));padding:10px 12px;font:inherit;outline:none}.studio-dialog-field-error{min-height:18px;color:#be123c;font-size:12px;font-weight:800}.studio-dialog-details{max-height:180px;margin:0;overflow:auto;border:1px solid var(--line,rgba(148,163,184,.28));border-radius:12px;background:var(--soft,rgba(241,245,249,.72));color:var(--text,var(--text-main,#0f172a));padding:12px;font-size:12px;line-height:1.55;white-space:pre-wrap}.studio-dialog-foot{display:flex;justify-content:flex-end;gap:10px;padding:14px 20px 18px;border-top:1px solid var(--line,rgba(148,163,184,.18))}.studio-dialog-btn{border:1px solid var(--line,rgba(148,163,184,.35));background:transparent;color:inherit;border-radius:10px;padding:9px 16px;font-size:13px;font-weight:900;cursor:pointer}.studio-dialog-btn.primary{border-color:var(--accent,#0f172a);background:var(--accent,#0f172a);color:var(--panel,#fff)}.studio-dialog-btn.danger{border-color:#e11d48;background:#e11d48;color:#fff}.studio-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(12px);z-index:100000;display:flex;align-items:center;gap:12px;max-width:min(560px,calc(100vw - 32px));padding:11px 14px;border-radius:999px;background:#111827;color:#fff;font-size:13px;font-weight:900;box-shadow:0 16px 40px rgba(15,23,42,.28);opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease}.studio-toast.show{opacity:1;pointer-events:auto;transform:translateX(-50%) translateY(0)}.studio-toast-action{border:1px solid rgba(255,255,255,.45);border-radius:999px;background:rgba(255,255,255,.14);color:inherit;padding:5px 10px;font:inherit;font-size:12px;cursor:pointer}.studio-toast-success,.studio-toast.ok{background:#047857}.studio-toast-warning,.studio-toast.warn{background:#b45309}.studio-toast-error,.studio-toast.err{background:#be123c}';
        document.head.appendChild(style);
    }

    function normalizeDialogOptions(input, options = {}){
        if(input && typeof input === 'object') return {...options, ...input};
        return {...options, message: String(input ?? '')};
    }

    function normalizeSeverity(value){
        const severity = String(value || '').toLowerCase();
        if(severity === 'ok') return 'success';
        if(severity === 'warn') return 'warning';
        if(severity === 'err' || severity === 'danger') return 'error';
        if(['success', 'warning', 'error', 'info'].includes(severity)) return severity;
        return 'info';
    }

    function getFocusable(container){
        return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(el => {
            return !el.hasAttribute('disabled') && el.getAttribute('aria-hidden') !== 'true' && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        });
    }

    function restoreElementFocus(el){
        if(el && el.isConnected && typeof el.focus === 'function'){
            setTimeout(() => el.focus({preventScroll:true}), 0);
        }
    }

    function showStudioDialog(input, options = {}){
        const opts = normalizeDialogOptions(input, options);
        const type = opts.type || 'alert';
        const isConfirm = type === 'confirm';
        const isPrompt = type === 'prompt';
        const isError = type === 'error';
        return new Promise(resolve => {
            const message = opts.message || opts.description || '';
            if(!document.body){
                if(isConfirm) resolve(window.confirm(message));
                else if(isPrompt) resolve(window.prompt(opts.label || opts.title || 'Input', opts.value || ''));
                else resolve(undefined);
                return;
            }
            ensureFeedbackStyles();

            document.querySelectorAll('.studio-runtime-dialog').forEach(el => {
                el.remove();
            });
            const restoreFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            const ids = {
                title: `studio-dialog-title-${++dialogId}`,
                body: `studio-dialog-body-${dialogId}`,
                input: `studio-dialog-input-${dialogId}`,
                error: `studio-dialog-error-${dialogId}`,
                details: `studio-dialog-details-${dialogId}`
            };

            const modal = document.createElement('div');
            modal.className = 'studio-dialog-modal studio-runtime-dialog';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-labelledby', ids.title);
            modal.setAttribute('aria-describedby', ids.body);

            const backdrop = document.createElement('div');
            backdrop.className = 'studio-dialog-backdrop';
            modal.appendChild(backdrop);

            const dialog = document.createElement('form');
            dialog.className = `studio-dialog studio-dialog-${type}`;
            dialog.noValidate = true;
            modal.appendChild(dialog);

            const head = document.createElement('div');
            head.className = 'studio-dialog-head';
            const title = document.createElement('h2');
            title.className = 'studio-dialog-title';
            title.id = ids.title;
            title.textContent = opts.title || (isConfirm ? '请确认' : isPrompt ? '请输入' : isError ? '发生错误' : '提示');
            const closeBtn = document.createElement('button');
            closeBtn.className = 'studio-dialog-close';
            closeBtn.type = 'button';
            closeBtn.setAttribute('aria-label', opts.closeLabel || '关闭');
            closeBtn.innerHTML = '&times;';
            head.append(title, closeBtn);

            const body = document.createElement('div');
            body.className = 'studio-dialog-body';
            body.id = ids.body;

            if(message){
                const desc = document.createElement('p');
                desc.className = 'studio-dialog-description';
                desc.textContent = message;
                body.appendChild(desc);
            }

            let inputEl = null;
            let errorEl = null;
            if(isPrompt){
                const field = document.createElement('label');
                field.className = 'studio-dialog-field';
                const label = document.createElement('span');
                label.className = 'studio-dialog-label';
                label.textContent = opts.label || '';
                inputEl = document.createElement(opts.multiline ? 'textarea' : 'input');
                inputEl.id = ids.input;
                inputEl.className = 'studio-dialog-input';
                inputEl.value = opts.value ?? '';
                inputEl.placeholder = opts.placeholder || '';
                if(!opts.multiline) inputEl.type = 'text';
                if(opts.multiline && opts.rows) inputEl.rows = opts.rows;
                errorEl = document.createElement('div');
                errorEl.id = ids.error;
                errorEl.className = 'studio-dialog-field-error';
                errorEl.setAttribute('role', 'alert');
                inputEl.setAttribute('aria-describedby', `${ids.body} ${ids.error}`);
                field.append(label, inputEl, errorEl);
                body.appendChild(field);
            }

            if(isError && opts.details){
                const detailsWrap = document.createElement('div');
                detailsWrap.className = 'studio-dialog-details-wrap';
                const detailsLabel = document.createElement('div');
                detailsLabel.className = 'studio-dialog-label';
                detailsLabel.textContent = opts.detailsLabel || '技术详情';
                const details = document.createElement('pre');
                details.id = ids.details;
                details.className = 'studio-dialog-details';
                details.textContent = String(opts.details);
                const copyBtn = document.createElement('button');
                copyBtn.className = 'studio-dialog-btn secondary';
                copyBtn.type = 'button';
                copyBtn.textContent = opts.copyText || '复制详情';
                copyBtn.addEventListener('click', async () => {
                    try {
                        await navigator.clipboard.writeText(String(opts.details));
                        showStudioToast({message: opts.copiedText || '详情已复制', severity:'success'});
                    } catch {
                        showStudioToast({message: opts.copyFailedText || '复制失败', severity:'error'});
                    }
                });
                detailsWrap.append(detailsLabel, details, copyBtn);
                body.appendChild(detailsWrap);
                modal.setAttribute('aria-describedby', `${ids.body} ${ids.details}`);
            }

            const foot = document.createElement('div');
            foot.className = 'studio-dialog-foot';
            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'studio-dialog-btn secondary';
            cancelBtn.type = 'button';
            cancelBtn.textContent = opts.cancelText || '取消';
            const confirmBtn = document.createElement('button');
            confirmBtn.className = `studio-dialog-btn ${opts.danger || isError ? 'danger' : 'primary'}`.trim();
            confirmBtn.type = 'submit';
            confirmBtn.textContent = opts.confirmText || (isError ? '知道了' : '确定');
            if(isConfirm || isPrompt) foot.appendChild(cancelBtn);
            foot.appendChild(confirmBtn);
            dialog.append(head, body, foot);

            let settled = false;
            const cleanup = value => {
                if(settled) return;
                settled = true;
                document.removeEventListener('keydown', onKeyDown, true);
                modal.remove();
                restoreElementFocus(restoreFocus);
                resolve(value);
            };
            const cancel = () => cleanup(isConfirm ? false : isPrompt ? null : undefined);
            const validatePrompt = () => {
                if(!isPrompt || !inputEl) return true;
                const value = inputEl.value;
                let validation = true;
                if(typeof opts.validate === 'function') validation = opts.validate(value);
                if(validation === true || validation === undefined || validation === null || validation === ''){
                    inputEl.removeAttribute('aria-invalid');
                    if(errorEl) errorEl.textContent = '';
                    return true;
                }
                const messageText = typeof validation === 'string' ? validation : opts.errorText || '请检查输入内容';
                inputEl.setAttribute('aria-invalid', 'true');
                if(errorEl) errorEl.textContent = messageText;
                inputEl.focus();
                return false;
            };
            const onKeyDown = event => {
                if(event.key === 'Escape'){
                    event.preventDefault();
                    cancel();
                    return;
                }
                if(event.key !== 'Tab') return;
                const focusable = getFocusable(dialog);
                if(!focusable.length){
                    event.preventDefault();
                    dialog.focus();
                    return;
                }
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if(event.shiftKey && document.activeElement === first){
                    event.preventDefault();
                    last.focus();
                } else if(!event.shiftKey && document.activeElement === last){
                    event.preventDefault();
                    first.focus();
                }
            };

            backdrop.addEventListener('click', cancel);
            closeBtn.addEventListener('click', cancel);
            cancelBtn.addEventListener('click', cancel);
            inputEl?.addEventListener('input', () => {
                if(inputEl.getAttribute('aria-invalid') === 'true') validatePrompt();
            });
            dialog.addEventListener('submit', event => {
                event.preventDefault();
                if(isPrompt){
                    if(validatePrompt()) cleanup(inputEl.value);
                    return;
                }
                cleanup(isConfirm ? true : undefined);
            });
            document.addEventListener('keydown', onKeyDown, true);
            document.body.appendChild(modal);
            const focusTarget = inputEl || confirmBtn;
            setTimeout(() => {
                focusTarget.focus();
                if(focusTarget.select && !opts.multiline) focusTarget.select();
            }, 0);
        });
    }

    function normalizeToastOptions(message, kind = ''){
        if(message && typeof message === 'object'){
            return {
                message: String(message.message ?? message.text ?? ''),
                severity: normalizeSeverity(message.severity || message.kind || kind),
                action: message.action,
                duration: Number(message.duration || 0) || undefined
            };
        }
        return {message: String(message || ''), severity: normalizeSeverity(kind), action: null, duration: undefined};
    }

    function showStudioToast(message, kind = ''){
        if(!document.body) return;
        ensureFeedbackStyles();
        const opts = normalizeToastOptions(message, kind);
        if(!opts.message) return;
        let toast = document.getElementById('studio-toast');
        if(!toast){
            toast = document.createElement('div');
            toast.id = 'studio-toast';
            toast.className = 'studio-toast';
            toast.setAttribute('role', 'status');
            toast.setAttribute('aria-live', 'polite');
            toast.setAttribute('aria-atomic', 'true');
            document.body.appendChild(toast);
        }
        clearTimeout(toast._timer);
        toast.className = `studio-toast studio-toast-${opts.severity}`;
        toast.setAttribute('role', opts.severity === 'error' || opts.severity === 'warning' ? 'alert' : 'status');
        toast.setAttribute('aria-live', opts.severity === 'error' || opts.severity === 'warning' ? 'assertive' : 'polite');
        toast.textContent = '';
        const text = document.createElement('span');
        text.className = 'studio-toast-message';
        text.textContent = opts.message;
        toast.appendChild(text);
        if(opts.action && opts.action.label){
            const actionBtn = document.createElement('button');
            actionBtn.className = 'studio-toast-action';
            actionBtn.type = 'button';
            actionBtn.textContent = opts.action.label;
            actionBtn.addEventListener('click', event => {
                event.preventDefault();
                if(typeof opts.action.onClick === 'function') opts.action.onClick(event);
                toast.classList.remove('show');
            });
            toast.appendChild(actionBtn);
        }
        requestAnimationFrame(() => toast.classList.add('show'));
        const duration = opts.duration ?? (opts.severity === 'error' ? 4200 : opts.action ? 5200 : 2600);
        toast._timer = setTimeout(() => toast.classList.remove('show'), duration);
    }

    window.StudioDialog = {
        alert(message, options = {}){
            return showStudioDialog(message, {...options, type:'alert'});
        },
        confirm(message, options = {}){
            return showStudioDialog(message, {...options, type:'confirm'});
        },
        toast: showStudioToast,
        formPrompt(options = {}){
            return showStudioDialog(options, {type:'prompt'});
        },
        error(options = {}){
            return showStudioDialog(options, {type:'error'});
        }
    };
})();
