import { state } from '../state.js';
import { getMarkdownTreeModuleDeps } from './tree_context.js';
import { buildExplorerEntries, hasCollapsedAncestor, treeEntryKey } from './tree_model.js';
import {
    canDropEntryIntoFolder,
    cancelTreeInlineDelete,
    cancelTreeInlineRename,
    clearTreeDragState,
    clearTreeExpandTimer,
    moveEntryToFolder,
    scheduleTreeExpandOnHover,
    setTreeDropTarget,
    setTreeRootDropTarget,
    startTreeInlineDelete,
    startTreeInlineRename,
    submitTreeInlineDelete,
    submitTreeInlineRename
} from './tree_controller.js';

export function renderFileTree() {
    const container = document.getElementById('file-tree');
    if (!container) {
        return;
    }

    const moduleDeps = getMarkdownTreeModuleDeps();

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
        item.dataset.entryType = entry.type;
        item.dataset.entryPath = entry.path;
        if (entry.id) {
            item.dataset.entryId = String(entry.id);
        }
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
        const iconSizePx = entry.type === 'image' ? 28 : 14;
        icon.style.width = `${iconSizePx}px`;
        icon.style.height = `${iconSizePx}px`;
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
                void moduleDeps.loadDoc(entry.path);
            });
        } else if (entry.type === 'image') {
            item.addEventListener('click', () => {
                if (hasInlineState) {
                    return;
                }
                void moduleDeps.loadImagePreview(entry.path);
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
                moduleDeps.setSelectedEntry({ type: 'folder', path: entry.path, name: entry.name });
            });
        }

        container.appendChild(item);
    });

    lucide.createIcons();
}
