import { apiPath } from './api.js';
import {
    buildSourceBadgesHtmlFromPaths,
    configureChatSourcesModule,
    goToChatSourceDoc,
    setupChatSourceGlobalActions,
    sourcePathsFromHits
} from './chat_sources.js';
import {
    configureChatSessionsModule,
    createNewChatSession,
    initializeChatSessions,
    loadChatSessionMessages,
    loadChatSessions
} from './chat_sessions.js';
import { state } from './state.js';
import { escapeHtml } from './utils.js';

let moduleDeps = {
    switchTab: () => {},
    loadDoc: async () => {},
    showSelectedStatus: () => {}
};

export function configureChatModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
    configureChatSourcesModule({
        switchTab: moduleDeps.switchTab,
        loadDoc: moduleDeps.loadDoc
    });
    configureChatSessionsModule({
        showSelectedStatus: moduleDeps.showSelectedStatus,
        renderChatWelcome
    });
}

function renderChatWelcome() {
    const history = document.getElementById('chat-history');
    history.innerHTML = `
        <div class="message system">
            <div class="message-content">Hello. I am your knowledge assistant. Ask me anything about your vault.</div>
        </div>
    `;
}

export function setupLLMView() {
    const btn = document.getElementById('send-chat-btn');
    const input = document.getElementById('chat-input');
    const newChatBtn = document.getElementById('new-chat-btn');
    const gotoBtn = document.getElementById('chat-source-goto-btn');
    let chatInputComposing = false;

    input.addEventListener('compositionstart', () => {
        chatInputComposing = true;
    });
    input.addEventListener('compositionend', () => {
        chatInputComposing = false;
    });

    const send = () => {
        const text = input.value.trim();
        if (!text) {
            return;
        }

        void handleChat(text);
        input.value = '';
    };

    btn.addEventListener('click', send);
    input.addEventListener('keydown', (event) => {
        if (event.isComposing || chatInputComposing || event.keyCode === 229) {
            return;
        }

        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            send();
        }
    });

    newChatBtn.addEventListener('click', () => {
        void createNewChatSession();
    });

    gotoBtn.addEventListener('click', () => {
        void goToChatSourceDoc();
    });

    setupChatSourceGlobalActions();
    void initializeChatSessions();
}

async function handleChat(query) {
    const history = document.getElementById('chat-history');

    const userMsg = document.createElement('div');
    userMsg.className = 'message user';
    userMsg.innerHTML = `<div class="message-content">${escapeHtml(query)}</div>`;
    history.appendChild(userMsg);

    const sysMsg = document.createElement('div');
    sysMsg.className = 'message system';
    sysMsg.innerHTML = '<div class="message-content">Thinking...</div>';
    history.appendChild(sysMsg);

    history.scrollTop = history.scrollHeight;

    try {
        const payload = { query, mode: 'vector' };
        if (state.activeChatSessionId) {
            payload.session_id = state.activeChatSessionId;
        }

        const res = await fetch(apiPath('/llm/answer'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            throw new Error('Failed to generate answer.');
        }

        const data = await res.json();
        if (data.session_id) {
            state.activeChatSessionId = data.session_id;
        }

        let html = marked.parse(data.answer || '');
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html);
        }

        const sourcePaths = sourcePathsFromHits(data.sources);
        const sourcesHtml = buildSourceBadgesHtmlFromPaths(sourcePaths);

        sysMsg.innerHTML = `<div class="message-content">${html}${sourcesHtml}</div>`;
        await loadChatSessions(state.activeChatSessionId);
        if (state.activeChatSessionId) {
            await loadChatSessionMessages(state.activeChatSessionId);
        }
    } catch (err) {
        sysMsg.innerHTML = '<div class="message-content error">Error generating response.</div>';
    }

    history.scrollTop = history.scrollHeight;
}
