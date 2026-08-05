export const JOINT_NAMES = [
    'hips', 'spine', 'chest', 'neck', 'head',
    'leftUpperArm', 'leftForearm', 'leftHand',
    'rightUpperArm', 'rightForearm', 'rightHand',
    'leftThigh', 'leftShin', 'leftFoot',
    'rightThigh', 'rightShin', 'rightFoot',
];

export const POSE_PRESETS = {
    standing:{label:'自然站立', joints:{leftUpperArm:[0,0,-6], rightUpperArm:[0,0,6], leftForearm:[0,0,-4], rightForearm:[0,0,4]}},
    a_pose:{label:'A-Pose', joints:{leftUpperArm:[0,0,-48], rightUpperArm:[0,0,48]}},
    t_pose:{label:'T-Pose', joints:{leftUpperArm:[0,0,-90], rightUpperArm:[0,0,90]}},
    walk:{label:'行走', joints:{chest:[0,8,0], leftUpperArm:[24,0,-8], rightUpperArm:[-24,0,8], leftThigh:[-25,0,0], rightThigh:[25,0,0], leftShin:[28,0,0], rightShin:[8,0,0]}},
    run:{label:'奔跑', joints:{spine:[18,0,0], leftUpperArm:[-48,0,-12], rightUpperArm:[42,0,12], leftForearm:[-72,0,0], rightForearm:[-76,0,0], leftThigh:[42,0,0], rightThigh:[-34,0,0], leftShin:[55,0,0], rightShin:[82,0,0]}},
    sitting:{label:'坐姿', joints:{spine:[-5,0,0], leftThigh:[-88,0,-5], rightThigh:[-88,0,5], leftShin:[88,0,0], rightShin:[88,0,0], leftUpperArm:[-18,0,-9], rightUpperArm:[-18,0,9]}},
    pointing:{label:'指向', joints:{chest:[0,-18,0], leftUpperArm:[0,0,-8], rightUpperArm:[-5,-8,88], rightForearm:[0,0,0]}},
    holding:{label:'双手持物', joints:{leftUpperArm:[-30,-10,-34], rightUpperArm:[-30,10,34], leftForearm:[-82,0,0], rightForearm:[-82,0,0]}},
};

const IK_CHAINS = {
    leftHand:{label:'左手', upper:'leftUpperArm', lower:'leftForearm', end:'leftHand', pole:[0,0,1], sign:1},
    rightHand:{label:'右手', upper:'rightUpperArm', lower:'rightForearm', end:'rightHand', pole:[0,0,1], sign:-1},
    leftFoot:{label:'左脚', upper:'leftThigh', lower:'leftShin', end:'leftFoot', pole:[0,0,1], sign:1},
    rightFoot:{label:'右脚', upper:'rightThigh', lower:'rightShin', end:'rightFoot', pole:[0,0,1], sign:1},
};

export const IK_TARGETS = Object.fromEntries(Object.entries(IK_CHAINS).map(([id, value]) => [id, value.label]));

function safeNumber(value, fallback=0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function safeVector(value, fallback, length) {
    const source = Array.isArray(value) ? value : fallback;
    return Array.from({length}, (_, index) => safeNumber(source[index], fallback[index]));
}

export function normalizeJointMap(value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const result = {};
    JOINT_NAMES.forEach(name => {
        if (Array.isArray(source[name])) result[name] = safeVector(source[name], [0,0,0,1], 4);
    });
    return result;
}

export function normalizeIkTargets(value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const result = {};
    Object.keys(IK_CHAINS).forEach(name => {
        if (Array.isArray(source[name])) result[name] = safeVector(source[name], [0,1,0], 3);
    });
    return result;
}

function quaternionFromDegrees(THREE, degrees=[0,0,0]) {
    const euler = new THREE.Euler(...degrees.map(value => safeNumber(value) * Math.PI / 180), 'XYZ');
    return new THREE.Quaternion().setFromEuler(euler);
}

export function poseJointMap(THREE, poseId='standing') {
    const preset = POSE_PRESETS[poseId] || POSE_PRESETS.standing;
    const result = {};
    JOINT_NAMES.forEach(name => { result[name] = [0,0,0,1]; });
    Object.entries(preset.joints).forEach(([name, degrees]) => { result[name] = quaternionFromDegrees(THREE, degrees).toArray(); });
    return result;
}

function addMesh(THREE, parent, geometry, material, position=[0,0,0], rotation=[0,0,0]) {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(...position);
    mesh.rotation.set(...rotation);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
}

function makeJoint(THREE, parent, name, position, joints) {
    const joint = new THREE.Group();
    joint.name = name;
    joint.position.set(...position);
    joint.userData.directorJoint = name;
    parent.add(joint);
    joints[name] = joint;
    return joint;
}

function makeLimb(THREE, parent, length, radius, material) {
    return addMesh(THREE, parent, new THREE.CylinderGeometry(radius, radius * 0.88, length, 9), material, [0, -length / 2, 0]);
}

export function createRiggedCharacter(THREE, record, preset, createLabel) {
    const root = new THREE.Group();
    const joints = {};
    const material = new THREE.MeshStandardMaterial({color:record.color, roughness:0.72, metalness:0.02});
    const darkMaterial = new THREE.MeshStandardMaterial({color:new THREE.Color(record.color).multiplyScalar(0.82), roughness:0.78});
    const height = preset.height;
    const headRadius = height * preset.head;
    const legHeight = height * (record.archetype === 'chibi' ? 0.22 : 0.36);
    const pelvisHeight = height * 0.105;
    const torsoHeight = height * (record.archetype === 'chibi' ? 0.27 : 0.32);
    const upperLegLength = legHeight * 0.53;
    const lowerLegLength = legHeight - upperLegLength;
    const armLength = height * (record.archetype === 'chibi' ? 0.25 : 0.33);
    const upperArmLength = armLength * 0.52;
    const forearmLength = armLength - upperArmLength;
    const limbRadius = Math.max(0.035, height * 0.035 * preset.build);
    const hips = makeJoint(THREE, root, 'hips', [0, legHeight, 0], joints);
    addMesh(THREE, hips, new THREE.BoxGeometry(preset.hips, pelvisHeight, preset.hips * 0.55), darkMaterial, [0, pelvisHeight / 2, 0]);
    const spine = makeJoint(THREE, hips, 'spine', [0, pelvisHeight, 0], joints);
    addMesh(THREE, spine, new THREE.CylinderGeometry(preset.shoulders * 0.44, preset.waist * 0.5, torsoHeight, 10), material, [0, torsoHeight / 2, 0]);
    const chest = makeJoint(THREE, spine, 'chest', [0, torsoHeight * 0.72, 0], joints);
    const neck = makeJoint(THREE, chest, 'neck', [0, torsoHeight * 0.28, 0], joints);
    addMesh(THREE, neck, new THREE.CylinderGeometry(headRadius * 0.34, headRadius * 0.38, headRadius * 0.55, 8), darkMaterial, [0, headRadius * 0.27, 0]);
    const head = makeJoint(THREE, neck, 'head', [0, headRadius * 0.52, 0], joints);
    addMesh(THREE, head, new THREE.SphereGeometry(headRadius, 18, 12), material, [0, headRadius, 0]);

    const armOffset = preset.shoulders * 0.52;
    const leftUpperArm = makeJoint(THREE, chest, 'leftUpperArm', [-armOffset, torsoHeight * 0.12, 0], joints);
    makeLimb(THREE, leftUpperArm, upperArmLength, limbRadius * 0.8, material);
    const leftForearm = makeJoint(THREE, leftUpperArm, 'leftForearm', [0, -upperArmLength, 0], joints);
    makeLimb(THREE, leftForearm, forearmLength, limbRadius * 0.68, material);
    const leftHand = makeJoint(THREE, leftForearm, 'leftHand', [0, -forearmLength, 0], joints);
    addMesh(THREE, leftHand, new THREE.SphereGeometry(limbRadius * 0.9, 10, 8), material);

    const rightUpperArm = makeJoint(THREE, chest, 'rightUpperArm', [armOffset, torsoHeight * 0.12, 0], joints);
    makeLimb(THREE, rightUpperArm, upperArmLength, limbRadius * 0.8, material);
    const rightForearm = makeJoint(THREE, rightUpperArm, 'rightForearm', [0, -upperArmLength, 0], joints);
    makeLimb(THREE, rightForearm, forearmLength, limbRadius * 0.68, material);
    const rightHand = makeJoint(THREE, rightForearm, 'rightHand', [0, -forearmLength, 0], joints);
    addMesh(THREE, rightHand, new THREE.SphereGeometry(limbRadius * 0.9, 10, 8), material);

    const legOffset = preset.hips * 0.24;
    const leftThigh = makeJoint(THREE, hips, 'leftThigh', [-legOffset, 0, 0], joints);
    makeLimb(THREE, leftThigh, upperLegLength, limbRadius * 0.94, material);
    const leftShin = makeJoint(THREE, leftThigh, 'leftShin', [0, -upperLegLength, 0], joints);
    makeLimb(THREE, leftShin, lowerLegLength, limbRadius * 0.78, material);
    const leftFoot = makeJoint(THREE, leftShin, 'leftFoot', [0, -lowerLegLength, 0], joints);
    addMesh(THREE, leftFoot, new THREE.BoxGeometry(limbRadius * 1.7, limbRadius * 0.75, limbRadius * 3.1), darkMaterial, [0, limbRadius * 0.1, limbRadius]);

    const rightThigh = makeJoint(THREE, hips, 'rightThigh', [legOffset, 0, 0], joints);
    makeLimb(THREE, rightThigh, upperLegLength, limbRadius * 0.94, material);
    const rightShin = makeJoint(THREE, rightThigh, 'rightShin', [0, -upperLegLength, 0], joints);
    makeLimb(THREE, rightShin, lowerLegLength, limbRadius * 0.78, material);
    const rightFoot = makeJoint(THREE, rightShin, 'rightFoot', [0, -lowerLegLength, 0], joints);
    addMesh(THREE, rightFoot, new THREE.BoxGeometry(limbRadius * 1.7, limbRadius * 0.75, limbRadius * 3.1), darkMaterial, [0, limbRadius * 0.1, limbRadius]);

    const label = createLabel(record.name);
    label.position.set(0, height + 0.22, 0);
    root.add(label);
    root.userData.characterHeight = height;
    root.userData.directorRig = {
        joints,
        lengths:{upperArm:upperArmLength, forearm:forearmLength, upperLeg:upperLegLength, shin:lowerLegLength},
    };
    applyCharacterPose(THREE, root, record);
    return root;
}

export function applyCharacterPose(THREE, root, record) {
    const rig = root?.userData?.directorRig;
    if (!rig) return;
    const base = poseJointMap(THREE, record.poseId);
    const overrides = normalizeJointMap(record.joints);
    JOINT_NAMES.forEach(name => {
        const joint = rig.joints[name];
        if (joint) joint.quaternion.fromArray(overrides[name] || base[name]).normalize();
    });
    root.updateMatrixWorld(true);
    Object.entries(normalizeIkTargets(record.ikTargets)).forEach(([targetName, target]) => solveCharacterIk(THREE, root, targetName, target));
}

export function captureCharacterJoints(root) {
    const joints = root?.userData?.directorRig?.joints || {};
    const result = {};
    JOINT_NAMES.forEach(name => { if (joints[name]) result[name] = joints[name].quaternion.toArray(); });
    return result;
}

export function characterEffectorPosition(root, targetName) {
    const chain = IK_CHAINS[targetName];
    const joint = chain ? root?.userData?.directorRig?.joints?.[chain.end] : null;
    if (!joint) return null;
    root.updateMatrixWorld(true);
    const world = joint.getWorldPosition(joint.position.clone());
    return root.worldToLocal(world).toArray();
}

function pointBoneAt(THREE, joint, worldDirection) {
    const parentQuaternion = joint.parent.getWorldQuaternion(new THREE.Quaternion()).invert();
    const localDirection = worldDirection.clone().applyQuaternion(parentQuaternion).normalize();
    joint.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), localDirection);
    joint.updateMatrixWorld(true);
}

export function solveCharacterIk(THREE, root, targetName, localTarget) {
    const chain = IK_CHAINS[targetName];
    const rig = root?.userData?.directorRig;
    if (!chain || !rig) return false;
    const upper = rig.joints[chain.upper];
    const lower = rig.joints[chain.lower];
    if (!upper || !lower) return false;
    root.updateMatrixWorld(true);
    const start = upper.getWorldPosition(new THREE.Vector3());
    const target = root.localToWorld(new THREE.Vector3().fromArray(localTarget));
    const isArm = chain.upper.includes('Arm');
    const lengthA = isArm ? rig.lengths.upperArm : rig.lengths.upperLeg;
    const lengthB = isArm ? rig.lengths.forearm : rig.lengths.shin;
    const delta = target.clone().sub(start);
    const requestedDistance = Math.max(0.0001, delta.length());
    const distance = Math.min(lengthA + lengthB - 0.0001, Math.max(Math.abs(lengthA - lengthB) + 0.0001, requestedDistance));
    const direction = delta.normalize();
    const poleWorld = new THREE.Vector3(...chain.pole).applyQuaternion(root.getWorldQuaternion(new THREE.Quaternion()));
    poleWorld.addScaledVector(direction, -poleWorld.dot(direction));
    if (poleWorld.lengthSq() < 0.000001) poleWorld.set(1,0,0).addScaledVector(direction, -direction.x);
    poleWorld.normalize().multiplyScalar(chain.sign);
    const along = (lengthA * lengthA - lengthB * lengthB + distance * distance) / (2 * distance);
    const perpendicular = Math.sqrt(Math.max(0, lengthA * lengthA - along * along));
    const elbow = start.clone().addScaledVector(direction, along).addScaledVector(poleWorld, perpendicular);
    pointBoneAt(THREE, upper, elbow.clone().sub(start));
    root.updateMatrixWorld(true);
    const lowerStart = lower.getWorldPosition(new THREE.Vector3());
    pointBoneAt(THREE, lower, target.clone().sub(lowerStart));
    root.updateMatrixWorld(true);
    return true;
}

export function normalizeTimeline(value) {
    const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const duration = Math.max(0.5, Math.min(120, safeNumber(source.duration, 5)));
    const fps = Math.max(1, Math.min(60, Math.round(safeNumber(source.fps, 24))));
    const tracks = (Array.isArray(source.tracks) ? source.tracks : []).slice(0, 64).map(track => ({
        targetId:String(track?.targetId || '').slice(0, 120),
        property:'camera',
        keyframes:(Array.isArray(track?.keyframes) ? track.keyframes : []).slice(0, 600).map(frame => ({
            id:String(frame?.id || ''),
            time:Math.max(0, Math.min(duration, safeNumber(frame?.time, 0))),
            position:safeVector(frame?.position, [0,2.2,7.5], 3),
            rotation:safeVector(frame?.rotation, [0,0,0,1], 4),
            focalLength:Math.max(10, Math.min(300, safeNumber(frame?.focalLength, 50))),
        })).sort((a,b) => a.time - b.time),
    })).filter(track => track.targetId);
    return {
        duration,
        fps,
        currentTime:Math.max(0, Math.min(duration, safeNumber(source.currentTime, 0))),
        tracks,
    };
}

export function cameraTrack(timeline, cameraId, create=false) {
    let track = timeline.tracks.find(item => item.targetId === cameraId && item.property === 'camera');
    if (!track && create) {
        track = {targetId:cameraId, property:'camera', keyframes:[]};
        timeline.tracks.push(track);
    }
    return track || null;
}

export function upsertCameraKeyframe(timeline, camera, idFactory) {
    const track = cameraTrack(timeline, camera.id, true);
    const tolerance = 0.5 / timeline.fps;
    let frame = track.keyframes.find(item => Math.abs(item.time - timeline.currentTime) <= tolerance);
    if (!frame) {
        frame = {id:idFactory(), time:timeline.currentTime};
        track.keyframes.push(frame);
    }
    frame.time = timeline.currentTime;
    frame.position = [...camera.position];
    frame.rotation = [...camera.rotation];
    frame.focalLength = camera.focalLength;
    track.keyframes.sort((a,b) => a.time - b.time);
    return frame;
}

export function removeCameraKeyframe(timeline, cameraId) {
    const track = cameraTrack(timeline, cameraId, false);
    if (!track?.keyframes.length) return false;
    const tolerance = Math.max(0.08, 0.75 / timeline.fps);
    let bestIndex = -1;
    let bestDistance = Infinity;
    track.keyframes.forEach((frame, index) => {
        const distance = Math.abs(frame.time - timeline.currentTime);
        if (distance < bestDistance) { bestDistance = distance; bestIndex = index; }
    });
    if (bestIndex < 0 || bestDistance > tolerance) return false;
    track.keyframes.splice(bestIndex, 1);
    return true;
}

export function sampleCameraTrack(THREE, timeline, cameraId) {
    const frames = cameraTrack(timeline, cameraId, false)?.keyframes || [];
    if (!frames.length) return null;
    if (timeline.currentTime <= frames[0].time) return {...frames[0], position:[...frames[0].position], rotation:[...frames[0].rotation]};
    if (timeline.currentTime >= frames[frames.length - 1].time) {
        const last = frames[frames.length - 1];
        return {...last, position:[...last.position], rotation:[...last.rotation]};
    }
    let left = frames[0];
    let right = frames[frames.length - 1];
    for (let index = 1; index < frames.length; index += 1) {
        if (frames[index].time >= timeline.currentTime) { left = frames[index - 1]; right = frames[index]; break; }
    }
    const span = Math.max(0.0001, right.time - left.time);
    const alpha = Math.max(0, Math.min(1, (timeline.currentTime - left.time) / span));
    const position = new THREE.Vector3().fromArray(left.position).lerp(new THREE.Vector3().fromArray(right.position), alpha).toArray();
    const rotation = new THREE.Quaternion().fromArray(left.rotation).slerp(new THREE.Quaternion().fromArray(right.rotation), alpha).toArray();
    return {time:timeline.currentTime, position, rotation, focalLength:left.focalLength + (right.focalLength - left.focalLength) * alpha};
}

export function maskColorForId(id) {
    let hash = 2166136261;
    for (const character of String(id || 'actor')) {
        hash ^= character.charCodeAt(0);
        hash = Math.imul(hash, 16777619);
    }
    const red = 48 + ((hash >>> 16) & 0xaf);
    const green = 48 + ((hash >>> 8) & 0xaf);
    const blue = 48 + (hash & 0xaf);
    return `#${[red, green, blue].map(value => value.toString(16).padStart(2, '0')).join('')}`;
}
