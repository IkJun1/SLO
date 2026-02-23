export function attachHorizontalSplitter(options) {
    const {
        splitter,
        getStartSize,
        getBounds,
        setSize,
        getNextSize
    } = options;

    let activePointerId = null;
    let startX = 0;
    let startSize = 0;

    const stopDragging = (event) => {
        if (activePointerId === null) {
            return;
        }
        if (event && event.pointerId !== undefined && event.pointerId !== activePointerId) {
            return;
        }

        if (splitter && splitter.releasePointerCapture) {
            try {
                splitter.releasePointerCapture(activePointerId);
            } catch (_err) {
            }
        }

        activePointerId = null;
        document.body.classList.remove('resizing-panels');
        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', stopDragging);
        window.removeEventListener('pointercancel', stopDragging);
    };

    const onPointerMove = (event) => {
        if (activePointerId === null || event.pointerId !== activePointerId) {
            return;
        }

        const delta = event.clientX - startX;
        const nextRaw = typeof getNextSize === 'function' ? getNextSize(startSize, delta) : startSize + delta;
        const bounds = getBounds();
        const next = Math.min(bounds.max, Math.max(bounds.min, nextRaw));
        setSize(next);
    };

    splitter.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) {
            return;
        }
        if (window.matchMedia('(max-width: 1180px)').matches) {
            return;
        }

        activePointerId = event.pointerId;
        startX = event.clientX;
        startSize = getStartSize();
        document.body.classList.add('resizing-panels');

        if (splitter.setPointerCapture) {
            splitter.setPointerCapture(activePointerId);
        }

        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', stopDragging);
        window.addEventListener('pointercancel', stopDragging);
        event.preventDefault();
    });
}

export function setupPanelSplitters() {
    const editorBody = document.querySelector('.editor-body');
    const editorColumn = document.querySelector('.editor-column');
    const editorSplitter = document.getElementById('editor-splitter');

    if (editorBody && editorColumn && editorSplitter) {
        attachHorizontalSplitter({
            splitter: editorSplitter,
            getStartSize: () => editorColumn.getBoundingClientRect().width,
            getBounds: () => {
                const total = editorBody.getBoundingClientRect().width;
                const min = 220;
                const max = Math.max(min, total - min - 8);
                return { min, max };
            },
            setSize: (widthPx) => {
                editorBody.style.setProperty('--editor-left-width', `${Math.round(widthPx)}px`);
            }
        });
    }

    const chatLayout = document.querySelector('.chat-layout');
    const chatSourcePanel = document.querySelector('.chat-source-panel');
    const chatSplitter = document.getElementById('chat-source-splitter');

    if (chatLayout && chatSourcePanel && chatSplitter) {
        attachHorizontalSplitter({
            splitter: chatSplitter,
            getStartSize: () => chatSourcePanel.getBoundingClientRect().width,
            getNextSize: (startSizePx, deltaX) => startSizePx - deltaX,
            getBounds: () => {
                const total = chatLayout.getBoundingClientRect().width;
                const min = 260;
                const sessionPanel = 260;
                const splitterWidth = 8;
                const minChatWidth = 340;
                const max = Math.max(min, total - sessionPanel - splitterWidth - minChatWidth);
                return { min, max };
            },
            setSize: (widthPx) => {
                chatLayout.style.setProperty('--chat-source-width', `${Math.round(widthPx)}px`);
            }
        });
    }
}
