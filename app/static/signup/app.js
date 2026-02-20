const API_PREFIX = '/api/v1/public';

function setMessage(text, tone = '') {
    const target = document.getElementById('signup-message');
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

function setupSignupForm() {
    const form = document.getElementById('signup-form');
    if (!form) {
        return;
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();

        const usernameEl = document.getElementById('username');
        const passwordEl = document.getElementById('password');
        const confirmEl = document.getElementById('confirm-password');
        const submitBtn = form.querySelector('.signup-btn');

        const username = String((usernameEl && usernameEl.value) || '').trim();
        const password = String((passwordEl && passwordEl.value) || '');
        const confirmPassword = String((confirmEl && confirmEl.value) || '');

        if (username === '' || password === '' || confirmPassword === '') {
            setMessage('All fields are required.', 'error');
            return;
        }

        if (password !== confirmPassword) {
            setMessage('Passwords do not match.', 'error');
            return;
        }

        if (submitBtn) {
            submitBtn.disabled = true;
        }
        setMessage('Creating account...');

        try {
            const res = await fetch(`${API_PREFIX}/auth/signup`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            const body = await safeJson(res);
            if (!res.ok) {
                throw new Error((body && body.error && body.error.message) || 'Sign up failed.');
            }

            setMessage('Account created. Redirecting to login...', 'success');
            window.location.assign('/login');
        } catch (err) {
            setMessage((err && err.message) || 'Sign up failed.', 'error');
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
            }
        }
    });
}

setupSignupForm();
