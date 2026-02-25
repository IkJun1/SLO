import { apiPath, safeJson } from './api.js';
import { state } from './state.js';
import { baseName, escapeHtml } from './utils.js';

let moduleDeps = {
    showSelectedStatus: () => {},
    expandAncestors: () => {},
    setSelectedEntry: () => {},
    refreshFileTree: async () => {}
};

export function configureMarkdownEditorModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function isImagePath(path) {
    const lower = String(path || '').toLowerCase();
    return (
        lower.endsWith('.png') ||
        lower.endsWith('.jpg') ||
        lower.endsWith('.jpeg') ||
        lower.endsWith('.gif') ||
        lower.endsWith('.webp')
    );
}

function setEditorReadonlyMode(contentReadonly) {
    const contentInput = document.getElementById('doc-content');
    if (!contentInput) {
        return;
    }

    contentInput.readOnly = contentReadonly;
}

function setEditorFilename(path) {
    const filenameElement = document.getElementById('doc-filename');
    if (!filenameElement) {
        return;
    }

    const rawPath = String(path || '').trim();
    filenameElement.textContent = rawPath ? baseName(rawPath) : '';
    filenameElement.title = rawPath;
}

export function refreshEditorFilename() {
    const targetPath = state.currentDocPath || state.currentImagePath || '';
    setEditorFilename(targetPath);
}

export async function openImagePicker() {
    if (!state.currentDocPath) {
        moduleDeps.showSelectedStatus('Open a document before inserting images.', 'error');
        return;
    }

    const input = document.getElementById('image-file-input');
    if (!input) {
        return;
    }
    input.click();
}

export function hasImageFile(dataTransfer) {
    if (!dataTransfer || !dataTransfer.files) {
        return false;
    }

    return Array.from(dataTransfer.files).some((file) => String(file.type || '').startsWith('image/'));
}

export function estimateDropIndexFromPointer(textarea, event) {
    const value = String(textarea.value || '');
    const rect = textarea.getBoundingClientRect();
    const styles = window.getComputedStyle(textarea);

    const fontSize = Number.parseFloat(styles.fontSize) || 15;
    const lineHeight = Number.parseFloat(styles.lineHeight) || fontSize * 1.6;
    const charWidth = fontSize * 0.62;

    const y = event.clientY - rect.top + textarea.scrollTop;
    const x = event.clientX - rect.left + textarea.scrollLeft;

    const lines = value.split('\n');
    const lineIndex = Math.max(0, Math.min(lines.length - 1, Math.floor(y / lineHeight)));
    const lineText = lines[lineIndex] || '';
    const colIndex = Math.max(0, Math.min(lineText.length, Math.round(x / Math.max(charWidth, 1))));

    let offset = 0;
    for (let idx = 0; idx < lineIndex; idx += 1) {
        offset += lines[idx].length + 1;
    }

    return Math.max(0, Math.min(value.length, offset + colIndex));
}

async function uploadImageFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    const res = await fetch(apiPath('/images'), {
        method: 'POST',
        body: formData
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to upload image.');
    }

    return body;
}

function insertMarkdownAtSelection(textarea, markdown) {
    const start = Number.isInteger(textarea.selectionStart) ? textarea.selectionStart : textarea.value.length;
    const end = Number.isInteger(textarea.selectionEnd) ? textarea.selectionEnd : start;

    const nextText = `${textarea.value.slice(0, start)}${markdown}${textarea.value.slice(end)}`;
    const nextCursor = start + markdown.length;

    textarea.value = nextText;
    textarea.setSelectionRange(nextCursor, nextCursor);
}

export async function insertUploadedImages(textarea, files) {
    if (!state.currentDocPath) {
        moduleDeps.showSelectedStatus('Open a document before inserting images.', 'error');
        return;
    }

    try {
        for (const file of files) {
            if (!String(file.type || '').startsWith('image/')) {
                continue;
            }

            const uploaded = await uploadImageFile(file);
            const markdown = `${uploaded.markdown}\n`;
            insertMarkdownAtSelection(textarea, markdown);
        }

        renderMarkdownPreview(textarea.value);
        scheduleSave();
        moduleDeps.showSelectedStatus('Image inserted.');
    } catch (err) {
        console.error(err);
        moduleDeps.showSelectedStatus((err && err.message) || 'Failed to upload image.', 'error');
    }
}

export async function loadDoc(path) {
    const requestedPath = String(path || '').trim();
    if (!requestedPath) {
        return;
    }

    try {
        const encodedPath = encodeURIComponent(requestedPath);
        const res = await fetch(apiPath(`/docs/by-path?path=${encodedPath}`));
        if (!res.ok) {
            throw new Error('Failed to load document.');
        }

        const doc = await res.json();
        state.currentDocPath = doc.path;
        state.currentImagePath = null;
        setEditorFilename(doc.path);

        document.getElementById('empty-state').style.display = 'none';
        document.getElementById('editor-container').style.display = 'flex';
        setEditorReadonlyMode(false);

        document.getElementById('doc-content').value = doc.content;
        renderMarkdownPreview(doc.content);

        moduleDeps.expandAncestors(doc.path);
        moduleDeps.setSelectedEntry({ type: 'doc', id: doc.id, path: doc.path, name: baseName(doc.path) });
    } catch (err) {
        console.error('Failed to load doc', err);
        moduleDeps.showSelectedStatus('Failed to load document.', 'error');
    }
}

function imageTitleFromPath(path) {
    const filename = baseName(path);
    const dotIdx = filename.lastIndexOf('.');
    if (dotIdx > 0) {
        return filename.slice(0, dotIdx);
    }
    return filename;
}

function imageMarkdownForPath(path) {
    const encodedPath = encodeURIComponent(path);
    const imageUrl = apiPath(`/images/by-path?path=${encodedPath}`);
    return `![${imageTitleFromPath(path)}](${imageUrl})`;
}

export async function loadImagePreview(path) {
    const requestedPath = String(path || '').trim();
    if (!requestedPath) {
        return;
    }

    state.currentDocPath = null;
    state.currentImagePath = requestedPath;
    setEditorFilename(requestedPath);
    document.getElementById('empty-state').style.display = 'none';
    document.getElementById('editor-container').style.display = 'flex';
    setEditorReadonlyMode(true);

    const markdown = imageMarkdownForPath(requestedPath);
    document.getElementById('doc-content').value = markdown;
    renderMarkdownPreview(markdown);

    moduleDeps.expandAncestors(requestedPath);
    moduleDeps.setSelectedEntry({ type: 'image', path: requestedPath, name: baseName(requestedPath) });
}

export function clearEditor() {
    state.currentDocPath = null;
    state.currentImagePath = null;
    setEditorFilename('');

    setEditorReadonlyMode(false);

    document.getElementById('editor-container').style.display = 'none';
    document.getElementById('empty-state').style.display = 'flex';
    document.getElementById('doc-content').value = '';
    renderMarkdownPreview('');
    window.dispatchEvent(new Event('slo:selection-changed'));
}

function setSaveStatus(text, opacity = null) {
    const status = document.getElementById('save-status');
    if (!status) {
        return;
    }

    status.textContent = text;
    if (opacity !== null) {
        status.style.opacity = opacity;
    }
}

export function scheduleSave() {
    if (!state.currentDocPath) {
        return;
    }

    setSaveStatus('Unsaved...', '1');

    if (state.saveTimeout) {
        clearTimeout(state.saveTimeout);
    }

    state.saveTimeout = setTimeout(saveCurrentDoc, 1000);
}

async function saveCurrentDoc() {
    if (!state.currentDocPath) {
        return;
    }

    const content = document.getElementById('doc-content').value;

    try {
        const encodedPath = encodeURIComponent(state.currentDocPath);
        const res = await fetch(apiPath(`/docs/by-path?path=${encodedPath}`), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });

        if (!res.ok) {
            throw new Error('Save failed');
        }

        setSaveStatus('Saved');
        setTimeout(() => {
            setSaveStatus('Saved', '0');
        }, 2000);

        await moduleDeps.refreshFileTree();
    } catch (err) {
        console.error('Save failed', err);
        setSaveStatus('Error saving', '1');
        moduleDeps.showSelectedStatus('Error saving', 'error');
    }
}

export function renderMarkdownPreview(markdownText) {
    const preview = document.getElementById('doc-preview');
    if (!preview) {
        return;
    }

    if (!markdownText || markdownText.trim() === '') {
        preview.innerHTML = '<p style="color: #858585;">Markdown preview will appear here.</p>';
        return;
    }

    try {
        let rendered = marked.parse(markdownText);
        if (window.DOMPurify) {
            rendered = window.DOMPurify.sanitize(rendered);
        }
        preview.innerHTML = rendered;
    } catch (_err) {
        preview.innerHTML = `<pre>${escapeHtml(markdownText)}</pre>`;
    }
}
