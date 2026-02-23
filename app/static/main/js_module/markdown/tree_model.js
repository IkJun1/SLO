import { state } from '../state.js';
import { parentPath } from '../utils.js';
import { getMarkdownTreeModuleDeps } from './tree_context.js';

export function buildExplorerEntries(docs, imagePaths) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    const folderEntries = state.folders.map((path) => ({
        type: 'folder',
        path,
        name: moduleDeps.baseName(path),
        depth: path.split('/').length - 1
    }));

    const docEntries = docs.map((doc) => ({
        type: 'doc',
        id: doc.id,
        path: doc.path,
        name: moduleDeps.baseName(doc.path),
        depth: doc.path.split('/').length - 1
    }));

    const imageEntries = imagePaths.map((path) => ({
        type: 'image',
        path,
        name: moduleDeps.baseName(path),
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

export function parseVaultTree(treeText) {
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

export function treeEntryKey(entry) {
    if (!entry || !entry.type || !entry.path) {
        return '';
    }
    return `${entry.type}:${entry.path}`;
}

export function treeEntryExists(type, path) {
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

export function treeEntryExistsByKey(key) {
    const raw = String(key || '');
    const sep = raw.indexOf(':');
    if (sep <= 0) {
        return false;
    }

    const type = raw.slice(0, sep);
    const path = raw.slice(sep + 1);
    return treeEntryExists(type, path);
}

export function inlineRenameDefaultValue(entry) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    if (!entry || !entry.path) {
        return '';
    }

    const leaf = moduleDeps.baseName(entry.path);
    if (entry.type === 'doc') {
        return leaf.replace(/\.md$/i, '');
    }
    return leaf;
}

export function buildPathFromInlineRename(entry, rawLabel) {
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

export function hasCollapsedAncestor(path) {
    let current = parentPath(path);
    while (current) {
        if (state.collapsedFolders.has(current)) {
            return true;
        }
        current = parentPath(current);
    }
    return false;
}

export function expandAncestors(path) {
    let current = parentPath(path);
    while (current) {
        state.collapsedFolders.delete(current);
        current = parentPath(current);
    }
}

export function pruneCollapsedFolders() {
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

export function refreshTreeSelectionState() {
    const moduleDeps = getMarkdownTreeModuleDeps();

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
                    name: moduleDeps.baseName(selectedDoc.path)
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
}
