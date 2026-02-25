export function renderMathIfAvailable(element) {
    if (!element) {
        return;
    }

    if (typeof window.renderMathInElement !== 'function') {
        return;
    }

    try {
        window.renderMathInElement(element, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '\\[', right: '\\]', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\(', right: '\\)', display: false }
            ],
            throwOnError: false,
            strict: 'ignore'
        });
    } catch (_err) {
    }
}
