const API_BASE = '/api/v1/user';

export function apiPath(path) {
    return `${API_BASE}${path}`;
}

export async function safeJson(response) {
    try {
        return await response.json();
    } catch (_err) {
        return null;
    }
}
