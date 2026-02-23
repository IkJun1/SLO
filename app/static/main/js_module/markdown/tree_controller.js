import { state } from '../state.js';
import { getMarkdownTreeModuleDeps } from './tree_context.js';
import {
    buildPathFromInlineRename,
    inlineRenameDefaultValue,
    treeEntryKey
} from './tree_model.js';
import { deleteDocNow, deleteFolderNow, deleteImageNow } from './tree_api.js';

let controllerDeps = {
    renderFileTree: () => {}
};

export function configureTreeControllerModule(deps) {
    controllerDeps = {
        ...controllerDeps,
        ...deps
    };
}

function clearTreeDropTargetStyles() {
    document.querySelectorAll('.tree-item.folder.drop-target').forEach((node) => {
        node.classList.remove('drop-target');
    });
}

export function setTreeRootDropTarget(enabled) {
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

export function clearTreeExpandTimer() {
    if (state.dragExpandTimer) {
        clearTimeout(state.dragExpandTimer);
        state.dragExpandTimer = null;
    }
    state.dragExpandFolderPath = null;
}

export function scheduleTreeExpandOnHover(folderPath) {
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
            controllerDeps.renderFileTree();
            setTreeDropTarget(nextPath);
        }
    }, 550);
}

export function setTreeDropTarget(folderPath) {
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

export function clearTreeDragState() {
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
    const moduleDeps = getMarkdownTreeModuleDeps();

    if (!entry || !entry.path) {
        return null;
    }

    const target = normalizeDropFolderPath(entry, targetFolderPath);
    if (target === null) {
        return null;
    }

    const name = moduleDeps.baseName(entry.path);
    if (!name) {
        return null;
    }

    if (target === '') {
        return name;
    }

    return `${target}/${name}`;
}

export function canDropEntryIntoFolder(entry, targetFolderPath) {
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

export function startTreeInlineRename(entry) {
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
    controllerDeps.renderFileTree();
}

export function cancelTreeInlineRename() {
    if (!state.treeInlineRename) {
        return;
    }
    state.treeInlineRename = null;
    controllerDeps.renderFileTree();
}

export function startTreeInlineDelete(entry) {
    const key = treeEntryKey(entry);
    if (!key) {
        return;
    }

    clearTreeDragState();
    state.treeInlineRename = null;
    state.treeInlineDeleteKey = key;
    controllerDeps.renderFileTree();
}

export function cancelTreeInlineDelete() {
    if (!state.treeInlineDeleteKey) {
        return;
    }
    state.treeInlineDeleteKey = null;
    controllerDeps.renderFileTree();
}

async function moveEntryToPath(entry, targetPath) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    if (!entry || !targetPath) {
        return null;
    }

    if (entry.type === 'doc') {
        const selectedDoc = state.docs.find((doc) => doc.path === entry.path);
        const docId = entry.id || (selectedDoc && selectedDoc.id);
        if (!docId) {
            throw new Error('Document id not found. Reload and try again.');
        }

        const result = await moduleDeps.renameDocWithPath(docId, targetPath);
        const movedPath = String(result.to_path || targetPath);
        state.currentDocPath = moduleDeps.remapMovedPath(state.currentDocPath, entry.path, movedPath) || null;
        moduleDeps.refreshEditorFilename();

        moduleDeps.expandAncestors(movedPath);
        await moduleDeps.refreshFileTree();

        if (state.currentDocPath === movedPath) {
            await moduleDeps.loadDoc(movedPath);
        } else {
            const refreshedDoc = state.docs.find((doc) => doc.path === movedPath);
            moduleDeps.setSelectedEntry({
                type: 'doc',
                id: docId,
                path: movedPath,
                name: (refreshedDoc && moduleDeps.baseName(refreshedDoc.path)) || moduleDeps.baseName(movedPath)
            });
        }

        return { type: 'doc', path: movedPath };
    }

    if (entry.type === 'image') {
        const result = await moduleDeps.renameImageWithPath(entry.path, targetPath);
        const movedPath = String(result.to_path || targetPath);
        state.currentImagePath = moduleDeps.remapMovedPath(state.currentImagePath, entry.path, movedPath) || null;
        moduleDeps.refreshEditorFilename();

        moduleDeps.expandAncestors(movedPath);
        await moduleDeps.refreshFileTree();

        if (state.currentImagePath === movedPath) {
            await moduleDeps.loadImagePreview(movedPath);
        } else {
            moduleDeps.setSelectedEntry({ type: 'image', path: movedPath, name: moduleDeps.baseName(movedPath) });
        }

        return { type: 'image', path: movedPath };
    }

    if (entry.type === 'folder') {
        const result = await moduleDeps.renameFolderWithPath(entry.path, targetPath);
        const fromPath = String(result.from_path || entry.path);
        const movedPath = String(result.to_path || targetPath);

        state.currentDocPath = moduleDeps.remapMovedPath(state.currentDocPath, fromPath, movedPath) || null;
        state.currentImagePath = moduleDeps.remapMovedPath(state.currentImagePath, fromPath, movedPath) || null;
        moduleDeps.refreshEditorFilename();

        if (state.selectedEntry) {
            const selectedPath = moduleDeps.remapMovedPath(state.selectedEntry.path, fromPath, movedPath);
            if (selectedPath) {
                state.selectedEntry = {
                    ...state.selectedEntry,
                    path: selectedPath
                };
            }
        }

        moduleDeps.expandAncestors(movedPath);
        await moduleDeps.refreshFileTree();
        moduleDeps.setSelectedEntry({ type: 'folder', path: movedPath, name: moduleDeps.baseName(movedPath) });

        return { type: 'folder', path: movedPath };
    }

    return null;
}

export async function moveEntryToFolder(entry, targetFolderPath) {
    const moduleDeps = getMarkdownTreeModuleDeps();

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
        moduleDeps.showSelectedStatus(`Moved ${label}: ${moved.path}`);
    } catch (err) {
        console.error(err);
        moduleDeps.showSelectedStatus((err && err.message) || 'Move failed.', 'error');
    } finally {
        state.dragDropInFlight = false;
        clearTreeDragState();
    }
}

export async function submitTreeInlineRename(entry) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    const rename = state.treeInlineRename;
    if (!rename || state.treeInlineBusy || !entry) {
        return;
    }

    const toPath = buildPathFromInlineRename(entry, rename.value);
    if (!toPath) {
        moduleDeps.showSelectedStatus('Name is required.', 'error');
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
        moduleDeps.showSelectedStatus(`Renamed ${label}: ${moved.path}`);
    } catch (err) {
        console.error(err);
        moduleDeps.showSelectedStatus((err && err.message) || 'Rename failed.', 'error');
    } finally {
        state.treeInlineBusy = false;
        controllerDeps.renderFileTree();
    }
}

export async function submitTreeInlineDelete(entry) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    if (!entry || state.treeInlineBusy) {
        return;
    }

    state.treeInlineBusy = true;
    state.treeInlineDeleteKey = null;

    try {
        if (entry.type === 'folder') {
            await deleteFolderNow(entry.path);
            moduleDeps.showSelectedStatus(`Deleted folder: ${entry.path}`);
        } else if (entry.type === 'image') {
            await deleteImageNow(entry.path);
            moduleDeps.showSelectedStatus(`Deleted image: ${entry.path}`);
        } else {
            await deleteDocNow(entry.path);
            moduleDeps.showSelectedStatus(`Deleted document: ${entry.path}`);
        }

        await moduleDeps.refreshFileTree();
    } catch (err) {
        console.error(err);
        moduleDeps.showSelectedStatus((err && err.message) || 'Delete failed.', 'error');
    } finally {
        state.treeInlineBusy = false;
        controllerDeps.renderFileTree();
    }
}
