// DOM event bindings for the digital-human page
// Split from state.js. Loaded as a classic script; shared symbols remain global.

$('reload-btn').onclick = () => loadConfig().catch(err => log(err.message, 'err'));
$('generate-btn').onclick = () => generate().catch(err => {
    log(err.message, 'err');
    $('generate-btn').disabled = false;
});

const templateGuideModal = $('template-guide-modal');
const openTemplateGuide = () => {
    templateGuideModal.classList.remove('hidden');
    templateGuideModal.querySelector('.dh-dialog-close')?.focus();
};
const closeTemplateGuide = () => {
    templateGuideModal.classList.add('hidden');
    $('template-guide-btn')?.focus();
};
$('template-guide-btn').onclick = openTemplateGuide;
templateGuideModal.querySelectorAll('[data-template-guide-close]').forEach(el => {
    el.addEventListener('click', closeTemplateGuide);
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !templateGuideModal.classList.contains('hidden')) {
        closeTemplateGuide();
    }
});

$('voice-upload-btn').onclick = () => $('voice-upload').click();
$('voice-library-upload-btn').onclick = () => $('voice-upload').click();
$('voice-preview-btn').onclick = () => previewSelectedVoice();
$('voice-select').onchange = () => {
    $('voice-preview-audio').pause();
    $('voice-preview-audio').removeAttribute('src');
    $('voice-preview-audio').classList.add('hidden');
    if (state.uploadedVoice?.voice_name && $('voice-select').value !== state.uploadedVoice.voice_name) {
        state.uploadedVoice = null;
        showVoiceAsset(null);
    }
    updateVoiceActions();
    renderVoicesLibrary();
    renderCurrentSelection();
};

document.querySelectorAll('[data-tab], [data-tab-jump]').forEach(btn => {
    btn.onclick = () => switchTab(btn.dataset.tab || btn.dataset.tabJump);
});
const requestedTab = new URLSearchParams(location.search).get('tab') || location.hash.replace('#', '');
if (requestedTab) switchTab(requestedTab);

$('voice-upload').onchange = (e) => handleUpload(e.target.files?.[0], 'voice')
    .catch(err => log(err.message, 'err'))
    .finally(() => { e.target.value = ''; });
$('video-upload').onchange = (e) => handleVideoUploads(e.target.files)
    .catch(err => log(err.message, 'err'))
    .finally(() => { e.target.value = ''; });

$('voice-upload-btn').ondragover = (e) => {
    e.preventDefault();
    $('voice-upload-btn').classList.add('dragging');
};
$('voice-upload-btn').ondragleave = () => $('voice-upload-btn').classList.remove('dragging');
$('voice-upload-btn').ondrop = (e) => {
    e.preventDefault();
    $('voice-upload-btn').classList.remove('dragging');
    handleUpload(e.dataTransfer.files?.[0], 'voice').catch(err => log(err.message, 'err'));
};
