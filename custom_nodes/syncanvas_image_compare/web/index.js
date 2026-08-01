function clampPosition(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : 50;
}

function imageUrl(value) {
    const item = Array.isArray(value) ? value[0] : value;
    if (typeof item === 'string') return item;
    if (!item || typeof item !== 'object') return '';
    return String(item.value || item.url || '');
}

function renderEmptySide(side, url, escapeHtml) {
    return `<div class="image-compare-empty-side ${url ? 'has-image' : ''}">
        <span class="image-compare-side-badge">${side}</span>
        ${url
            ? `<img src="${escapeHtml(url)}" alt="图像 ${side}">`
            : '<i data-lucide="image" aria-hidden="true"></i>'}
    </div>`;
}

function renderCompare({node, escapeHtml, context}) {
    const inputs = context.collectInputs?.() || {};
    const a = imageUrl(inputs.a);
    const b = imageUrl(inputs.b);
    const position = clampPosition(node.data?.position);
    node.data = {...(node.data || {}), position};

    if (!a || !b) {
        return `<div class="image-compare-node is-waiting" data-image-compare>
            <div class="image-compare-empty-grid">
                ${renderEmptySide('A', a, escapeHtml)}
                ${renderEmptySide('B', b, escapeHtml)}
            </div>
            <div class="image-compare-status">
                <i data-lucide="link-2" aria-hidden="true"></i>
                <span>${a || b ? '还需连接另一张图片' : '连接 A 和 B 两张图片'}</span>
            </div>
        </div>`;
    }

    return `<div class="image-compare-node is-ready" data-image-compare style="--compare-position:${position}%">
        <div class="image-compare-frame">
            <img class="image-compare-image image-compare-image-b" src="${escapeHtml(b)}" alt="图像 B">
            <img class="image-compare-image image-compare-image-a" src="${escapeHtml(a)}" alt="图像 A">
            <span class="image-compare-side-badge image-compare-badge-a">A</span>
            <span class="image-compare-side-badge image-compare-badge-b">B</span>
            <div class="image-compare-divider" aria-hidden="true">
                <span><i data-lucide="grip-vertical"></i></span>
            </div>
            <input class="image-compare-range" type="range" min="0" max="100" step="1" value="${position}" aria-label="图像比对位置">
        </div>
        <div class="image-compare-meter" aria-hidden="true">
            <span>A</span><div><i></i></div><span>B</span>
        </div>
    </div>`;
}

function bindCompare({root, node, save}) {
    const wrap = root.querySelector('[data-image-compare]');
    const range = root.querySelector('.image-compare-range');
    if (!wrap || !range) return;

    const update = () => {
        const position = clampPosition(range.value);
        wrap.style.setProperty('--compare-position', `${position}%`);
        node.data = {...(node.data || {}), position};
        save?.();
    };
    range.addEventListener('input', update);
    return () => range.removeEventListener('input', update);
}

function serializeCompare({node}) {
    node.data = {...(node.data || {}), position:clampPosition(node.data?.position)};
    ['images', 'inputNodeIds', 'promptDraftHtml', 'promptDraftText', 'runSettings'].forEach(key => delete node[key]);
    if (!node.outputText) delete node.outputText;
    if (node.structuredOutput == null) delete node.structuredOutput;
    if (!node.extensionOutputs || !Object.keys(node.extensionOutputs).length) delete node.extensionOutputs;
    return node;
}

export function register(api) {
    api.registerNode('compare', {
        render: renderCompare,
        bind: bindCompare,
        serialize: serializeCompare,
    });
}
