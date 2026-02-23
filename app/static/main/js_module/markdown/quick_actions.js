import { createDocWithPath, createFolderWithPath, remapMovedPath, renameDocWithPath, renameFolderWithPath, renameImageWithPath } from '../markdown_actions.js';
import { clearEditor, loadDoc, loadImagePreview, refreshEditorFilename } from '../markdown_editor.js';
import { state } from '../state.js';
import { baseName, parentPath } from '../utils.js';
import { deleteDocNow, deleteFolderNow, deleteImageNow } from './tree_api.js';
import { expandAncestors } from './tree_model.js';
import { setSelectedEntry, showSelectedStatus } from './selection_status.js';
import { refreshFileTree } from './tree_refresh.js';

export function openQuickAction(mode) {
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

export function hideQuickAction() {
    state.quickActionMode = null;
    document.getElementById('quick-action-panel').classList.add('hidden');
}

export function hideConfirmPanel() {
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

export async function submitQuickAction() {
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
            refreshEditorFilename();

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

export function openDeleteConfirm(source) {
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

export async function confirmDeleteSelection() {
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
