const API_PREFIX = '/api/v1';

function setMessage(text, tone = '') {
    const target = document.getElementById('login-message');
    if (!target) {
        return;
    }

    target.textContent = text;
    target.classList.remove('error', 'success');
    if (tone) {
        target.classList.add(tone);
    }
}

async function safeJson(response) {
    try {
        return await response.json();
    } catch (_err) {
        return null;
    }
}

async function ensureSignupCompleted() {
    try {
        const res = await fetch(`${API_PREFIX}/auth/status`);
        const body = await safeJson(res);
        if (!res.ok) {
            return;
        }

        if (!body || body.has_users !== true) {
            window.location.replace('/signup');
        }
    } catch (_err) {
    }
}

function setupLoginForm() {
    const form = document.getElementById('login-form');
    if (!form) {
        return;
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();

        const usernameEl = document.getElementById('username');
        const passwordEl = document.getElementById('password');
        const submitBtn = form.querySelector('.login-btn');

        const username = String((usernameEl && usernameEl.value) || '').trim();
        const password = String((passwordEl && passwordEl.value) || '');

        if (username === '' || password === '') {
            setMessage('Username and password are required.', 'error');
            return;
        }

        if (submitBtn) {
            submitBtn.disabled = true;
        }
        setMessage('Signing in...');

        try {
            const res = await fetch(`${API_PREFIX}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            const body = await safeJson(res);
            if (!res.ok) {
                throw new Error((body && body.error && body.error.message) || 'Login failed.');
            }

            if (!body || body.authenticated !== true) {
                throw new Error('Login failed.');
            }

            sessionStorage.setItem('slo-auth-user', String(body.username || username));
            setMessage('Login successful. Redirecting...', 'success');
            window.location.assign('/app');
        } catch (err) {
            setMessage((err && err.message) || 'Login failed.', 'error');
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
            }
        }
    });
}

void ensureSignupCompleted();
setupLoginForm();
