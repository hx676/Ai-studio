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

    function ensureDialogStyles(){
        if(document.getElementById('studio-dialog-style')) return;
        const style = document.createElement('style');
        style.id = 'studio-dialog-style';
        style.textContent = `
            .studio-dialog-modal{position:fixed;inset:0;z-index:99999;display:grid;place-items:center;padding:24px;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
            .studio-dialog-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.42);backdrop-filter:blur(8px)}
            .studio-dialog{position:relative;width:min(420px,calc(100vw - 32px));background:var(--panel,#fff);color:var(--text,#0f172a);border:1px solid rgba(148,163,184,.28);box-shadow:0 24px 80px rgba(15,23,42,.28);border-radius:14px;overflow:hidden}
            .studio-theme-dark .studio-dialog,.theme-dark .studio-dialog{background:#111827;color:#f8fafc;border-color:rgba(148,163,184,.22)}
            .studio-dialog-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 20px 10px}
            .studio-dialog-title{font-size:16px;font-weight:800;line-height:1.35}
            .studio-dialog-close{border:0;background:transparent;color:inherit;font-size:22px;line-height:1;cursor:pointer;opacity:.62;padding:0}
            .studio-dialog-close:hover{opacity:1}
            .studio-dialog-body{padding:4px 20px 18px;font-size:14px;line-height:1.7;color:var(--muted,#475569);white-space:pre-wrap}
            .studio-theme-dark .studio-dialog-body,.theme-dark .studio-dialog-body{color:#cbd5e1}
            .studio-dialog-foot{display:flex;justify-content:flex-end;gap:10px;padding:14px 20px 18px;border-top:1px solid rgba(148,163,184,.18)}
            .studio-dialog-btn{border:1px solid rgba(148,163,184,.35);background:transparent;color:inherit;border-radius:8px;padding:9px 16px;font-size:13px;font-weight:800;cursor:pointer}
            .studio-dialog-btn.primary{border-color:#2563eb;background:#2563eb;color:#fff}
            .studio-dialog-btn.danger{border-color:#e11d48;background:#e11d48;color:#fff}
            .studio-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(12px);z-index:100000;max-width:min(520px,calc(100vw - 32px));padding:10px 16px;border-radius:999px;background:#111827;color:#fff;font-size:13px;font-weight:800;box-shadow:0 16px 40px rgba(15,23,42,.28);opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease}
            .studio-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
            .studio-toast.ok{background:#047857}.studio-toast.warn{background:#b45309}.studio-toast.err{background:#be123c}
        `;
        document.head.appendChild(style);
    }

    function normalizeDialogOptions(input, options = {}){
        if(input && typeof input === 'object') return {...input};
        return {...options, message: String(input ?? '')};
    }

    function showStudioDialog(input, options = {}){
        const opts = normalizeDialogOptions(input, options);
        const isConfirm = opts.type === 'confirm';
        return new Promise(resolve => {
            if(!document.body){
                resolve(isConfirm ? window.confirm(opts.message || '') : undefined);
                return;
            }
            ensureDialogStyles();
            const modal = document.createElement('div');
            modal.className = 'studio-dialog-modal';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            const confirmClass = opts.danger ? 'danger' : 'primary';
            modal.innerHTML = `
                <div class="studio-dialog-backdrop" data-studio-dialog-cancel></div>
                <section class="studio-dialog">
                    <div class="studio-dialog-head">
                        <div class="studio-dialog-title">${escapeDialogHtml(opts.title || (isConfirm ? '请确认' : '提示'))}</div>
                        <button class="studio-dialog-close" type="button" aria-label="关闭" data-studio-dialog-cancel>&times;</button>
                    </div>
                    <div class="studio-dialog-body">${escapeDialogHtml(opts.message || opts.description || '')}</div>
                    <div class="studio-dialog-foot">
                        ${isConfirm ? `<button class="studio-dialog-btn" type="button" data-studio-dialog-cancel>${escapeDialogHtml(opts.cancelText || '取消')}</button>` : ''}
                        <button class="studio-dialog-btn ${confirmClass}" type="button" data-studio-dialog-ok>${escapeDialogHtml(opts.confirmText || '确定')}</button>
                    </div>
                </section>
            `;
            const cleanup = value => {
                modal.remove();
                document.removeEventListener('keydown', onKey);
                resolve(value);
            };
            const onKey = event => {
                if(event.key === 'Escape') cleanup(isConfirm ? false : undefined);
            };
            modal.querySelectorAll('[data-studio-dialog-cancel]').forEach(el => {
                el.addEventListener('click', () => cleanup(isConfirm ? false : undefined));
            });
            modal.querySelector('[data-studio-dialog-ok]')?.addEventListener('click', () => cleanup(isConfirm ? true : undefined));
            document.addEventListener('keydown', onKey);
            document.body.appendChild(modal);
            modal.querySelector('[data-studio-dialog-ok]')?.focus();
        });
    }

    function escapeDialogHtml(value){
        return String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[s]));
    }

    function showStudioToast(message, kind = ''){
        if(!document.body) return;
        ensureDialogStyles();
        let toast = document.getElementById('studio-toast');
        if(!toast){
            toast = document.createElement('div');
            toast.id = 'studio-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = String(message || '');
        toast.className = `studio-toast show ${kind}`.trim();
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => toast.classList.remove('show'), 2200);
    }

    window.StudioDialog = {
        alert(message, options = {}){
            return showStudioDialog(message, {...options, type:'alert'});
        },
        confirm(message, options = {}){
            return showStudioDialog(message, {...options, type:'confirm'});
        },
        toast: showStudioToast
    };
})();
