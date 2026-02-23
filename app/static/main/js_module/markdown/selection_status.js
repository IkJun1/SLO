import { state } from '../state.js';

let moduleDeps = {
    renderFileTree: () => {}
};

export function configureMarkdownSelectionStatusModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function setSelectedEntry(entry) {
    state.selectedEntry = entry;
    moduleDeps.renderFileTree();
    updateSelectedLabel();
}

export function updateSelectedLabel() {
    const label = document.getElementById('explorer-selected');
    if (!label) {
        return;
    }

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

export function showSelectedStatus(message, tone = 'info') {
    const label = document.getElementById('explorer-selected');
    if (!label) {
        return;
    }

    if (state.selectedLabelTimer) {
        clearTimeout(state.selectedLabelTimer);
    }

    label.textContent = message;
    label.style.color = tone === 'error' ? '#ff9d9d' : 'var(--accent-hover)';

    state.selectedLabelTimer = setTimeout(() => {
        updateSelectedLabel();
    }, 2400);
}
