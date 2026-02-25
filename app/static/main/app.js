import { configureChatModule, setupLLMView } from './js_module/chat.js';
import { setupDesktopContextMenu } from './js_module/desktop_context_menu.js';
import { setupDesktopShell } from './js_module/desktop_shell.js';
import { configureGraphModule, initGraph, setupGraphView } from './js_module/graph.js';
import { setupPanelSplitters } from './js_module/layout.js';
import { expandAncestors, loadDoc, refreshFileTree, setSelectedEntry, setupMarkdownView, showSelectedStatus } from './js_module/markdown.js';
import { configureNavigationModule, setupNavigation, switchTab } from './js_module/navigation.js';
import { configureTrashModule, loadTrashItems, setupTrashView } from './js_module/trash.js';

document.addEventListener('DOMContentLoaded', () => {
    lucide.createIcons();
    initApp();
});

function initApp() {
    if (window.marked && typeof marked.setOptions === 'function') {
        marked.setOptions({ gfm: true, breaks: true });
    }

    configureNavigationModule({
        initGraph,
        loadTrashItems
    });
    configureGraphModule({
        showSelectedStatus,
        switchTab,
        loadDoc,
        expandAncestors,
        setSelectedEntry
    });
    configureTrashModule({
        showSelectedStatus,
        switchTab,
        refreshFileTree,
        loadDoc
    });
    configureChatModule({
        switchTab,
        loadDoc,
        showSelectedStatus
    });

    setupNavigation();
    setupMarkdownView();
    setupGraphView();
    setupTrashView();
    setupLLMView();
    setupPanelSplitters();
    void setupDesktopShell();
    setupDesktopContextMenu();

    refreshFileTree();
}
