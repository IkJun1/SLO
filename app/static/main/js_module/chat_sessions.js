import { apiPath, safeJson } from './api.js';
import {
    buildSourceBadgesHtmlFromPaths,
    clearChatSourcePreview,
    extractDocPathsFromText,
    normalizeSourcePathList
} from './chat_sources.js';
import { renderMathIfAvailable } from './math_renderer.js';
import { state } from './state.js';
import { escapeHtml } from './utils.js';

let moduleDeps = {
    showSelectedStatus: () => {},
    renderChatWelcome: () => {}
};

export function configureChatSessionsModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

function formatChatSessionTime(value) {
    if (!value) {
        return '';
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return '';
    }
    return date.toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function startSessionRename(session) {
    state.chatEditingSessionId = String((session && session.session_id) || '').trim() || null;
    state.chatEditingSessionTitle = String((session && session.title) || 'Chat');
    state.chatDeletingSessionId = null;
    renderChatSessionList();
}

function cancelSessionRename() {
    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    renderChatSessionList();
}

async function saveSessionRename(sessionId) {
    const targetId = String(sessionId || '').trim();
    if (!targetId) {
        return;
    }

    const trimmed = String(state.chatEditingSessionTitle || '').trim();
    if (!trimmed) {
        moduleDeps.showSelectedStatus('Title cannot be empty.', 'error');
        return;
    }

    const res = await fetch(apiPath(`/chat/sessions/${encodeURIComponent(targetId)}`), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed })
    });
    if (!res.ok) {
        throw new Error('Failed to rename chat session.');
    }

    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    await loadChatSessions(targetId);
}

function requestDeleteSession(sessionId) {
    const targetId = String(sessionId || '').trim();
    if (!targetId) {
        return;
    }
    state.chatDeletingSessionId = targetId;
    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    renderChatSessionList();
}

function cancelDeleteSession() {
    state.chatDeletingSessionId = null;
    renderChatSessionList();
}

async function confirmDeleteSession(sessionId) {
    const targetId = String(sessionId || '').trim();
    if (!targetId) {
        return;
    }

    const res = await fetch(apiPath(`/chat/sessions/${encodeURIComponent(targetId)}`), {
        method: 'DELETE'
    });
    if (!res.ok) {
        throw new Error('Failed to delete chat session.');
    }

    state.chatDeletingSessionId = null;

    const wasActive = state.activeChatSessionId === targetId;
    await loadChatSessions();

    if (!wasActive) {
        return;
    }

    if (state.chatSessions.length > 0) {
        await loadChatSessionMessages(state.chatSessions[0].session_id);
    } else {
        state.activeChatSessionId = null;
        state.chatMessages = [];
        moduleDeps.renderChatWelcome();
        clearChatSourcePreview();
    }
}

function startEditUserMessage(message) {
    const role = String((message && message.role) || '').toLowerCase();
    if (role !== 'user') {
        return;
    }

    const messageId = String((message && message.message_id) || '').trim();
    if (!messageId) {
        return;
    }

    state.chatEditingMessageId = messageId;
    state.chatEditingMessageText = String((message && message.content) || '');
    state.chatDeletingMessageId = null;
    renderChatHistoryFromState();
}

function cancelEditUserMessage() {
    state.chatEditingMessageId = null;
    state.chatEditingMessageText = '';
    renderChatHistoryFromState();
}

function findPairedAssistantMessageIndex(messages, userIndex) {
    if (!Array.isArray(messages) || userIndex < 0) {
        return -1;
    }

    for (let idx = userIndex + 1; idx < messages.length; idx += 1) {
        const role = String((messages[idx] && messages[idx].role) || '').toLowerCase();
        if (role === 'assistant') {
            return idx;
        }
        if (role === 'user') {
            break;
        }
    }

    return -1;
}

async function saveEditedUserMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
        return;
    }

    const content = String(state.chatEditingMessageText || '').trim();
    if (!content) {
        moduleDeps.showSelectedStatus('Message cannot be empty.', 'error');
        return;
    }

    const messages = Array.isArray(state.chatMessages) ? state.chatMessages : [];
    const targetIndex = messages.findIndex((item) => String((item && item.message_id) || '').trim() === targetId);
    const localMessage = targetIndex >= 0 && messages[targetIndex] && typeof messages[targetIndex] === 'object'
        ? messages[targetIndex]
        : null;
    const previousUserContent = localMessage ? String(localMessage.content || '') : null;

    const pairedAssistantIndex = findPairedAssistantMessageIndex(messages, targetIndex);
    const pairedAssistant = pairedAssistantIndex >= 0
        ? messages[pairedAssistantIndex]
        : null;
    const previousAssistantSnapshot = pairedAssistant && typeof pairedAssistant === 'object'
        ? {
            content: String(pairedAssistant.content || ''),
            sourceDocPaths: Array.isArray(pairedAssistant.source_doc_paths)
                ? [...pairedAssistant.source_doc_paths]
                : []
        }
        : null;

    let insertedPlaceholderId = '';
    if (localMessage) {
        localMessage.content = content;
    }

    if (pairedAssistant && typeof pairedAssistant === 'object') {
        pairedAssistant.content = 'Thinking...';
        pairedAssistant.source_doc_paths = [];
    } else if (targetIndex >= 0) {
        insertedPlaceholderId = `pending-assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        messages.splice(targetIndex + 1, 0, {
            message_id: insertedPlaceholderId,
            session_id: localMessage && typeof localMessage.session_id === 'string'
                ? localMessage.session_id
                : (state.activeChatSessionId || ''),
            role: 'assistant',
            content: 'Thinking...',
            source_doc_paths: [],
            created_at: new Date().toISOString()
        });
    }

    state.chatEditingMessageId = null;
    state.chatEditingMessageText = '';
    renderChatHistoryFromState();
    moduleDeps.showSelectedStatus('Regenerating answer...');

    let updateSucceeded = false;
    try {
        const res = await fetch(apiPath(`/chat/messages/${encodeURIComponent(targetId)}`), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        const body = await safeJson(res);
        if (!res.ok) {
            throw new Error((body && body.error && body.error.message) || 'Failed to update message.');
        }
        updateSucceeded = true;

        const sessionId = body && typeof body.session_id === 'string'
            ? body.session_id.trim()
            : '';

        if (sessionId !== '') {
            state.activeChatSessionId = sessionId;
        }

        await loadChatSessions(state.activeChatSessionId);
        if (state.activeChatSessionId) {
            await loadChatSessionMessages(state.activeChatSessionId);
        }

        moduleDeps.showSelectedStatus('Message updated.');
    } catch (err) {
        console.error(err);

        if (!updateSucceeded) {
            if (localMessage && previousUserContent !== null) {
                localMessage.content = previousUserContent;
            }

            if (pairedAssistant && previousAssistantSnapshot) {
                pairedAssistant.content = previousAssistantSnapshot.content;
                pairedAssistant.source_doc_paths = [...previousAssistantSnapshot.sourceDocPaths];
            }

            if (insertedPlaceholderId !== '') {
                const placeholderIndex = messages.findIndex(
                    (item) => String((item && item.message_id) || '').trim() === insertedPlaceholderId
                );
                if (placeholderIndex >= 0) {
                    messages.splice(placeholderIndex, 1);
                }
            }

            state.chatEditingMessageId = targetId;
            state.chatEditingMessageText = content;
            renderChatHistoryFromState();
        }

        moduleDeps.showSelectedStatus((err && err.message) || 'Failed to update message.', 'error');
    }
}

function requestDeleteUserMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
        return;
    }
    state.chatDeletingMessageId = targetId;
    state.chatEditingMessageId = null;
    state.chatEditingMessageText = '';
    renderChatHistoryFromState();
}

function cancelDeleteUserMessage() {
    state.chatDeletingMessageId = null;
    renderChatHistoryFromState();
}

async function confirmDeleteUserMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
        return;
    }

    const res = await fetch(apiPath(`/chat/messages/${encodeURIComponent(targetId)}`), {
        method: 'DELETE'
    });
    if (!res.ok) {
        throw new Error('Failed to delete message.');
    }

    state.chatDeletingMessageId = null;

    await loadChatSessions(state.activeChatSessionId);
    if (state.activeChatSessionId) {
        await loadChatSessionMessages(state.activeChatSessionId);
    }
}

function appendChatMessage(historyEl, message) {
    const wrapper = document.createElement('div');
    const rawRole = String((message && message.role) || '').toLowerCase();
    const role = rawRole === 'user' ? 'user' : 'assistant';
    wrapper.className = `message ${role === 'assistant' ? 'system' : 'user'}`;

    const messageId = String((message && message.message_id) || '').trim();

    if (role === 'assistant') {
        const content = document.createElement('div');
        content.className = 'message-content';
        let html = marked.parse(String(message.content || ''));
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }
        const sourcePaths = normalizeSourcePathList(message.source_doc_paths);
        const fallbackPaths = sourcePaths.length > 0 ? sourcePaths : extractDocPathsFromText(message.content || '');
        const sourcesHtml = buildSourceBadgesHtmlFromPaths(fallbackPaths);
        content.innerHTML = `${html}${sourcesHtml}`;
        renderMathIfAvailable(content);
        wrapper.appendChild(content);
        historyEl.appendChild(wrapper);
        return;
    }

    if (state.chatEditingMessageId === messageId) {
        const editor = document.createElement('div');
        editor.className = 'message-edit-area';

        const textarea = document.createElement('textarea');
        textarea.className = 'message-edit-input';
        textarea.value = state.chatEditingMessageText || String(message.content || '');
        textarea.addEventListener('input', () => {
            state.chatEditingMessageText = textarea.value;
        });

        const actions = document.createElement('div');
        actions.className = 'message-edit-actions';

        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'message-tool-btn';
        saveBtn.textContent = 'Save';
        saveBtn.addEventListener('click', () => {
            void saveEditedUserMessage(messageId);
        });

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'message-tool-btn';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => {
            cancelEditUserMessage();
        });

        actions.appendChild(saveBtn);
        actions.appendChild(cancelBtn);
        editor.appendChild(textarea);
        editor.appendChild(actions);
        wrapper.appendChild(editor);
        historyEl.appendChild(wrapper);
        return;
    }

    const content = document.createElement('div');
    content.className = 'message-content';
    content.innerHTML = escapeHtml(String(message.content || ''));
    wrapper.appendChild(content);

    if (role === 'user' && messageId) {
        const tools = document.createElement('div');
        tools.className = 'message-tools';

        if (state.chatDeletingMessageId === messageId) {
            const confirmBtn = document.createElement('button');
            confirmBtn.type = 'button';
            confirmBtn.className = 'message-tool-btn danger';
            confirmBtn.textContent = 'Confirm Delete';
            confirmBtn.addEventListener('click', () => {
                void confirmDeleteUserMessage(messageId);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'message-tool-btn';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.addEventListener('click', () => {
                cancelDeleteUserMessage();
            });

            tools.appendChild(confirmBtn);
            tools.appendChild(cancelBtn);
        } else {
            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'message-tool-btn';
            editBtn.textContent = 'Edit';
            editBtn.addEventListener('click', () => {
                startEditUserMessage(message);
            });

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'message-tool-btn danger';
            deleteBtn.textContent = 'Delete';
            deleteBtn.addEventListener('click', () => {
                requestDeleteUserMessage(messageId);
            });

            tools.appendChild(editBtn);
            tools.appendChild(deleteBtn);
        }

        wrapper.appendChild(tools);
    }

    historyEl.appendChild(wrapper);
}

function renderChatHistoryFromState() {
    const history = document.getElementById('chat-history');
    if (!history) {
        return;
    }

    const messages = Array.isArray(state.chatMessages) ? state.chatMessages : [];
    history.innerHTML = '';
    if (messages.length === 0) {
        moduleDeps.renderChatWelcome();
        return;
    }

    messages.forEach((message) => {
        appendChatMessage(history, message);
    });

    history.scrollTop = history.scrollHeight;
}

function renderChatSessionList() {
    const list = document.getElementById('chat-session-list');
    list.innerHTML = '';

    if (!Array.isArray(state.chatSessions) || state.chatSessions.length === 0) {
        list.innerHTML = '<div class="chat-session-empty">No chat sessions yet.</div>';
        return;
    }

    state.chatSessions.forEach((session) => {
        const sessionId = String(session.session_id || '').trim();
        const item = document.createElement('div');
        item.className = 'chat-session-item';
        if (state.activeChatSessionId === sessionId) {
            item.classList.add('active');
        }

        if (state.chatEditingSessionId === sessionId) {
            const editWrap = document.createElement('div');
            editWrap.className = 'chat-session-edit-wrap';

            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'chat-session-edit-input';
            input.value = state.chatEditingSessionTitle || String(session.title || 'Chat');
            input.addEventListener('input', () => {
                state.chatEditingSessionTitle = input.value;
            });

            const actions = document.createElement('div');
            actions.className = 'chat-session-actions';

            const saveBtn = document.createElement('button');
            saveBtn.type = 'button';
            saveBtn.className = 'chat-session-action-btn';
            saveBtn.textContent = 'Save';
            saveBtn.addEventListener('click', () => {
                void saveSessionRename(sessionId);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'chat-session-action-btn';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.addEventListener('click', () => {
                cancelSessionRename();
            });

            actions.appendChild(saveBtn);
            actions.appendChild(cancelBtn);
            editWrap.appendChild(input);
            editWrap.appendChild(actions);
            item.appendChild(editWrap);
        } else {
            item.innerHTML = `
                <div class="chat-session-row">
                    <div class="chat-session-name">${escapeHtml(session.title || 'Chat')}</div>
                    <div class="chat-session-actions"></div>
                </div>
                <div class="chat-session-time">${escapeHtml(formatChatSessionTime(session.updated_at))}</div>
            `;

            item.addEventListener('click', () => {
                void loadChatSessionMessages(sessionId);
            });

            const actionsWrap = item.querySelector('.chat-session-actions');
            if (actionsWrap) {
                if (state.chatDeletingSessionId === sessionId) {
                    const confirmBtn = document.createElement('button');
                    confirmBtn.type = 'button';
                    confirmBtn.className = 'chat-session-action-btn danger';
                    confirmBtn.textContent = 'Confirm';
                    confirmBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        void confirmDeleteSession(sessionId);
                    });

                    const cancelBtn = document.createElement('button');
                    cancelBtn.type = 'button';
                    cancelBtn.className = 'chat-session-action-btn';
                    cancelBtn.textContent = 'Cancel';
                    cancelBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        cancelDeleteSession();
                    });

                    actionsWrap.appendChild(confirmBtn);
                    actionsWrap.appendChild(cancelBtn);
                } else {
                    const renameBtn = document.createElement('button');
                    renameBtn.type = 'button';
                    renameBtn.className = 'chat-session-action-btn';
                    renameBtn.textContent = 'Rename';
                    renameBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        startSessionRename(session);
                    });

                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.className = 'chat-session-action-btn danger';
                    deleteBtn.textContent = 'Delete';
                    deleteBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        requestDeleteSession(sessionId);
                    });

                    actionsWrap.appendChild(renameBtn);
                    actionsWrap.appendChild(deleteBtn);
                }
            }
        }

        list.appendChild(item);
    });
}

export async function loadChatSessions(preferredSessionId = null) {
    const res = await fetch(apiPath('/chat/sessions'));
    if (!res.ok) {
        throw new Error('Failed to load chat sessions.');
    }

    const data = await res.json();
    const sessions = Array.isArray(data.sessions) ? data.sessions : [];
    state.chatSessions = sessions;

    if (preferredSessionId) {
        state.activeChatSessionId = preferredSessionId;
    } else if (state.activeChatSessionId && !sessions.some((item) => item.session_id === state.activeChatSessionId)) {
        state.activeChatSessionId = null;
    }

    if (state.chatEditingSessionId && !sessions.some((item) => item.session_id === state.chatEditingSessionId)) {
        state.chatEditingSessionId = null;
        state.chatEditingSessionTitle = '';
    }

    if (state.chatDeletingSessionId && !sessions.some((item) => item.session_id === state.chatDeletingSessionId)) {
        state.chatDeletingSessionId = null;
    }

    renderChatSessionList();
}

export async function createNewChatSession() {
    const res = await fetch(apiPath('/chat/sessions'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
    });
    if (!res.ok) {
        throw new Error('Failed to create chat session.');
    }

    const session = await res.json();
    state.activeChatSessionId = session.session_id;
    state.chatMessages = [];
    state.chatEditingSessionId = null;
    state.chatEditingSessionTitle = '';
    state.chatDeletingSessionId = null;
    await loadChatSessions(session.session_id);
    moduleDeps.renderChatWelcome();
    clearChatSourcePreview();
}

export async function loadChatSessionMessages(sessionId) {
    const target = String(sessionId || '').trim();
    if (!target) {
        return;
    }

    const res = await fetch(apiPath(`/chat/sessions/${encodeURIComponent(target)}/messages`));
    if (!res.ok) {
        throw new Error('Failed to load chat messages.');
    }

    const data = await res.json();
    const session = data.session || null;
    const messages = Array.isArray(data.messages) ? data.messages : [];
    const previousSessionId = state.activeChatSessionId;

    state.activeChatSessionId = session && session.session_id ? session.session_id : target;
    state.chatMessages = messages;
    if (previousSessionId !== state.activeChatSessionId) {
        state.chatEditingMessageId = null;
        state.chatEditingMessageText = '';
        state.chatDeletingMessageId = null;
    }
    renderChatSessionList();
    clearChatSourcePreview();
    renderChatHistoryFromState();
}

export async function initializeChatSessions() {
    try {
        await loadChatSessions();
        if (state.activeChatSessionId) {
            await loadChatSessionMessages(state.activeChatSessionId);
            return;
        }

        if (state.chatSessions.length > 0) {
            await loadChatSessionMessages(state.chatSessions[0].session_id);
            return;
        }

        state.chatMessages = [];
        moduleDeps.renderChatWelcome();
    } catch (err) {
        console.error(err);
        state.chatMessages = [];
        moduleDeps.renderChatWelcome();
    }
}
