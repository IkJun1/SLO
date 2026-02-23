import { state } from './state.js';

let moduleDeps = {
    initGraph: () => {},
    loadTrashItems: async () => {}
};

export function configureNavigationModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function setupNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach((item) => {
        item.addEventListener('click', () => {
            const tab = item.dataset.tab;
            switchTab(tab);
        });
    });
}

export function switchTab(tabId) {
    state.activeTab = tabId;

    document.querySelectorAll('.nav-item').forEach((el) => el.classList.remove('active'));
    document.querySelector(`.nav-item[data-tab="${tabId}"]`).classList.add('active');

    document.querySelectorAll('.view').forEach((el) => el.classList.remove('active'));
    document.getElementById(`view-${tabId}`).classList.add('active');

    if (tabId === 'graph' && !state.graphInitialized) {
        moduleDeps.initGraph();
    }

    if (tabId === 'trash') {
        void moduleDeps.loadTrashItems();
    }
}
