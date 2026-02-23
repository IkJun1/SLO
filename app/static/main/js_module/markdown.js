import { remapMovedPath, renameDocWithPath, renameFolderWithPath, renameImageWithPath } from './markdown_actions.js';
import { clearEditor, configureMarkdownEditorModule, estimateDropIndexFromPointer, hasImageFile, insertUploadedImages, loadDoc, loadImagePreview, openImagePicker, refreshEditorFilename, renderMarkdownPreview, scheduleSave } from './markdown_editor.js';
import { configureMarkdownTreeModule, expandAncestors, renderFileTree } from './markdown_tree.js';
import { baseName } from './utils.js';
import { confirmDeleteSelection, hideConfirmPanel, hideQuickAction, openDeleteConfirm, openQuickAction, submitQuickAction } from './markdown/quick_actions.js';
import { configureMarkdownSelectionStatusModule, setSelectedEntry, showSelectedStatus, updateSelectedLabel } from './markdown/selection_status.js';
import { configureMarkdownTreeRefreshModule, refreshFileTree } from './markdown/tree_refresh.js';

configureMarkdownSelectionStatusModule({
    renderFileTree
});

configureMarkdownTreeRefreshModule({
    showSelectedStatus,
    updateSelectedLabel
});

configureMarkdownEditorModule({
    showSelectedStatus,
    expandAncestors,
    setSelectedEntry,
    refreshFileTree
});

configureMarkdownTreeModule({
    baseName,
    clearEditor,
    expandAncestors,
    loadDoc,
    loadImagePreview,
    refreshEditorFilename,
    refreshFileTree,
    renameDocWithPath,
    renameFolderWithPath,
    renameImageWithPath,
    remapMovedPath,
    setSelectedEntry,
    showSelectedStatus
});

export function setupMarkdownView() {
    const createDocBtn = document.getElementById('create-doc-btn');
    if (createDocBtn) {
        createDocBtn.addEventListener('click', () => openQuickAction('create-doc'));
    }

    const createFolderBtn = document.getElementById('create-folder-btn');
    if (createFolderBtn) {
        createFolderBtn.addEventListener('click', () => openQuickAction('create-folder'));
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

    const contentInput = document.getElementById('doc-content');
    const imageFileInput = document.getElementById('image-file-input');

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

export {
    expandAncestors,
    loadDoc,
    refreshFileTree,
    setSelectedEntry,
    showSelectedStatus
};
