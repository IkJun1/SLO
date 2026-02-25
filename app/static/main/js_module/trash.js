import { apiPath, safeJson } from './api.js';
import { state } from './state.js';
import { baseName, escapeHtml } from './utils.js';

let moduleDeps = {
    refreshFileTree: async () => {},
    loadDoc: async () => {},
    switchTab: () => {},
    showSelectedStatus: () => {}
};

export function configureTrashModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function setupTrashView() {
    const refreshBtn = document.getElementById('refresh-trash-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            void loadTrashItems();
        });
    }

    const restoreBtn = document.getElementById('trash-restore-btn');
    if (restoreBtn) {
        restoreBtn.addEventListener('click', () => {
            void restoreSelectedTrashItem();
        });
    }

    const purgeBtn = document.getElementById('trash-purge-btn');
    if (purgeBtn) {
        purgeBtn.addEventListener('click', () => {
            void purgeSelectedTrashItem();
        });
    }
}

export async function loadTrashItems() {
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
        moduleDeps.showSelectedStatus('Failed to load trash.', 'error');
    }
}

function renderTrashList() {
    const list = document.getElementById('trash-list');
    if (!list) {
        return;
    }

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
        li.dataset.trashKey = key;
        li.dataset.entryType = String(trashItem.entry_type || '');
        li.dataset.docId = String(trashItem.doc_id || '');
        li.dataset.trashPath = String(trashItem.trash_path || '');
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

    if (!emptyMessage || !card) {
        return;
    }

    const selected = state.trashItems.find((item) => trashItemKey(item) === state.selectedTrashKey);
    if (!selected) {
        emptyMessage.classList.remove('hidden');
        card.classList.add('hidden');
        return;
    }

    emptyMessage.classList.add('hidden');
    card.classList.remove('hidden');

    const typeEl = document.getElementById('trash-detail-type');
    const originalEl = document.getElementById('trash-detail-original');
    const pathEl = document.getElementById('trash-detail-path');
    const timeEl = document.getElementById('trash-detail-time');

    if (typeEl) {
        typeEl.textContent = selected.entry_type;
    }
    if (originalEl) {
        originalEl.textContent = selected.original_path;
    }
    if (pathEl) {
        pathEl.textContent = selected.trash_path;
    }
    if (timeEl) {
        timeEl.textContent = new Date(selected.deleted_at).toLocaleString();
    }
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
        await moduleDeps.refreshFileTree();
        await loadTrashItems();
        state.graphInitialized = false;

        if (payload.entry_type === 'doc') {
            const restoredDoc = state.docs.find((doc) => doc.path === result.restored_path);
            if (restoredDoc) {
                await moduleDeps.loadDoc(restoredDoc.path);
                moduleDeps.switchTab('markdown');
            }
        }

        moduleDeps.showSelectedStatus(`Restored: ${result.restored_path || 'item'}`);
    } catch (err) {
        console.error(err);
        moduleDeps.showSelectedStatus(err.message || 'Restore failed.', 'error');
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

        await moduleDeps.refreshFileTree();
        await loadTrashItems();
        state.graphInitialized = false;
        moduleDeps.showSelectedStatus('Permanently deleted item.');
    } catch (err) {
        console.error(err);
        moduleDeps.showSelectedStatus(err.message || 'Permanent delete failed.', 'error');
    }
}

function trashItemKey(item) {
    if (item.entry_type === 'doc') {
        return `doc:${item.doc_id}`;
    }
    return `folder:${item.trash_path}`;
}
