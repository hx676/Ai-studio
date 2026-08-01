const MAX_FILES = 500;
const MAX_FILE_BYTES = 500 * 1024 * 1024;
const MAX_TOTAL_BYTES = 1024 * 1024 * 1024;

const MIME_EXTENSIONS = new Map([
    ['image/jpeg', '.jpg'], ['image/png', '.png'], ['image/webp', '.webp'],
    ['image/gif', '.gif'], ['image/avif', '.avif'], ['image/svg+xml', '.svg'],
    ['audio/mpeg', '.mp3'], ['audio/wav', '.wav'], ['audio/x-wav', '.wav'],
    ['audio/flac', '.flac'], ['audio/mp4', '.m4a'], ['audio/ogg', '.ogg'],
    ['video/mp4', '.mp4'], ['video/webm', '.webm'], ['video/quicktime', '.mov'],
    ['video/x-matroska', '.mkv'],
]);

function isEnglish() {
    return (globalThis.StudioI18n?.lang?.() || document.documentElement.lang || '').toLowerCase().startsWith('en');
}

function text(zh, en) {
    return isEnglish() ? en : zh;
}

function mediaKind(url, explicit = '') {
    const declared = String(explicit || '').toLowerCase();
    if (['image', 'audio', 'video'].includes(declared)) return declared;
    const clean = String(url || '').split(/[?#]/)[0].toLowerCase();
    if (/\.(mp3|wav|flac|m4a|aac|ogg|opus)$/.test(clean) || /^data:audio\//.test(clean)) return 'audio';
    if (/\.(mp4|webm|mov|m4v|mkv|avi)$/.test(clean) || /^data:video\//.test(clean)) return 'video';
    return 'image';
}

function connectedMedia(context) {
    const raw = context.collectInputs?.()?.files;
    const values = Array.isArray(raw) ? raw : raw == null ? [] : [raw];
    return values.map((item, index) => {
        const value = typeof item === 'string' ? item : item?.value || item?.url || '';
        return {
            url: String(value || '').trim(),
            kind: mediaKind(value, item?.kind),
            index,
        };
    }).filter(item => item.url);
}

function sanitizeStem(value, fallback = 'syncanvas-output') {
    let stem = String(value || '').normalize('NFKC')
        .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '-')
        .replace(/\s+/g, ' ')
        .replace(/[. ]+$/g, '')
        .trim();
    if (!stem) stem = fallback;
    if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem)) stem = `_${stem}`;
    return stem.slice(0, 120) || fallback;
}

function sourceName(url) {
    try {
        const path = new URL(url, location.href).pathname;
        return decodeURIComponent(path.split('/').filter(Boolean).pop() || '');
    } catch (_) {
        return '';
    }
}

function splitName(value) {
    const clean = sanitizeStem(value, 'syncanvas-output');
    const match = clean.match(/^(.*?)(\.[A-Za-z0-9]{1,10})$/);
    return match ? {stem:sanitizeStem(match[1]), extension:match[2].toLowerCase()} : {stem:clean, extension:''};
}

function candidateName(item, blob, prefix, number) {
    const original = splitName(sourceName(item.url));
    const extension = original.extension || MIME_EXTENSIONS.get(String(blob.type || '').split(';')[0].toLowerCase()) || '';
    const stem = prefix ? `${sanitizeStem(prefix)}-${String(number).padStart(3, '0')}` : original.stem || `syncanvas-${item.kind}-${String(number).padStart(3, '0')}`;
    return `${sanitizeStem(stem)}${extension}`;
}

async function fileExists(directory, name) {
    try {
        await directory.getFileHandle(name, {create:false});
        return true;
    } catch (error) {
        if (error?.name === 'NotFoundError') return false;
        throw error;
    }
}

async function availableName(directory, requested, conflictMode) {
    if (conflictMode === 'overwrite' || !await fileExists(directory, requested)) return requested;
    const {stem, extension} = splitName(requested);
    for (let suffix = 2; suffix <= 9999; suffix += 1) {
        const candidate = `${stem}-${suffix}${extension}`;
        if (!await fileExists(directory, candidate)) return candidate;
    }
    throw new Error(text('无法为重名文件生成可用名称', 'Could not create a unique file name'));
}

async function fetchBlob(item, totalBytes) {
    let response;
    try {
        response = await fetch(item.url, {cache:'no-store', credentials:'same-origin'});
    } catch (_) {
        throw new Error(text(`无法读取连接的${item.kind === 'image' ? '图片' : item.kind === 'audio' ? '音频' : '视频'}`, `Could not read the connected ${item.kind}`));
    }
    if (!response.ok) throw new Error(text(`读取媒体失败 (${response.status})`, `Media request failed (${response.status})`));
    const declared = Number(response.headers.get('content-length') || 0);
    if (declared > MAX_FILE_BYTES) throw new Error(text('单个文件超过 500 MiB 限制', 'A file exceeds the 500 MiB limit'));
    if (declared && totalBytes + declared > MAX_TOTAL_BYTES) throw new Error(text('本次导出总量超过 1 GiB 限制', 'The export exceeds the 1 GiB total limit'));
    const blob = await response.blob();
    if (blob.size > MAX_FILE_BYTES) throw new Error(text('单个文件超过 500 MiB 限制', 'A file exceeds the 500 MiB limit'));
    if (totalBytes + blob.size > MAX_TOTAL_BYTES) throw new Error(text('本次导出总量超过 1 GiB 限制', 'The export exceeds the 1 GiB total limit'));
    return blob;
}

function statusText(node, count) {
    if (node.running) return text('正在写入文件…', 'Writing files…');
    if (node.runStatus === 'succeeded') return text(`已保存 ${node.data?.savedFiles?.length || 0} 个文件`, `Saved ${node.data?.savedFiles?.length || 0} files`);
    if (node.runStatus === 'failed') return node.data?.lastError || text('导出失败', 'Export failed');
    if (node.runStatus === 'cancelled') return text('已取消选择文件夹', 'Folder selection cancelled');
    return count ? text(`已连接 ${count} 个媒体文件`, `${count} media files connected`) : text('连接图片、音频或视频', 'Connect images, audio, or video');
}

function renderExport({node, escapeHtml, context}) {
    const media = connectedMedia(context);
    const data = node.data || {};
    const saved = Array.isArray(data.savedFiles) ? data.savedFiles : [];
    const recent = saved.slice(0, 4).map(name => `<li title="${escapeHtml(name)}">${escapeHtml(name)}</li>`).join('');
    return `<div class="folder-export-node ${node.runStatus === 'failed' ? 'is-error' : ''}" data-folder-export>
        <div class="folder-export-summary">
            <span class="folder-export-icon"><i data-lucide="folder-output" aria-hidden="true"></i></span>
            <div><strong>${escapeHtml(statusText(node, media.length))}</strong><small>${escapeHtml(data.lastFolderName ? `${text('最近文件夹', 'Last folder')}: ${data.lastFolderName}` : text('文件夹路径不会发送到后端', 'The folder path is never sent to the backend'))}</small></div>
        </div>
        <label class="folder-export-field"><span>${text('文件名前缀（可选）', 'Filename prefix (optional)')}</span><input data-extension-state="filenamePrefix" maxlength="80" value="${escapeHtml(data.filenamePrefix || '')}" placeholder="SynCanvas"></label>
        <label class="folder-export-field"><span>${text('重名处理', 'Name conflicts')}</span><select data-extension-state="conflictMode"><option value="increment" ${data.conflictMode !== 'overwrite' ? 'selected' : ''}>${text('自动追加序号', 'Add a number')}</option><option value="overwrite" ${data.conflictMode === 'overwrite' ? 'selected' : ''}>${text('覆盖原文件', 'Overwrite')}</option></select></label>
        ${recent ? `<ul class="folder-export-files">${recent}${saved.length > 4 ? `<li>${text(`另有 ${saved.length - 4} 个文件`, `${saved.length - 4} more files`)}</li>` : ''}</ul>` : '<div class="folder-export-empty"><i data-lucide="link-2" aria-hidden="true"></i><span>' + text('支持批量连接，按顺序写入', 'Batch connections are written in order') + '</span></div>'}
        <button class="folder-export-run" type="button" data-extension-run ${!media.length || node.running ? 'disabled' : ''}><i data-lucide="folder-open" aria-hidden="true"></i><span>${node.running ? text('正在导出…', 'Exporting…') : text('选择文件夹并导出', 'Choose Folder & Export')}</span></button>
        <p class="folder-export-note">${text('单文件 500 MiB，单次累计 1 GiB', '500 MiB per file, 1 GiB per export')}</p>
    </div>`;
}

async function runExport({node, context}) {
    if (typeof globalThis.showDirectoryPicker !== 'function') {
        throw new Error(text('当前浏览器不支持文件夹选择器，请使用最新版 Chrome 或 Edge', 'This browser does not support folder selection. Use a current Chrome or Edge release.'));
    }
    const media = connectedMedia(context);
    if (!media.length) throw new Error(text('请先连接图片、音频或视频', 'Connect images, audio, or video first'));
    if (media.length > MAX_FILES) throw new Error(text('单次最多导出 500 个文件', 'A single export is limited to 500 files'));

    node.data = {...(node.data || {}), lastError:''};
    node.running = true;
    node.runStatus = 'running';
    context.update?.(node);
    try {
        const directory = await globalThis.showDirectoryPicker({id:'syncanvas-output-folder', mode:'readwrite', startIn:'downloads'});
        if (typeof directory.requestPermission === 'function') {
            const permission = await directory.requestPermission({mode:'readwrite'});
            if (permission !== 'granted') throw new Error(text('没有该文件夹的写入权限', 'Write permission was not granted for this folder'));
        }
        const saved = [];
        let totalBytes = 0;
        for (let index = 0; index < media.length; index += 1) {
            const item = media[index];
            const blob = await fetchBlob(item, totalBytes);
            totalBytes += blob.size;
            const requested = candidateName(item, blob, node.data.filenamePrefix || '', index + 1);
            const name = await availableName(directory, requested, node.data.conflictMode || 'increment');
            const handle = await directory.getFileHandle(name, {create:true});
            const writable = await handle.createWritable();
            let completed = false;
            try {
                await writable.write(blob);
                await writable.close();
                completed = true;
            } finally {
                if (!completed && typeof writable.abort === 'function') await writable.abort().catch(() => {});
            }
            saved.push(name);
        }
        const folderName = String(directory.name || text('所选文件夹', 'Selected folder'));
        const relativePaths = saved.map(name => `${folderName}/${name}`);
        node.data = {...node.data, savedFiles:saved, lastFolderName:folderName, lastSavedAt:Date.now(), lastError:''};
        node.outputText = relativePaths.join('\n');
        node.structuredOutput = {folder:folderName, files:saved, count:saved.length, bytes:totalBytes};
        node.extensionOutputs = {paths:[{kind:'text', value:node.outputText}]};
        node.runStatus = 'succeeded';
        return {outputs:node.extensionOutputs, output_text:node.outputText, structured_output:node.structuredOutput};
    } catch (error) {
        if (error?.name === 'AbortError') {
            node.runStatus = 'cancelled';
            node.data = {...node.data, lastError:''};
            return null;
        }
        node.runStatus = 'failed';
        node.data = {...node.data, lastError:error?.message || String(error)};
        throw error;
    } finally {
        node.running = false;
        context.update?.(node);
        context.save?.();
    }
}

function serializeExport({node}) {
    node.data = {
        filenamePrefix:String(node.data?.filenamePrefix || '').slice(0, 80),
        conflictMode:node.data?.conflictMode === 'overwrite' ? 'overwrite' : 'increment',
        savedFiles:Array.isArray(node.data?.savedFiles) ? node.data.savedFiles.slice(0, MAX_FILES).map(String) : [],
        lastFolderName:String(node.data?.lastFolderName || '').slice(0, 160),
        lastSavedAt:Number(node.data?.lastSavedAt) || 0,
        lastError:String(node.data?.lastError || '').slice(0, 500),
    };
    node.running = false;
    return node;
}

export function register(api) {
    api.registerNode('export', {
        render:renderExport,
        run:runExport,
        serialize:serializeExport,
    });
}
