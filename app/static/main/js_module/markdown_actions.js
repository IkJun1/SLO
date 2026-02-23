import { apiPath, safeJson } from './api.js';
import { baseName } from './utils.js';

export async function createDocWithPath(path) {
    const title = inferTitleFromPath(path);
    const res = await fetch(apiPath('/docs'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            path,
            title,
            content: `# ${title}\n\n`,
            create_parents: true,
            overwrite: false
        })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to create document.');
    }

    return res.json();
}

export async function createFolderWithPath(path) {
    const res = await fetch(apiPath('/folders'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, create_parents: true })
    });

    if (!res.ok) {
        const body = await safeJson(res);
        throw new Error((body && body.error && body.error.message) || 'Failed to create folder.');
    }
}

export async function renameDocWithPath(docId, toPath) {
    const res = await fetch(apiPath('/docs/move'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            doc_id: docId,
            to_path: toPath,
            overwrite: false
        })
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to rename document.');
    }

    return body;
}

export async function renameImageWithPath(fromPath, toPath) {
    const res = await fetch(apiPath('/images/rename'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            from_path: fromPath,
            to_path: toPath,
            overwrite: false
        })
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to rename image.');
    }

    return body;
}

export async function renameFolderWithPath(fromPath, toPath) {
    const res = await fetch(apiPath('/folders/move'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            from_path: fromPath,
            to_path: toPath,
            overwrite: false
        })
    });

    const body = await safeJson(res);
    if (!res.ok) {
        throw new Error((body && body.error && body.error.message) || 'Failed to rename folder.');
    }

    return body;
}

export function remapMovedPath(path, fromPrefix, toPrefix) {
    const original = String(path || '').trim();
    if (!original || !fromPrefix || !toPrefix) {
        return original;
    }

    if (original === fromPrefix) {
        return toPrefix;
    }

    const withSlash = `${fromPrefix}/`;
    if (original.startsWith(withSlash)) {
        return `${toPrefix}${original.slice(fromPrefix.length)}`;
    }

    return original;
}

export function inferTitleFromPath(path) {
    const name = baseName(path).replace(/\.md$/i, '');
    if (!name) {
        return 'Untitled';
    }

    return name
        .split(/[-_\s]+/)
        .filter(Boolean)
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

