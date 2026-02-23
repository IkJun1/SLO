import { apiPath, safeJson } from '../api.js';
import { state } from '../state.js';
import { getMarkdownTreeModuleDeps } from './tree_context.js';

export async function deleteDocNow(docPath) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    const encodedPath = encodeURIComponent(docPath);
    const res = await fetch(apiPath(`/docs/by-path?path=${encodedPath}`), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'ui-delete' })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to delete document.');
    }

    if (state.currentDocPath === docPath) {
        moduleDeps.clearEditor();
    }

    if (state.selectedEntry && state.selectedEntry.type === 'doc' && state.selectedEntry.path === docPath) {
        state.selectedEntry = null;
    }
}

export async function deleteImageNow(imagePath) {
    const moduleDeps = getMarkdownTreeModuleDeps();

    const encodedPath = encodeURIComponent(imagePath);
    const res = await fetch(apiPath(`/images/by-path?path=${encodedPath}`), {
        method: 'DELETE'
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to delete image.');
    }

    if (state.selectedEntry && state.selectedEntry.type === 'image' && state.selectedEntry.path === imagePath) {
        state.selectedEntry = null;
    }

    moduleDeps.clearEditor();
}

export async function deleteFolderNow(path) {
    const res = await fetch(apiPath('/folders'), {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, recursive: true, reason: 'ui-delete' })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to delete folder.');
    }

    if (state.selectedEntry && state.selectedEntry.type === 'folder' && state.selectedEntry.path === path) {
        state.selectedEntry = null;
    }
}
