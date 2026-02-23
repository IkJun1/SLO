import { apiPath, safeJson } from './api.js';
import { state } from './state.js';
import { baseName } from './utils.js';

let moduleDeps = {
    showSelectedStatus: () => {},
    switchTab: () => {},
    loadDoc: async () => {},
    expandAncestors: () => {},
    setSelectedEntry: () => {}
};

export function configureGraphModule(deps) {
    moduleDeps = {
        ...moduleDeps,
        ...deps
    };
}

export function setupGraphView() {
    const refreshBtn = document.getElementById('refresh-graph');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            state.graphInitialized = false;
            const container = document.getElementById('graph-container');
            if (container) {
                container.innerHTML = '';
            }
            void initGraph();
        });
    }

    const runSyncBtn = document.getElementById('run-embedding-sync');
    if (runSyncBtn) {
        runSyncBtn.addEventListener('click', () => {
            void runEmbeddingSync();
        });
    }

    void loadEmbeddingSyncStatus();
}

function setEmbeddingSyncStatus(text, tone = 'idle') {
    const status = document.getElementById('embedding-sync-status');
    if (!status) {
        return;
    }

    status.textContent = text;
    if (tone === 'idle') {
        delete status.dataset.tone;
        return;
    }

    status.dataset.tone = tone;
}

async function loadEmbeddingSyncStatus() {
    try {
        const res = await fetch(apiPath('/embeddings/status'));
        if (!res.ok) {
            throw new Error('Failed to load embedding status.');
        }

        const data = await res.json();
        if (data.running) {
            setEmbeddingSyncStatus('Running', 'running');
            return;
        }

        if (data.failed > 0) {
            setEmbeddingSyncStatus(`Failed ${data.failed}`, 'error');
            return;
        }

        if (data.pending > 0) {
            setEmbeddingSyncStatus(`Pending ${data.pending}`, 'running');
            return;
        }

        setEmbeddingSyncStatus('Ready', 'success');
    } catch (_err) {
        setEmbeddingSyncStatus('Unavailable', 'error');
    }
}

async function runEmbeddingSync() {
    const runButton = document.getElementById('run-embedding-sync');
    if (runButton) {
        runButton.disabled = true;
    }

    setEmbeddingSyncStatus('Running', 'running');
    try {
        const res = await fetch(apiPath('/embeddings/run'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });

        if (!res.ok) {
            const body = await safeJson(res);
            throw new Error((body && body.error && body.error.message) || 'Embedding sync failed.');
        }

        const result = await res.json();
        if (!result.started) {
            const message = result.message || 'Embedding sync is already running.';
            moduleDeps.showSelectedStatus(message, 'error');
            setEmbeddingSyncStatus('Running', 'running');
            return;
        }

        if (result.failed > 0) {
            setEmbeddingSyncStatus(`Failed ${result.failed}`, 'error');
        } else if (result.remaining_pending > 0) {
            setEmbeddingSyncStatus(`Pending ${result.remaining_pending}`, 'running');
        } else {
            setEmbeddingSyncStatus('Ready', 'success');
        }

        const message = result.message
            || `Embedded ${result.processed} chunks (${result.remaining_pending} pending)`;
        moduleDeps.showSelectedStatus(message);
    } catch (err) {
        console.error(err);
        setEmbeddingSyncStatus('Failed', 'error');
        moduleDeps.showSelectedStatus(err.message || 'Embedding sync failed.', 'error');
    } finally {
        if (runButton) {
            runButton.disabled = false;
        }
    }
}

export async function initGraph() {
    try {
        const res = await fetch(apiPath('/graph3d?layout=pca&include_edges=true&top_k_edges=5'));
        if (!res.ok) {
            throw new Error('Failed to load graph.');
        }

        const json = await res.json();
        const data = json.data;

        const gData = {
            nodes: data.nodes.map((node) => ({
                id: node.node_id,
                name: node.title,
                val: node.node_type === 'folder' ? 3.3 : 2.1,
                nodeType: node.node_type,
                path: node.path
            })),
            links: data.edges.map((edge) => ({
                source: edge.from_node_id,
                target: edge.to_node_id,
                edgeType: edge.edge_type,
                weight: edge.weight
            }))
        };

        const nodeCount = document.getElementById('node-count');
        if (nodeCount) {
            nodeCount.textContent = gData.nodes.length;
        }

        const container = document.getElementById('graph-container');
        if (!container) {
            return;
        }

        const graph = ForceGraph3D()(container)
            .graphData(gData)
            .backgroundColor('#151a24')
            .nodeLabel((node) => {
                if (node.nodeType !== 'doc') {
                    return '';
                }
                return node.name || baseName(node.path);
            })
            .nodeColor((node) => (node.nodeType === 'folder' ? '#e8b26a' : '#7c4dff'))
            .nodeResolution(16)
            .nodeVal((node) => node.val)
            .linkColor((link) => (link.edgeType === 'folder_tree' ? '#6f81a3' : '#4b4b4b'))
            .linkOpacity(0.82)
            .linkWidth((link) => (link.edgeType === 'folder_tree' ? 1.4 : 0.8))
            .onNodeClick((node) => {
                if (node.nodeType === 'doc' && node.path) {
                    moduleDeps.switchTab('markdown');
                    void moduleDeps.loadDoc(node.path);
                    return;
                }

                if (node.nodeType === 'folder') {
                    moduleDeps.expandAncestors(node.path);
                    moduleDeps.setSelectedEntry({ type: 'folder', path: node.path, name: baseName(node.path) });
                    moduleDeps.switchTab('markdown');
                }
            });

        const labelLayer = document.createElement('div');
        labelLayer.className = 'graph-label-layer';
        container.appendChild(labelLayer);

        const labels = new Map();
        const docLabelDistance = 300;

        gData.nodes.forEach((node) => {
            const label = document.createElement('div');
            label.className = `graph-node-label ${node.nodeType}`;
            label.textContent = node.name || baseName(node.path);
            labelLayer.appendChild(label);
            labels.set(node.id, label);
        });

        const updateLabels = () => {
            const camPos = typeof graph.cameraPosition === 'function' ? graph.cameraPosition() : null;
            const toScreen = typeof graph.graph2ScreenCoords === 'function';
            if (!camPos || !toScreen) {
                return;
            }

            gData.nodes.forEach((node) => {
                const label = labels.get(node.id);
                if (!label) {
                    return;
                }

                const nx = Number(node.x);
                const ny = Number(node.y);
                const nz = Number(node.z);

                if (!Number.isFinite(nx) || !Number.isFinite(ny) || !Number.isFinite(nz)) {
                    label.style.display = 'none';
                    return;
                }

                let visible = node.nodeType === 'folder';
                if (!visible) {
                    const dx = nx - camPos.x;
                    const dy = ny - camPos.y;
                    const dz = nz - camPos.z;
                    const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
                    visible = distance <= docLabelDistance;
                }

                if (!visible) {
                    label.style.display = 'none';
                    return;
                }

                const projected = graph.graph2ScreenCoords(nx, ny, nz);
                if (!projected || !Number.isFinite(projected.x) || !Number.isFinite(projected.y)) {
                    label.style.display = 'none';
                    return;
                }

                const offsetY = node.nodeType === 'folder' ? 18 : 14;
                label.style.display = 'block';
                label.style.transform = `translate(-50%, -50%) translate(${projected.x}px, ${projected.y - offsetY}px)`;
            });
        };

        graph.onEngineTick(updateLabels).onEngineStop(updateLabels);
        const controls = typeof graph.controls === 'function' ? graph.controls() : null;
        if (controls && typeof controls.addEventListener === 'function') {
            controls.addEventListener('change', updateLabels);
        }
        updateLabels();

        state.graphInitialized = true;
    } catch (err) {
        console.error('Graph load failed', err);
        const container = document.getElementById('graph-container');
        if (container) {
            container.textContent = 'Failed to load graph data.';
        }
    }
}
