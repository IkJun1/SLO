import { openQuickAction, openDeleteConfirm } from './markdown/quick_actions.js';
import { openImagePicker } from './markdown_editor.js';
import { refreshFileTree, setSelectedEntry, showSelectedStatus } from './markdown.js';
import { state } from './state.js';
import { loadTrashItems } from './trash.js';
import { baseName } from './utils.js';

function getDesktopBridge() {
    const bridge = window.sloDesktop;
    if (!bridge) {
        return null;
    }

    if (typeof bridge.showContextMenu !== 'function') {
        return null;
    }

    if (typeof bridge.onContextMenuAction !== 'function') {
        return null;
    }

    return bridge;
}

function treeEntryFromElement(item) {
    const type = String(item.dataset.entryType || '').trim();
    const path = String(item.dataset.entryPath || '').trim();
    if (!type || !path) {
        return null;
    }

    const entry = {
        type,
        path,
        name: baseName(path)
    };

    if (type === 'doc') {
        const idFromDataset = String(item.dataset.entryId || '').trim();
        if (idFromDataset) {
            entry.id = idFromDataset;
        } else {
            const found = state.docs.find((doc) => String(doc.path || '') === path);
            if (found && found.id) {
                entry.id = String(found.id);
            }
        }
    }

    return entry;
}

function contextFromTarget(target) {
    const treeContainer = target.closest('#file-tree');
    if (treeContainer) {
        const treeItem = target.closest('.tree-item[data-entry-type][data-entry-path]');
        if (treeItem) {
            const entry = treeEntryFromElement(treeItem);
            if (!entry) {
                return null;
            }

            setSelectedEntry(entry);
            return {
                type: 'tree-entry',
                entryType: entry.type,
                path: entry.path
            };
        }

        return {
            type: 'tree-root'
        };
    }

    const trashList = target.closest('#trash-list');
    if (trashList) {
        const trashItem = target.closest('.trash-item[data-trash-key]');
        if (trashItem) {
            trashItem.click();
            return {
                type: 'trash-entry',
                trashKey: String(trashItem.dataset.trashKey || '')
            };
        }

        return {
            type: 'trash-root'
        };
    }

    if (target.closest('#doc-content')) {
        return {
            type: 'editor',
            canInsertImage: Boolean(state.currentDocPath)
        };
    }

    return null;
}

async function copyToClipboard(text) {
    const value = String(text || '').trim();
    if (!value) {
        return false;
    }

    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        try {
            await navigator.clipboard.writeText(value);
            return true;
        } catch (_err) {
        }
    }

    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();

    let copied = false;
    try {
        copied = document.execCommand('copy');
    } catch (_err) {
        copied = false;
    }

    textarea.remove();
    return copied;
}

function entryFromPayload(payload) {
    const raw = payload && typeof payload === 'object' ? payload : {};
    const type = String(raw.entryType || '').trim();
    const path = String(raw.path || '').trim();
    if (!type || !path) {
        return null;
    }

    const entry = {
        type,
        path,
        name: baseName(path)
    };

    if (type === 'doc') {
        const found = state.docs.find((doc) => String(doc.path || '') === path);
        if (found && found.id) {
            entry.id = String(found.id);
        }
    }

    return entry;
}

function renameModeForEntry(entry) {
    if (!entry) {
        return null;
    }

    if (entry.type === 'doc') {
        return 'rename-doc';
    }
    if (entry.type === 'folder') {
        return 'rename-folder';
    }
    if (entry.type === 'image') {
        return 'rename-image';
    }
    return null;
}

function selectedEntryFallback() {
    const selected = state.selectedEntry;
    if (selected && selected.type && selected.path) {
        return selected;
    }
    return null;
}

function selectTrashByKey(trashKey) {
    const value = String(trashKey || '').trim();
    if (!value) {
        return;
    }

    const items = document.querySelectorAll('#trash-list .trash-item[data-trash-key]');
    for (const item of items) {
        if (!(item instanceof HTMLElement)) {
            continue;
        }
        if (String(item.dataset.trashKey || '') !== value) {
            continue;
        }
        item.click();
        return;
    }
}

async function handleContextMenuAction(message) {
    const action = String((message && message.action) || '').trim();
    const payload = message && typeof message.payload === 'object' ? message.payload : {};
    if (!action) {
        return;
    }

    if (action === 'new_doc') {
        openQuickAction('create-doc');
        return;
    }

    if (action === 'new_folder') {
        openQuickAction('create-folder');
        return;
    }

    if (action === 'rename') {
        const entry = entryFromPayload(payload) || selectedEntryFallback();
        const mode = renameModeForEntry(entry);
        if (!entry || !mode) {
            showSelectedStatus('Select an item to rename.', 'error');
            return;
        }

        setSelectedEntry(entry);
        openQuickAction(mode);
        return;
    }

    if (action === 'move_to_trash') {
        const entry = entryFromPayload(payload) || selectedEntryFallback();
        if (!entry) {
            showSelectedStatus('Select an item to delete.', 'error');
            return;
        }

        setSelectedEntry(entry);
        openDeleteConfirm('selected');
        return;
    }

    if (action === 'copy_path') {
        const path = String(payload.path || '').trim();
        const copied = await copyToClipboard(path);
        if (copied) {
            showSelectedStatus(`Copied path: ${path}`);
        } else {
            showSelectedStatus('Failed to copy path.', 'error');
        }
        return;
    }

    if (action === 'refresh_tree') {
        await refreshFileTree();
        return;
    }

    if (action === 'refresh_trash') {
        await loadTrashItems();
        return;
    }

    if (action === 'restore_trash') {
        selectTrashByKey(payload.trashKey);
        const restoreButton = document.getElementById('trash-restore-btn');
        if (restoreButton) {
            restoreButton.click();
        }
        return;
    }

    if (action === 'purge_trash') {
        selectTrashByKey(payload.trashKey);
        const purgeButton = document.getElementById('trash-purge-btn');
        if (purgeButton) {
            purgeButton.click();
        }
        return;
    }

    if (action === 'insert_image') {
        await openImagePicker();
    }
}

function bindContextMenuOpen(bridge) {
    document.addEventListener('contextmenu', (event) => {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) {
            return;
        }

        const context = contextFromTarget(target);
        if (!context) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        void bridge.showContextMenu(context);
    });
}

export function setupDesktopContextMenu() {
    const bridge = getDesktopBridge();
    if (!bridge) {
        return;
    }

    bindContextMenuOpen(bridge);
    const dispose = bridge.onContextMenuAction((message) => {
        void handleContextMenuAction(message);
    });

    window.addEventListener('beforeunload', () => {
        dispose();
    }, { once: true });
}
