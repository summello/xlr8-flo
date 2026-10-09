import { useEffect, useRef, useState } from 'react';
import { apiClient } from '../../api/client';
import { organizationName, writeHeaders, type Organizations } from './api';

export default function OrganizationChooser({ back, navigate }: { back: string; navigate: (path: string) => void }) {
    const [organizations, setOrganizations] = useState<Organizations | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const summary = useRef<HTMLParagraphElement>(null);
    useEffect(() => { if (error) summary.current?.focus(); }, [error]);
    async function load() {
        try {
            const { data, response } = await apiClient.GET('/api/v1/auth/organizations');
            if (response.status === 200 && data) { setOrganizations(data); setError(''); }
            else setError('Organizations could not be loaded. Try again or sign out.');
        } catch { setError('Check your connection and try again.'); }
    }
    useEffect(() => {
        let active = true;
        void apiClient.GET('/api/v1/auth/organizations').then(({ data, response }) => {
            if (!active) return;
            if (response.status === 200 && data) { setOrganizations(data); setError(''); }
            else setError('Organizations could not be loaded. Try again or sign out.');
        }).catch(() => { if (active) setError('Check your connection and try again.'); });
        return () => { active = false; };
    }, []);
    async function select(org_id: string) {
        setBusy(true); setError('');
        try {
            const { response } = await apiClient.POST('/api/v1/auth/organization', { body: { org_id }, headers: writeHeaders() });
            if (response.status === 204) window.location.assign(back);
            else setError('Organization could not be selected. Pick another organization, try again, or sign out.');
        } catch { setError('Check your connection and try again.'); }
        finally { setBusy(false); }
    }
    async function logout() {
        try {
            const { response } = await apiClient.POST('/api/v1/auth/logout', { headers: writeHeaders() });
            if (response.status === 204 || response.status === 401) navigate('/sign-in');
            else setError('Sign out did not complete. Try again.');
        } catch { setError('Check your connection and try again.'); }
    }
    return <div className="auth-form">{error && <p className="auth-summary" role="alert" tabIndex={-1} ref={summary}>{error}</p>}{!organizations && !error && <p role="status">Loading organizations</p>}<div className="org-list" role="group" aria-label="Your organizations">{organizations?.items.map((item, index) => <button className="org-btn" key={item.org_id} disabled={busy} onClick={() => void select(item.org_id)}><span><strong>{organizationName(item)}</strong><small>Tenant {index + 1} of {organizations.items.length}</small></span>{item.last_used_at && <small>Last used {new Date(item.last_used_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</small>}</button>)}</div><p>Each organization is a separate tenant with its own data. Nothing is shared between them.</p>{error && <button onClick={() => void load()}>Try again</button>}<button className="auth-link" disabled={busy} onClick={() => void logout()}>Sign out</button></div>;
}
