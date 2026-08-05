import {
    IK_TARGETS,
    JOINT_NAMES,
    POSE_PRESETS,
    applyCharacterPose,
    cameraTrack,
    captureCharacterJoints,
    characterEffectorPosition,
    createRiggedCharacter,
    maskColorForId,
    normalizeIkTargets,
    normalizeJointMap,
    normalizeTimeline,
    poseJointMap,
    removeCameraKeyframe,
    sampleCameraTrack,
    solveCharacterIk,
    upsertCameraKeyframe,
} from './director-features.js';

const THREE_MODULE_URL = '/static/vendor/js/three-0.160.0.module.js?v=2026.08.04.1';
const TRANSFORM_CONTROLS_URL = '/static/vendor/js/three-transform-controls-0.160.0.js?v=2026.08.04.1';
const GLTF_LOADER_URL = '/static/vendor/js/three-gltf-loader-0.160.0.js?v=2026.08.04.1';
const HISTORY_LIMIT = 40;
const ASPECT_RATIOS = {'1:1':1, '4:3':4 / 3, '16:9':16 / 9, '9:16':9 / 16, '21:9':21 / 9};
const CHARACTER_PRESETS = {
    male: {label:'男性素体', height:1.82, head:0.085, shoulders:0.52, waist:0.33, hips:0.36, build:1},
    female: {label:'女性素体', height:1.72, head:0.09, shoulders:0.43, waist:0.27, hips:0.39, build:0.9},
    broad: {label:'宽厚素体', height:1.84, head:0.084, shoulders:0.66, waist:0.45, hips:0.46, build:1.18},
    athletic: {label:'健壮素体', height:1.86, head:0.083, shoulders:0.6, waist:0.36, hips:0.4, build:1.12},
    slim: {label:'纤细素体', height:1.78, head:0.09, shoulders:0.4, waist:0.25, hips:0.31, build:0.78},
    teen: {label:'少年素体', height:1.56, head:0.1, shoulders:0.39, waist:0.27, hips:0.31, build:0.82},
    child: {label:'儿童素体', height:1.22, head:0.125, shoulders:0.31, waist:0.24, hips:0.27, build:0.75},
    chibi: {label:'二头身', height:0.92, head:0.23, shoulders:0.34, waist:0.24, hips:0.3, build:0.9},
};
const JOINT_LABELS = {
    hips:'骨盆', spine:'脊柱', chest:'胸腔', neck:'颈部', head:'头部',
    leftUpperArm:'左上臂', leftForearm:'左前臂', leftHand:'左手',
    rightUpperArm:'右上臂', rightForearm:'右前臂', rightHand:'右手',
    leftThigh:'左大腿', leftShin:'左小腿', leftFoot:'左脚',
    rightThigh:'右大腿', rightShin:'右小腿', rightFoot:'右脚',
};
const COLOR_PALETTE = ['#e87532', '#2f7dd1', '#d13d3d', '#7d36bd', '#dca80f', '#08a8b5', '#ec4899', '#16a34a'];
let threePromise = null;
let transformControlsPromise = null;
let gltfLoaderPromise = null;

function loadThree() {
    if (!threePromise) threePromise = import(THREE_MODULE_URL);
    return threePromise;
}

function loadTransformControls() {
    if (!transformControlsPromise) transformControlsPromise = import(TRANSFORM_CONTROLS_URL);
    return transformControlsPromise;
}

function loadGltfLoader() {
    if (!gltfLoaderPromise) gltfLoaderPromise = import(GLTF_LOADER_URL);
    return gltfLoaderPromise;
}

function cloneJson(value) {
    return JSON.parse(JSON.stringify(value));
}

function safeNumber(value, fallback=0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function safeVector(value, fallback, length) {
    const source = Array.isArray(value) ? value : fallback;
    return Array.from({length}, (_, index) => safeNumber(source[index], fallback[index]));
}

function safeColor(value, fallback) {
    const text = String(value || '').trim();
    return /^#[0-9a-f]{6}$/i.test(text) ? text : fallback;
}

function safeModelUrl(value) {
    const text = String(value || '').trim().slice(0, 4000);
    return text.startsWith('/assets/node-extensions/3d-director/') && /\.(glb|gltf)(\?|$)/i.test(text) ? text : '';
}

function uid(prefix) {
    if (globalThis.crypto?.randomUUID) return `${prefix}-${crypto.randomUUID()}`;
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
}

function defaultCamera() {
    return {
        id:'camera-1', name:'机位01', position:[0, 2.2, 7.5], rotation:[-0.08, 0, 0, 0.9968],
        focalLength:50, aspect:'16:9', near:0.1, far:200,
    };
}

function normalizeCamera(camera, index=0) {
    const fallback = defaultCamera();
    return {
        id:String(camera?.id || (index === 0 ? fallback.id : uid('camera'))),
        name:String(camera?.name || `机位${String(index + 1).padStart(2, '0')}`).slice(0, 80),
        position:safeVector(camera?.position, fallback.position, 3),
        rotation:safeVector(camera?.rotation, fallback.rotation, 4),
        focalLength:Math.max(10, Math.min(300, safeNumber(camera?.focalLength, 50))),
        aspect:ASPECT_RATIOS[camera?.aspect] ? camera.aspect : '16:9',
        near:Math.max(0.01, safeNumber(camera?.near, 0.1)),
        far:Math.max(1, safeNumber(camera?.far, 200)),
        visible:camera?.visible !== false,
        locked:Boolean(camera?.locked),
    };
}

function normalizeObject(object, index=0) {
    const type = ['character','geometry','model'].includes(object?.type) ? object.type : 'character';
    const archetype = CHARACTER_PRESETS[object?.archetype] ? object.archetype : 'male';
    const geometryType = ['box','sphere','cylinder','plane'].includes(object?.geometryType) ? object.geometryType : 'box';
    const label = type === 'character' ? CHARACTER_PRESETS[archetype].label : type === 'model' ? '导入模型' : ({box:'立方体', sphere:'球体', cylinder:'圆柱体', plane:'平面'}[geometryType]);
    const id = String(object?.id || uid(type === 'character' ? 'actor' : type));
    return {
        id,
        type,
        archetype,
        geometryType,
        name:String(object?.name || `${label}${String(index + 1).padStart(2, '0')}`).slice(0, 80),
        position:safeVector(object?.position, [0, 0, 0], 3),
        rotation:safeVector(object?.rotation, [0, 0, 0, 1], 4),
        scale:safeVector(object?.scale, [1, 1, 1], 3).map(value => Math.max(0.05, Math.min(20, value))),
        color:safeColor(object?.color, COLOR_PALETTE[index % COLOR_PALETTE.length]),
        visible:object?.visible !== false,
        locked:Boolean(object?.locked),
        groupId:String(object?.groupId || ''),
        poseId:POSE_PRESETS[object?.poseId] ? object.poseId : 'standing',
        joints:normalizeJointMap(object?.joints),
        ikTargets:normalizeIkTargets(object?.ikTargets),
        maskColor:safeColor(object?.maskColor, maskColorForId(id)),
        assetUrl:type === 'model' ? safeModelUrl(object?.assetUrl) : '',
        assetName:type === 'model' ? String(object?.assetName || '').slice(0, 180) : '',
    };
}

export function normalizeScene(value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const cameras = (Array.isArray(source.cameras) ? source.cameras : []).map(normalizeCamera);
    if (!cameras.length) cameras.push(defaultCamera());
    const activeCameraId = cameras.some(camera => camera.id === source.activeCameraId) ? source.activeCameraId : cameras[0].id;
    const environment = source.environment && typeof source.environment === 'object' ? source.environment : {};
    return {
        schemaVersion:2,
        activeCameraId,
        objects:(Array.isArray(source.objects) ? source.objects : []).slice(0, 300).map(normalizeObject),
        cameras:cameras.slice(0, 32),
        environment:{
            backgroundUrl:String(environment.backgroundUrl || '').slice(0, 4000),
            backgroundMode:environment.backgroundMode === 'panorama' ? 'panorama' : 'flat',
            skyColor:safeColor(environment.skyColor, '#d8dde5'),
            groundColor:safeColor(environment.groundColor, '#9098a3'),
            groundOpacity:Math.max(0, Math.min(1, safeNumber(environment.groundOpacity, 0.42))),
            groundVisible:environment.groundVisible !== false,
            gridVisible:environment.gridVisible !== false,
            labelsVisible:environment.labelsVisible !== false,
        },
        settings:{
            transformSpace:source.settings?.transformSpace === 'local' ? 'local' : 'world',
            snapEnabled:source.settings?.snapEnabled !== false,
            translationSnap:Math.max(0.01, Math.min(10, safeNumber(source.settings?.translationSnap, 0.1))),
            rotationSnap:Math.max(1, Math.min(90, safeNumber(source.settings?.rotationSnap, 5))),
            scaleSnap:Math.max(0.01, Math.min(2, safeNumber(source.settings?.scaleSnap, 0.05))),
        },
        timeline:normalizeTimeline(source.timeline),
    };
}

export function normalizeDirectorData(node) {
    if (!node.data || typeof node.data !== 'object' || Array.isArray(node.data)) node.data = {};
    node.data.schemaVersion = 2;
    node.data.previewUrl = String(node.data.previewUrl || '').slice(0, 4000);
    node.data.depthUrl = String(node.data.depthUrl || '').slice(0, 4000);
    node.data.characterMaskUrl = String(node.data.characterMaskUrl || '').slice(0, 4000);
    node.data.cameraOutputFingerprint = String(node.data.cameraOutputFingerprint || '').slice(0, 128);
    node.data.directorNote = String(node.data.directorNote || '').slice(0, 8000);
    node.data.scene = normalizeScene(node.data.scene || node.data);
    // V2.0 already rendered previewUrl from the active shot camera. Preserve that
    // output once during migration, then fingerprint future renders so a changed
    // scene or active camera can never silently emit an old frame.
    if (node.data.previewUrl && !node.data.cameraOutputFingerprint) {
        node.data.cameraOutputFingerprint = cameraOutputFingerprint(node.data.scene);
    }
    return node.data;
}

function cameraOutputFingerprint(scene) {
    const serialized = JSON.stringify({
        activeCameraId:scene.activeCameraId,
        objects:scene.objects,
        cameras:scene.cameras,
        environment:scene.environment,
    });
    let first = 0x811c9dc5;
    let second = 0x9e3779b9;
    for (let index = 0; index < serialized.length; index += 1) {
        const code = serialized.charCodeAt(index);
        first = Math.imul(first ^ code, 0x01000193);
        second = Math.imul(second ^ code, 0x85ebca6b);
    }
    return `${serialized.length.toString(36)}-${(first >>> 0).toString(36)}-${(second >>> 0).toString(36)}`;
}

export function currentCameraImageUrl(node) {
    normalizeDirectorData(node);
    if (!node.data.previewUrl) return '';
    return node.data.cameraOutputFingerprint === cameraOutputFingerprint(node.data.scene)
        ? node.data.previewUrl
        : '';
}

function inputValue(input) {
    const item = Array.isArray(input) ? input[0] : input;
    if (item && typeof item === 'object' && Object.prototype.hasOwnProperty.call(item, 'value')) return item.value;
    if (item && typeof item === 'object' && item.url) return item.url;
    return item;
}

export function applyConnectedInputs(node, inputs={}) {
    normalizeDirectorData(node);
    const background = inputValue(inputs.background);
    if (typeof background === 'string' && background.trim()) node.data.scene.environment.backgroundUrl = background.trim().slice(0, 4000);
    const note = inputValue(inputs.director_note);
    if (typeof note === 'string' && note.trim()) node.data.directorNote = note.slice(0, 8000);
    const sceneInput = inputValue(inputs.scene_in);
    if (sceneInput && typeof sceneInput === 'object' && !Array.isArray(sceneInput)) {
        const fingerprint = JSON.stringify(sceneInput);
        if (fingerprint !== node.data.inputSceneFingerprint) {
            node.data.scene = normalizeScene(sceneInput);
            node.data.inputSceneFingerprint = fingerprint.slice(0, 2000000);
        }
    }
    return node.data;
}

function buildShotPrompt(scene, note='') {
    const camera = scene.cameras.find(item => item.id === scene.activeCameraId) || scene.cameras[0];
    const characters = scene.objects.filter(item => item.type === 'character' && item.visible !== false);
    const geometry = scene.objects.filter(item => item.type === 'geometry' && item.visible !== false);
    const positions = characters.slice(0, 12).map(item => `${item.name}位于(${item.position.map(value => Number(value.toFixed(2))).join(', ')})`).join('；');
    const background = scene.environment.backgroundUrl
        ? (scene.environment.backgroundMode === 'panorama' ? '使用全景环境背景' : '使用平面场景背景')
        : `天空颜色${scene.environment.skyColor}`;
    const parts = [
        `${camera.aspect}画幅，${Number(camera.focalLength.toFixed(1))}mm镜头`,
        `机位位置(${camera.position.map(value => Number(value.toFixed(2))).join(', ')})`,
        `场景包含${characters.length}名角色${geometry.length ? `和${geometry.length}个几何模型` : ''}`,
        background,
    ];
    if (positions) parts.push(`角色站位：${positions}`);
    if (note.trim()) parts.push(`导演要求：${note.trim()}`);
    return parts.join('。') + '。';
}

export function syncDirectorOutputs(node) {
    normalizeDirectorData(node);
    const scene = cloneJson(node.data.scene);
    const prompt = buildShotPrompt(scene, node.data.directorNote || '');
    const cameraImageUrl = currentCameraImageUrl(node);
    const cameraOutputsCurrent = Boolean(cameraImageUrl);
    node.outputText = prompt;
    node.structuredOutput = scene;
    node.extensionOutputs = {
        preview:cameraImageUrl ? [{kind:'image', value:cameraImageUrl}] : [],
        depth:cameraOutputsCurrent && node.data.depthUrl ? [{kind:'image', value:node.data.depthUrl}] : [],
        character_mask:cameraOutputsCurrent && node.data.characterMaskUrl ? [{kind:'image', value:node.data.characterMaskUrl}] : [],
        scene:[{kind:'json', value:scene}],
        shot_prompt:[{kind:'text', value:prompt}],
    };
    node.images = cameraImageUrl ? [{url:cameraImageUrl, name:'3d-director-camera.png'}] : [];
    return node.extensionOutputs;
}

function aspectDimensions(aspect) {
    const ratio = ASPECT_RATIOS[aspect] || ASPECT_RATIOS['16:9'];
    if (ratio >= 1) return [1280, Math.max(320, Math.round(1280 / ratio))];
    return [Math.max(320, Math.round(1280 * ratio)), 1280];
}

function focalLengthToFov(focalLength) {
    return 2 * Math.atan(36 / (2 * Math.max(10, focalLength))) * 180 / Math.PI;
}

function canvasBlob(canvas, type='image/png', quality=0.94) {
    return new Promise((resolve, reject) => {
        try {
            canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('无法生成镜头预览')), type, quality);
        } catch (error) {
            reject(error);
        }
    });
}

async function linearDepthBlob(canvas, camera) {
    const output = document.createElement('canvas');
    output.width = canvas.width;
    output.height = canvas.height;
    const context = output.getContext('2d', {willReadFrequently:true});
    context.drawImage(canvas, 0, 0);
    const image = context.getImageData(0, 0, output.width, output.height);
    const pixels = image.data;
    const unpackDownscale = 255 / 256;
    const factors = [
        unpackDownscale / (256 * 256 * 256),
        unpackDownscale / (256 * 256),
        unpackDownscale / 256,
        unpackDownscale,
    ];
    const displayFar = Math.min(camera.far, 30);
    for (let index = 0; index < pixels.length; index += 4) {
        if (pixels[index] === 255 && pixels[index + 1] === 255 && pixels[index + 2] === 255 && pixels[index + 3] === 255) continue;
        const depth = (pixels[index] / 255) * factors[0]
            + (pixels[index + 1] / 255) * factors[1]
            + (pixels[index + 2] / 255) * factors[2]
            + (pixels[index + 3] / 255) * factors[3];
        const viewZ = (camera.near * camera.far) / ((camera.far - camera.near) * depth - camera.far);
        const distance = -viewZ;
        const normalized = Math.max(0, Math.min(1, (distance - camera.near) / Math.max(0.001, displayFar - camera.near)));
        const gray = Math.round((1 - normalized) * 255);
        pixels[index] = gray;
        pixels[index + 1] = gray;
        pixels[index + 2] = gray;
        pixels[index + 3] = 255;
    }
    context.putImageData(image, 0, 0);
    return canvasBlob(output);
}

function createHeaderButton(label, className='') {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `director-header-action ${className}`.trim();
    button.textContent = label;
    return button;
}

function createLabelSprite(THREE, text) {
    const canvas = document.createElement('canvas');
    canvas.width = 384;
    canvas.height = 96;
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = 'rgba(15, 23, 42, .86)';
    context.beginPath();
    context.roundRect(12, 12, 360, 72, 24);
    context.fill();
    context.fillStyle = '#ffffff';
    context.font = '700 32px system-ui, sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    const clean = String(text || '').slice(0, 18);
    context.fillText(clean, 192, 48);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({map:texture, transparent:true, depthTest:false});
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(1.5, 0.375, 1);
    sprite.userData.directorLabel = true;
    return sprite;
}

function characterGroup(THREE, record) {
    const preset = CHARACTER_PRESETS[record.archetype] || CHARACTER_PRESETS.male;
    return createRiggedCharacter(THREE, record, preset, text => createLabelSprite(THREE, text));
}

function geometryGroup(THREE, record) {
    const root = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({color:record.color, roughness:0.68, metalness:0.04, side:THREE.DoubleSide});
    let mesh;
    if (record.geometryType === 'sphere') {
        mesh = new THREE.Mesh(new THREE.SphereGeometry(0.55, 24, 18), material);
        mesh.position.y = 0.55;
    } else if (record.geometryType === 'cylinder') {
        mesh = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 1.1, 20), material);
        mesh.position.y = 0.55;
    } else if (record.geometryType === 'plane') {
        mesh = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 1.5), material);
        mesh.rotation.x = -Math.PI / 2;
        mesh.position.y = 0.015;
    } else {
        mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material);
        mesh.position.y = 0.5;
    }
    mesh.castShadow = record.geometryType !== 'plane';
    mesh.receiveShadow = true;
    root.add(mesh);
    const label = createLabelSprite(THREE, record.name);
    label.position.set(0, record.geometryType === 'plane' ? 0.35 : 1.35, 0);
    root.add(label);
    return root;
}

function modelPlaceholder(THREE, record) {
    const root = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({color:record.color, roughness:0.55, metalness:0.08, wireframe:true});
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.8, 0.8), material);
    mesh.position.y = 0.9;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.userData.directorModelPlaceholder = true;
    root.add(mesh);
    const label = createLabelSprite(THREE, record.name || record.assetName || '导入模型');
    label.position.set(0, 2.1, 0);
    root.add(label);
    root.userData.modelState = record.assetUrl ? 'loading' : 'missing';
    return root;
}

function setRecordTransform(THREE, group, record) {
    group.position.fromArray(record.position);
    group.quaternion.fromArray(record.rotation).normalize();
    group.scale.fromArray(record.scale);
    group.visible = record.visible !== false;
    group.traverse(child => { child.userData.objectId = record.id; });
}

function disposeObject(object) {
    object.traverse(child => {
        child.geometry?.dispose?.();
        if (Array.isArray(child.material)) child.material.forEach(material => { material.map?.dispose?.(); material.dispose?.(); });
        else { child.material?.map?.dispose?.(); child.material?.dispose?.(); }
    });
}

export async function mountDirectorEditor({container, editorApi, node, context}) {
    normalizeDirectorData(node);
    const [THREE, {TransformControls}, {GLTFLoader}] = await Promise.all([loadThree(), loadTransformControls(), loadGltfLoader()]);
    let sceneData = node.data.scene;
    let selected = null;
    let viewMode = 'director';
    let transformMode = 'translate';
    let drag = null;
    let frameRequest = 0;
    let backgroundTexture = null;
    let backgroundLoadToken = 0;
    let disposed = false;
    let saveBusy = false;
    let transformDragging = false;
    let objectBuildToken = 0;
    let playbackFrame = 0;
    let playbackStartedAt = 0;
    let playbackStartTime = 0;
    const runtimeObjects = new Map();
    const shotCameras = new Map();
    const cameraHelpers = new Map();
    const characterEditorState = new Map();
    const history = [JSON.stringify(sceneData)];
    let historyIndex = 0;

    container.innerHTML = `<div class="director-editor" tabindex="0">
        <aside class="director-sidebar director-sidebar-left">
            <div class="director-sidebar-heading"><strong>场景对象</strong><button type="button" data-toggle-add>＋</button></div>
            <label class="director-search"><span>⌕</span><input type="search" placeholder="搜索角色、机位或模型" data-scene-search></label>
            <div class="director-scene-tree" data-scene-tree></div>
        </aside>
        <main class="director-stage-area">
            <div class="director-stage-topbar">
                <div class="director-view-tabs">
                    <button type="button" class="active" data-view="director">导演视角</button>
                    <button type="button" data-view="camera">机位视角</button>
                </div>
                <select data-active-camera aria-label="当前机位"></select>
                <select data-transform-space aria-label="坐标空间"><option value="world">世界坐标</option><option value="local">局部坐标</option></select>
                <button type="button" class="director-topbar-button" data-toggle-snap>吸附：开</button>
                <span class="director-stage-hint">空白处拖动旋转视角 · 滚轮缩放</span>
            </div>
            <div class="director-viewport" data-viewport>
                <div class="director-axis-badge"><b>Y</b><span>X</span><i>Z</i></div>
                <div class="director-status" data-status>准备就绪</div>
            </div>
            <div class="director-timeline" data-timeline>
                <button type="button" data-timeline-play>▶</button>
                <button type="button" data-timeline-key title="在当前时间记录机位">＋关键帧</button>
                <button type="button" data-timeline-delete title="删除当前附近关键帧">－关键帧</button>
                <input type="range" min="0" max="5" step="0.041667" value="0" data-timeline-scrubber aria-label="镜头时间">
                <span data-timeline-time>0.00 / 5.00 秒</span>
            </div>
            <div class="director-bottom-toolbar">
                <button type="button" data-mode="select" title="选择 (Q)">选择</button>
                <button type="button" class="active" data-mode="translate" title="移动 (W)">移动</button>
                <button type="button" data-mode="rotate" title="旋转 (E)">旋转</button>
                <button type="button" data-mode="scale" title="缩放 (R)">缩放</button>
                <span class="director-toolbar-separator"></span>
                <button type="button" data-toggle-add>添加角色</button>
                <button type="button" data-action="add-camera">添加机位</button>
                <button type="button" data-action="duplicate">复制</button>
                <button type="button" data-action="delete">删除</button>
                <button type="button" data-action="focus">聚焦</button>
                <div class="director-add-menu" data-add-menu hidden>
                    <strong>添加角色</strong>
                    ${Object.entries(CHARACTER_PRESETS).map(([id, preset]) => `<button type="button" data-add-character="${id}">${preset.label}</button>`).join('')}
                    <strong>群众</strong>
                    <button type="button" data-add-crowd="3">群众（3×3）</button>
                    <strong>几何模型</strong>
                    <button type="button" data-add-geometry="box">立方体</button>
                    <button type="button" data-add-geometry="sphere">球体</button>
                    <button type="button" data-add-geometry="cylinder">圆柱体</button>
                    <button type="button" data-add-geometry="plane">平面</button>
                    <strong>外部模型</strong>
                    <button type="button" data-import-model>导入 GLB/GLTF</button>
                </div>
                <input type="file" accept=".glb,.gltf,model/gltf-binary,model/gltf+json" data-model-file hidden>
            </div>
        </main>
        <aside class="director-sidebar director-sidebar-right">
            <div class="director-sidebar-heading"><strong data-inspector-title>3D 场景</strong></div>
            <div class="director-inspector" data-inspector></div>
        </aside>
    </div>`;

    const root = container.querySelector('.director-editor');
    const viewport = root.querySelector('[data-viewport]');
    const treeElement = root.querySelector('[data-scene-tree]');
    const inspector = root.querySelector('[data-inspector]');
    const inspectorTitle = root.querySelector('[data-inspector-title]');
    const searchInput = root.querySelector('[data-scene-search]');
    const cameraSelect = root.querySelector('[data-active-camera]');
    const transformSpaceSelect = root.querySelector('[data-transform-space]');
    const snapButton = root.querySelector('[data-toggle-snap]');
    const modelFileInput = root.querySelector('[data-model-file]');
    const timelinePlayButton = root.querySelector('[data-timeline-play]');
    const timelineScrubber = root.querySelector('[data-timeline-scrubber]');
    const timelineTime = root.querySelector('[data-timeline-time]');
    const addMenu = root.querySelector('[data-add-menu]');
    const statusElement = root.querySelector('[data-status]');

    const undoButton = createHeaderButton('撤销');
    const redoButton = createHeaderButton('重做');
    const previewButton = createHeaderButton('生成当前机位画面');
    const saveButton = createHeaderButton('保存并关闭', 'is-primary');
    editorApi.actions.prepend(undoButton, redoButton, previewButton, saveButton);

    const scene = new THREE.Scene();
    const renderer = new THREE.WebGLRenderer({antialias:true, alpha:false, preserveDrawingBuffer:true});
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.domElement.className = 'director-webgl-canvas';
    renderer.domElement.setAttribute('aria-label', '3D 导演台视口');
    viewport.prepend(renderer.domElement);

    const editorCamera = new THREE.PerspectiveCamera(48, 1, 0.05, 500);
    editorCamera.position.set(8, 5.2, 10.5);
    const orbitTarget = new THREE.Vector3(0, 1.1, 0);
    const orbit = new THREE.Spherical().setFromVector3(editorCamera.position.clone().sub(orbitTarget));
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const sceneRoot = new THREE.Group();
    scene.add(sceneRoot);
    const grid = new THREE.GridHelper(40, 40, 0x31517a, 0x718096);
    grid.material.transparent = true;
    grid.material.opacity = 0.58;
    scene.add(grid);
    const groundMaterial = new THREE.MeshStandardMaterial({color:0x9098a3, transparent:true, opacity:0.42, roughness:0.9, side:THREE.DoubleSide});
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), groundMaterial);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.012;
    ground.receiveShadow = true;
    scene.add(ground);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x445066, 2.1));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.8);
    keyLight.position.set(6, 10, 7);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.set(2048, 2048);
    keyLight.shadow.camera.left = -15;
    keyLight.shadow.camera.right = 15;
    keyLight.shadow.camera.top = 15;
    keyLight.shadow.camera.bottom = -15;
    scene.add(keyLight);
    const selectionBox = new THREE.BoxHelper(undefined, 0x0ea5e9);
    selectionBox.visible = false;
    scene.add(selectionBox);
    const transformControls = new TransformControls(editorCamera, renderer.domElement);
    transformControls.setMode(transformMode);
    transformControls.setSpace(sceneData.settings.transformSpace);
    scene.add(transformControls);
    const gltfLoader = new GLTFLoader();
    transformSpaceSelect.value = sceneData.settings.transformSpace;

    function setStatus(message, error=false) {
        statusElement.textContent = String(message || '');
        statusElement.classList.toggle('is-error', Boolean(error));
    }

    function requestRender() {
        if (disposed || frameRequest) return;
        frameRequest = requestAnimationFrame(() => {
            frameRequest = 0;
            const camera = activeRenderCamera();
            if (!camera) return;
            renderer.render(scene, camera);
        });
    }

    function updateEditorCamera() {
        orbit.phi = Math.max(0.08, Math.min(Math.PI - 0.08, orbit.phi));
        orbit.radius = Math.max(1.2, Math.min(80, orbit.radius));
        editorCamera.position.copy(orbitTarget).add(new THREE.Vector3().setFromSpherical(orbit));
        editorCamera.lookAt(orbitTarget);
        requestRender();
    }

    function activeShotRecord() {
        return sceneData.cameras.find(camera => camera.id === sceneData.activeCameraId) || sceneData.cameras[0];
    }

    function activeRenderCamera() {
        return viewMode === 'camera' ? (shotCameras.get(sceneData.activeCameraId) || editorCamera) : editorCamera;
    }

    function cameraFromRecord(record) {
        const camera = new THREE.PerspectiveCamera(focalLengthToFov(record.focalLength), 1, record.near, record.far);
        camera.position.fromArray(record.position);
        camera.quaternion.fromArray(record.rotation).normalize();
        camera.updateProjectionMatrix();
        camera.userData.cameraId = record.id;
        return camera;
    }

    function clearRuntimeObjects() {
        objectBuildToken += 1;
        runtimeObjects.forEach(object => {
            sceneRoot.remove(object);
            disposeObject(object);
        });
        runtimeObjects.clear();
    }

    function loadImportedModel(rootObject, record, token) {
        if (!record.assetUrl) {
            rootObject.userData.modelState = 'missing';
            return;
        }
        gltfLoader.load(record.assetUrl, gltf => {
            if (disposed || token !== objectBuildToken || runtimeObjects.get(record.id) !== rootObject) {
                disposeObject(gltf.scene);
                return;
            }
            [...rootObject.children].forEach(child => { rootObject.remove(child); disposeObject(child); });
            const content = gltf.scene;
            content.traverse(child => {
                if (child.isMesh) {
                    child.castShadow = true;
                    child.receiveShadow = true;
                }
                child.userData.objectId = record.id;
            });
            content.updateMatrixWorld(true);
            const bounds = new THREE.Box3().setFromObject(content);
            const size = bounds.getSize(new THREE.Vector3());
            const largest = Math.max(size.x, size.y, size.z, 0.001);
            const fitScale = 2.4 / largest;
            content.scale.multiplyScalar(fitScale);
            content.updateMatrixWorld(true);
            const fitted = new THREE.Box3().setFromObject(content);
            const center = fitted.getCenter(new THREE.Vector3());
            content.position.x -= center.x;
            content.position.y -= fitted.min.y;
            content.position.z -= center.z;
            rootObject.add(content);
            const finalBounds = new THREE.Box3().setFromObject(content);
            const label = createLabelSprite(THREE, record.name || record.assetName || '导入模型');
            label.position.set(0, finalBounds.max.y + 0.25, 0);
            label.userData.objectId = record.id;
            label.visible = sceneData.environment.labelsVisible !== false;
            rootObject.add(label);
            rootObject.userData.modelState = 'ready';
            updateSelectionBox();
            requestRender();
            setStatus(`模型已加载：${record.assetName || record.name}`);
        }, undefined, error => {
            if (disposed || token !== objectBuildToken) return;
            rootObject.userData.modelState = 'failed';
            setStatus(`模型加载失败：${error?.message || '文件不可用'}`, true);
            requestRender();
        });
    }

    function clearCameras() {
        cameraHelpers.forEach(helper => { scene.remove(helper); helper.dispose?.(); });
        shotCameras.forEach(camera => scene.remove(camera));
        cameraHelpers.clear();
        shotCameras.clear();
    }

    function rebuildObjects() {
        clearRuntimeObjects();
        const token = objectBuildToken;
        sceneData.objects.forEach(record => {
            const object = record.type === 'geometry' ? geometryGroup(THREE, record) : record.type === 'model' ? modelPlaceholder(THREE, record) : characterGroup(THREE, record);
            setRecordTransform(THREE, object, record);
            object.traverse(child => {
                if (child.userData.directorLabel) child.visible = sceneData.environment.labelsVisible !== false;
            });
            sceneRoot.add(object);
            runtimeObjects.set(record.id, object);
            if (record.type === 'model') loadImportedModel(object, record, token);
        });
        updateTransformAttachment();
    }

    function rebuildCameras() {
        clearCameras();
        sceneData.cameras.forEach(record => {
            const camera = cameraFromRecord(record);
            const helperCamera = camera.clone();
            helperCamera.far = Math.min(record.far, 8);
            helperCamera.updateProjectionMatrix();
            helperCamera.updateMatrixWorld(true);
            const helper = new THREE.CameraHelper(helperCamera);
            helper.userData.directorCamera = helperCamera;
            helper.visible = viewMode === 'director' && record.visible !== false;
            scene.add(camera, helper);
            shotCameras.set(record.id, camera);
            cameraHelpers.set(record.id, helper);
        });
        if (!shotCameras.has(sceneData.activeCameraId)) sceneData.activeCameraId = sceneData.cameras[0].id;
        renderCameraSelect();
    }

    function applyEnvironment() {
        const environment = sceneData.environment;
        grid.visible = environment.gridVisible !== false;
        ground.visible = environment.groundVisible !== false;
        groundMaterial.color.set(environment.groundColor);
        groundMaterial.opacity = environment.groundOpacity;
        runtimeObjects.forEach(object => object.traverse(child => {
            if (child.userData.directorLabel) child.visible = environment.labelsVisible !== false;
        }));
        const token = ++backgroundLoadToken;
        if (backgroundTexture) {
            backgroundTexture.dispose();
            backgroundTexture = null;
        }
        scene.background = new THREE.Color(environment.skyColor);
        const url = String(environment.backgroundUrl || '');
        if (url) {
            const loader = new THREE.TextureLoader();
            loader.setCrossOrigin('anonymous');
            loader.load(url, texture => {
                if (disposed || token !== backgroundLoadToken) { texture.dispose(); return; }
                texture.colorSpace = THREE.SRGBColorSpace;
                if (environment.backgroundMode === 'panorama') texture.mapping = THREE.EquirectangularReflectionMapping;
                backgroundTexture = texture;
                scene.background = texture;
                requestRender();
            }, undefined, () => {
                if (token === backgroundLoadToken) setStatus('背景图加载失败，已使用天空颜色', true);
            });
        }
        requestRender();
    }

    function rebuildAll() {
        rebuildObjects();
        rebuildCameras();
        applyEnvironment();
        renderSceneTree();
        renderInspector();
        updateSelectionBox();
        resizeRenderer();
        requestRender();
    }

    function writeObjectRecord(id) {
        const record = sceneData.objects.find(item => item.id === id);
        const object = runtimeObjects.get(id);
        if (!record || !object) return;
        record.position = object.position.toArray();
        record.rotation = object.quaternion.toArray();
        record.scale = object.scale.toArray();
    }

    function writeCameraRecord(id) {
        const record = sceneData.cameras.find(item => item.id === id);
        const camera = shotCameras.get(id);
        if (!record || !camera) return;
        record.position = camera.position.toArray();
        record.rotation = camera.quaternion.toArray();
    }

    function applyTransformSettings() {
        const settings = sceneData.settings;
        transformControls.setSpace(settings.transformSpace);
        transformControls.setTranslationSnap(settings.snapEnabled ? settings.translationSnap : null);
        transformControls.setRotationSnap(settings.snapEnabled ? settings.rotationSnap * Math.PI / 180 : null);
        transformControls.setScaleSnap(settings.snapEnabled ? settings.scaleSnap : null);
        transformSpaceSelect.value = settings.transformSpace;
        snapButton.textContent = `吸附：${settings.snapEnabled ? '开' : '关'}`;
        snapButton.classList.toggle('active', settings.snapEnabled);
    }

    function updateTransformAttachment() {
        transformControls.camera = activeRenderCamera();
        transformControls.detach();
        transformControls.visible = false;
        if (transformMode === 'select') return;
        if (selected?.kind === 'object') {
            const object = runtimeObjects.get(selected.id);
            const record = sceneData.objects.find(item => item.id === selected.id);
            if (object && record && !record.locked && object.visible) {
                transformControls.setMode(transformMode);
                transformControls.attach(object);
                transformControls.visible = true;
            }
        } else if (selected?.kind === 'camera' && viewMode === 'director' && transformMode !== 'scale') {
            const camera = shotCameras.get(selected.id);
            const record = sceneData.cameras.find(item => item.id === selected.id);
            if (camera && record && !record.locked) {
                transformControls.setMode(transformMode);
                transformControls.attach(camera);
                transformControls.visible = true;
            }
        }
        requestRender();
    }

    function pushHistory() {
        const snapshot = JSON.stringify(sceneData);
        if (history[historyIndex] === snapshot) return;
        history.splice(historyIndex + 1);
        history.push(snapshot);
        if (history.length > HISTORY_LIMIT) history.shift();
        historyIndex = history.length - 1;
        refreshHistoryButtons();
    }

    function refreshHistoryButtons() {
        undoButton.disabled = historyIndex <= 0;
        redoButton.disabled = historyIndex >= history.length - 1;
    }

    function saveState(options={}) {
        node.data.scene = sceneData;
        syncDirectorOutputs(node);
        if (options.history !== false) pushHistory();
        context.save?.();
        refreshHistoryButtons();
    }

    function restoreHistory(index) {
        if (index < 0 || index >= history.length) return;
        historyIndex = index;
        sceneData = normalizeScene(JSON.parse(history[index]));
        node.data.scene = sceneData;
        selected = null;
        syncDirectorOutputs(node);
        rebuildAll();
        context.save?.();
        refreshHistoryButtons();
    }

    function uniqueName(prefix, list) {
        let index = 1;
        let name = `${prefix}${String(index).padStart(2, '0')}`;
        const names = new Set(list.map(item => item.name));
        while (names.has(name)) name = `${prefix}${String(++index).padStart(2, '0')}`;
        return name;
    }

    function addCharacter(archetype, position=null) {
        const preset = CHARACTER_PRESETS[archetype] || CHARACTER_PRESETS.male;
        const count = sceneData.objects.filter(item => item.type === 'character').length;
        const columns = [0, -1.15, 1.15, -2.3, 2.3];
        const record = normalizeObject({
            id:uid('actor'), type:'character', archetype,
            name:uniqueName('角色', sceneData.objects),
            position:position || [columns[count % columns.length], 0, -Math.floor(count / columns.length) * 1.2],
            color:COLOR_PALETTE[count % COLOR_PALETTE.length],
        }, count);
        record.name = record.name || preset.label;
        sceneData.objects.push(record);
        rebuildObjects();
        selectEntity('object', record.id);
        renderSceneTree();
        saveState();
        setStatus(`已添加${preset.label}`);
    }

    function addCrowd(size=3) {
        const originX = -((size - 1) * 1.15) / 2;
        const originZ = -1.2;
        for (let row = 0; row < size; row += 1) {
            for (let column = 0; column < size; column += 1) {
                const archetypes = Object.keys(CHARACTER_PRESETS).filter(item => item !== 'chibi');
                const archetype = archetypes[(row * size + column) % archetypes.length];
                const count = sceneData.objects.filter(item => item.type === 'character').length;
                sceneData.objects.push(normalizeObject({
                    id:uid('actor'), type:'character', archetype,
                    name:uniqueName('角色', sceneData.objects),
                    position:[originX + column * 1.15, 0, originZ - row * 1.35],
                    color:COLOR_PALETTE[count % COLOR_PALETTE.length],
                }, count));
            }
        }
        rebuildObjects();
        renderSceneTree();
        saveState();
        setStatus(`已添加群众（${size}×${size}）`);
    }

    function addGeometry(geometryType) {
        const count = sceneData.objects.filter(item => item.type === 'geometry').length;
        const label = {box:'立方体', sphere:'球体', cylinder:'圆柱体', plane:'平面'}[geometryType] || '模型';
        const record = normalizeObject({
            id:uid('geometry'), type:'geometry', geometryType,
            name:uniqueName(label, sceneData.objects),
            position:[(count % 4) * 1.4 - 2.1, 0, 1.5 + Math.floor(count / 4) * 1.4],
            color:COLOR_PALETTE[(count + 3) % COLOR_PALETTE.length],
        }, count);
        sceneData.objects.push(record);
        rebuildObjects();
        selectEntity('object', record.id);
        renderSceneTree();
        saveState();
        setStatus(`已添加${label}`);
    }

    async function importModel(file) {
        if (!file) return;
        if (!/\.(glb|gltf)$/i.test(file.name || '')) {
            setStatus('请选择 GLB 或内嵌资源的 GLTF 文件', true);
            return;
        }
        if (file.size > 100 * 1024 * 1024) {
            setStatus('3D 模型不能超过 100 MiB', true);
            return;
        }
        setStatus(`正在导入模型：${file.name}…`);
        try {
            const uploaded = await context.uploadAsset(file, {kind:'model', extensionId:'3d-director', filename:file.name});
            const record = normalizeObject({
                id:uid('model'), type:'model', name:uniqueName('模型', sceneData.objects),
                assetUrl:uploaded.url, assetName:uploaded.name || file.name,
                position:[0,0,0], color:'#8aa4c8',
            }, sceneData.objects.length);
            sceneData.objects.push(record);
            rebuildObjects();
            selectEntity('object', record.id);
            renderSceneTree();
            saveState();
            setStatus(`已导入模型：${record.assetName}`);
        } catch (error) {
            setStatus(`模型导入失败：${error.message || error}`, true);
        }
    }

    function addCamera() {
        const record = normalizeCamera({
            id:uid('camera'),
            name:uniqueName('机位', sceneData.cameras),
            position:editorCamera.position.toArray(),
            rotation:editorCamera.quaternion.toArray(),
            focalLength:50,
            aspect:activeShotRecord()?.aspect || '16:9',
        }, sceneData.cameras.length);
        sceneData.cameras.push(record);
        sceneData.activeCameraId = record.id;
        rebuildCameras();
        selectEntity('camera', record.id);
        renderSceneTree();
        saveState();
        setStatus('已从导演视角创建机位');
    }

    function duplicateSelected() {
        if (!selected) return;
        if (selected.kind === 'object') {
            const source = sceneData.objects.find(item => item.id === selected.id);
            if (!source) return;
            const copy = normalizeObject({...cloneJson(source), id:uid(source.type === 'character' ? 'actor' : source.type), name:`${source.name}副本`, position:[source.position[0] + 0.7, source.position[1], source.position[2] + 0.35]}, sceneData.objects.length);
            sceneData.objects.push(copy);
            rebuildObjects();
            selectEntity('object', copy.id);
        } else {
            const source = sceneData.cameras.find(item => item.id === selected.id);
            if (!source) return;
            const copy = normalizeCamera({...cloneJson(source), id:uid('camera'), name:`${source.name}副本`, position:[source.position[0] + 0.5, source.position[1], source.position[2]]}, sceneData.cameras.length);
            sceneData.cameras.push(copy);
            sceneData.activeCameraId = copy.id;
            rebuildCameras();
            selectEntity('camera', copy.id);
        }
        renderSceneTree();
        saveState();
        setStatus('已复制选中对象');
    }

    function deleteSelected() {
        if (!selected) return;
        if (selected.kind === 'camera') {
            if (sceneData.cameras.length <= 1) { setStatus('至少需要保留一个机位', true); return; }
            sceneData.cameras = sceneData.cameras.filter(item => item.id !== selected.id);
            sceneData.timeline.tracks = sceneData.timeline.tracks.filter(track => track.targetId !== selected.id);
            sceneData.activeCameraId = sceneData.cameras[0].id;
            rebuildCameras();
        } else {
            sceneData.objects = sceneData.objects.filter(item => item.id !== selected.id);
            rebuildObjects();
        }
        selected = null;
        renderSceneTree();
        renderInspector();
        updateSelectionBox();
        saveState();
        setStatus('已删除选中对象');
    }

    function selectEntity(kind, id) {
        selected = id ? {kind, id} : null;
        if (kind === 'camera' && id) {
            sceneData.activeCameraId = id;
            cameraSelect.value = id;
        }
        updateSelectionBox();
        updateTransformAttachment();
        renderSceneTree();
        renderInspector();
        requestRender();
    }

    function updateSelectionBox() {
        if (selected?.kind === 'object') {
            const object = runtimeObjects.get(selected.id);
            if (object?.visible) {
                selectionBox.setFromObject(object);
                selectionBox.visible = true;
                return;
            }
        }
        selectionBox.visible = false;
    }

    function renderCameraSelect() {
        cameraSelect.replaceChildren();
        sceneData.cameras.forEach(camera => {
            const option = document.createElement('option');
            option.value = camera.id;
            option.textContent = `${camera.name} · ${camera.aspect} · ${Number(camera.focalLength)}mm`;
            cameraSelect.appendChild(option);
        });
        cameraSelect.value = sceneData.activeCameraId;
    }

    function createTreeSection(title, items, kind) {
        const section = document.createElement('section');
        section.className = 'director-tree-section';
        const heading = document.createElement('div');
        heading.className = 'director-tree-section-title';
        heading.textContent = `${title} ${items.length}`;
        section.appendChild(heading);
        items.forEach(record => {
            const row = document.createElement('div');
            row.className = `director-tree-row ${selected?.kind === kind && selected.id === record.id ? 'selected' : ''}`;
            const select = document.createElement('button');
            select.type = 'button';
            select.className = 'director-tree-select';
            const icon = document.createElement('span');
            icon.textContent = kind === 'camera' ? '▣' : record.type === 'character' ? '♙' : record.type === 'model' ? '⬡' : '◇';
            const name = document.createElement('span');
            name.textContent = record.name;
            select.append(icon, name);
            select.addEventListener('click', () => selectEntity(kind, record.id));
            const visible = document.createElement('button');
            visible.type = 'button';
            visible.className = 'director-tree-toggle';
            visible.title = record.visible === false ? '显示' : '隐藏';
            visible.textContent = record.visible === false ? '○' : '●';
            visible.addEventListener('click', event => {
                event.stopPropagation();
                record.visible = record.visible === false;
                if (kind === 'object') runtimeObjects.get(record.id).visible = record.visible;
                else cameraHelpers.get(record.id).visible = record.visible && viewMode === 'director';
                renderSceneTree(); updateSelectionBox(); saveState(); requestRender();
            });
            const lock = document.createElement('button');
            lock.type = 'button';
            lock.className = 'director-tree-toggle';
            lock.title = record.locked ? '解锁' : '锁定';
            lock.textContent = record.locked ? '■' : '□';
            lock.addEventListener('click', event => {
                event.stopPropagation(); record.locked = !record.locked; renderSceneTree(); renderInspector(); saveState();
            });
            row.append(select, visible, lock);
            section.appendChild(row);
        });
        return section;
    }

    function renderSceneTree() {
        const query = String(searchInput.value || '').trim().toLowerCase();
        const matches = item => !query || String(item.name || '').toLowerCase().includes(query);
        const characters = sceneData.objects.filter(item => item.type === 'character' && matches(item));
        const geometry = sceneData.objects.filter(item => item.type === 'geometry' && matches(item));
        const models = sceneData.objects.filter(item => item.type === 'model' && matches(item));
        const cameras = sceneData.cameras.filter(matches);
        treeElement.replaceChildren(
            createTreeSection('角色', characters, 'object'),
            createTreeSection('几何模型', geometry, 'object'),
            createTreeSection('导入模型', models, 'object'),
            createTreeSection('摄像机', cameras, 'camera'),
        );
    }

    function vectorFields(name, values, step='0.1') {
        return `<div class="director-vector-row" data-vector="${name}">
            ${['X','Y','Z'].map((axis, index) => `<label><span>${axis}</span><input type="number" step="${step}" data-vector-index="${index}" value="${Number(values[index].toFixed(3))}"></label>`).join('')}
        </div>`;
    }

    function quaternionEuler(quaternion) {
        const value = new THREE.Quaternion().fromArray(quaternion).normalize();
        const euler = new THREE.Euler().setFromQuaternion(value, 'XYZ');
        return [euler.x, euler.y, euler.z].map(radian => radian * 180 / Math.PI);
    }

    function eulerQuaternion(degrees) {
        const euler = new THREE.Euler(...degrees.map(value => value * Math.PI / 180), 'XYZ');
        return new THREE.Quaternion().setFromEuler(euler).toArray();
    }

    function renderInspector() {
        if (selected?.kind === 'object') {
            const record = sceneData.objects.find(item => item.id === selected.id);
            if (!record) { selected = null; return renderInspector(); }
            inspectorTitle.textContent = record.type === 'character' ? '角色属性' : record.type === 'model' ? '导入模型' : '模型属性';
            const uiState = characterEditorState.get(record.id) || {joint:'chest', ik:'leftHand'};
            characterEditorState.set(record.id, uiState);
            const poseJoints = record.type === 'character' ? poseJointMap(THREE, record.poseId) : {};
            const jointRotation = record.type === 'character' ? quaternionEuler(record.joints[uiState.joint] || poseJoints[uiState.joint] || [0,0,0,1]) : [0,0,0];
            const runtimeObject = runtimeObjects.get(record.id);
            const ikPosition = record.type === 'character' ? (record.ikTargets[uiState.ik] || characterEffectorPosition(runtimeObject, uiState.ik) || [0,1,0]) : [0,1,0];
            inspector.innerHTML = `<div class="director-property-group">
                <label class="director-property"><span>名称</span><input type="text" data-field="name" maxlength="80" value="${escapeHtml(record.name)}"></label>
                <label class="director-property"><span>颜色</span><input type="color" data-field="color" value="${record.color}"></label>
            </div>
            ${record.type === 'model' ? `<div class="director-property-group"><strong>模型文件</strong><div class="director-asset-name">${escapeHtml(record.assetName || record.assetUrl || '未设置')}</div></div>` : ''}
            ${record.type === 'character' ? `<div class="director-property-group"><strong>姿态预设</strong>
                <label class="director-property"><span>姿态</span><select data-pose>${Object.entries(POSE_PRESETS).map(([id, preset]) => `<option value="${id}" ${id === record.poseId ? 'selected' : ''}>${preset.label}</option>`).join('')}</select></label>
            </div>
            <div class="director-property-group"><strong>骨骼微调</strong>
                <label class="director-property"><span>关节</span><select data-joint-select>${JOINT_NAMES.map(name => `<option value="${name}" ${name === uiState.joint ? 'selected' : ''}>${JOINT_LABELS[name] || name}</option>`).join('')}</select></label>
                ${vectorFields('jointRotation', jointRotation, '1')}
                <button type="button" class="director-inspector-button" data-reset-joint>重置当前关节</button>
            </div>
            <div class="director-property-group"><strong>基础 IK</strong>
                <label class="director-property"><span>末端</span><select data-ik-select>${Object.entries(IK_TARGETS).map(([id, label]) => `<option value="${id}" ${id === uiState.ik ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
                ${vectorFields('ikTarget', ikPosition)}
                <div class="director-inline-actions"><button type="button" class="director-inspector-button" data-apply-ik>应用 IK</button><button type="button" class="director-inspector-button" data-clear-ik>清除 IK</button></div>
            </div>` : ''}
            <div class="director-property-group"><strong>位置</strong>${vectorFields('position', record.position)}</div>
            <div class="director-property-group"><strong>旋转</strong>${vectorFields('rotation', quaternionEuler(record.rotation), '1')}</div>
            <div class="director-property-group"><strong>缩放</strong>${vectorFields('scale', record.scale, '0.05')}</div>
            <div class="director-property-group director-checks">
                <label><input type="checkbox" data-field="visible" ${record.visible !== false ? 'checked' : ''}>显示</label>
                <label><input type="checkbox" data-field="locked" ${record.locked ? 'checked' : ''}>锁定</label>
            </div>`;
            bindObjectInspector(record);
            return;
        }
        if (selected?.kind === 'camera') {
            const record = sceneData.cameras.find(item => item.id === selected.id);
            if (!record) { selected = null; return renderInspector(); }
            inspectorTitle.textContent = '机位属性';
            inspector.innerHTML = `<div class="director-property-group">
                <label class="director-property"><span>名称</span><input type="text" data-field="name" maxlength="80" value="${escapeHtml(record.name)}"></label>
                <label class="director-property"><span>画幅</span><select data-field="aspect">${Object.keys(ASPECT_RATIOS).map(aspect => `<option value="${aspect}" ${aspect === record.aspect ? 'selected' : ''}>${aspect}</option>`).join('')}</select></label>
                <label class="director-property"><span>焦段</span><input type="number" min="10" max="300" step="1" data-field="focalLength" value="${record.focalLength}"><em>mm</em></label>
            </div>
            <div class="director-property-group"><strong>位置</strong>${vectorFields('position', record.position)}</div>
            <div class="director-property-group"><strong>旋转</strong>${vectorFields('rotation', quaternionEuler(record.rotation), '1')}</div>
            <div class="director-property-group director-checks">
                <label><input type="checkbox" data-field="visible" ${record.visible !== false ? 'checked' : ''}>显示视锥</label>
                <label><input type="checkbox" data-field="locked" ${record.locked ? 'checked' : ''}>锁定</label>
            </div>
            <button type="button" class="director-inspector-button" data-camera-from-view>使用当前导演视角</button>
            <div class="director-scene-summary">当前机位已有 <span>${cameraTrack(sceneData.timeline, record.id)?.keyframes.length || 0}</span> 个关键帧</div>`;
            bindCameraInspector(record);
            return;
        }
        inspectorTitle.textContent = '3D 场景';
        const environment = sceneData.environment;
        inspector.innerHTML = `<div class="director-property-group">
            <label class="director-property"><span>背景模式</span><select data-environment="backgroundMode"><option value="flat" ${environment.backgroundMode === 'flat' ? 'selected' : ''}>平面背景</option><option value="panorama" ${environment.backgroundMode === 'panorama' ? 'selected' : ''}>全景球</option></select></label>
            <label class="director-property"><span>天空颜色</span><input type="color" data-environment="skyColor" value="${environment.skyColor}"></label>
            <label class="director-property"><span>地面颜色</span><input type="color" data-environment="groundColor" value="${environment.groundColor}"></label>
            <label class="director-property"><span>地面透明度</span><input type="range" min="0" max="1" step="0.05" data-environment="groundOpacity" value="${environment.groundOpacity}"><em>${environment.groundOpacity.toFixed(2)}</em></label>
        </div>
        <div class="director-property-group director-checks">
            <label><input type="checkbox" data-environment="gridVisible" ${environment.gridVisible ? 'checked' : ''}>显示网格</label>
            <label><input type="checkbox" data-environment="groundVisible" ${environment.groundVisible ? 'checked' : ''}>地面</label>
            <label><input type="checkbox" data-environment="labelsVisible" ${environment.labelsVisible ? 'checked' : ''}>角色标签</label>
        </div>
        <div class="director-property-group"><strong>变换吸附</strong>
            <label class="director-property"><span>位移</span><input type="number" min="0.01" max="10" step="0.01" data-transform-setting="translationSnap" value="${sceneData.settings.translationSnap}"><em>m</em></label>
            <label class="director-property"><span>旋转</span><input type="number" min="1" max="90" step="1" data-transform-setting="rotationSnap" value="${sceneData.settings.rotationSnap}"><em>°</em></label>
            <label class="director-property"><span>缩放</span><input type="number" min="0.01" max="2" step="0.01" data-transform-setting="scaleSnap" value="${sceneData.settings.scaleSnap}"></label>
        </div>
        <div class="director-property-group"><strong>镜头时间轴</strong>
            <label class="director-property"><span>时长</span><input type="number" min="0.5" max="120" step="0.5" data-timeline-setting="duration" value="${sceneData.timeline.duration}"><em>秒</em></label>
            <label class="director-property"><span>帧率</span><input type="number" min="1" max="60" step="1" data-timeline-setting="fps" value="${sceneData.timeline.fps}"><em>fps</em></label>
        </div>
        <div class="director-scene-summary"><span>${sceneData.objects.filter(item => item.type === 'character').length}</span> 个角色 · <span>${sceneData.objects.filter(item => item.type === 'model').length}</span> 个导入模型 · <span>${sceneData.cameras.length}</span> 个机位</div>`;
        bindEnvironmentInspector();
    }

    function vectorValues(group) {
        return [...group.querySelectorAll('[data-vector-index]')].map(input => safeNumber(input.value, 0));
    }

    function bindObjectInspector(record) {
        inspector.querySelector('[data-field="name"]').addEventListener('change', event => {
            record.name = String(event.target.value || record.name).trim().slice(0, 80) || record.name;
            rebuildObjects(); renderSceneTree(); selectEntity('object', record.id); saveState();
        });
        inspector.querySelector('[data-field="color"]').addEventListener('change', event => {
            record.color = safeColor(event.target.value, record.color); rebuildObjects(); selectEntity('object', record.id); saveState();
        });
        inspector.querySelectorAll('[data-vector="position"], [data-vector="rotation"], [data-vector="scale"]').forEach(group => {
            const applyVector = history => {
                const values = vectorValues(group);
                if (group.dataset.vector === 'rotation') record.rotation = eulerQuaternion(values);
                else if (group.dataset.vector === 'scale') record.scale = values.map(value => Math.max(0.05, Math.min(20, value)));
                else record.position = values;
                const object = runtimeObjects.get(record.id);
                setRecordTransform(THREE, object, record); updateSelectionBox(); saveState({history}); requestRender();
            };
            group.addEventListener('input', () => applyVector(false));
            group.addEventListener('change', () => applyVector(true));
        });
        inspector.querySelector('[data-field="visible"]').addEventListener('change', event => {
            record.visible = event.target.checked; runtimeObjects.get(record.id).visible = record.visible; renderSceneTree(); updateSelectionBox(); saveState(); requestRender();
        });
        inspector.querySelector('[data-field="locked"]').addEventListener('change', event => {
            record.locked = event.target.checked; renderSceneTree(); updateTransformAttachment(); saveState();
        });
        if (record.type === 'character') bindCharacterInspector(record);
    }

    function bindCharacterInspector(record) {
        const uiState = characterEditorState.get(record.id) || {joint:'chest', ik:'leftHand'};
        inspector.querySelector('[data-pose]').addEventListener('change', event => {
            record.poseId = POSE_PRESETS[event.target.value] ? event.target.value : 'standing';
            record.joints = poseJointMap(THREE, record.poseId);
            record.ikTargets = {};
            const object = runtimeObjects.get(record.id);
            applyCharacterPose(THREE, object, record);
            updateSelectionBox(); renderInspector(); saveState(); requestRender();
            setStatus(`已应用姿态：${POSE_PRESETS[record.poseId].label}`);
        });
        inspector.querySelector('[data-joint-select]').addEventListener('change', event => {
            uiState.joint = JOINT_NAMES.includes(event.target.value) ? event.target.value : 'chest';
            characterEditorState.set(record.id, uiState);
            renderInspector();
        });
        const jointGroup = inspector.querySelector('[data-vector="jointRotation"]');
        const applyJoint = history => {
            record.joints[uiState.joint] = eulerQuaternion(vectorValues(jointGroup));
            record.ikTargets = {};
            applyCharacterPose(THREE, runtimeObjects.get(record.id), record);
            updateSelectionBox(); saveState({history}); requestRender();
        };
        jointGroup.addEventListener('input', () => applyJoint(false));
        jointGroup.addEventListener('change', () => applyJoint(true));
        inspector.querySelector('[data-reset-joint]').addEventListener('click', () => {
            record.joints[uiState.joint] = poseJointMap(THREE, record.poseId)[uiState.joint];
            record.ikTargets = {};
            applyCharacterPose(THREE, runtimeObjects.get(record.id), record);
            renderInspector(); updateSelectionBox(); saveState(); requestRender();
        });
        inspector.querySelector('[data-ik-select]').addEventListener('change', event => {
            uiState.ik = Object.prototype.hasOwnProperty.call(IK_TARGETS, event.target.value) ? event.target.value : 'leftHand';
            characterEditorState.set(record.id, uiState);
            renderInspector();
        });
        inspector.querySelector('[data-apply-ik]').addEventListener('click', () => {
            const object = runtimeObjects.get(record.id);
            const target = vectorValues(inspector.querySelector('[data-vector="ikTarget"]'));
            record.ikTargets[uiState.ik] = target;
            applyCharacterPose(THREE, object, record);
            if (!solveCharacterIk(THREE, object, uiState.ik, target)) {
                setStatus('IK 求解失败，请检查角色骨骼', true);
                return;
            }
            record.joints = captureCharacterJoints(object);
            updateSelectionBox(); renderInspector(); saveState(); requestRender();
            setStatus(`${IK_TARGETS[uiState.ik]} IK 已应用`);
        });
        inspector.querySelector('[data-clear-ik]').addEventListener('click', () => {
            delete record.ikTargets[uiState.ik];
            record.joints = poseJointMap(THREE, record.poseId);
            applyCharacterPose(THREE, runtimeObjects.get(record.id), record);
            updateSelectionBox(); renderInspector(); saveState(); requestRender();
            setStatus(`${IK_TARGETS[uiState.ik]} IK 已清除`);
        });
    }

    function updateCameraRuntime(record, options={}) {
        const camera = shotCameras.get(record.id);
        if (!camera) return;
        camera.position.fromArray(record.position);
        camera.quaternion.fromArray(record.rotation).normalize();
        camera.fov = focalLengthToFov(record.focalLength);
        camera.near = record.near;
        camera.far = record.far;
        camera.updateProjectionMatrix();
        const helper = cameraHelpers.get(record.id);
        const helperCamera = helper?.userData?.directorCamera;
        if (helperCamera) {
            helperCamera.position.copy(camera.position);
            helperCamera.quaternion.copy(camera.quaternion);
            helperCamera.fov = camera.fov;
            helperCamera.near = camera.near;
            helperCamera.far = Math.min(camera.far, 8);
            helperCamera.aspect = camera.aspect;
            helperCamera.updateProjectionMatrix();
            helperCamera.updateMatrixWorld(true);
        }
        helper.visible = record.visible !== false && viewMode === 'director';
        helper.update();
        if (options.refreshSelect !== false) renderCameraSelect();
        requestRender();
    }

    function bindCameraInspector(record) {
        inspector.querySelector('[data-field="name"]').addEventListener('change', event => {
            record.name = String(event.target.value || record.name).trim().slice(0, 80) || record.name;
            renderSceneTree(); renderCameraSelect(); saveState();
        });
        inspector.querySelector('[data-field="aspect"]').addEventListener('change', event => { record.aspect = event.target.value; updateCameraRuntime(record); saveState(); });
        inspector.querySelector('[data-field="focalLength"]').addEventListener('change', event => { record.focalLength = Math.max(10, Math.min(300, safeNumber(event.target.value, 50))); updateCameraRuntime(record); saveState(); });
        inspector.querySelectorAll('[data-vector]').forEach(group => {
            const applyVector = history => {
                const values = vectorValues(group);
                if (group.dataset.vector === 'rotation') record.rotation = eulerQuaternion(values);
                else record.position = values;
                updateCameraRuntime(record); saveState({history});
            };
            group.addEventListener('input', () => applyVector(false));
            group.addEventListener('change', () => applyVector(true));
        });
        inspector.querySelector('[data-field="visible"]').addEventListener('change', event => { record.visible = event.target.checked; updateCameraRuntime(record); renderSceneTree(); saveState(); });
        inspector.querySelector('[data-field="locked"]').addEventListener('change', event => { record.locked = event.target.checked; renderSceneTree(); updateTransformAttachment(); saveState(); });
        inspector.querySelector('[data-camera-from-view]').addEventListener('click', () => {
            record.position = editorCamera.position.toArray();
            record.rotation = editorCamera.quaternion.toArray();
            updateCameraRuntime(record); renderInspector(); saveState(); setStatus('机位已更新为当前导演视角');
        });
    }

    function bindEnvironmentInspector() {
        inspector.querySelectorAll('[data-environment]').forEach(control => control.addEventListener('change', event => {
            const key = control.dataset.environment;
            let value = control.type === 'checkbox' ? control.checked : control.value;
            if (key === 'groundOpacity') value = Math.max(0, Math.min(1, safeNumber(value, 0.42)));
            sceneData.environment[key] = value;
            applyEnvironment(); renderInspector(); saveState();
        }));
        inspector.querySelectorAll('[data-transform-setting]').forEach(control => control.addEventListener('change', () => {
            const key = control.dataset.transformSetting;
            const limits = key === 'rotationSnap' ? [1,90,5] : key === 'scaleSnap' ? [0.01,2,0.05] : [0.01,10,0.1];
            sceneData.settings[key] = Math.max(limits[0], Math.min(limits[1], safeNumber(control.value, limits[2])));
            applyTransformSettings(); renderInspector(); saveState();
        }));
        inspector.querySelectorAll('[data-timeline-setting]').forEach(control => control.addEventListener('change', () => {
            const key = control.dataset.timelineSetting;
            if (key === 'duration') sceneData.timeline.duration = Math.max(0.5, Math.min(120, safeNumber(control.value, 5)));
            else sceneData.timeline.fps = Math.max(1, Math.min(60, Math.round(safeNumber(control.value, 24))));
            sceneData.timeline.currentTime = Math.min(sceneData.timeline.currentTime, sceneData.timeline.duration);
            updateTimelineUi(); renderInspector(); saveState();
        }));
    }

    function setMode(mode) {
        transformMode = ['select','translate','rotate','scale'].includes(mode) ? mode : 'select';
        root.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('active', button.dataset.mode === transformMode));
        updateTransformAttachment();
        setStatus({select:'选择模式', translate:'移动模式：拖动轴向箭头或对象', rotate:'旋转模式：拖动彩色圆环', scale:'缩放模式：拖动轴向方块'}[transformMode]);
    }

    function setView(mode) {
        viewMode = mode === 'camera' ? 'camera' : 'director';
        root.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === viewMode));
        cameraHelpers.forEach((helper, id) => {
            const record = sceneData.cameras.find(camera => camera.id === id);
            helper.visible = viewMode === 'director' && record?.visible !== false;
        });
        updateTransformAttachment();
        resizeRenderer();
        requestRender();
    }

    function pointerPosition(event) {
        const rect = renderer.domElement.getBoundingClientRect();
        pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        return rect;
    }

    function objectAtPointer(event) {
        pointerPosition(event);
        raycaster.setFromCamera(pointer, activeRenderCamera());
        const hits = raycaster.intersectObjects([...runtimeObjects.values()].filter(object => object.visible), true);
        for (const hit of hits) {
            let target = hit.object;
            while (target && !target.userData.objectId) target = target.parent;
            if (target?.userData.objectId) return runtimeObjects.get(target.userData.objectId) || target;
        }
        return null;
    }

    function planePoint(event, y) {
        pointerPosition(event);
        raycaster.setFromCamera(pointer, activeRenderCamera());
        const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -y);
        const target = new THREE.Vector3();
        return raycaster.ray.intersectPlane(plane, target) ? target : null;
    }

    function beginObjectDrag(event, object, record) {
        if (record.locked || transformMode === 'select') return false;
        const startPoint = transformMode === 'translate' ? planePoint(event, object.position.y) : null;
        drag = {
            kind:'transform', id:record.id, mode:transformMode,
            startX:event.clientX, startY:event.clientY,
            position:object.position.clone(), quaternion:object.quaternion.clone(), scale:object.scale.clone(),
            planePoint:startPoint,
        };
        renderer.domElement.setPointerCapture?.(event.pointerId);
        return true;
    }

    function onPointerDown(event) {
        if (event.button > 2) return;
        if (transformDragging || transformControls.axis) return;
        root.focus({preventScroll:true});
        const hit = event.button === 0 ? objectAtPointer(event) : null;
        if (hit) {
            const id = hit.userData.objectId;
            const record = sceneData.objects.find(item => item.id === id);
            selectEntity('object', id);
            if (record && beginObjectDrag(event, hit, record)) {
                event.preventDefault();
                return;
            }
        } else if (event.button === 0) {
            selectEntity(null, '');
        }
        if (viewMode === 'director') {
            drag = {kind:event.button === 2 ? 'pan' : 'orbit', startX:event.clientX, startY:event.clientY, theta:orbit.theta, phi:orbit.phi, target:orbitTarget.clone()};
            renderer.domElement.setPointerCapture?.(event.pointerId);
            event.preventDefault();
        }
    }

    function onPointerMove(event) {
        if (transformDragging) return;
        if (!drag) return;
        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (drag.kind === 'orbit') {
            orbit.theta = drag.theta - dx * 0.007;
            orbit.phi = drag.phi - dy * 0.007;
            updateEditorCamera();
            return;
        }
        if (drag.kind === 'pan') {
            const scale = orbit.radius * 0.0014;
            const right = new THREE.Vector3(1, 0, 0).applyQuaternion(editorCamera.quaternion);
            const up = new THREE.Vector3(0, 1, 0);
            orbitTarget.copy(drag.target).addScaledVector(right, -dx * scale).addScaledVector(up, dy * scale);
            updateEditorCamera();
            return;
        }
        const object = runtimeObjects.get(drag.id);
        if (!object) return;
        if (drag.mode === 'translate') {
            if (event.shiftKey) {
                object.position.copy(drag.position);
                object.position.y = Math.max(0, drag.position.y - dy * Math.max(0.002, orbit.radius * 0.0012));
            } else {
                const current = planePoint(event, drag.position.y);
                if (current && drag.planePoint) object.position.copy(drag.position).add(current.sub(drag.planePoint));
            }
        } else if (drag.mode === 'rotate') {
            const euler = new THREE.Euler().setFromQuaternion(drag.quaternion, 'XYZ');
            euler.y += dx * 0.01;
            euler.x += dy * 0.008;
            object.quaternion.setFromEuler(euler);
        } else if (drag.mode === 'scale') {
            const factor = Math.max(0.05, Math.min(20, Math.exp((dx - dy) * 0.008)));
            object.scale.copy(drag.scale).multiplyScalar(factor);
        }
        if (sceneData.settings.snapEnabled) {
            if (drag.mode === 'translate') object.position.toArray().forEach((value, index) => object.position.setComponent(index, Math.round(value / sceneData.settings.translationSnap) * sceneData.settings.translationSnap));
            if (drag.mode === 'rotate') {
                const step = sceneData.settings.rotationSnap * Math.PI / 180;
                const euler = new THREE.Euler().setFromQuaternion(object.quaternion, 'XYZ');
                euler.set(Math.round(euler.x / step) * step, Math.round(euler.y / step) * step, Math.round(euler.z / step) * step);
                object.quaternion.setFromEuler(euler);
            }
            if (drag.mode === 'scale') object.scale.toArray().forEach((value, index) => object.scale.setComponent(index, Math.max(0.05, Math.round(value / sceneData.settings.scaleSnap) * sceneData.settings.scaleSnap)));
        }
        writeObjectRecord(drag.id);
        updateSelectionBox();
        requestRender();
    }

    function onPointerUp(event) {
        if (!drag) return;
        const transformed = drag.kind === 'transform';
        drag = null;
        try { renderer.domElement.releasePointerCapture?.(event.pointerId); } catch (_) {}
        if (transformed) {
            renderInspector();
            saveState();
            setStatus('对象变换已保存');
        }
    }

    function focusSelected() {
        if (selected?.kind !== 'object') return;
        const object = runtimeObjects.get(selected.id);
        if (!object) return;
        const box = new THREE.Box3().setFromObject(object);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3()).length();
        orbitTarget.copy(center);
        orbit.radius = Math.max(2.5, size * 2.4);
        updateEditorCamera();
        setView('director');
    }

    function updateTimelineUi() {
        const timeline = sceneData.timeline;
        timelineScrubber.max = String(timeline.duration);
        timelineScrubber.step = String(1 / timeline.fps);
        timelineScrubber.value = String(timeline.currentTime);
        timelineTime.textContent = `${timeline.currentTime.toFixed(2)} / ${timeline.duration.toFixed(2)} 秒`;
        timelinePlayButton.textContent = playbackFrame ? '❚❚' : '▶';
    }

    function applyTimelineTime(value, options={}) {
        const timeline = sceneData.timeline;
        timeline.currentTime = Math.max(0, Math.min(timeline.duration, safeNumber(value, 0)));
        sceneData.cameras.forEach(record => {
            const sample = sampleCameraTrack(THREE, timeline, record.id);
            if (!sample) return;
            record.position = sample.position;
            record.rotation = sample.rotation;
            record.focalLength = sample.focalLength;
            updateCameraRuntime(record, {refreshSelect:false});
        });
        updateTimelineUi();
        updateSelectionBox();
        requestRender();
        if (options.persist) saveState({history:options.history !== false});
        if (options.refreshInspector) renderInspector();
    }

    function stopPlayback(persist=true) {
        if (playbackFrame) cancelAnimationFrame(playbackFrame);
        playbackFrame = 0;
        updateTimelineUi();
        if (persist) {
            renderCameraSelect();
            renderInspector();
            saveState({history:false});
        }
    }

    function playbackStep(now) {
        if (!playbackFrame) return;
        const elapsed = (now - playbackStartedAt) / 1000;
        const next = playbackStartTime + elapsed;
        if (next >= sceneData.timeline.duration) {
            applyTimelineTime(sceneData.timeline.duration);
            stopPlayback(true);
            return;
        }
        applyTimelineTime(next);
        playbackFrame = requestAnimationFrame(playbackStep);
    }

    function togglePlayback() {
        if (playbackFrame) {
            stopPlayback(true);
            return;
        }
        if (sceneData.timeline.currentTime >= sceneData.timeline.duration) applyTimelineTime(0);
        playbackStartedAt = performance.now();
        playbackStartTime = sceneData.timeline.currentTime;
        playbackFrame = requestAnimationFrame(playbackStep);
        updateTimelineUi();
    }

    function addCameraKeyframe() {
        const record = selected?.kind === 'camera' ? sceneData.cameras.find(item => item.id === selected.id) : activeShotRecord();
        if (!record) return;
        upsertCameraKeyframe(sceneData.timeline, record, () => uid('keyframe'));
        renderInspector(); saveState(); updateTimelineUi();
        setStatus(`已在 ${sceneData.timeline.currentTime.toFixed(2)} 秒记录${record.name}`);
    }

    function deleteCameraKeyframe() {
        const record = selected?.kind === 'camera' ? sceneData.cameras.find(item => item.id === selected.id) : activeShotRecord();
        if (!record || !removeCameraKeyframe(sceneData.timeline, record.id)) {
            setStatus('当前时间附近没有可删除的关键帧', true);
            return;
        }
        renderInspector(); saveState(); updateTimelineUi();
        setStatus('关键帧已删除');
    }

    function resizeRenderer() {
        const width = Math.max(320, viewport.clientWidth);
        const height = Math.max(240, viewport.clientHeight);
        renderer.setSize(width, height, false);
        editorCamera.aspect = width / height;
        editorCamera.updateProjectionMatrix();
        const shot = shotCameras.get(sceneData.activeCameraId);
        if (shot) { shot.aspect = width / height; shot.updateProjectionMatrix(); }
        requestRender();
    }

    async function capturePreview() {
        if (saveBusy) return node.data.previewUrl;
        saveBusy = true;
        previewButton.disabled = true;
        saveButton.disabled = true;
        setStatus('正在从当前激活摄像机生成画面、深度图和角色蒙版…');
        const record = activeShotRecord();
        const camera = record ? shotCameras.get(record.id) : null;
        if (!record || !camera) {
            saveBusy = false;
            previewButton.disabled = false;
            saveButton.disabled = false;
            throw new Error('当前场景没有可用的激活摄像机');
        }
        const oldSize = renderer.getSize(new THREE.Vector2());
        const oldPixelRatio = renderer.getPixelRatio();
        const oldAspect = camera.aspect;
        const helperVisibility = [...cameraHelpers.entries()].map(([id, helper]) => [id, helper.visible]);
        const selectionVisible = selectionBox.visible;
        const transformVisible = transformControls.visible;
        const gridVisible = grid.visible;
        const groundVisible = ground.visible;
        const oldBackground = scene.background;
        const oldOverrideMaterial = scene.overrideMaterial;
        const oldOutputColorSpace = renderer.outputColorSpace;
        const oldToneMapping = renderer.toneMapping;
        const objectVisibility = [...runtimeObjects.entries()].map(([id, object]) => [id, object.visible]);
        const labelVisibility = [];
        runtimeObjects.forEach(object => object.traverse(child => {
            if (child.userData.directorLabel) labelVisibility.push([child, child.visible]);
        }));
        const depthMaterial = new THREE.MeshDepthMaterial({depthPacking:THREE.RGBADepthPacking});
        const maskMaterials = [];
        const originalMaterials = [];
        try {
            const [width, height] = aspectDimensions(record.aspect);
            cameraHelpers.forEach(helper => { helper.visible = false; });
            selectionBox.visible = false;
            transformControls.visible = false;
            grid.visible = false;
            labelVisibility.forEach(([label]) => { label.visible = false; });
            renderer.setPixelRatio(1);
            renderer.setSize(width, height, false);
            camera.aspect = width / height;
            camera.updateProjectionMatrix();

            scene.background = oldBackground;
            scene.overrideMaterial = null;
            renderer.render(scene, camera);
            const previewBlob = await canvasBlob(renderer.domElement);

            scene.background = new THREE.Color(0xffffff);
            scene.overrideMaterial = depthMaterial;
            renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
            renderer.toneMapping = THREE.NoToneMapping;
            renderer.render(scene, camera);
            const depthBlob = await linearDepthBlob(renderer.domElement, camera);

            scene.overrideMaterial = null;
            scene.background = new THREE.Color(0x000000);
            ground.visible = false;
            runtimeObjects.forEach((object, id) => {
                const objectRecord = sceneData.objects.find(item => item.id === id);
                object.visible = objectRecord?.type === 'character' && objectRecord.visible !== false;
                if (objectRecord?.type !== 'character') return;
                const material = new THREE.MeshBasicMaterial({color:objectRecord.maskColor, toneMapped:false});
                maskMaterials.push(material);
                object.traverse(child => {
                    if (!child.isMesh) return;
                    originalMaterials.push([child, child.material]);
                    child.material = material;
                });
            });
            renderer.render(scene, camera);
            const maskBlob = await canvasBlob(renderer.domElement);

            const stamp = Date.now();
            const [previewUpload, depthUpload, maskUpload] = await Promise.all([
                context.uploadAsset(previewBlob, {filename:`3d-director-${stamp}.png`}),
                context.uploadAsset(depthBlob, {filename:`3d-director-depth-${stamp}.png`}),
                context.uploadAsset(maskBlob, {filename:`3d-director-character-mask-${stamp}.png`}),
            ]);
            node.data.previewUrl = previewUpload.url;
            node.data.depthUrl = depthUpload.url;
            node.data.characterMaskUrl = maskUpload.url;
            node.data.cameraOutputFingerprint = cameraOutputFingerprint(sceneData);
            syncDirectorOutputs(node);
            context.save?.();
            context.update?.(node);
            setStatus(`已从${record.name}生成摄像机画面、深度图和角色蒙版`);
            return previewUpload.url;
        } catch (error) {
            setStatus(`输出生成失败：${error.message || error}`, true);
            throw error;
        } finally {
            originalMaterials.forEach(([mesh, material]) => { mesh.material = material; });
            maskMaterials.forEach(material => material.dispose());
            depthMaterial.dispose();
            objectVisibility.forEach(([id, visible]) => { const object = runtimeObjects.get(id); if (object) object.visible = visible; });
            labelVisibility.forEach(([label, visible]) => { label.visible = visible; });
            scene.background = oldBackground;
            scene.overrideMaterial = oldOverrideMaterial;
            renderer.outputColorSpace = oldOutputColorSpace;
            renderer.toneMapping = oldToneMapping;
            ground.visible = groundVisible;
            grid.visible = gridVisible;
            renderer.setPixelRatio(oldPixelRatio);
            renderer.setSize(oldSize.x, oldSize.y, false);
            camera.aspect = oldAspect;
            camera.updateProjectionMatrix();
            helperVisibility.forEach(([id, visible]) => { const helper = cameraHelpers.get(id); if (helper) helper.visible = visible; });
            selectionBox.visible = selectionVisible;
            transformControls.visible = transformVisible;
            saveBusy = false;
            previewButton.disabled = false;
            saveButton.disabled = false;
            resizeRenderer();
        }
    }

    function closeAddMenu() { addMenu.hidden = true; }
    function toggleAddMenu() { addMenu.hidden = !addMenu.hidden; }

    root.querySelectorAll('[data-toggle-add]').forEach(button => button.addEventListener('click', toggleAddMenu));
    root.querySelectorAll('[data-add-character]').forEach(button => button.addEventListener('click', () => { addCharacter(button.dataset.addCharacter); closeAddMenu(); }));
    root.querySelectorAll('[data-add-crowd]').forEach(button => button.addEventListener('click', () => { addCrowd(Number(button.dataset.addCrowd) || 3); closeAddMenu(); }));
    root.querySelectorAll('[data-add-geometry]').forEach(button => button.addEventListener('click', () => { addGeometry(button.dataset.addGeometry); closeAddMenu(); }));
    root.querySelector('[data-import-model]').addEventListener('click', () => { closeAddMenu(); modelFileInput.click(); });
    modelFileInput.addEventListener('change', () => {
        const file = modelFileInput.files?.[0];
        modelFileInput.value = '';
        importModel(file).catch(error => setStatus(`模型导入失败：${error.message || error}`, true));
    });
    root.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
    root.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
    root.querySelector('[data-action="add-camera"]').addEventListener('click', addCamera);
    root.querySelector('[data-action="duplicate"]').addEventListener('click', duplicateSelected);
    root.querySelector('[data-action="delete"]').addEventListener('click', deleteSelected);
    root.querySelector('[data-action="focus"]').addEventListener('click', focusSelected);
    searchInput.addEventListener('input', renderSceneTree);
    cameraSelect.addEventListener('change', () => {
        sceneData.activeCameraId = cameraSelect.value;
        if (viewMode === 'camera') resizeRenderer();
        updateTransformAttachment(); saveState(); requestRender();
    });
    transformSpaceSelect.addEventListener('change', () => {
        sceneData.settings.transformSpace = transformSpaceSelect.value === 'local' ? 'local' : 'world';
        applyTransformSettings(); saveState();
        setStatus(`已切换为${sceneData.settings.transformSpace === 'local' ? '局部' : '世界'}坐标`);
    });
    snapButton.addEventListener('click', () => {
        sceneData.settings.snapEnabled = !sceneData.settings.snapEnabled;
        applyTransformSettings(); saveState();
    });
    timelinePlayButton.addEventListener('click', togglePlayback);
    root.querySelector('[data-timeline-key]').addEventListener('click', addCameraKeyframe);
    root.querySelector('[data-timeline-delete]').addEventListener('click', deleteCameraKeyframe);
    timelineScrubber.addEventListener('input', () => { if (playbackFrame) stopPlayback(false); applyTimelineTime(timelineScrubber.value); });
    timelineScrubber.addEventListener('change', () => applyTimelineTime(timelineScrubber.value, {persist:true, refreshInspector:true}));
    undoButton.addEventListener('click', () => restoreHistory(historyIndex - 1));
    redoButton.addEventListener('click', () => restoreHistory(historyIndex + 1));
    previewButton.addEventListener('click', () => capturePreview().catch(() => {}));
    saveButton.addEventListener('click', async () => {
        saveState({history:false});
        try { await capturePreview(); } catch (_) {}
        editorApi.close('save');
    });

    transformControls.addEventListener('dragging-changed', event => {
        transformDragging = Boolean(event.value);
        if (transformDragging) drag = null;
    });
    transformControls.addEventListener('objectChange', () => {
        if (selected?.kind === 'object') {
            writeObjectRecord(selected.id);
            updateSelectionBox();
        } else if (selected?.kind === 'camera') {
            writeCameraRecord(selected.id);
            const record = sceneData.cameras.find(item => item.id === selected.id);
            if (record) updateCameraRuntime(record, {refreshSelect:false});
        }
        syncDirectorOutputs(node);
        context.save?.();
        requestRender();
    });
    transformControls.addEventListener('mouseUp', () => {
        if (selected?.kind === 'object') writeObjectRecord(selected.id);
        if (selected?.kind === 'camera') writeCameraRecord(selected.id);
        renderInspector(); renderCameraSelect(); saveState();
        setStatus('轴向变换已保存');
    });
    transformControls.addEventListener('change', requestRender);

    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    renderer.domElement.addEventListener('pointercancel', onPointerUp);
    renderer.domElement.addEventListener('contextmenu', event => event.preventDefault());
    renderer.domElement.addEventListener('wheel', event => {
        if (viewMode !== 'director') return;
        event.preventDefault();
        orbit.radius *= Math.exp(event.deltaY * 0.0012);
        updateEditorCamera();
    }, {passive:false});
    root.addEventListener('keydown', event => {
        const tag = event.target?.tagName?.toLowerCase();
        if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); restoreHistory(event.shiftKey ? historyIndex + 1 : historyIndex - 1); return; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); restoreHistory(historyIndex + 1); return; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') { event.preventDefault(); duplicateSelected(); return; }
        if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); deleteSelected(); return; }
        if (event.key.toLowerCase() === 'w') setMode('translate');
        if (event.key.toLowerCase() === 'e') setMode('rotate');
        if (event.key.toLowerCase() === 'r') setMode('scale');
        if (event.key.toLowerCase() === 'q') setMode('select');
        if (event.key.toLowerCase() === 'f') focusSelected();
        if (event.key === 'Escape') { closeAddMenu(); selectEntity(null, ''); }
    });

    const resizeObserver = new ResizeObserver(resizeRenderer);
    resizeObserver.observe(viewport);
    applyTransformSettings();
    updateTimelineUi();
    rebuildAll();
    refreshHistoryButtons();
    updateEditorCamera();
    requestAnimationFrame(() => root.focus({preventScroll:true}));

    return () => {
        if (disposed) return;
        disposed = true;
        node.data.scene = sceneData;
        syncDirectorOutputs(node);
        context.save?.();
        resizeObserver.disconnect();
        if (frameRequest) cancelAnimationFrame(frameRequest);
        if (playbackFrame) cancelAnimationFrame(playbackFrame);
        transformControls.detach();
        scene.remove(transformControls);
        transformControls.dispose();
        clearRuntimeObjects();
        clearCameras();
        selectionBox.geometry?.dispose?.();
        selectionBox.material?.dispose?.();
        grid.geometry?.dispose?.();
        grid.material?.dispose?.();
        ground.geometry.dispose();
        groundMaterial.dispose();
        backgroundTexture?.dispose?.();
        renderer.dispose();
    };
}
