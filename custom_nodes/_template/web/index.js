export function register(api) {
    api.registerNode('uppercase', {
        render({node, escapeHtml}) {
            const text = node.data?.text || '';
            return `
                <label class="extension-field">
                    <span>Text</span>
                    <textarea data-extension-state="text">${escapeHtml(text)}</textarea>
                </label>
                <button type="button" class="extension-run" data-extension-run>Run</button>
                <pre class="extension-output">${escapeHtml(node.outputText || '')}</pre>`;
        }
    });
}
