import { apiPath } from '../api.js';
import { clearEditor, isImagePath } from '../markdown_editor.js';
import { state } from '../state.js';
import { parseVaultTree, pruneCollapsedFolders, refreshTreeSelectionState } from './tree_model.js';
import { renderFileTree } from './tree_view.js';

let moduleDeps = {
    showSelectedStatus: () => {},
    updateSelectedLabel: () => {}
};

export function configureMarkdownTreeRefreshModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export async function refreshFileTree() {
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

        refreshTreeSelectionState();
        renderFileTree();
        moduleDeps.updateSelectedLabel();
    } catch (err) {
        console.error('Failed to load file tree', err);
        moduleDeps.showSelectedStatus('Failed to load file tree.', 'error');
    }
}
