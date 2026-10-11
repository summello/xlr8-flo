// Bearer material lives only in this module, including during SPA sign-in.
let token: string | null = null;
let detour = false;
export function captureToken(): string | null {
    const match = window.location.pathname.match(/^\/invite\/([^/]+)$/);
    if (match) {
        token = match[1] ?? null;
        detour = false;
        window.history.replaceState(null, '', '/invite');
    }
    return token;
}
export function startDetour() { detour = true; }
export function onDetour() { return detour; }
export function clearToken() { token = null; detour = false; }
