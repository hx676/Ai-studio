(function(){
    const SECTIONS = ['api', 'agents', 'comfyui', 'extensions', 'node-engine'];
    const SECTION_KEY = 'studio_settings_section';
    let currentSection = 'api';

    function normalizeSection(value){
        const section = String(value || '').trim().toLowerCase();
        return SECTIONS.includes(section) ? section : 'api';
    }

    function frameFor(section){
        return document.getElementById(`settings-frame-${section}`);
    }

    function sendFrameState(frame){
        if(!frame || !frame.src) return;
        const theme = window.StudioTheme ? window.StudioTheme.get() : 'light';
        const lang = window.StudioI18n ? window.StudioI18n.lang() : 'zh';
        try {
            frame.contentWindow?.postMessage({type:'studio-theme', theme}, '*');
            frame.contentWindow?.postMessage({type:'studio-lang', lang}, '*');
        } catch(e) {}
    }

    function ensureFrame(section){
        const frame = frameFor(section);
        if(!frame) return null;
        if(!frame.src){
            frame.addEventListener('load', () => sendFrameState(frame), {once:true});
            frame.src = frame.dataset.src;
        } else {
            sendFrameState(frame);
        }
        return frame;
    }

    function notifyParent(section){
        if(window.parent === window) return;
        try {
            window.parent.postMessage({type:'studio-settings-navigate', section}, '*');
        } catch(e) {}
    }

    function activateSection(value, options = {}){
        const section = normalizeSection(value);
        currentSection = section;
        localStorage.setItem(SECTION_KEY, section);

        document.querySelectorAll('[data-settings-section]').forEach(element => {
            const active = element.dataset.settingsSection === section;
            element.classList.toggle('active', active);
            if(element.matches('[role="tab"]')) element.setAttribute('aria-selected', active ? 'true' : 'false');
        });

        ensureFrame(section);
        if(options.notify !== false) notifyParent(section);
    }

    function forwardToFrames(message){
        document.querySelectorAll('.settings-frame').forEach(frame => {
            if(frame.src){
                try { frame.contentWindow?.postMessage(message, '*'); } catch(e) {}
            }
        });
    }

    document.querySelectorAll('.settings-nav-item').forEach(button => {
        button.addEventListener('click', () => activateSection(button.dataset.settingsSection));
    });

    window.addEventListener('message', event => {
        if(event.source !== window.parent) return;
        if(event.data?.type === 'studio-settings-route'){
            activateSection(event.data.section, {notify:false});
        } else if(event.data?.type === 'studio-lang'){
            if(window.StudioI18n) window.StudioI18n.set(event.data.lang);
        }
    });

    window.addEventListener('studio-theme-change', event => {
        forwardToFrames({type:'studio-theme', theme:event.detail?.theme || 'light'});
    });

    window.addEventListener('studio-lang-change', event => {
        forwardToFrames({type:'studio-lang', lang:event.detail?.lang || 'zh'});
        document.title = window.StudioI18n ? window.StudioI18n.t('settings.title') : '设置';
    });

    document.addEventListener('DOMContentLoaded', () => {
        const requested = new URLSearchParams(window.location.search).get('section');
        const saved = localStorage.getItem(SECTION_KEY);
        activateSection(requested || saved || currentSection, {notify:false});
        if(window.StudioI18n) window.StudioI18n.apply();
        if(window.lucide) window.lucide.createIcons();
    }, {once:true});
})();
