/**
 * Agreement Management System — Shared Utilities
 * API client, auth helpers, toast notifications, common UI
 */

const API_BASE = '/api';

// ==========================================
// Auth Token Management
// ==========================================
const Auth = {
    getToken() {
        return localStorage.getItem('ag_token');
    },
    setToken(token) {
        localStorage.setItem('ag_token', token);
    },
    setUser(user) {
        localStorage.setItem('ag_user', JSON.stringify(user));
    },
    getUser() {
        const u = localStorage.getItem('ag_user');
        return u ? JSON.parse(u) : null;
    },
    logout() {
        localStorage.removeItem('ag_token');
        localStorage.removeItem('ag_user');
        window.location.href = '/login';
    },
    isLoggedIn() {
        return !!this.getToken();
    },
    isAdmin() {
        const user = this.getUser();
        return user && user.role === 'admin';
    },
    isConsultant() {
        const user = this.getUser();
        return user && user.role === 'consultant';
    },
    requireAuth() {
        if (!this.isLoggedIn()) {
            window.location.href = '/login';
            return false;
        }
        return true;
    }
};

// ==========================================
// API Client
// ==========================================
async function apiRequest(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const headers = {};

    const token = Auth.getToken();
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    if (options.body && !(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
    }

    try {
        const response = await fetch(url, {
            ...options,
            headers: { ...headers, ...options.headers }
        });

        // Handle 401 — show session expired popup instead of immediate logout
        if (response.status === 401) {
            // Check if this is a login/register request (don't show popup for auth attempts)
            const isAuthRequest = endpoint.startsWith('/auth/');
            if (!isAuthRequest) {
                showSessionExpiredPopup();
                throw new Error('Session expired. Please log in again.');
            }
        }

        // Safely parse response — handle non-JSON responses
        let data;
        const contentType = response.headers.get('content-type') || '';
        const responseText = await response.text();

        try {
            data = JSON.parse(responseText);
        } catch (parseError) {
            // Response is not valid JSON
            if (!response.ok) {
                throw new Error(responseText || `Request failed with status ${response.status}`);
            }
            throw new Error('Server returned an invalid response. Please try again.');
        }

        if (!response.ok) {
            // Handle Pydantic 422 validation errors (detail is an array of objects)
            if (data.detail && Array.isArray(data.detail)) {
                const messages = data.detail.map(err => {
                    // Pydantic error: { loc: [...], msg: "...", type: "..." }
                    const field = err.loc ? err.loc[err.loc.length - 1] : '';
                    const msg = err.msg || 'Invalid value';
                    // Clean up Pydantic "Value error, " prefix
                    const cleanMsg = msg.replace(/^Value error,?\s*/i, '');
                    return field ? `${field}: ${cleanMsg}` : cleanMsg;
                });
                throw new Error(messages.join('. '));
            }
            throw new Error(data.detail || data.message || 'Request failed');
        }

        return data;
    } catch (error) {
        if (error.message === 'Failed to fetch') {
            throw new Error('Network error. Please check your connection.');
        }
        throw error;
    }
}

const api = {
    get: (endpoint) => apiRequest(endpoint, { method: 'GET' }),
    post: (endpoint, body) => apiRequest(endpoint, { method: 'POST', body }),
    put: (endpoint, body) => apiRequest(endpoint, { method: 'PUT', body }),
    delete: (endpoint) => apiRequest(endpoint, { method: 'DELETE' }),
    upload: (endpoint, formData) => apiRequest(endpoint, { method: 'POST', body: formData }),
};

// ==========================================
// Toast Notifications
// ==========================================
function showToast(message, type = 'info') {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const icons = {
        success: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>`,
        error: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>`,
        warning: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><path d="M12 9v4M12 17h.01"/></svg>`,
        info: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0284c7" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>`
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `${icons[type] || icons.info}<span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ==========================================
// Session Expired Popup (non-dismissible)
// ==========================================
let _sessionExpiredShown = false;
function showSessionExpiredPopup() {
    if (_sessionExpiredShown) return; // prevent multiple popups
    _sessionExpiredShown = true;

    const overlay = document.createElement('div');
    overlay.className = 'session-expired-overlay';
    overlay.innerHTML = `
        <div class="session-expired-modal">
            <div class="session-expired-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:48px;height:48px">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M12 6v6l4 2"/>
                </svg>
            </div>
            <h3 class="session-expired-title">Session Expired</h3>
            <p class="session-expired-message">Your session has expired due to inactivity.<br>Please log in again to continue where you left off.</p>
            <button class="btn btn-primary session-expired-ok" id="session-expired-ok-btn">OK</button>
        </div>
    `;
    document.body.appendChild(overlay);

    // Force layout to trigger animation
    requestAnimationFrame(() => overlay.classList.add('visible'));

    // Only the OK button can dismiss
    document.getElementById('session-expired-ok-btn').onclick = () => {
        Auth.logout();
    };
}

// ==========================================
// Modal Helpers
// ==========================================
function showConfirm(title, message) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay open';
        overlay.style.zIndex = '10003';
        overlay.innerHTML = `
            <div class="modal">
                <h3 class="modal-title">${title}</h3>
                <div class="modal-body">${message}</div>
                <div class="modal-actions">
                    <button class="btn btn-secondary" id="modal-cancel">Cancel</button>
                    <button class="btn btn-danger" id="modal-confirm">Confirm</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('#modal-cancel').onclick = () => {
            overlay.remove();
            resolve(false);
        };
        overlay.querySelector('#modal-confirm').onclick = () => {
            overlay.remove();
            resolve(true);
        };
        overlay.onclick = (e) => {
            if (e.target === overlay) {
                overlay.remove();
                resolve(false);
            }
        };
    });
}

// ==========================================
// Date Formatting
// ==========================================
function formatDate(dateStr) {
    if (!dateStr) return '—';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return dateStr;
        const day = String(d.getDate()).padStart(2, '0');
        const month = d.toLocaleString('en-US', { month: 'long' });
        const year = String(d.getFullYear()).slice(-2);
        return `${day}-${month}-${year}`;
    } catch {
        return dateStr;
    }
}

function formatCurrency(amount, currency) {
    if (amount == null) return '—';
    const sym = currency || '₹';
    if (sym === '$') {
        return '$' + Number(amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    // Indian Rupee formatting
    return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatINR(amount) {
    if (amount == null) return '—';
    return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function daysUntil(dateStr) {
    if (!dateStr) return null;
    try {
        const d = new Date(dateStr);
        const now = new Date();
        return Math.ceil((d - now) / (1000 * 60 * 60 * 24));
    } catch {
        return null;
    }
}

// ==========================================
// Status Badge
// ==========================================
function statusBadge(status) {
    const s = (status || 'pending').toLowerCase();
    return `<span class="badge badge-${s}">${s}</span>`;
}

// ==========================================
// SVG Icons (inline to avoid external deps)
// ==========================================
const Icons = {
    dashboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>`,
    file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/></svg>`,
    upload: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
    users: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>`,
    bell: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/></svg>`,
    search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>`,
    x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>`,
    logout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
    back: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>`,
    calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
    dollar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>`,
    building: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="2" width="16" height="20" rx="1"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M8 10h.01M16 10h.01M8 14h.01M16 14h.01"/></svg>`,
    shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    menu: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>`,
    edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`,
    check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>`,
    save: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>`,
};

// ==========================================
// Sidebar Logo SVG
// ==========================================
const LogoSVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/></svg>`;

// ==========================================
// LiveSocket — Real-Time WebSocket Client
// ==========================================
/**
 * Reusable WebSocket client with:
 * - JWT-authenticated connections
 * - Auto-reconnect with exponential backoff (1s → 2s → 4s → max 30s)
 * - Ping/pong keep-alive (every 30s)
 * - Page visibility handling (pause when hidden, reconnect when visible)
 * - Debounced event handling to prevent rapid re-renders
 *
 * Usage:
 *   const ws = new LiveSocket({
 *       agreement_uploaded: () => { loadAgreements(); },
 *       payment_updated: (data) => { loadPayments(); },
 *   });
 *   ws.connect();
 */
class LiveSocket {
    constructor(handlers = {}) {
        this._handlers = handlers;
        this._ws = null;
        this._reconnectDelay = 1000;       // Start at 1 second
        this._maxReconnectDelay = 30000;    // Max 30 seconds
        this._reconnectTimer = null;
        this._pingInterval = null;
        this._connected = false;
        this._intentionalClose = false;
        this._debounceTimers = {};
        this._debounceMs = 500;             // Debounce rapid events (500ms)
    }

    connect() {
        const token = Auth.getToken();
        if (!token) return;

        // Build WebSocket URL (ws:// for http, wss:// for https)
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`;

        try {
            this._ws = new WebSocket(url);
        } catch (e) {
            this._scheduleReconnect();
            return;
        }

        this._ws.onopen = () => {
            this._connected = true;
            this._reconnectDelay = 1000; // Reset backoff on successful connect
            this._startPing();
        };

        this._ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.event === 'pong') return; // Keep-alive response
                this._handleEvent(msg.event, msg.data || {});
            } catch (e) {
                // Ignore malformed messages
            }
        };

        this._ws.onclose = (event) => {
            this._connected = false;
            this._stopPing();
            // 4001 = invalid token → don't reconnect (session expired popup will handle it)
            if (event.code === 4001) return;
            if (!this._intentionalClose) {
                this._scheduleReconnect();
            }
        };

        this._ws.onerror = () => {
            // Error will trigger onclose, which handles reconnection
        };

        // Handle page visibility changes
        if (!this._visibilityHandler) {
            this._visibilityHandler = () => {
                if (document.hidden) {
                    // Tab hidden — stop ping to save resources
                    this._stopPing();
                } else {
                    // Tab visible again — reconnect if needed
                    if (!this._connected) {
                        clearTimeout(this._reconnectTimer);
                        this._reconnectDelay = 1000;
                        this._scheduleReconnect();
                    } else {
                        this._startPing();
                    }
                }
            };
            document.addEventListener('visibilitychange', this._visibilityHandler);
        }
    }

    disconnect() {
        this._intentionalClose = true;
        this._stopPing();
        clearTimeout(this._reconnectTimer);
        if (this._ws) {
            this._ws.close();
            this._ws = null;
        }
        this._connected = false;
    }

    _handleEvent(event, data) {
        const handler = this._handlers[event];
        if (!handler) return;

        // Debounce: if the same event fires multiple times within _debounceMs,
        // only execute the handler once (prevents rapid UI re-renders)
        clearTimeout(this._debounceTimers[event]);
        this._debounceTimers[event] = setTimeout(() => {
            try {
                handler(data);
            } catch (e) {
                // Handler errors should not crash the WebSocket
            }
        }, this._debounceMs);
    }

    _scheduleReconnect() {
        if (this._intentionalClose) return;
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
            this.connect();
        }, this._reconnectDelay);
        // Exponential backoff
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxReconnectDelay);
    }

    _startPing() {
        this._stopPing();
        this._pingInterval = setInterval(() => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                try {
                    this._ws.send('ping');
                } catch (e) {
                    // Send failed — connection is dead
                    this._connected = false;
                    this._stopPing();
                    this._scheduleReconnect();
                }
            }
        }, 30000); // Ping every 30 seconds
    }

    _stopPing() {
        clearInterval(this._pingInterval);
        this._pingInterval = null;
    }
}
