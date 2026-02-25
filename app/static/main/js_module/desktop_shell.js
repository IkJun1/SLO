import { createNewChatSession } from './chat_sessions.js';
import { openQuickAction, openDeleteConfirm } from './markdown/quick_actions.js';
import { openImagePicker } from './markdown_editor.js';
import { state } from './state.js';
import { loadTrashItems } from './trash.js';
import { baseName } from './utils.js';

const TAB_LABELS = {
    markdown: 'Markdown',
    graph: 'Graph',
    trash: 'Trash',
    llm: 'Chat'
};

const TAB_GROUP_IDS = {
    markdown: 'desktop-actions-markdown',
    graph: 'desktop-actions-graph',
    trash: 'desktop-actions-trash',
    llm: 'desktop-actions-llm'
};

function getDesktopBridge() {
    const bridge = window.sloDesktop;
    if (!bridge) {
        return null;
    }

    if (typeof bridge.getSettingsState !== 'function') {
        return null;
    }

    return bridge;
}

function setText(id, text) {
    const target = document.getElementById(id);
    if (!target) {
        return;
    }
    target.textContent = text;
}

async function refreshActiveUrl(bridge) {
    try {
        const payload = await bridge.getSettingsState();
        const active = String((payload && payload.settings && payload.settings.active_url) || '').trim();
        setText('desktop-active-url', active === '' ? 'Not connected' : active);
    } catch (_err) {
        setText('desktop-active-url', 'Connection info unavailable');
    }
}

function selectedTrashName() {
    const selectedKey = String(state.selectedTrashKey || '');
    if (selectedKey === '') {
        return '';
    }

    const selected = state.trashItems.find((item) => {
        if (item.entry_type === 'doc') {
            return `doc:${item.doc_id}` === selectedKey;
        }
        return `folder:${item.trash_path}` === selectedKey;
    });

    if (!selected) {
        return '';
    }

    return baseName(selected.original_path || selected.trash_path || '');
}

function selectedChatTitle() {
    const activeId = String(state.activeChatSessionId || '');
    if (activeId === '') {
        return '';
    }

    const active = state.chatSessions.find((item) => String(item.session_id || '') === activeId);
    if (!active) {
        return '';
    }

    return String(active.title || '').trim();
}

function contextLabelForTab(tabId) {
    if (tabId === 'markdown') {
        const selected = state.selectedEntry;
        if (selected && selected.path) {
            return String(selected.path);
        }
        if (state.currentDocPath) {
            return String(state.currentDocPath);
        }
        if (state.currentImagePath) {
            return String(state.currentImagePath);
        }
        return 'No selection';
    }

    if (tabId === 'graph') {
        return 'Use graph actions';
    }

    if (tabId === 'trash') {
        const selected = selectedTrashName();
        return selected === '' ? 'No trash item selected' : selected;
    }

    if (tabId === 'llm') {
        const selected = selectedChatTitle();
        return selected === '' ? 'No chat selected' : selected;
    }

    return 'Ready';
}

function updateActionGroups(tabId) {
    Object.values(TAB_GROUP_IDS).forEach((id) => {
        const group = document.getElementById(id);
        if (!group) {
            return;
        }
        group.classList.remove('active');
    });

    const targetGroupId = TAB_GROUP_IDS[tabId];
    if (!targetGroupId) {
        return;
    }

    const targetGroup = document.getElementById(targetGroupId);
    if (!targetGroup) {
        return;
    }

    targetGroup.classList.add('active');
}

function updateToolbarContext() {
    const tabId = String(state.activeTab || 'markdown');
    const tabLabel = TAB_LABELS[tabId] || 'View';
    setText('desktop-tab-label', tabLabel);
    setText('desktop-context-label', contextLabelForTab(tabId));
    updateActionGroups(tabId);
}

function bindButton(id, handler) {
    const button = document.getElementById(id);
    if (!button) {
        return;
    }

    button.addEventListener('click', () => {
        Promise.resolve(handler())
            .catch((err) => {
                console.error(err);
            })
            .finally(() => {
                queueMicrotask(() => {
                    updateToolbarContext();
                });
            });
    });
}

function bindDesktopBarActions(bridge) {
    bindButton('desktop-open-settings-btn', async () => {
        await bridge.openSettingsPage();
    });

    bindButton('desktop-reload-btn', async () => {
        window.location.reload();
    });

    bindButton('desktop-md-new-doc-btn', async () => {
        openQuickAction('create-doc');
    });

    bindButton('desktop-md-new-folder-btn', async () => {
        openQuickAction('create-folder');
    });

    bindButton('desktop-md-insert-image-btn', async () => {
        await openImagePicker();
    });

    bindButton('desktop-md-delete-btn', async () => {
        openDeleteConfirm('selected');
    });

    bindButton('desktop-graph-refresh-btn', async () => {
        const target = document.getElementById('refresh-graph');
        if (target) {
            target.click();
        }
    });

    bindButton('desktop-graph-sync-btn', async () => {
        const target = document.getElementById('run-embedding-sync');
        if (target) {
            target.click();
        }
    });

    bindButton('desktop-trash-refresh-btn', async () => {
        await loadTrashItems();
    });

    bindButton('desktop-trash-restore-btn', async () => {
        const target = document.getElementById('trash-restore-btn');
        if (target) {
            target.click();
        }
    });

    bindButton('desktop-trash-purge-btn', async () => {
        const target = document.getElementById('trash-purge-btn');
        if (target) {
            target.click();
        }
    });

    bindButton('desktop-chat-new-btn', async () => {
        await createNewChatSession();
    });
}

function bindContextRefresh(bridge) {
    window.addEventListener('slo:tab-changed', () => {
        updateToolbarContext();
    });

    window.addEventListener('slo:selection-changed', () => {
        updateToolbarContext();
    });

    window.addEventListener('focus', () => {
        void refreshActiveUrl(bridge);
        updateToolbarContext();
    });

    document.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) {
            return;
        }

        if (
            target.closest('#file-tree')
            || target.closest('#trash-list')
            || target.closest('#chat-session-list')
            || target.closest('#sidebar')
        ) {
            queueMicrotask(() => {
                updateToolbarContext();
            });
        }
    });
}

export async function setupDesktopShell() {
    const bridge = getDesktopBridge();
    if (!bridge) {
        return;
    }

    const topbar = document.getElementById('desktop-topbar');
    if (!topbar) {
        return;
    }

    document.body.classList.add('desktop-shell');
    topbar.classList.remove('hidden');
    bindDesktopBarActions(bridge);
    bindContextRefresh(bridge);
    updateToolbarContext();
    await refreshActiveUrl(bridge);
}
