import { apiPath } from './api.js';
import { renderMathIfAvailable } from './math_renderer.js';
import { state } from './state.js';
import { baseName, escapeHtml } from './utils.js';

let moduleDeps = {
    switchTab: () => {},
    loadDoc: async () => {}
};

export function configureChatSourcesModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function clearChatSourcePreview() {
    state.chatSourceDocPath = null;

    const pathLabel = document.getElementById('chat-source-path');
    const gotoBtn = document.getElementById('chat-source-goto-btn');
    const preview = document.getElementById('chat-source-preview');

    if (pathLabel) {
        pathLabel.textContent = 'No source selected';
    }
    if (gotoBtn) {
        gotoBtn.disabled = true;
    }
    if (preview) {
        preview.innerHTML = '<p class="chat-source-empty">Click a source badge to preview the document here.</p>';
    }
}

export async function loadChatSourcePreview(path) {
    const targetPath = String(path || '').trim();
    if (!targetPath) {
        return;
    }

    const pathLabel = document.getElementById('chat-source-path');
    const gotoBtn = document.getElementById('chat-source-goto-btn');
    const preview = document.getElementById('chat-source-preview');

    if (pathLabel) {
        pathLabel.textContent = targetPath;
    }
    if (gotoBtn) {
        gotoBtn.disabled = true;
    }
    if (preview) {
        preview.innerHTML = '<p class="chat-source-empty">Loading source document...</p>';
    }

    try {
        const encoded = encodeURIComponent(targetPath);
        const res = await fetch(apiPath(`/docs/by-path?path=${encoded}`));
        if (!res.ok) {
            throw new Error('Failed to load source document.');
        }

        const doc = await res.json();
        let html = marked.parse(String(doc.content || ''));
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }

        state.chatSourceDocPath = String(doc.path || targetPath);
        if (pathLabel) {
            pathLabel.textContent = state.chatSourceDocPath;
        }
        if (preview) {
            preview.innerHTML = html;
            renderMathIfAvailable(preview);
        }
        if (gotoBtn) {
            gotoBtn.disabled = false;
        }
    } catch (err) {
        console.error(err);
        state.chatSourceDocPath = null;
        if (preview) {
            preview.innerHTML = '<p class="chat-source-empty">Failed to load source preview.</p>';
        }
        if (gotoBtn) {
            gotoBtn.disabled = true;
        }
    }
}

export async function goToChatSourceDoc() {
    if (!state.chatSourceDocPath) {
        return;
    }

    moduleDeps.switchTab('markdown');
    await moduleDeps.loadDoc(state.chatSourceDocPath);
}

function normalizeSourcePath(path) {
    const raw = String(path || '').trim();
    if (!raw) {
        return null;
    }

    const cleaned = raw
        .replace(/^['"`\[(]+/, '')
        .replace(/[\]\)"'`,.;:!?]+$/, '');

    if (!cleaned || !cleaned.includes('.md')) {
        return null;
    }
    return cleaned;
}

export function extractDocPathsFromText(text) {
    const sourceText = String(text || '');
    const matches = sourceText.match(/[^\s"'`<>\]\)]+\.md/g) || [];
    const seen = new Set();
    const paths = [];

    matches.forEach((candidate) => {
        const normalized = normalizeSourcePath(candidate);
        if (!normalized || seen.has(normalized)) {
            return;
        }
        seen.add(normalized);
        paths.push(normalized);
    });

    return paths;
}

export function buildSourceBadgesHtmlFromPaths(paths) {
    const items = Array.isArray(paths) ? paths : [];
    if (items.length === 0) {
        return '';
    }

    let html = '<div class="sources-list">';
    items.forEach((path) => {
        const normalized = normalizeSourcePath(path);
        if (!normalized) {
            return;
        }
        const encodedPath = encodeURIComponent(normalized);
        html += `<div class="source-badge" onclick="openDocFromChat('${encodedPath}')">${escapeHtml(baseName(normalized))}</div>`;
    });
    html += '</div>';
    return html;
}

export function sourcePathsFromHits(sources) {
    if (!Array.isArray(sources)) {
        return [];
    }

    const seen = new Set();
    const paths = [];
    sources.forEach((source) => {
        if (!source || typeof source !== 'object') {
            return;
        }
        const normalized = normalizeSourcePath(source.doc_path);
        if (!normalized || seen.has(normalized)) {
            return;
        }
        seen.add(normalized);
        paths.push(normalized);
    });
    return paths;
}

export function normalizeSourcePathList(paths) {
    if (!Array.isArray(paths)) {
        return [];
    }

    const seen = new Set();
    const normalized = [];
    paths.forEach((item) => {
        const path = normalizeSourcePath(item);
        if (!path || seen.has(path)) {
            return;
        }
        seen.add(path);
        normalized.push(path);
    });
    return normalized;
}

export function setupChatSourceGlobalActions() {
    window.openDocFromChat = (encodedPath) => {
        const path = decodeURIComponent(encodedPath || '');
        if (!path) {
            return;
        }
        void loadChatSourcePreview(path);
    };
}
