import {
    applyConnectedInputs,
    currentCameraImageUrl,
    mountDirectorEditor,
    normalizeDirectorData,
    syncDirectorOutputs,
} from './director-editor.js?v=2.1.0';

function sceneCounts(scene) {
    const objects = Array.isArray(scene?.objects) ? scene.objects : [];
    const characters = objects.filter(item => item.type === 'character').length;
    const geometry = objects.filter(item => item.type === 'geometry').length;
    const models = objects.filter(item => item.type === 'model').length;
    const cameras = Array.isArray(scene?.cameras) ? scene.cameras.length : 0;
    return {characters, geometry, models, cameras};
}

function renderDirector({node, escapeHtml, context}) {
    normalizeDirectorData(node);
    const preview = currentCameraImageUrl(node);
    const counts = sceneCounts(node.data.scene);
    const activeCamera = node.data.scene.cameras.find(camera => camera.id === node.data.scene.activeCameraId) || node.data.scene.cameras[0];
    return `<div class="director-node-card" data-director-card>
        <div class="director-node-preview ${preview ? 'has-preview' : ''}">
            ${preview
                ? `<img src="${escapeHtml(preview)}" alt="当前激活摄像机画面">`
                : `<div class="director-node-empty"><span class="director-node-empty-icon">3D</span><strong>尚未生成摄像机画面</strong><small>打开导演台，选择机位后保存输出</small></div>`}
            <div class="director-node-badges">
                <span>${counts.characters} 角色</span>
                <span>${counts.cameras} 机位</span>
                ${counts.geometry ? `<span>${counts.geometry} 几何体</span>` : ''}
                ${counts.models ? `<span>${counts.models} 导入模型</span>` : ''}
            </div>
        </div>
        <div class="director-node-meta">
            <div><strong>${escapeHtml(activeCamera?.name || '机位01')}</strong><span>${escapeHtml(activeCamera?.aspect || '16:9')} · ${Number(activeCamera?.focalLength || 50)}mm</span></div>
            <button type="button" class="director-node-open" data-director-open>打开导演台</button>
        </div>
    </div>`;
}

function bindDirector({root, node, context, update, save}) {
    const button = root.querySelector('[data-director-open]');
    if (!button) return;
    const open = event => {
        event.preventDefault();
        event.stopPropagation();
        applyConnectedInputs(node, context.collectInputs?.() || {});
        syncDirectorOutputs(node);
        context.openEditor({
            title: '3D 导演台',
            className: 'director-editor-host',
            closeOnEscape: false,
            mount: (container, editorApi) => mountDirectorEditor({
                container,
                editorApi,
                node,
                context,
            }),
            onClose: () => {
                syncDirectorOutputs(node);
                update?.(node);
                save?.();
            },
        });
    };
    button.addEventListener('click', open);
    return () => button.removeEventListener('click', open);
}

function serializeDirector({node}) {
    normalizeDirectorData(node);
    syncDirectorOutputs(node);
    delete node.data.editor;
    delete node._directorRuntime;
    return node;
}

function migrateDirector({node}) {
    normalizeDirectorData(node);
    syncDirectorOutputs(node);
    return node;
}

function runDirector({node, context}) {
    applyConnectedInputs(node, context.collectInputs?.() || {});
    syncDirectorOutputs(node);
    const cameraImageUrl = currentCameraImageUrl(node);
    if (!cameraImageUrl) {
        throw new Error('当前摄像机画面尚未生成或场景已改变，请打开3D导演台并点击“保存并关闭”');
    }
    context.update?.(node);
    context.save?.();
    return {
        preview: cameraImageUrl,
        depth: node.data.depthUrl || '',
        character_mask: node.data.characterMaskUrl || '',
        scene: node.data.scene,
        shot_prompt: node.outputText || '',
    };
}

export function register(api) {
    api.registerNode('director-stage', {
        render: renderDirector,
        bind: bindDirector,
        serialize: serializeDirector,
        migrate: migrateDirector,
        run: runDirector,
    });
}
