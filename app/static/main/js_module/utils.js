export function baseName(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
}

export function parentPath(path) {
    const idx = path.lastIndexOf('/');
    if (idx <= 0) {
        return '';
    }
    return path.slice(0, idx);
}

export function escapeHtml(text) {
    if (!text) {
        return '';
    }

    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
