import { CheckCircle } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';
import { apiClient } from '../../api/client';
import { writeHeaders, organizationName, type Organizations, type Session } from './api';
export default function AccountMenu({ navigate }: {
    navigate: (href: string) => void;
}) {
    const [organizations, setOrganizations] = useState<Organizations | null>(null);
    const [sessions, setSessions] = useState<Session[] | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        let active = true;
        void apiClient.GET('/api/v1/auth/organizations').then(({ data }) => {
            if (active && data) setOrganizations(data);
        }).catch(() => { if (active) setError('Organizations could not be loaded. Try again.'); });
        return () => { active = false; };
    }, []);
    async function switchOrganization(org_id: string) {
        setBusy(true); setError('');
        try {
            const { response } = await apiClient.POST('/api/v1/auth/organization', { body: { org_id }, headers: writeHeaders() });
            if (response.status === 204) window.location.assign('/');
            else setError('Organization could not be selected. Try again or sign out.');
        } catch { setError('Check your connection and try again.'); }
        finally { setBusy(false); }
    }
    async function list() {
        setBusy(true);
        setError('');
        try {
            const { data, response } = await apiClient.GET('/api/v1/auth/sessions');
            if (response.status === 200 && data)
                setSessions(data);
            else
                setError('Sessions could not be loaded. Try again.');
        }
        catch {
            setError('Sessions could not be loaded. Check your connection and try again.');
        }
        finally {
            setBusy(false);
        }
    }
    async function logout() {
        setBusy(true);
        setError('');
        try {
            const { response } = await apiClient.POST('/api/v1/auth/logout', { headers: writeHeaders() });
            if (response.status === 204 || response.status === 401)
                navigate('/sign-in');
            else
                setError('Sign out did not complete. Try again.');
        }
        catch {
            setError('Sign out did not complete. Check your connection and try again.');
        }
        finally {
            setBusy(false);
        }
    }
    async function revoke(id: string, current: boolean) {
        setBusy(true);
        setError('');
        try {
            const { response } = await apiClient.DELETE('/api/v1/auth/sessions/{session_id}', { params: { path: { session_id: id } }, headers: writeHeaders() });
            if (response.status !== 204)
                setError('The session was not revoked. Try again.');
            else if (current)
                navigate('/sign-in');
            else
                await list();
        }
        catch {
            setError('The session was not revoked. Check your connection and try again.');
        }
        finally {
            setBusy(false);
        }
    }
    return <details className="account-menu"><summary>Account menu</summary><div className="account-panel">{organizations && <section aria-label="Your organizations">{organizations.items.map(item => <button key={item.org_id} disabled={busy || item.org_id === organizations.current_org_id} onClick={() => void switchOrganization(item.org_id)}>{organizationName(item)}{item.org_id === organizations.current_org_id && <><CheckCircle aria-hidden="true"/> Current organization</>}</button>)}</section>}<button disabled={busy} onClick={() => void list()}>Sessions</button><button disabled={busy} onClick={() => void logout()}>Sign out</button>{error && <p role="alert">{error}</p>}{sessions && <section aria-label="Sessions"><h2>Sessions</h2>{sessions.length === 0 ? <p>No active sessions.</p> : <ul>{sessions.map(session => <li key={session.id}>{session.user_agent} · Last seen {new Date(session.last_seen_at).toLocaleString()}{session.current && ' · Current session'}<button disabled={busy} onClick={() => void revoke(session.id, session.current)}>Revoke</button></li>)}</ul>}</section>}</div></details>;
}
