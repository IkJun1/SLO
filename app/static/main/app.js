document.addEventListener('DOMContentLoaded', () => {
    lucide.createIcons();
    initApp();
});

const API_BASE = '/api/v1';

const state = {
    activeTab: 'markdown',
    currentDocPath: null,
    currentImagePath: null,
    graphInitialized: false,
    saveTimeout: null,
    imageRenameTimeout: null,
    docs: [],
    imageFiles: [],
    folders: [],
    collapsedFolders: new Set(),
    trashItems: [],
    selectedTrashKey: null,
    selectedEntry: null,
    draggingEntry: null,
    dragOverFolderPath: null,
    dragDropInFlight: false,
    dragExpandTimer: null,
    dragExpandFolderPath: null,
    dragOverRoot: false,
    treeInlineRename: null,
    treeInlineDeleteKey: null,
    treeInlineBusy: false,
    quickActionMode: null,
    pendingDelete: null,
    chatSessions: [],
    activeChatSessionId: null,
    chatSourceDocPath: null,
    chatEditingSessionId: null,
    chatEditingSessionTitle: '',
    chatDeletingSessionId: null,
    chatMessages: [],
    chatEditingMessageId: null,
    chatEditingMessageText: '',
    chatDeletingMessageId: null
};

let selectedLabelTimer = null;

function apiPath(path) {
    return `${API_BASE}${path}`;
}

function initApp() {
    if (window.marked && typeof marked.setOptions === 'function') {
        marked.setOptions({ gfm: true, breaks: true });
    }

    setupNavigation();
    setupMarkdownView();
    setupGraphView();
    setupTrashView();
    setupLLMView();
    setupPanelSplitters();

    refreshFileTree();
}

function attachHorizontalSplitter(options) {
    const {
        splitter,
        getStartSize,
        getBounds,
        setSize,
        getNextSize
    } = options;

    let activePointerId = null;
    let startX = 0;
    let startSize = 0;

    const stopDragging = (event) => {
        if (activePointerId === null) {
            return;
        }
        if (event && event.pointerId !== undefined && event.pointerId !== activePointerId) {
            return;
        }

        if (splitter && splitter.releasePointerCapture) {
            try {
                splitter.releasePointerCapture(activePointerId);
            } catch (_err) {
            }
        }

        activePointerId = null;
        document.body.classList.remove('resizing-panels');
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', stopDragging);
        window.removeEventListener('pointercancel', stopDragging);
    };

    const onPointerMove = (event) => {
        if (activePointerId === null || event.pointerId !== activePointerId) {
            return;
        }

        const delta = event.clientX - startX;
        const nextRaw = typeof getNextSize === 'function' ? getNextSize(startSize, delta) : startSize + delta;
        const bounds = getBounds();
        const next = Math.min(bounds.max, Math.max(bounds.min, nextRaw));
        setSize(next);
    };

    splitter.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) {
            return;
        }
        if (window.matchMedia('(max-width: 1180px)').matches) {
            return;
        }

        activePointerId = event.pointerId;
        startX = event.clientX;
        startSize = getStartSize();
        document.body.classList.add('resizing-panels');

        if (splitter.setPointerCapture) {
            splitter.setPointerCapture(activePointerId);
        }

        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', stopDragging);
        window.addEventListener('pointercancel', stopDragging);
        event.preventDefault();
    });
}

function setupPanelSplitters() {
    const editorBody = document.querySelector('.editor-body');
    const editorColumn = document.querySelector('.editor-column');
    const editorSplitter = document.getElementById('editor-splitter');

    if (editorBody && editorColumn && editorSplitter) {
        attachHorizontalSplitter({
            splitter: editorSplitter,
            getStartSize: () => editorColumn.getBoundingClientRect().width,
            getBounds: () => {
                const total = editorBody.getBoundingClientRect().width;
                const min = 220;
                const max = Math.max(min, total - min - 8);
                return { min, max };
            },
            setSize: (widthPx) => {
                editorBody.style.setProperty('--editor-left-width', `${Math.round(widthPx)}px`);
            }
        });
    }

    const chatLayout = document.querySelector('.chat-layout');
    const chatSourcePanel = document.querySelector('.chat-source-panel');
    const chatSplitter = document.getElementById('chat-source-splitter');

    if (chatLayout && chatSourcePanel && chatSplitter) {
        attachHorizontalSplitter({
            splitter: chatSplitter,
            getStartSize: () => chatSourcePanel.getBoundingClientRect().width,
            getNextSize: (startSizePx, deltaX) => startSizePx - deltaX,
            getBounds: () => {
                const total = chatLayout.getBoundingClientRect().width;
                const min = 260;
                const sessionPanel = 260;
                const splitterWidth = 8;
                const minChatWidth = 340;
                const max = Math.max(min, total - sessionPanel - splitterWidth - minChatWidth);
                return { min, max };
            },
            setSize: (widthPx) => {
                chatLayout.style.setProperty('--chat-source-width', `${Math.round(widthPx)}px`);
            }
        });
    }
}

function setupNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach((item) => {
        item.addEventListener('click', () => {
            const tab = item.dataset.tab;
            switchTab(tab);
        });
    });
}

function switchTab(tabId) {
    state.activeTab = tabId;

    document.querySelectorAll('.nav-item').forEach((el) => el.classList.remove('active'));
    document.querySelector(`.nav-item[data-tab="${tabId}"]`).classList.add('active');

    document.querySelectorAll('.view').forEach((el) => el.classList.remove('active'));
    document.getElementById(`view-${tabId}`).classList.add('active');

    if (tabId === 'graph' && !state.graphInitialized) {
        initGraph();
    }

    if (tabId === 'trash') {
        void loadTrashItems();
    }
}

function setupMarkdownView() {
    const createDocBtn = document.getElementById('create-doc-btn');
    if (createDocBtn) {
        createDocBtn.addEventListener('click', () => openQuickAction('create-doc'));
    }

    const createFolderBtn = document.getElementById('create-folder-btn');
    if (createFolderBtn) {
        createFolderBtn.addEventListener('click', () => openQuickAction('create-folder'));
    }

    const renameEntryBtn = document.getElementById('rename-entry-btn');
    if (renameEntryBtn) {
        renameEntryBtn.addEventListener('click', () => openRenameAction());
    }

    const deleteEntryBtn = document.getElementById('delete-entry-btn');
    if (deleteEntryBtn) {
        deleteEntryBtn.addEventListener('click', () => openDeleteConfirm('selected'));
    }

    const deleteDocBtn = document.getElementById('delete-doc-btn');
    if (deleteDocBtn) {
        deleteDocBtn.addEventListener('click', () => openDeleteConfirm('current-doc'));
    }

    const insertImageBtn = document.getElementById('insert-image-btn');
    if (insertImageBtn) {
        insertImageBtn.addEventListener('click', () => {
            void openImagePicker();
        });
    }

    document.getElementById('quick-submit-btn').addEventListener('click', submitQuickAction);
    document.getElementById('quick-cancel-btn').addEventListener('click', hideQuickAction);

    document.getElementById('confirm-delete-btn').addEventListener('click', confirmDeleteSelection);
    document.getElementById('confirm-cancel-btn').addEventListener('click', hideConfirmPanel);

    const pathInput = document.getElementById('quick-path-input');
    let pathInputComposing = false;
    pathInput.addEventListener('compositionstart', () => {
        pathInputComposing = true;
    });
    pathInput.addEventListener('compositionend', () => {
        pathInputComposing = false;
    });
    pathInput.addEventListener('keydown', (event) => {
        if (event.isComposing || pathInputComposing || event.keyCode === 229) {
            return;
        }

        if (event.key === 'Enter') {
            event.preventDefault();
            submitQuickAction();
        }
    });

    const titleInput = document.getElementById('doc-title');
    const contentInput = document.getElementById('doc-content');
    const imageFileInput = document.getElementById('image-file-input');

    titleInput.addEventListener('input', () => {
        if (state.currentImagePath) {
            scheduleImageRename();
            return;
        }
        scheduleSave();
    });
    contentInput.addEventListener('input', () => {
        scheduleSave();
        renderMarkdownPreview(contentInput.value);
    });

    if (imageFileInput) {
        imageFileInput.addEventListener('change', () => {
            const files = imageFileInput.files;
            if (files && files.length > 0) {
                void insertUploadedImages(contentInput, Array.from(files));
            }
            imageFileInput.value = '';
        });
    }

    contentInput.addEventListener('dragover', (event) => {
        if (!hasImageFile(event.dataTransfer)) {
            return;
        }
        event.preventDefault();
        contentInput.classList.add('image-drop-active');
        if (event.dataTransfer) {
            event.dataTransfer.dropEffect = 'copy';
        }
    });

    contentInput.addEventListener('dragleave', () => {
        contentInput.classList.remove('image-drop-active');
    });

    contentInput.addEventListener('drop', (event) => {
        if (!hasImageFile(event.dataTransfer)) {
            return;
        }

        event.preventDefault();
        contentInput.classList.remove('image-drop-active');

        const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []).filter((file) =>
            String(file.type || '').startsWith('image/')
        );
        if (files.length === 0) {
            return;
        }

        const dropIndex = estimateDropIndexFromPointer(contentInput, event);
        contentInput.focus();
        contentInput.setSelectionRange(dropIndex, dropIndex);

        void insertUploadedImages(contentInput, files);
    });

    renderMarkdownPreview('');
    setSelectedEntry(null);
}

function isImagePath(path) {
    const lower = String(path || '').toLowerCase();
    return (
        lower.endsWith('.png') ||
        lower.endsWith('.jpg') ||
        lower.endsWith('.jpeg') ||
        lower.endsWith('.gif') ||
        lower.endsWith('.webp')
    );
}

function setEditorReadonlyMode(contentReadonly, titleReadonly = contentReadonly) {
    const titleInput = document.getElementById('doc-title');
    const contentInput = document.getElementById('doc-content');
    if (!titleInput || !contentInput) {
        return;
    }

    titleInput.readOnly = titleReadonly;
    contentInput.readOnly = contentReadonly;
}

async function openImagePicker() {
    if (!state.currentDocPath) {
        showSelectedStatus('Open a document before inserting images.', 'error');
        return;
    }

    const input = document.getElementById('image-file-input');
    if (!input) {
        return;
    }
    input.click();
}

function openRenameAction() {
    const selected = state.selectedEntry;
    if (!selected || (selected.type !== 'image' && selected.type !== 'doc' && selected.type !== 'folder')) {
        showSelectedStatus('Select a document, image, or folder to rename.', 'error');
        return;
    }

    if (selected.type === 'doc') {
        openQuickAction('rename-doc');
        return;
    }

    if (selected.type === 'image') {
        openQuickAction('rename-image');
        return;
    }

    openQuickAction('rename-folder');
}

function hasImageFile(dataTransfer) {
    if (!dataTransfer || !dataTransfer.files) {
        return false;
    }

    return Array.from(dataTransfer.files).some((file) => String(file.type || '').startsWith('image/'));
}

function estimateDropIndexFromPointer(textarea, event) {
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

async function insertUploadedImages(textarea, files) {
    if (!state.currentDocPath) {
        showSelectedStatus('Open a document before inserting images.', 'error');
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
        showSelectedStatus('Image inserted.');
    } catch (err) {
        console.error(err);
        showSelectedStatus((err && err.message) || 'Failed to upload image.', 'error');
    }
}

function openQuickAction(mode) {
    hideConfirmPanel();

    state.quickActionMode = mode;
    const panel = document.getElementById('quick-action-panel');
    const label = document.getElementById('quick-action-label');
    const input = document.getElementById('quick-path-input');
    const submitBtn = document.getElementById('quick-submit-btn');

    if (mode === 'create-doc') {
        label.textContent = 'Create document: enter vault-relative markdown path';
        input.placeholder = 'notes/new-note.md';
        submitBtn.textContent = 'Create Doc';
    } else if (mode === 'create-folder') {
        label.textContent = 'Create folder: enter vault-relative folder path';
        input.placeholder = 'notes/new-folder';
        submitBtn.textContent = 'Create Folder';
    } else if (mode === 'rename-doc') {
        label.textContent = 'Rename document: enter new markdown path';
        input.placeholder = 'notes/renamed-note.md';
        submitBtn.textContent = 'Rename Doc';
    } else if (mode === 'rename-folder') {
        label.textContent = 'Rename folder: enter new folder path';
        input.placeholder = 'notes/renamed-folder';
        submitBtn.textContent = 'Rename Folder';
    } else {
        label.textContent = 'Rename image: enter new path under images/';
        input.placeholder = 'images/2026/02/19/renamed.png';
        submitBtn.textContent = 'Rename Image';
    }

    input.value = getDefaultPathForMode(mode);
    panel.classList.remove('hidden');
    input.focus();
    input.select();
}

function hideQuickAction() {
    state.quickActionMode = null;
    document.getElementById('quick-action-panel').classList.add('hidden');
}

function hideConfirmPanel() {
    state.pendingDelete = null;
    document.getElementById('confirm-panel').classList.add('hidden');
}

function getDefaultPathForMode(mode) {
    const selected = state.selectedEntry;
    if (mode === 'rename-doc') {
        if (selected && selected.type === 'doc') {
            return selected.path;
        }
        return '';
    }

    if (mode === 'rename-image') {
        if (selected && selected.type === 'image') {
            return selected.path;
        }
        return '';
    }

    if (mode === 'rename-folder') {
        if (selected && selected.type === 'folder') {
            return selected.path;
        }
        return '';
    }

    const suffix = mode === 'create-doc' ? 'new-note.md' : 'new-folder';

    if (!selected) {
        return suffix;
    }

    if (selected.type === 'folder') {
        return `${selected.path}/${suffix}`;
    }

    const parent = parentPath(selected.path);
    if (parent) {
        return `${parent}/${suffix}`;
    }

    return suffix;
}

async function submitQuickAction() {
    const mode = state.quickActionMode;
    if (!mode) {
        return;
    }

    const input = document.getElementById('quick-path-input');
    const rawPath = input.value.trim();
    if (!rawPath) {
        showSelectedStatus('Path is required.', 'error');
        input.focus();
        return;
    }

    try {
        if (mode === 'create-doc') {
            expandAncestors(rawPath);
            const created = await createDocWithPath(rawPath);
            hideQuickAction();
            await refreshFileTree();
            await loadDoc(created.path);
            showSelectedStatus(`Created document: ${created.path}`);
            return;
        }

        if (mode === 'rename-doc') {
            const selected = state.selectedEntry;
            if (!selected || selected.type !== 'doc') {
                throw new Error('Select a document to rename.');
            }

            const selectedDoc = state.docs.find((doc) => doc.path === selected.path);
            const docId = (selected && selected.id) || (selectedDoc && selectedDoc.id);
            if (!docId) {
                throw new Error('Document id not found. Reload and try again.');
            }

            let toPath = rawPath;
            if (!toPath.includes('/')) {
                const parent = parentPath(selected.path);
                toPath = parent ? `${parent}/${toPath}` : toPath;
            }
            if (!toPath.toLowerCase().endsWith('.md')) {
                toPath = `${toPath}.md`;
            }

            const result = await renameDocWithPath(docId, toPath);
            expandAncestors(result.to_path);
            hideQuickAction();
            await refreshFileTree();
            await loadDoc(result.to_path);
            showSelectedStatus(`Renamed document: ${result.to_path}`);
            return;
        }

        if (mode === 'rename-image') {
            const selected = state.selectedEntry;
            if (!selected || selected.type !== 'image') {
                throw new Error('Select an image to rename.');
            }

            let toPath = rawPath;
            if (!toPath.includes('/')) {
                const parent = parentPath(selected.path);
                toPath = parent ? `${parent}/${toPath}` : toPath;
            }

            const lastDot = String(selected.path).lastIndexOf('.');
            const oldExt = lastDot >= 0 ? String(selected.path).slice(lastDot) : '';
            const targetName = baseName(toPath);
            const targetDot = targetName.lastIndexOf('.');
            const hasTargetExt = targetDot > 0 && targetDot < targetName.length - 1;
            if (oldExt && !hasTargetExt) {
                toPath = `${toPath}${oldExt}`;
            }

            const result = await renameImageWithPath(selected.path, toPath);
            expandAncestors(result.to_path);
            hideQuickAction();
            await refreshFileTree();
            await loadImagePreview(result.to_path);
            showSelectedStatus(`Renamed image: ${result.to_path}`);
            return;
        }

        if (mode === 'rename-folder') {
            const selected = state.selectedEntry;
            if (!selected || selected.type !== 'folder') {
                throw new Error('Select a folder to rename.');
            }

            let toPath = rawPath;
            if (!toPath.includes('/')) {
                const parent = parentPath(selected.path);
                toPath = parent ? `${parent}/${toPath}` : toPath;
            }

            const result = await renameFolderWithPath(selected.path, toPath);
            const fromPath = result.from_path || selected.path;
            const movedPath = result.to_path;

            state.currentDocPath = remapMovedPath(state.currentDocPath, fromPath, movedPath) || null;
            state.currentImagePath = remapMovedPath(state.currentImagePath, fromPath, movedPath) || null;

            if (state.selectedEntry) {
                const selectedPath = remapMovedPath(state.selectedEntry.path, fromPath, movedPath);
                if (selectedPath) {
                    state.selectedEntry = {
                        ...state.selectedEntry,
                        path: selectedPath
                    };
                }
            }

            expandAncestors(movedPath);
            hideQuickAction();
            await refreshFileTree();
            setSelectedEntry({ type: 'folder', path: movedPath, name: baseName(movedPath) });
            showSelectedStatus(`Renamed folder: ${movedPath}`);
            return;
        }

        expandAncestors(rawPath);
        await createFolderWithPath(rawPath);
        hideQuickAction();
        await refreshFileTree();
        setSelectedEntry({ type: 'folder', path: rawPath, name: baseName(rawPath) });
        showSelectedStatus(`Created folder: ${rawPath}`);
    } catch (err) {
        console.error(err);
        showSelectedStatus(err.message || 'Action failed.', 'error');
    }
}

function openDeleteConfirm(source) {
    hideQuickAction();

    let target = null;
    if (source === 'current-doc') {
        if (state.currentDocPath) {
            target = {
                type: 'doc',
                path: state.currentDocPath,
                name: baseName(state.currentDocPath)
            };
        } else if (state.currentImagePath) {
            target = {
                type: 'image',
                path: state.currentImagePath,
                name: baseName(state.currentImagePath)
            };
        }
    } else if (state.selectedEntry) {
        target = { ...state.selectedEntry };
    }

    if (!target) {
        showSelectedStatus('Select a document or folder first.', 'error');
        return;
    }

    state.pendingDelete = target;
    const panel = document.getElementById('confirm-panel');
    const message = document.getElementById('confirm-message');

    if (target.type === 'folder') {
        message.textContent = `Delete folder "${target.path}" recursively? Items will move to trash.`;
    } else if (target.type === 'image') {
        message.textContent = `Delete image "${target.path}" permanently?`;
    } else {
        message.textContent = `Delete document "${target.path}"? It will move to trash.`;
    }

    panel.classList.remove('hidden');
}

async function confirmDeleteSelection() {
    if (!state.pendingDelete) {
        return;
    }

    const target = { ...state.pendingDelete };

    try {
        if (target.type === 'folder') {
            await deleteFolderNow(target.path);

            if (state.currentDocPath && (state.currentDocPath === target.path || state.currentDocPath.startsWith(`${target.path}/`))) {
                clearEditor();
            }

            if (state.selectedEntry && state.selectedEntry.type === 'image') {
                const imagePath = state.selectedEntry.path;
                if (imagePath === target.path || imagePath.startsWith(`${target.path}/`)) {
                    clearEditor();
                }
            }

            showSelectedStatus(`Deleted folder: ${target.path}`);
        } else if (target.type === 'image') {
            await deleteImageNow(target.path);
            showSelectedStatus(`Deleted image: ${target.path}`);
        } else {
            await deleteDocNow(target.path);
            showSelectedStatus(`Deleted document: ${target.path}`);
        }

        hideConfirmPanel();
        await refreshFileTree();
    } catch (err) {
        console.error(err);
        showSelectedStatus(err.message || 'Delete failed.', 'error');
    }
}

function setSelectedEntry(entry) {
    state.selectedEntry = entry;
    renderFileTree();
    updateSelectedLabel();
}

function updateSelectedLabel() {
    const label = document.getElementById('explorer-selected');
    const selected = state.selectedEntry;

    if (!selected) {
        label.textContent = 'Selected: none';
        label.style.color = 'var(--text-secondary)';
        return;
    }

    const prefix = selected.type === 'folder' ? 'folder' : (selected.type === 'image' ? 'image' : 'doc');
    label.textContent = `Selected: ${prefix} ${selected.path}`;
    label.style.color = 'var(--text-secondary)';
}

function showSelectedStatus(message, tone = 'info') {
    const label = document.getElementById('explorer-selected');
    if (selectedLabelTimer) {
        clearTimeout(selectedLabelTimer);
    }

    label.textContent = message;
    label.style.color = tone === 'error' ? '#ff9d9d' : 'var(--accent-hover)';

    selectedLabelTimer = setTimeout(() => {
        updateSelectedLabel();
    }, 2400);
}

async function refreshFileTree() {
    try {
        const treeRes = await fetch(apiPath('/tree'));
        if (!treeRes.ok) {
            throw new Error('Failed to fetch folder tree.');
        }
        const treeText = await treeRes.text();
        const parsedTree = parseVaultTree(treeText);
        state.folders = parsedTree.folders;
        state.imageFiles = parsedTree.files.filter((path) => isImagePath(path));

        const docsRes = await fetch(apiPath('/docs'));
        if (!docsRes.ok) {
            throw new Error('Failed to fetch documents.');
        }

        const docsData = await docsRes.json();
        state.docs = Array.isArray(docsData.docs) ? docsData.docs : [];
        pruneCollapsedFolders();

        if (state.currentDocPath && !state.docs.some((doc) => doc.path === state.currentDocPath)) {
            clearEditor();
        }

        if (state.currentImagePath && !state.imageFiles.some((path) => path === state.currentImagePath)) {
            clearEditor();
        }

        if (state.selectedEntry) {
            if (state.selectedEntry.type === 'doc') {
                const selectedDoc = state.docs.find((doc) => doc.path === state.selectedEntry.path);
                const docExists = Boolean(selectedDoc);
                if (!docExists) {
                    state.selectedEntry = null;
                } else {
                    state.selectedEntry = {
                        ...state.selectedEntry,
                        id: selectedDoc.id,
                        name: baseName(selectedDoc.path)
                    };
                }
            } else if (state.selectedEntry.type === 'image') {
                const imageExists = state.imageFiles.some((path) => path === state.selectedEntry.path);
                if (!imageExists) {
                    state.selectedEntry = null;
                }
            } else {
                const prefix = `${state.selectedEntry.path}/`;
                const folderExists = state.folders.some(
                    (folderPath) => folderPath === state.selectedEntry.path || folderPath.startsWith(prefix)
                ) || state.docs.some(
                    (doc) => doc.path === state.selectedEntry.path || doc.path.startsWith(prefix)
                ) || state.imageFiles.some(
                    (imagePath) => imagePath === state.selectedEntry.path || imagePath.startsWith(prefix)
                );
                if (!folderExists) {
                    state.selectedEntry = null;
                }
            }
        }

        if (state.treeInlineRename) {
            const renameType = String(state.treeInlineRename.type || '');
            const renamePath = String(state.treeInlineRename.path || '');
            if (!treeEntryExists(renameType, renamePath)) {
                state.treeInlineRename = null;
            }
        }

        if (state.treeInlineDeleteKey && !treeEntryExistsByKey(state.treeInlineDeleteKey)) {
            state.treeInlineDeleteKey = null;
        }

        renderFileTree();
        updateSelectedLabel();
    } catch (err) {
        console.error('Failed to load file tree', err);
        showSelectedStatus('Failed to load file tree.', 'error');
    }
}

function buildExplorerEntries(docs, imagePaths) {
    const folderEntries = state.folders.map((path) => ({
        type: 'folder',
        path,
        name: baseName(path),
        depth: path.split('/').length - 1
    }));

    const docEntries = docs.map((doc) => ({
        type: 'doc',
        id: doc.id,
        path: doc.path,
        name: baseName(doc.path),
        depth: doc.path.split('/').length - 1
    }));

    const imageEntries = imagePaths.map((path) => ({
        type: 'image',
        path,
        name: baseName(path),
        depth: path.split('/').length - 1
    }));

    const entries = [...folderEntries, ...docEntries, ...imageEntries];
    entries.sort((a, b) => {
        if (a.path !== b.path) {
            return a.path.localeCompare(b.path);
        }
        if (a.type === b.type) {
            return 0;
        }

        const rank = { folder: 0, doc: 1, image: 2 };
        return rank[a.type] - rank[b.type];
    });

    return entries;
}

function parseVaultTree(treeText) {
    const lines = String(treeText || '').split('\n');
    const folders = [];
    const files = [];
    const stack = [];

    const pattern = /^((?:\|   |    )*)(?:\|-- |`-- )(.*)$/;

    lines.forEach((line) => {
        const match = line.match(pattern);
        if (!match) {
            return;
        }

        const indent = match[1] || '';
        const nameRaw = (match[2] || '').trim();
        const depth = Math.floor(indent.length / 4);

        stack.length = depth;

        if (nameRaw.endsWith('/')) {
            const folderName = nameRaw.slice(0, -1);
            stack[depth] = folderName;
            const folderPath = stack.join('/');
            if (folderPath) {
                folders.push(folderPath);
            }
            return;
        }

        const fileParts = stack.slice(0, depth);
        fileParts.push(nameRaw);
        const filePath = fileParts.join('/');
        if (filePath) {
            files.push(filePath);
        }
    });

    folders.sort((a, b) => a.localeCompare(b));
    files.sort((a, b) => a.localeCompare(b));
    return { folders, files };
}

function clearTreeDropTargetStyles() {
    document.querySelectorAll('.tree-item.folder.drop-target').forEach((node) => {
        node.classList.remove('drop-target');
    });
}

function setTreeRootDropTarget(enabled) {
    const next = Boolean(enabled);
    if (state.dragOverRoot === next) {
        return;
    }

    state.dragOverRoot = next;
    const container = document.getElementById('file-tree');
    if (!container) {
        return;
    }
    container.classList.toggle('root-drop-target', next);
}

function clearTreeExpandTimer() {
    if (state.dragExpandTimer) {
        clearTimeout(state.dragExpandTimer);
        state.dragExpandTimer = null;
    }
    state.dragExpandFolderPath = null;
}

function scheduleTreeExpandOnHover(folderPath) {
    const nextPath = String(folderPath || '').trim();
    if (nextPath === '' || !state.collapsedFolders.has(nextPath)) {
        clearTreeExpandTimer();
        return;
    }

    if (state.dragExpandFolderPath === nextPath && state.dragExpandTimer) {
        return;
    }

    clearTreeExpandTimer();
    state.dragExpandFolderPath = nextPath;
    state.dragExpandTimer = setTimeout(() => {
        state.dragExpandTimer = null;

        if (!state.draggingEntry) {
            state.dragExpandFolderPath = null;
            return;
        }

        if (state.dragExpandFolderPath !== nextPath) {
            return;
        }

        if (state.collapsedFolders.has(nextPath)) {
            state.collapsedFolders.delete(nextPath);
            renderFileTree();
            setTreeDropTarget(nextPath);
        }
    }, 550);
}

function setTreeDropTarget(folderPath) {
    const nextPath = folderPath ? String(folderPath).trim() : null;
    if (state.dragOverFolderPath === nextPath) {
        return;
    }

    state.dragOverFolderPath = nextPath;
    if (nextPath !== null) {
        setTreeRootDropTarget(false);
    }
    document.querySelectorAll('.tree-item.folder[data-folder-path]').forEach((node) => {
        node.classList.toggle('drop-target', nextPath !== null && node.dataset.folderPath === nextPath);
    });
}

function clearTreeDragState() {
    state.draggingEntry = null;
    state.dragOverFolderPath = null;
    state.dragOverRoot = false;

    clearTreeExpandTimer();
    setTreeRootDropTarget(false);
    clearTreeDropTargetStyles();
    document.querySelectorAll('.tree-item.dragging').forEach((node) => {
        node.classList.remove('dragging');
    });
}

function normalizeDropFolderPath(entry, targetFolderPath) {
    const target = String(targetFolderPath || '').trim();
    if (target !== '') {
        return target;
    }

    if (!entry) {
        return null;
    }

    if (entry.type === 'image') {
        return 'images';
    }

    return '';
}

function moveTargetPathForFolder(entry, targetFolderPath) {
    if (!entry || !entry.path) {
        return null;
    }

    const target = normalizeDropFolderPath(entry, targetFolderPath);
    if (target === null) {
        return null;
    }

    const name = baseName(entry.path);
    if (!name) {
        return null;
    }

    if (target === '') {
        return name;
    }

    return `${target}/${name}`;
}

function canDropEntryIntoFolder(entry, targetFolderPath) {
    if (!entry) {
        return false;
    }

    const target = normalizeDropFolderPath(entry, targetFolderPath);
    if (target === null) {
        return false;
    }

    if (entry.type === 'image' && target !== 'images' && !target.startsWith('images/')) {
        return false;
    }

    if (entry.type === 'folder') {
        if (target === entry.path || target.startsWith(`${entry.path}/`)) {
            return false;
        }
    }

    const nextPath = moveTargetPathForFolder(entry, target);
    if (!nextPath || nextPath === entry.path) {
        return false;
    }

    return true;
}

function treeEntryKey(entry) {
    if (!entry || !entry.type || !entry.path) {
        return '';
    }
    return `${entry.type}:${entry.path}`;
}

function treeEntryExists(type, path) {
    if (!type || !path) {
        return false;
    }
    if (type === 'doc') {
        return state.docs.some((doc) => doc.path === path);
    }
    if (type === 'image') {
        return state.imageFiles.some((imagePath) => imagePath === path);
    }
    if (type === 'folder') {
        return state.folders.some((folderPath) => folderPath === path);
    }
    return false;
}

function treeEntryExistsByKey(key) {
    const raw = String(key || '');
    const sep = raw.indexOf(':');
    if (sep <= 0) {
        return false;
    }

    const type = raw.slice(0, sep);
    const path = raw.slice(sep + 1);
    return treeEntryExists(type, path);
}

function inlineRenameDefaultValue(entry) {
    if (!entry || !entry.path) {
        return '';
    }

    const leaf = baseName(entry.path);
    if (entry.type === 'doc') {
        return leaf.replace(/\.md$/i, '');
    }
    return leaf;
}

function buildPathFromInlineRename(entry, rawLabel) {
    if (!entry || !entry.path) {
        return null;
    }

    const sanitized = String(rawLabel || '').replace(/[\\/]/g, ' ').trim();
    if (sanitized === '') {
        return null;
    }

    const parent = parentPath(entry.path);
    if (entry.type === 'doc') {
        const filename = sanitized.toLowerCase().endsWith('.md') ? sanitized : `${sanitized}.md`;
        return parent ? `${parent}/${filename}` : filename;
    }

    if (entry.type === 'image') {
        const oldExtIdx = String(entry.path).lastIndexOf('.');
        const oldExt = oldExtIdx >= 0 ? String(entry.path).slice(oldExtIdx) : '';
        const hasExt = /\.[^./\\]+$/.test(sanitized);
        const filename = oldExt && !hasExt ? `${sanitized}${oldExt}` : sanitized;
        return parent ? `${parent}/${filename}` : filename;
    }

    return parent ? `${parent}/${sanitized}` : sanitized;
}

function startTreeInlineRename(entry) {
    const key = treeEntryKey(entry);
    if (!key) {
        return;
    }

    clearTreeDragState();
    state.treeInlineDeleteKey = null;
    state.treeInlineRename = {
        key,
        type: entry.type,
        id: entry.id || null,
        path: entry.path,
        value: inlineRenameDefaultValue(entry),
        shouldFocus: true,
    };
    renderFileTree();
}

function cancelTreeInlineRename() {
    if (!state.treeInlineRename) {
        return;
    }
    state.treeInlineRename = null;
    renderFileTree();
}

function startTreeInlineDelete(entry) {
    const key = treeEntryKey(entry);
    if (!key) {
        return;
    }

    clearTreeDragState();
    state.treeInlineRename = null;
    state.treeInlineDeleteKey = key;
    renderFileTree();
}

function cancelTreeInlineDelete() {
    if (!state.treeInlineDeleteKey) {
        return;
    }
    state.treeInlineDeleteKey = null;
    renderFileTree();
}

async function moveEntryToPath(entry, targetPath) {
    if (!entry || !targetPath) {
        return null;
    }

    if (entry.type === 'doc') {
        const selectedDoc = state.docs.find((doc) => doc.path === entry.path);
        const docId = entry.id || (selectedDoc && selectedDoc.id);
        if (!docId) {
            throw new Error('Document id not found. Reload and try again.');
        }

        const result = await renameDocWithPath(docId, targetPath);
        const movedPath = String(result.to_path || targetPath);
        state.currentDocPath = remapMovedPath(state.currentDocPath, entry.path, movedPath) || null;

        expandAncestors(movedPath);
        await refreshFileTree();

        if (state.currentDocPath === movedPath) {
            await loadDoc(movedPath);
        } else {
            const refreshedDoc = state.docs.find((doc) => doc.path === movedPath);
            setSelectedEntry({
                type: 'doc',
                id: docId,
                path: movedPath,
                name: (refreshedDoc && baseName(refreshedDoc.path)) || baseName(movedPath)
            });
        }

        return { type: 'doc', path: movedPath };
    }

    if (entry.type === 'image') {
        const result = await renameImageWithPath(entry.path, targetPath);
        const movedPath = String(result.to_path || targetPath);
        state.currentImagePath = remapMovedPath(state.currentImagePath, entry.path, movedPath) || null;

        expandAncestors(movedPath);
        await refreshFileTree();

        if (state.currentImagePath === movedPath) {
            await loadImagePreview(movedPath);
        } else {
            setSelectedEntry({ type: 'image', path: movedPath, name: baseName(movedPath) });
        }

        return { type: 'image', path: movedPath };
    }

    if (entry.type === 'folder') {
        const result = await renameFolderWithPath(entry.path, targetPath);
        const fromPath = String(result.from_path || entry.path);
        const movedPath = String(result.to_path || targetPath);

        state.currentDocPath = remapMovedPath(state.currentDocPath, fromPath, movedPath) || null;
        state.currentImagePath = remapMovedPath(state.currentImagePath, fromPath, movedPath) || null;

        if (state.selectedEntry) {
            const selectedPath = remapMovedPath(state.selectedEntry.path, fromPath, movedPath);
            if (selectedPath) {
                state.selectedEntry = {
                    ...state.selectedEntry,
                    path: selectedPath
                };
            }
        }

        expandAncestors(movedPath);
        await refreshFileTree();
        setSelectedEntry({ type: 'folder', path: movedPath, name: baseName(movedPath) });

        return { type: 'folder', path: movedPath };
    }

    return null;
}

async function moveEntryToFolder(entry, targetFolderPath) {
    if (!entry || state.dragDropInFlight) {
        return;
    }

    if (!canDropEntryIntoFolder(entry, targetFolderPath)) {
        return;
    }

    const targetPath = moveTargetPathForFolder(entry, targetFolderPath);
    if (!targetPath) {
        return;
    }

    state.dragDropInFlight = true;

    try {
        const moved = await moveEntryToPath(entry, targetPath);
        if (!moved) {
            return;
        }

        const label = moved.type === 'folder' ? 'folder' : (moved.type === 'image' ? 'image' : 'document');
        showSelectedStatus(`Moved ${label}: ${moved.path}`);
    } catch (err) {
        console.error(err);
        showSelectedStatus((err && err.message) || 'Move failed.', 'error');
    } finally {
        state.dragDropInFlight = false;
        clearTreeDragState();
    }
}

async function submitTreeInlineRename(entry) {
    const rename = state.treeInlineRename;
    if (!rename || state.treeInlineBusy || !entry) {
        return;
    }

    const toPath = buildPathFromInlineRename(entry, rename.value);
    if (!toPath) {
        showSelectedStatus('Name is required.', 'error');
        return;
    }

    if (toPath === entry.path) {
        cancelTreeInlineRename();
        return;
    }

    state.treeInlineBusy = true;
    state.treeInlineRename = null;

    try {
        const moved = await moveEntryToPath(entry, toPath);
        if (!moved) {
            return;
        }
        const label = moved.type === 'folder' ? 'folder' : (moved.type === 'image' ? 'image' : 'document');
        showSelectedStatus(`Renamed ${label}: ${moved.path}`);
    } catch (err) {
        console.error(err);
        showSelectedStatus((err && err.message) || 'Rename failed.', 'error');
    } finally {
        state.treeInlineBusy = false;
        renderFileTree();
    }
}

async function submitTreeInlineDelete(entry) {
    if (!entry || state.treeInlineBusy) {
        return;
    }

    state.treeInlineBusy = true;
    state.treeInlineDeleteKey = null;

    try {
        if (entry.type === 'folder') {
            await deleteFolderNow(entry.path);
            showSelectedStatus(`Deleted folder: ${entry.path}`);
        } else if (entry.type === 'image') {
            await deleteImageNow(entry.path);
            showSelectedStatus(`Deleted image: ${entry.path}`);
        } else {
            await deleteDocNow(entry.path);
            showSelectedStatus(`Deleted document: ${entry.path}`);
        }

        await refreshFileTree();
    } catch (err) {
        console.error(err);
        showSelectedStatus((err && err.message) || 'Delete failed.', 'error');
    } finally {
        state.treeInlineBusy = false;
        renderFileTree();
    }
}

function renderFileTree() {
    const container = document.getElementById('file-tree');
    container.innerHTML = '';
    container.ondragover = (event) => {
        if (!state.draggingEntry) {
            return;
        }

        const target = event.target;
        const folderItem = target instanceof Element ? target.closest('.tree-item.folder[data-folder-path]') : null;
        const anyItem = target instanceof Element ? target.closest('.tree-item') : null;
        if (!folderItem) {
            setTreeDropTarget(null);
            clearTreeExpandTimer();

            if (!anyItem && canDropEntryIntoFolder(state.draggingEntry, '')) {
                event.preventDefault();
                if (event.dataTransfer) {
                    event.dataTransfer.dropEffect = 'move';
                }
                setTreeRootDropTarget(true);
            } else {
                setTreeRootDropTarget(false);
            }
        }
    };
    container.ondrop = (event) => {
        if (!state.draggingEntry) {
            return;
        }

        const target = event.target;
        const folderItem = target instanceof Element ? target.closest('.tree-item.folder[data-folder-path]') : null;
        const anyItem = target instanceof Element ? target.closest('.tree-item') : null;
        if (folderItem) {
            return;
        }

        const draggedEntry = { ...state.draggingEntry };
        clearTreeDragState();

        if (anyItem || !canDropEntryIntoFolder(draggedEntry, '')) {
            event.preventDefault();
            return;
        }

        event.preventDefault();
        void moveEntryToFolder(draggedEntry, '');
    };
    container.ondragleave = (event) => {
        if (!state.draggingEntry) {
            return;
        }

        const related = event.relatedTarget;
        if (related instanceof Node && container.contains(related)) {
            return;
        }

        setTreeDropTarget(null);
        setTreeRootDropTarget(false);
        clearTreeExpandTimer();
    };

    const entries = buildExplorerEntries(state.docs, state.imageFiles);
    entries.forEach((entry) => {
        if (hasCollapsedAncestor(entry.path)) {
            return;
        }

        const item = document.createElement('div');
        item.className = `tree-item ${entry.type}`;
        const key = treeEntryKey(entry);
        const renameState = state.treeInlineRename;
        const isInlineRename = Boolean(renameState && renameState.key === key);
        const isInlineDelete = state.treeInlineDeleteKey === key;
        const hasInlineState = isInlineRename || isInlineDelete;
        if (hasInlineState) {
            item.classList.add('has-inline-state');
        }

        const active =
            state.selectedEntry &&
            state.selectedEntry.type === entry.type &&
            state.selectedEntry.path === entry.path;

        if (active) {
            item.classList.add('active');
        }

        const isDraggingThis =
            state.draggingEntry &&
            state.draggingEntry.type === entry.type &&
            state.draggingEntry.path === entry.path;
        if (isDraggingThis) {
            item.classList.add('dragging');
        }

        item.style.paddingLeft = `${16 + entry.depth * 14}px`;
        item.draggable = !hasInlineState && !state.treeInlineBusy;

        item.addEventListener('dragstart', (event) => {
            if (state.dragDropInFlight) {
                event.preventDefault();
                return;
            }

            state.draggingEntry = {
                type: entry.type,
                id: entry.id || null,
                path: entry.path,
                name: entry.name
            };
            setTreeDropTarget(null);
            setTreeRootDropTarget(false);
            clearTreeExpandTimer();
            item.classList.add('dragging');

            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', `${entry.type}:${entry.path}`);
            }
        });

        item.addEventListener('dragend', () => {
            clearTreeDragState();
        });

        if (entry.type === 'folder') {
            item.dataset.folderPath = entry.path;
            if (state.dragOverFolderPath === entry.path) {
                item.classList.add('drop-target');
            }

            item.addEventListener('dragover', (event) => {
                const dragged = state.draggingEntry;
                if (!dragged) {
                    return;
                }

                if (!canDropEntryIntoFolder(dragged, entry.path)) {
                    if (state.dragOverFolderPath === entry.path) {
                        setTreeDropTarget(null);
                    }
                    clearTreeExpandTimer();
                    return;
                }

                event.preventDefault();
                if (event.dataTransfer) {
                    event.dataTransfer.dropEffect = 'move';
                }
                setTreeDropTarget(entry.path);
                scheduleTreeExpandOnHover(entry.path);
            });

            item.addEventListener('drop', (event) => {
                const dragged = state.draggingEntry;
                if (!dragged || !canDropEntryIntoFolder(dragged, entry.path)) {
                    return;
                }

                event.preventDefault();
                event.stopPropagation();
                const draggedEntry = { ...dragged };
                clearTreeDragState();
                void moveEntryToFolder(draggedEntry, entry.path);
            });

            const toggle = document.createElement('i');
            toggle.dataset.lucide = state.collapsedFolders.has(entry.path) ? 'chevron-right' : 'chevron-down';
            toggle.className = 'tree-toggle';
            item.appendChild(toggle);
        } else {
            const spacer = document.createElement('span');
            spacer.className = 'tree-toggle-spacer';
            item.appendChild(spacer);
        }

        const icon = document.createElement('i');
        if (entry.type === 'folder') {
            icon.dataset.lucide = 'folder';
        } else if (entry.type === 'image') {
            icon.dataset.lucide = 'image';
        } else {
            icon.dataset.lucide = 'file-text';
        }
        icon.style.width = '14px';
        item.appendChild(icon);

        const nameWrap = document.createElement('div');
        nameWrap.className = 'tree-item-name-wrap';

        if (isInlineRename && renameState) {
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'tree-inline-input';
            input.value = String(renameState.value || '');
            input.title = entry.path;
            input.placeholder = 'Rename...';

            input.addEventListener('click', (event) => {
                event.stopPropagation();
            });
            input.addEventListener('input', () => {
                if (!state.treeInlineRename || state.treeInlineRename.key !== key) {
                    return;
                }
                state.treeInlineRename = {
                    ...state.treeInlineRename,
                    value: input.value,
                    shouldFocus: false,
                };
            });
            input.addEventListener('keydown', (event) => {
                event.stopPropagation();
                if (event.key === 'Enter') {
                    event.preventDefault();
                    void submitTreeInlineRename(entry);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    cancelTreeInlineRename();
                }
            });

            nameWrap.appendChild(input);

            if (renameState.shouldFocus) {
                requestAnimationFrame(() => {
                    input.focus();
                    input.select();
                });
                state.treeInlineRename = {
                    ...state.treeInlineRename,
                    shouldFocus: false,
                };
            }
        } else {
            const text = document.createElement('span');
            text.className = 'tree-item-name';
            text.textContent = entry.name;
            text.title = entry.path;
            nameWrap.appendChild(text);
        }

        item.appendChild(nameWrap);

        const actions = document.createElement('div');
        actions.className = 'tree-item-actions';
        if (hasInlineState) {
            actions.classList.add('show');
        }

        if (isInlineRename) {
            const saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.className = 'tree-inline-btn tree-inline-btn-primary';
            saveBtn.textContent = 'Save';
            saveBtn.disabled = state.treeInlineBusy;
            saveBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                void submitTreeInlineRename(entry);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'tree-inline-btn';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.disabled = state.treeInlineBusy;
            cancelBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                cancelTreeInlineRename();
            });

            actions.appendChild(saveBtn);
            actions.appendChild(cancelBtn);
        } else if (isInlineDelete) {
            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'tree-inline-btn tree-inline-btn-danger';
            deleteBtn.textContent = 'Delete';
            deleteBtn.disabled = state.treeInlineBusy;
            deleteBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                void submitTreeInlineDelete(entry);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'tree-inline-btn';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.disabled = state.treeInlineBusy;
            cancelBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                cancelTreeInlineDelete();
            });

            actions.appendChild(deleteBtn);
            actions.appendChild(cancelBtn);
        } else {
            const renameBtn = document.createElement('button');
            renameBtn.type = 'button';
            renameBtn.className = 'tree-item-icon-btn';
            renameBtn.title = 'Rename';
            renameBtn.innerHTML = '<i data-lucide="pencil"></i>';
            renameBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                startTreeInlineRename(entry);
            });

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'tree-item-icon-btn danger';
            deleteBtn.title = 'Delete';
            deleteBtn.innerHTML = '<i data-lucide="trash-2"></i>';
            deleteBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                startTreeInlineDelete(entry);
            });

            actions.appendChild(renameBtn);
            actions.appendChild(deleteBtn);
        }

        actions.addEventListener('click', (event) => {
            event.stopPropagation();
        });
        item.appendChild(actions);

        if (entry.type === 'doc') {
            item.addEventListener('click', () => {
                if (hasInlineState) {
                    return;
                }
                void loadDoc(entry.path);
            });
        } else if (entry.type === 'image') {
            item.addEventListener('click', () => {
                if (hasInlineState) {
                    return;
                }
                void loadImagePreview(entry.path);
            });
        } else {
            item.addEventListener('click', () => {
                if (hasInlineState) {
                    return;
                }
                if (state.collapsedFolders.has(entry.path)) {
                    state.collapsedFolders.delete(entry.path);
                } else {
                    state.collapsedFolders.add(entry.path);
                }
                setSelectedEntry({ type: 'folder', path: entry.path, name: entry.name });
            });
        }

        container.appendChild(item);
    });

    lucide.createIcons();
}

async function createDocWithPath(path) {
    const title = inferTitleFromPath(path);
    const res = await fetch(apiPath('/docs'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            path,
            title,
            content: `# ${title}\n\n`,
            create_parents: true,
            overwrite: false
        })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to create document.');
    }

    return res.json();
}

async function createFolderWithPath(path) {
    const res = await fetch(apiPath('/folders'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, create_parents: true })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to create folder.');
    }
}

async function renameDocWithPath(docId, toPath) {
    const res = await fetch(apiPath('/docs/move'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            doc_id: docId,
            to_path: toPath,
            overwrite: false
        })
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to rename document.');
    }

    return body;
}

async function renameImageWithPath(fromPath, toPath) {
    const res = await fetch(apiPath('/images/rename'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            from_path: fromPath,
            to_path: toPath,
            overwrite: false
        })
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to rename image.');
    }

    return body;
}

async function renameFolderWithPath(fromPath, toPath) {
    const res = await fetch(apiPath('/folders/move'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            from_path: fromPath,
            to_path: toPath,
            overwrite: false
        })
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to rename folder.');
    }

    return body;
}

function remapMovedPath(path, fromPrefix, toPrefix) {
    const original = String(path || '').trim();
    if (!original || !fromPrefix || !toPrefix) {
        return original;
    }

    if (original === fromPrefix) {
        return toPrefix;
    }

    const withSlash = `${fromPrefix}/`;
    if (original.startsWith(withSlash)) {
        return `${toPrefix}${original.slice(fromPrefix.length)}`;
    }

    return original;
}

async function loadDoc(path) {
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
        if (state.imageRenameTimeout) {
            clearTimeout(state.imageRenameTimeout);
            state.imageRenameTimeout = null;
        }
        state.currentDocPath = doc.path;
        state.currentImagePath = null;

        document.getElementById('empty-state').style.display = 'none';
        document.getElementById('editor-container').style.display = 'flex';
        setEditorReadonlyMode(false);

        document.getElementById('doc-title').value = doc.title;
        document.getElementById('doc-content').value = doc.content;
        renderMarkdownPreview(doc.content);

        expandAncestors(doc.path);
        setSelectedEntry({ type: 'doc', id: doc.id, path: doc.path, name: baseName(doc.path) });
    } catch (err) {
        console.error('Failed to load doc', err);
        showSelectedStatus('Failed to load document.', 'error');
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

function imageRenamedPathFromTitle(currentPath, rawTitle) {
    const sanitized = String(rawTitle || '').replace(/[\\/]/g, ' ').trim();
    if (sanitized === '') {
        return null;
    }

    const lastDot = currentPath.lastIndexOf('.');
    const extension = lastDot >= 0 ? currentPath.slice(lastDot) : '';
    const parent = parentPath(currentPath);
    const filename = `${sanitized}${extension}`;
    return parent ? `${parent}/${filename}` : filename;
}

async function loadImagePreview(path) {
    const requestedPath = String(path || '').trim();
    if (!requestedPath) {
        return;
    }

    if (state.imageRenameTimeout) {
        clearTimeout(state.imageRenameTimeout);
        state.imageRenameTimeout = null;
    }

    state.currentDocPath = null;
    state.currentImagePath = requestedPath;
    document.getElementById('empty-state').style.display = 'none';
    document.getElementById('editor-container').style.display = 'flex';
    setEditorReadonlyMode(true, false);

    document.getElementById('doc-title').value = imageTitleFromPath(requestedPath);
    const markdown = imageMarkdownForPath(requestedPath);
    document.getElementById('doc-content').value = markdown;
    renderMarkdownPreview(markdown);

    expandAncestors(requestedPath);
    setSelectedEntry({ type: 'image', path: requestedPath, name: baseName(requestedPath) });
}

function clearEditor() {
    state.currentDocPath = null;
    state.currentImagePath = null;

    if (state.imageRenameTimeout) {
        clearTimeout(state.imageRenameTimeout);
        state.imageRenameTimeout = null;
    }

    setEditorReadonlyMode(false);

    document.getElementById('editor-container').style.display = 'none';
    document.getElementById('empty-state').style.display = 'flex';
    document.getElementById('doc-title').value = '';
    document.getElementById('doc-content').value = '';
    renderMarkdownPreview('');
}

function scheduleImageRename() {
    if (!state.currentImagePath) {
        return;
    }

    const status = document.getElementById('save-status');
    status.textContent = 'Renaming...';
    status.style.opacity = '1';

    if (state.imageRenameTimeout) {
        clearTimeout(state.imageRenameTimeout);
    }

    state.imageRenameTimeout = setTimeout(() => {
        void renameCurrentImageFromTitle();
    }, 700);
}

async function renameCurrentImageFromTitle() {
    const currentPath = state.currentImagePath;
    if (!currentPath) {
        return;
    }

    const titleInput = document.getElementById('doc-title');
    const targetPath = imageRenamedPathFromTitle(currentPath, titleInput.value);
    if (!targetPath) {
        showSelectedStatus('Image name cannot be empty.', 'error');
        titleInput.value = imageTitleFromPath(currentPath);
        return;
    }

    if (targetPath === currentPath) {
        const status = document.getElementById('save-status');
        status.textContent = 'Saved';
        setTimeout(() => {
            status.style.opacity = '0';
        }, 1200);
        return;
    }

    try {
        const result = await renameImageWithPath(currentPath, targetPath);
        state.currentImagePath = result.to_path;

        if (state.selectedEntry && state.selectedEntry.type === 'image') {
            state.selectedEntry = {
                ...state.selectedEntry,
                path: result.to_path,
                name: baseName(result.to_path)
            };
        }

        titleInput.value = imageTitleFromPath(result.to_path);
        const markdown = imageMarkdownForPath(result.to_path);
        document.getElementById('doc-content').value = markdown;
        renderMarkdownPreview(markdown);

        expandAncestors(result.to_path);
        await refreshFileTree();
        setSelectedEntry({ type: 'image', path: result.to_path, name: baseName(result.to_path) });

        const status = document.getElementById('save-status');
        status.textContent = 'Saved';
        setTimeout(() => {
            status.style.opacity = '0';
        }, 1200);
    } catch (err) {
        console.error(err);
        document.getElementById('save-status').textContent = 'Error saving';
        showSelectedStatus((err && err.message) || 'Failed to rename image.', 'error');
        titleInput.value = imageTitleFromPath(state.currentImagePath || currentPath);
    }
}

function scheduleSave() {
    if (!state.currentDocPath) {
        return;
    }

    const status = document.getElementById('save-status');
    status.textContent = 'Unsaved...';
    status.style.opacity = '1';

    if (state.saveTimeout) {
        clearTimeout(state.saveTimeout);
    }

    state.saveTimeout = setTimeout(saveCurrentDoc, 1000);
}

async function saveCurrentDoc() {
    if (!state.currentDocPath) {
        return;
    }

    const title = document.getElementById('doc-title').value;
    const content = document.getElementById('doc-content').value;

    try {
        const encodedPath = encodeURIComponent(state.currentDocPath);
        const res = await fetch(apiPath(`/docs/by-path?path=${encodedPath}`), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, content })
        });

        if (!res.ok) {
            throw new Error('Save failed');
        }

        const status = document.getElementById('save-status');
        status.textContent = 'Saved';
        setTimeout(() => {
            status.style.opacity = '0';
        }, 2000);

        await refreshFileTree();
    } catch (err) {
        console.error('Save failed', err);
        document.getElementById('save-status').textContent = 'Error saving';
    }
}

async function deleteDocNow(docPath) {
    const encodedPath = encodeURIComponent(docPath);
    const res = await fetch(apiPath(`/docs/by-path?path=${encodedPath}`), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'ui-delete' })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to delete document.');
    }

    if (state.currentDocPath === docPath) {
        clearEditor();
    }

    if (state.selectedEntry && state.selectedEntry.type === 'doc' && state.selectedEntry.path === docPath) {
        state.selectedEntry = null;
    }
}

async function deleteImageNow(imagePath) {
    const encodedPath = encodeURIComponent(imagePath);
    const res = await fetch(apiPath(`/images/by-path?path=${encodedPath}`), {
        method: 'DELETE'
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to delete image.');
    }

    if (state.selectedEntry && state.selectedEntry.type === 'image' && state.selectedEntry.path === imagePath) {
        state.selectedEntry = null;
    }

    clearEditor();
}

async function deleteFolderNow(path) {
    const res = await fetch(apiPath('/folders'), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, recursive: true, reason: 'ui-delete' })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to delete folder.');
    }

    if (state.selectedEntry && state.selectedEntry.type === 'folder' && state.selectedEntry.path === path) {
        state.selectedEntry = null;
    }
}

function setupTrashView() {
    document.getElementById('refresh-trash-btn').addEventListener('click', () => {
        void loadTrashItems();
    });

    document.getElementById('trash-restore-btn').addEventListener('click', () => {
        void restoreSelectedTrashItem();
    });

    document.getElementById('trash-purge-btn').addEventListener('click', () => {
        void purgeSelectedTrashItem();
    });
}

async function loadTrashItems() {
    try {
        const res = await fetch(apiPath('/trash'));
        if (!res.ok) {
            throw new Error('Failed to fetch trash items.');
        }

        const data = await res.json();
        state.trashItems = Array.isArray(data.items) ? data.items : [];

        if (!state.trashItems.some((item) => trashItemKey(item) === state.selectedTrashKey)) {
            state.selectedTrashKey = null;
        }

        renderTrashList();
        renderTrashDetail();
    } catch (err) {
        console.error(err);
        showSelectedStatus('Failed to load trash.', 'error');
    }
}

function renderTrashList() {
    const list = document.getElementById('trash-list');
    list.innerHTML = '';

    if (state.trashItems.length === 0) {
        const item = document.createElement('li');
        item.className = 'trash-item';
        item.innerHTML = '<p class="trash-item-title">Trash is empty</p>';
        list.appendChild(item);
        return;
    }

    state.trashItems.forEach((trashItem) => {
        const key = trashItemKey(trashItem);
        const li = document.createElement('li');
        li.className = 'trash-item';
        if (state.selectedTrashKey === key) {
            li.classList.add('active');
        }

        const name = baseName(trashItem.original_path || trashItem.trash_path);
        li.innerHTML = `
            <p class="trash-item-title">${escapeHtml(name)}</p>
            <p class="trash-item-meta">
                <span class="trash-type-pill ${trashItem.entry_type}">${escapeHtml(trashItem.entry_type)}</span>
                <span>${escapeHtml(new Date(trashItem.deleted_at).toLocaleString())}</span>
            </p>
        `;

        li.addEventListener('click', () => {
            state.selectedTrashKey = key;
            renderTrashList();
            renderTrashDetail();
        });

        list.appendChild(li);
    });
}

function renderTrashDetail() {
    const emptyMessage = document.getElementById('trash-empty-message');
    const card = document.getElementById('trash-detail-card');

    const selected = state.trashItems.find((item) => trashItemKey(item) === state.selectedTrashKey);
    if (!selected) {
        emptyMessage.classList.remove('hidden');
        card.classList.add('hidden');
        return;
    }

    emptyMessage.classList.add('hidden');
    card.classList.remove('hidden');

    document.getElementById('trash-detail-type').textContent = selected.entry_type;
    document.getElementById('trash-detail-original').textContent = selected.original_path;
    document.getElementById('trash-detail-path').textContent = selected.trash_path;
    document.getElementById('trash-detail-time').textContent = new Date(selected.deleted_at).toLocaleString();
}

function selectedTrashItemPayload() {
    const selected = state.trashItems.find((item) => trashItemKey(item) === state.selectedTrashKey);
    if (!selected) {
        return null;
    }

    const payload = { entry_type: selected.entry_type };
    if (selected.entry_type === 'doc') {
        payload.doc_id = selected.doc_id;
    } else {
        payload.trash_path = selected.trash_path;
    }
    return payload;
}

async function restoreSelectedTrashItem() {
    const payload = selectedTrashItemPayload();
    if (!payload) {
        return;
    }

    try {
        const res = await fetch(apiPath('/trash/restore'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const body = await safeJson(res);
            throw new Error((body && body.error && body.error.message) || 'Restore failed.');
        }

        const result = await res.json();
        await refreshFileTree();
        await loadTrashItems();
        state.graphInitialized = false;

        if (payload.entry_type === 'doc') {
            const restoredDoc = state.docs.find((doc) => doc.path === result.restored_path);
            if (restoredDoc) {
                await loadDoc(restoredDoc.path);
                switchTab('markdown');
            }
        }

        showSelectedStatus(`Restored: ${result.restored_path || 'item'}`);
    } catch (err) {
        console.error(err);
        showSelectedStatus(err.message || 'Restore failed.', 'error');
    }
}

async function purgeSelectedTrashItem() {
    const payload = selectedTrashItemPayload();
    if (!payload) {
        return;
    }

    try {
        const res = await fetch(apiPath('/trash'), {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const body = await safeJson(res);
            throw new Error((body && body.error && body.error.message) || 'Permanent delete failed.');
        }

        await refreshFileTree();
        await loadTrashItems();
        state.graphInitialized = false;
        showSelectedStatus('Permanently deleted item.');
    } catch (err) {
        console.error(err);
        showSelectedStatus(err.message || 'Permanent delete failed.', 'error');
    }
}

function trashItemKey(item) {
    if (item.entry_type === 'doc') {
        return `doc:${item.doc_id}`;
    }
    return `folder:${item.trash_path}`;
}

function setupGraphView() {
    document.getElementById('refresh-graph').addEventListener('click', () => {
        state.graphInitialized = false;
        document.getElementById('graph-container').innerHTML = '';
        initGraph();
    });

    document.getElementById('run-embedding-sync').addEventListener('click', () => {
        void runEmbeddingSync();
    });

    void loadEmbeddingSyncStatus();
}

function setEmbeddingSyncStatus(text, tone = 'idle') {
    const status = document.getElementById('embedding-sync-status');
    if (!status) {
        return;
    }

    status.textContent = text;
    if (tone === 'idle') {
        delete status.dataset.tone;
        return;
    }

    status.dataset.tone = tone;
}

async function loadEmbeddingSyncStatus() {
    try {
        const res = await fetch(apiPath('/embeddings/status'));
        if (!res.ok) {
            throw new Error('Failed to load embedding status.');
        }

        const data = await res.json();
        if (data.running) {
            setEmbeddingSyncStatus('Running', 'running');
            return;
        }

        if (data.failed > 0) {
            setEmbeddingSyncStatus(`Failed ${data.failed}`, 'error');
            return;
        }

        if (data.pending > 0) {
            setEmbeddingSyncStatus(`Pending ${data.pending}`, 'running');
            return;
        }

        setEmbeddingSyncStatus('Ready', 'success');
    } catch (_err) {
        setEmbeddingSyncStatus('Unavailable', 'error');
    }
}

async function runEmbeddingSync() {
    const runButton = document.getElementById('run-embedding-sync');
    if (runButton) {
        runButton.disabled = true;
    }

    setEmbeddingSyncStatus('Running', 'running');
    try {
        const res = await fetch(apiPath('/embeddings/run'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });

        if (!res.ok) {
            const body = await safeJson(res);
            throw new Error((body && body.error && body.error.message) || 'Embedding sync failed.');
        }

        const result = await res.json();
        if (!result.started) {
            const message = result.message || 'Embedding sync is already running.';
            showSelectedStatus(message, 'error');
            setEmbeddingSyncStatus('Running', 'running');
            return;
        }

        if (result.failed > 0) {
            setEmbeddingSyncStatus(`Failed ${result.failed}`, 'error');
        } else if (result.remaining_pending > 0) {
            setEmbeddingSyncStatus(`Pending ${result.remaining_pending}`, 'running');
        } else {
            setEmbeddingSyncStatus('Ready', 'success');
        }

        const message = result.message
            || `Embedded ${result.processed} chunks (${result.remaining_pending} pending)`;
        showSelectedStatus(message);
    } catch (err) {
        console.error(err);
        setEmbeddingSyncStatus('Failed', 'error');
        showSelectedStatus(err.message || 'Embedding sync failed.', 'error');
    } finally {
        if (runButton) {
            runButton.disabled = false;
        }
    }
}

async function initGraph() {
    try {
        const res = await fetch(apiPath('/graph3d?layout=pca&include_edges=true&top_k_edges=5'));
        if (!res.ok) {
            throw new Error('Failed to load graph.');
        }

        const json = await res.json();
        const data = json.data;

        const gData = {
            nodes: data.nodes.map((node) => ({
                id: node.node_id,
                name: node.title,
                val: node.node_type === 'folder' ? 3.3 : 2.1,
                nodeType: node.node_type,
                path: node.path
            })),
            links: data.edges.map((edge) => ({
                source: edge.from_node_id,
                target: edge.to_node_id,
                edgeType: edge.edge_type,
                weight: edge.weight
            }))
        };

        document.getElementById('node-count').textContent = gData.nodes.length;

        const graph = ForceGraph3D()(document.getElementById('graph-container'))
            .graphData(gData)
            .backgroundColor('#151a24')
            .nodeLabel((node) => {
                if (node.nodeType !== 'doc') {
                    return '';
                }
                return node.name || baseName(node.path);
            })
            .nodeColor((node) => (node.nodeType === 'folder' ? '#e8b26a' : '#7c4dff'))
            .nodeResolution(16)
            .nodeVal((node) => node.val)
            .linkColor((link) => (link.edgeType === 'folder_tree' ? '#6f81a3' : '#4b4b4b'))
            .linkOpacity(0.82)
            .linkWidth((link) => (link.edgeType === 'folder_tree' ? 1.4 : 0.8))
            .onNodeClick((node) => {
                if (node.nodeType === 'doc' && node.path) {
                    switchTab('markdown');
                    void loadDoc(node.path);
                    return;
                }

                if (node.nodeType === 'folder') {
                    expandAncestors(node.path);
                    setSelectedEntry({ type: 'folder', path: node.path, name: baseName(node.path) });
                    switchTab('markdown');
                }
            });

        const container = document.getElementById('graph-container');
        const labelLayer = document.createElement('div');
        labelLayer.className = 'graph-label-layer';
        container.appendChild(labelLayer);

        const labels = new Map();
        const DOC_LABEL_DISTANCE = 300;

        gData.nodes.forEach((node) => {
            const label = document.createElement('div');
            label.className = `graph-node-label ${node.nodeType}`;
            label.textContent = node.name || baseName(node.path);
            labelLayer.appendChild(label);
            labels.set(node.id, label);
        });

        const updateLabels = () => {
            const camPos = typeof graph.cameraPosition === 'function' ? graph.cameraPosition() : null;
            const toScreen = typeof graph.graph2ScreenCoords === 'function';
            if (!camPos || !toScreen) {
                return;
            }

            gData.nodes.forEach((node) => {
                const label = labels.get(node.id);
                if (!label) {
                    return;
                }

                const nx = Number(node.x);
                const ny = Number(node.y);
                const nz = Number(node.z);

                if (!Number.isFinite(nx) || !Number.isFinite(ny) || !Number.isFinite(nz)) {
                    label.style.display = 'none';
                    return;
                }

                let visible = node.nodeType === 'folder';
                if (!visible) {
                    const dx = nx - camPos.x;
                    const dy = ny - camPos.y;
                    const dz = nz - camPos.z;
                    const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
                    visible = distance <= DOC_LABEL_DISTANCE;
                }

                if (!visible) {
                    label.style.display = 'none';
                    return;
                }

                const projected = graph.graph2ScreenCoords(nx, ny, nz);
                if (!projected || !Number.isFinite(projected.x) || !Number.isFinite(projected.y)) {
                    label.style.display = 'none';
                    return;
                }

                const offsetY = node.nodeType === 'folder' ? 18 : 14;
                label.style.display = 'block';
                label.style.transform = `translate(-50%, -50%) translate(${projected.x}px, ${projected.y - offsetY}px)`;
            });
        };

        graph.onEngineTick(updateLabels).onEngineStop(updateLabels);
        const controls = typeof graph.controls === 'function' ? graph.controls() : null;
        if (controls && typeof controls.addEventListener === 'function') {
            controls.addEventListener('change', updateLabels);
        }
        updateLabels();

        state.graphInitialized = true;
    } catch (err) {
        console.error('Graph load failed', err);
        document.getElementById('graph-container').textContent = 'Failed to load graph data.';
    }
}

function renderChatWelcome() {
    const history = document.getElementById('chat-history');
    history.innerHTML = `
        <div class="message system">
            <div class="message-content">Hello. I am your knowledge assistant. Ask me anything about your vault.</div>
        </div>
    `;
}

function clearChatSourcePreview() {
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

async function loadChatSourcePreview(path) {
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

async function goToChatSourceDoc() {
    if (!state.chatSourceDocPath) {
        return;
    }

    switchTab('markdown');
    await loadDoc(state.chatSourceDocPath);
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

function extractDocPathsFromText(text) {
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

function buildSourceBadgesHtmlFromPaths(paths) {
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

function sourcePathsFromHits(sources) {
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

function normalizeSourcePathList(paths) {
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

function formatChatSessionTime(value) {
    if (!value) {
        return '';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return '';
    }
    return date.toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function startSessionRename(session) {
    state.chatEditingSessionId = String((session && session.session_id) || '').trim() || null;
    state.chatEditingSessionTitle = String((session && session.title) || 'Chat');
    state.chatDeletingSessionId = null;
    renderChatSessionList();
}

function cancelSessionRename() {
    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    renderChatSessionList();
}

async function saveSessionRename(sessionId) {
    const targetId = String(sessionId || '').trim();
    if (!targetId) {
        return;
    }

    const trimmed = String(state.chatEditingSessionTitle || '').trim();
    if (!trimmed) {
        showSelectedStatus('Title cannot be empty.', 'error');
        return;
    }

    const res = await fetch(apiPath(`/chat/sessions/${encodeURIComponent(targetId)}`), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed })
    });
    if (!res.ok) {
        throw new Error('Failed to rename chat session.');
    }

    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    await loadChatSessions(targetId);
}

function requestDeleteSession(sessionId) {
    const targetId = String(sessionId || '').trim();
    if (!targetId) {
        return;
    }
    state.chatDeletingSessionId = targetId;
    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    renderChatSessionList();
}

function cancelDeleteSession() {
    state.chatDeletingSessionId = null;
    renderChatSessionList();
}

async function confirmDeleteSession(sessionId) {
    const targetId = String(sessionId || '').trim();
    if (!targetId) {
        return;
    }

    const res = await fetch(apiPath(`/chat/sessions/${encodeURIComponent(targetId)}`), {
        method: 'DELETE'
    });
    if (!res.ok) {
        throw new Error('Failed to delete chat session.');
    }

    state.chatDeletingSessionId = null;

    const wasActive = state.activeChatSessionId === targetId;
    await loadChatSessions();

    if (!wasActive) {
        return;
    }

    if (state.chatSessions.length > 0) {
        await loadChatSessionMessages(state.chatSessions[0].session_id);
    } else {
        state.activeChatSessionId = null;
        state.chatMessages = [];
        renderChatWelcome();
        clearChatSourcePreview();
    }
}

function startEditUserMessage(message) {
    const role = String((message && message.role) || '').toLowerCase();
    if (role !== 'user') {
        return;
    }

    const messageId = String((message && message.message_id) || '').trim();
    if (!messageId) {
        return;
    }

    state.chatEditingMessageId = messageId;
    state.chatEditingMessageText = String((message && message.content) || '');
    state.chatDeletingMessageId = null;
    renderChatHistoryFromState();
}

function cancelEditUserMessage() {
    state.chatEditingMessageId = null;
    state.chatEditingMessageText = '';
    renderChatHistoryFromState();
}

function findPairedAssistantMessageIndex(messages, userIndex) {
    if (!Array.isArray(messages) || userIndex < 0) {
        return -1;
    }

    for (let idx = userIndex + 1; idx < messages.length; idx += 1) {
        const role = String((messages[idx] && messages[idx].role) || '').toLowerCase();
        if (role === 'assistant') {
            return idx;
        }
        if (role === 'user') {
            break;
        }
    }

    return -1;
}

async function saveEditedUserMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
        return;
    }

    const content = String(state.chatEditingMessageText || '').trim();
    if (!content) {
        showSelectedStatus('Message cannot be empty.', 'error');
        return;
    }

    const messages = Array.isArray(state.chatMessages) ? state.chatMessages : [];
    const targetIndex = messages.findIndex((item) => String((item && item.message_id) || '').trim() === targetId);
    const localMessage = targetIndex >= 0 && messages[targetIndex] && typeof messages[targetIndex] === 'object'
        ? messages[targetIndex]
        : null;
    const previousUserContent = localMessage ? String(localMessage.content || '') : null;

    const pairedAssistantIndex = findPairedAssistantMessageIndex(messages, targetIndex);
    const pairedAssistant = pairedAssistantIndex >= 0
        ? messages[pairedAssistantIndex]
        : null;
    const previousAssistantSnapshot = pairedAssistant && typeof pairedAssistant === 'object'
        ? {
            content: String(pairedAssistant.content || ''),
            sourceDocPaths: Array.isArray(pairedAssistant.source_doc_paths)
                ? [...pairedAssistant.source_doc_paths]
                : []
        }
        : null;

    let insertedPlaceholderId = '';
    if (localMessage) {
        localMessage.content = content;
    }

    if (pairedAssistant && typeof pairedAssistant === 'object') {
        pairedAssistant.content = 'Thinking...';
        pairedAssistant.source_doc_paths = [];
    } else if (targetIndex >= 0) {
        insertedPlaceholderId = `pending-assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        messages.splice(targetIndex + 1, 0, {
            message_id: insertedPlaceholderId,
            session_id: localMessage && typeof localMessage.session_id === 'string'
                ? localMessage.session_id
                : (state.activeChatSessionId || ''),
            role: 'assistant',
            content: 'Thinking...',
            source_doc_paths: [],
            created_at: new Date().toISOString()
        });
    }

    state.chatEditingMessageId = null;
    state.chatEditingMessageText = '';
    renderChatHistoryFromState();
    showSelectedStatus('Regenerating answer...');

    let updateSucceeded = false;
    try {
        const res = await fetch(apiPath(`/chat/messages/${encodeURIComponent(targetId)}`), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        const body = await safeJson(res);
        if (!res.ok) {
            throw new Error((body && body.error && body.error.message) || 'Failed to update message.');
        }
        updateSucceeded = true;

        const sessionId = body && typeof body.session_id === 'string'
            ? body.session_id.trim()
            : '';

        if (sessionId !== '') {
            state.activeChatSessionId = sessionId;
        }

        await loadChatSessions(state.activeChatSessionId);
        if (state.activeChatSessionId) {
            await loadChatSessionMessages(state.activeChatSessionId);
        }

        showSelectedStatus('Message updated.');
    } catch (err) {
        console.error(err);

        if (!updateSucceeded) {
            if (localMessage && previousUserContent !== null) {
                localMessage.content = previousUserContent;
            }

            if (pairedAssistant && previousAssistantSnapshot) {
                pairedAssistant.content = previousAssistantSnapshot.content;
                pairedAssistant.source_doc_paths = [...previousAssistantSnapshot.sourceDocPaths];
            }

            if (insertedPlaceholderId !== '') {
                const placeholderIndex = messages.findIndex(
                    (item) => String((item && item.message_id) || '').trim() === insertedPlaceholderId
                );
                if (placeholderIndex >= 0) {
                    messages.splice(placeholderIndex, 1);
                }
            }

            state.chatEditingMessageId = targetId;
            state.chatEditingMessageText = content;
            renderChatHistoryFromState();
        }

        showSelectedStatus((err && err.message) || 'Failed to update message.', 'error');
    }
}

function requestDeleteUserMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
        return;
    }
    state.chatDeletingMessageId = targetId;
    state.chatEditingMessageId = null;
    state.chatEditingMessageText = '';
    renderChatHistoryFromState();
}

function cancelDeleteUserMessage() {
    state.chatDeletingMessageId = null;
    renderChatHistoryFromState();
}

async function confirmDeleteUserMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
        return;
    }

    const res = await fetch(apiPath(`/chat/messages/${encodeURIComponent(targetId)}`), {
        method: 'DELETE'
    });
    if (!res.ok) {
        throw new Error('Failed to delete message.');
    }

    state.chatDeletingMessageId = null;

    await loadChatSessions(state.activeChatSessionId);
    if (state.activeChatSessionId) {
        await loadChatSessionMessages(state.activeChatSessionId);
    }
}

function appendChatMessage(historyEl, message) {
    const wrapper = document.createElement('div');
    const rawRole = String((message && message.role) || '').toLowerCase();
    const role = rawRole === 'user' ? 'user' : 'assistant';
    wrapper.className = `message ${role === 'assistant' ? 'system' : 'user'}`;

    const messageId = String((message && message.message_id) || '').trim();

    if (role === 'assistant') {
        const content = document.createElement('div');
        content.className = 'message-content';
        let html = marked.parse(String(message.content || ''));
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }
        const sourcePaths = normalizeSourcePathList(message.source_doc_paths);
        const fallbackPaths = sourcePaths.length > 0 ? sourcePaths : extractDocPathsFromText(message.content || '');
        const sourcesHtml = buildSourceBadgesHtmlFromPaths(fallbackPaths);
        content.innerHTML = `${html}${sourcesHtml}`;
        wrapper.appendChild(content);
        historyEl.appendChild(wrapper);
        return;
    }

    if (state.chatEditingMessageId === messageId) {
        const editor = document.createElement('div');
        editor.className = 'message-edit-area';

        const textarea = document.createElement('textarea');
        textarea.className = 'message-edit-input';
        textarea.value = state.chatEditingMessageText || String(message.content || '');
        textarea.addEventListener('input', () => {
            state.chatEditingMessageText = textarea.value;
        });

        const actions = document.createElement('div');
        actions.className = 'message-edit-actions';

        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'message-tool-btn';
        saveBtn.textContent = 'Save';
        saveBtn.addEventListener('click', () => {
            void saveEditedUserMessage(messageId);
        });

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'message-tool-btn';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => {
            cancelEditUserMessage();
        });

        actions.appendChild(saveBtn);
        actions.appendChild(cancelBtn);
        editor.appendChild(textarea);
        editor.appendChild(actions);
        wrapper.appendChild(editor);
        historyEl.appendChild(wrapper);
        return;
    }

    const content = document.createElement('div');
    content.className = 'message-content';
    content.innerHTML = escapeHtml(String(message.content || ''));
    wrapper.appendChild(content);

    if (role === 'user' && messageId) {
        const tools = document.createElement('div');
        tools.className = 'message-tools';

        if (state.chatDeletingMessageId === messageId) {
            const confirmBtn = document.createElement('button');
            confirmBtn.type = 'button';
            confirmBtn.className = 'message-tool-btn danger';
            confirmBtn.textContent = 'Confirm Delete';
            confirmBtn.addEventListener('click', () => {
                void confirmDeleteUserMessage(messageId);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'message-tool-btn';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.addEventListener('click', () => {
                cancelDeleteUserMessage();
            });

            tools.appendChild(confirmBtn);
            tools.appendChild(cancelBtn);
        } else {
            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'message-tool-btn';
            editBtn.textContent = 'Edit';
            editBtn.addEventListener('click', () => {
                startEditUserMessage(message);
            });

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'message-tool-btn danger';
            deleteBtn.textContent = 'Delete';
            deleteBtn.addEventListener('click', () => {
                requestDeleteUserMessage(messageId);
            });

            tools.appendChild(editBtn);
            tools.appendChild(deleteBtn);
        }

        wrapper.appendChild(tools);
    }

    historyEl.appendChild(wrapper);
}

function renderChatHistoryFromState() {
    const history = document.getElementById('chat-history');
    if (!history) {
        return;
    }

    const messages = Array.isArray(state.chatMessages) ? state.chatMessages : [];
    history.innerHTML = '';
    if (messages.length === 0) {
        renderChatWelcome();
        return;
    }

    messages.forEach((message) => {
        appendChatMessage(history, message);
    });

    history.scrollTop = history.scrollHeight;
}

function renderChatSessionList() {
    const list = document.getElementById('chat-session-list');
    list.innerHTML = '';

    if (!Array.isArray(state.chatSessions) || state.chatSessions.length === 0) {
        list.innerHTML = '<div class="chat-session-empty">No chat sessions yet.</div>';
        return;
    }

    state.chatSessions.forEach((session) => {
        const sessionId = String(session.session_id || '').trim();
        const item = document.createElement('div');
        item.className = 'chat-session-item';
        if (state.activeChatSessionId === sessionId) {
            item.classList.add('active');
        }

        if (state.chatEditingSessionId === sessionId) {
            const editWrap = document.createElement('div');
            editWrap.className = 'chat-session-edit-wrap';

            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'chat-session-edit-input';
            input.value = state.chatEditingSessionTitle || String(session.title || 'Chat');
            input.addEventListener('input', () => {
                state.chatEditingSessionTitle = input.value;
            });

            const actions = document.createElement('div');
            actions.className = 'chat-session-actions';

            const saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.className = 'chat-session-action-btn';
            saveBtn.textContent = 'Save';
            saveBtn.addEventListener('click', () => {
                void saveSessionRename(sessionId);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'chat-session-action-btn';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.addEventListener('click', () => {
                cancelSessionRename();
            });

            actions.appendChild(saveBtn);
            actions.appendChild(cancelBtn);
            editWrap.appendChild(input);
            editWrap.appendChild(actions);
            item.appendChild(editWrap);
        } else {
            item.innerHTML = `
                <div class="chat-session-row">
                    <div class="chat-session-name">${escapeHtml(session.title || 'Chat')}</div>
                    <div class="chat-session-actions"></div>
                </div>
                <div class="chat-session-time">${escapeHtml(formatChatSessionTime(session.updated_at))}</div>
            `;

            item.addEventListener('click', () => {
                void loadChatSessionMessages(sessionId);
            });

            const actionsWrap = item.querySelector('.chat-session-actions');
            if (actionsWrap) {
                if (state.chatDeletingSessionId === sessionId) {
                    const confirmBtn = document.createElement('button');
                    confirmBtn.type = 'button';
                    confirmBtn.className = 'chat-session-action-btn danger';
                    confirmBtn.textContent = 'Confirm';
                    confirmBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        void confirmDeleteSession(sessionId);
                    });

                    const cancelBtn = document.createElement('button');
                    cancelBtn.type = 'button';
                    cancelBtn.className = 'chat-session-action-btn';
                    cancelBtn.textContent = 'Cancel';
                    cancelBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        cancelDeleteSession();
                    });

                    actionsWrap.appendChild(confirmBtn);
                    actionsWrap.appendChild(cancelBtn);
                } else {
                    const renameBtn = document.createElement('button');
                    renameBtn.type = 'button';
                    renameBtn.className = 'chat-session-action-btn';
                    renameBtn.textContent = 'Rename';
                    renameBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        startSessionRename(session);
                    });

                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.className = 'chat-session-action-btn danger';
                    deleteBtn.textContent = 'Delete';
                    deleteBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        requestDeleteSession(sessionId);
                    });

                    actionsWrap.appendChild(renameBtn);
                    actionsWrap.appendChild(deleteBtn);
                }
            }
        }

        list.appendChild(item);
    });
}

async function loadChatSessions(preferredSessionId = null) {
    const res = await fetch(apiPath('/chat/sessions'));
    if (!res.ok) {
        throw new Error('Failed to load chat sessions.');
    }

    const data = await res.json();
    const sessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.chatSessions = sessions;

    if (preferredSessionId) {
        state.activeChatSessionId = preferredSessionId;
    } else if (state.activeChatSessionId && !sessions.some((item) => item.session_id === state.activeChatSessionId)) {
        state.activeChatSessionId = null;
    }

    if (state.chatEditingSessionId && !sessions.some((item) => item.session_id === state.chatEditingSessionId)) {
        state.chatEditingSessionId = null;
        state.chatEditingSessionTitle = '';
    }

    if (state.chatDeletingSessionId && !sessions.some((item) => item.session_id === state.chatDeletingSessionId)) {
        state.chatDeletingSessionId = null;
    }

    renderChatSessionList();
}

async function createNewChatSession() {
    const res = await fetch(apiPath('/chat/sessions'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    });
    if (!res.ok) {
        throw new Error('Failed to create chat session.');
    }

    const session = await res.json();
    state.activeChatSessionId = session.session_id;
    state.chatMessages = [];
    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    state.chatDeletingSessionId = null;
    await loadChatSessions(session.session_id);
    renderChatWelcome();
    clearChatSourcePreview();
}

async function loadChatSessionMessages(sessionId) {
    const target = String(sessionId || '').trim();
    if (!target) {
        return;
    }

    const res = await fetch(apiPath(`/chat/sessions/${encodeURIComponent(target)}/messages`));
    if (!res.ok) {
        throw new Error('Failed to load chat messages.');
    }

    const data = await res.json();
    const session = data.session || null;
    const messages = Array.isArray(data.messages) ? data.messages : [];
    const previousSessionId = state.activeChatSessionId;

    state.activeChatSessionId = session && session.session_id ? session.session_id : target;
    state.chatMessages = messages;
    if (previousSessionId !== state.activeChatSessionId) {
        state.chatEditingMessageId = null;
        state.chatEditingMessageText = '';
        state.chatDeletingMessageId = null;
    }
    renderChatSessionList();
    clearChatSourcePreview();
    renderChatHistoryFromState();
}

async function initializeChatSessions() {
    try {
        await loadChatSessions();
        if (state.activeChatSessionId) {
            await loadChatSessionMessages(state.activeChatSessionId);
            return;
        }

        if (state.chatSessions.length > 0) {
            await loadChatSessionMessages(state.chatSessions[0].session_id);
            return;
        }

        state.chatMessages = [];
        renderChatWelcome();
    } catch (err) {
        console.error(err);
        state.chatMessages = [];
        renderChatWelcome();
    }
}

function setupLLMView() {
    const btn = document.getElementById('send-chat-btn');
    const input = document.getElementById('chat-input');
    const newChatBtn = document.getElementById('new-chat-btn');
    const gotoBtn = document.getElementById('chat-source-goto-btn');
    let chatInputComposing = false;

    input.addEventListener('compositionstart', () => {
        chatInputComposing = true;
    });
    input.addEventListener('compositionend', () => {
        chatInputComposing = false;
    });

    const send = () => {
        const text = input.value.trim();
        if (!text) {
            return;
        }

        void handleChat(text);
        input.value = '';
    };

    btn.addEventListener('click', send);
    input.addEventListener('keydown', (event) => {
        if (event.isComposing || chatInputComposing || event.keyCode === 229) {
            return;
        }

        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            send();
        }
    });

    newChatBtn.addEventListener('click', () => {
        void createNewChatSession();
    });

    gotoBtn.addEventListener('click', () => {
        void goToChatSourceDoc();
    });

    void initializeChatSessions();
}

async function handleChat(query) {
    const history = document.getElementById('chat-history');

    const userMsg = document.createElement('div');
    userMsg.className = 'message user';
    userMsg.innerHTML = `<div class="message-content">${escapeHtml(query)}</div>`;
    history.appendChild(userMsg);

    const sysMsg = document.createElement('div');
    sysMsg.className = 'message system';
    sysMsg.innerHTML = '<div class="message-content">Thinking...</div>';
    history.appendChild(sysMsg);

    history.scrollTop = history.scrollHeight;

    try {
        const payload = { query, mode: 'vector' };
        if (state.activeChatSessionId) {
            payload.session_id = state.activeChatSessionId;
        }

        const res = await fetch(apiPath('/llm/answer'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            throw new Error('Failed to generate answer.');
        }

        const data = await res.json();
        if (data.session_id) {
            state.activeChatSessionId = data.session_id;
        }

        let html = marked.parse(data.answer || '');
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }

        const sourcePaths = sourcePathsFromHits(data.sources);
        const sourcesHtml = buildSourceBadgesHtmlFromPaths(sourcePaths);

        sysMsg.innerHTML = `<div class="message-content">${html}${sourcesHtml}</div>`;
        await loadChatSessions(state.activeChatSessionId);
        if (state.activeChatSessionId) {
            await loadChatSessionMessages(state.activeChatSessionId);
        }
    } catch (err) {
        sysMsg.innerHTML = '<div class="message-content error">Error generating response.</div>';
    }

    history.scrollTop = history.scrollHeight;
}

window.openDocFromChat = (encodedPath) => {
    const path = decodeURIComponent(encodedPath || '');
    if (!path) {
        return;
    }
    void loadChatSourcePreview(path);
};

function renderMarkdownPreview(markdownText) {
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

function inferTitleFromPath(path) {
    const name = baseName(path).replace(/\.md$/i, '');
    if (!name) {
        return 'Untitled';
    }

    return name
        .split(/[-_\s]+/)
        .filter(Boolean)
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

function parentPath(path) {
    const idx = path.lastIndexOf('/');
    if (idx <= 0) {
        return '';
    }
    return path.slice(0, idx);
}

function hasCollapsedAncestor(path) {
    let current = parentPath(path);
    while (current) {
        if (state.collapsedFolders.has(current)) {
            return true;
        }
        current = parentPath(current);
    }
    return false;
}

function expandAncestors(path) {
    let current = parentPath(path);
    while (current) {
        state.collapsedFolders.delete(current);
        current = parentPath(current);
    }
}

function pruneCollapsedFolders() {
    const known = new Set(state.folders);

    state.docs.forEach((doc) => {
        const parts = String(doc.path || '').split('/');
        for (let idx = 1; idx < parts.length; idx += 1) {
            known.add(parts.slice(0, idx).join('/'));
        }
    });

    Array.from(state.collapsedFolders).forEach((folderPath) => {
        if (!known.has(folderPath)) {
            state.collapsedFolders.delete(folderPath);
        }
    });
}

function baseName(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
}

async function safeJson(response) {
    try {
        return await response.json();
    } catch (_err) {
        return null;
    }
}

function escapeHtml(text) {
    if (!text) {
        return '';
    }

    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
