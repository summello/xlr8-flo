import { Buildings, CheckCircle, Info } from '@phosphor-icons/react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AuthLayout, FieldError, Password, Summary } from '../auth/Screens';
import { destination, probe } from '../auth/api';
import { accept, lookup, type Invitation, type Expired } from './api';
import { captureToken, clearToken, onDetour, startDetour } from './token';

type Failure = { title: string; body?: string; fields?: Record<string, string> };
type State = 'checking' | 'form' | 'existing' | 'expired' | 'unavailable' | 'network' | 'accepted';
const NETWORK = { title: 'We could not reach the server', body: 'Nothing was submitted. What you typed is kept. Try again.' };
function tenant(invitation: Invitation) { return invitation.org_name + (invitation.tenant_label ? `, ${invitation.tenant_label}` : ''); }
function expiry(value: string) {
    const date = new Date(value);
    const relative = new Intl.RelativeTimeFormat('en', { numeric: 'auto' }).format(Math.ceil((date.getTime() - Date.now()) / 86400000), 'day');
    return `${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}, ${relative}`;
}
function Aside({ invitation }: { invitation: Invitation }) {
    return <aside className="auth-aside invite-aside"><p className="auth-brand"><Buildings aria-hidden="true" />XLR8 FLO</p><h2>Join {tenant(invitation)} on XLR8 FLO</h2><p className="auth-aside-copy">{invitation.inviter_name} invited you to plan and track capital projects together.</p><dl className="invite-details">{[
        ['Invited By', invitation.inviter_name + (invitation.inviter_title ? `, ${invitation.inviter_title}` : '')],
        ['Tenant', tenant(invitation)], ['Your Role', invitation.role_name], ['Access', invitation.access_words], ['Expires', expiry(invitation.expires_at)],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="invite-caption">Your organization and role come from this invitation, not from the link you opened.</p></aside>;
}
function PlaceholderAside() {
    return <aside className="auth-aside invite-aside invite-placeholder" aria-hidden="true"><p className="auth-brand"><Buildings />XLR8 FLO</p><h2><span className="invite-placeholder-block" /></h2><p className="auth-aside-copy"><span className="invite-placeholder-block" /></p><dl className="invite-details">{['Invited By', 'Tenant', 'Your Role', 'Access', 'Expires'].map(label => <div key={label}><dt><span className="invite-placeholder-block" /></dt><dd><span className="invite-placeholder-block" /></dd></div>)}</dl><p className="invite-caption">Your organization and role come from this invitation, not from the link you opened.</p></aside>;
}
function Box({ title, children, done = false }: { title: string; children: string; done?: boolean }) {
    return <div className="invite-box">{done ? <CheckCircle aria-hidden="true" /> : <Info aria-hidden="true" />}<strong>{title}</strong><p>{children}</p></div>;
}
export default function InviteScreen({ navigate }: { navigate: (href: string) => void }) {
    const [token] = useState(captureToken);
    const [state, setState] = useState<State>(token ? 'checking' : 'unavailable');
    const [invitation, setInvitation] = useState<Invitation | null>(null);
    const [expired, setExpired] = useState<Expired | null>(null);
    const [name, setName] = useState('');
    const [password, setPassword] = useState('');
    const [terms, setTerms] = useState(false);
    const [failure, setFailure] = useState<Failure | null>(null);
    const [busy, setBusy] = useState(false);
    const [next, setNext] = useState<'enroll' | 'home'>('home');
    const submission = useRef<{ fingerprint: string; key: string } | null>(null);
    useEffect(() => {
        const meta = document.createElement('meta');
        meta.name = 'referrer'; meta.content = 'no-referrer'; document.head.append(meta);
        return () => meta.remove();
    }, []);
    async function load(active: () => boolean) {
        if (!token) return;
        try {
            const { data, response } = await lookup(token);
            if (!active()) return;
            if (response.status !== 200 || !data) { setState('unavailable'); return; }
            if (data.state === 'expired') { setExpired(data); setState('expired'); return; }
            if (!('org_name' in data)) { setState('unavailable'); return; }
            setInvitation(data); setName(data.full_name ?? '');
            if (onDetour()) {
                const access = await probe();
                if (!active()) return;
                if (access === 'verified' || access === 'choose') setState('existing');
                else if (access === 'error') { setState('network'); setFailure(NETWORK); }
                else navigate(`${destination(access, '/invite')}?return=%2Finvite`);
            } else setState('form');
        } catch { if (active()) { setState('network'); setFailure(NETWORK); } }
    }
    useEffect(() => {
        let active = true;
        void Promise.resolve().then(() => { if (active) return load(() => active); });
        return () => { active = false; };
        // Capture once; SPA re-entry mounts a fresh screen with the remembered token.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [token]);
    function unavailable() { submission.current = null; setPassword(''); setInvitation(null); clearToken(); setState('unavailable'); setFailure(null); }
    async function submit(event: FormEvent) {
        event.preventDefault();
        if (!token || busy) return;
        const fields: Record<string, string> = {};
        if (state !== 'existing') {
            if (!name.trim()) fields.full_name = 'Enter your full name.';
            if (Array.from(password.normalize('NFKC')).length < 15) fields.password = 'Use at least 15 characters.';
            if (!terms) fields.accepted_terms = 'Agree to continue.';
        }
        const count = Object.keys(fields).length;
        if (count) { setFailure({ title: `Fix ${count} ${count === 1 ? 'thing' : 'things'} to continue`, fields }); return; }
        const body = state === 'existing' ? {} : { full_name: name, password, accepted_terms: terms };
        const fingerprint = JSON.stringify(body);
        if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() };
        setBusy(true); setFailure(null);
        try {
            const { response, data } = await accept(token, body, submission.current!.key);
            if (response.status === 200 && data) { setNext(data.next); setPassword(''); submission.current = null; clearToken(); setState('accepted'); }
            else if (response.status === 422) {
                setPassword(''); submission.current = null;
                setFailure({ title: 'Fix 1 thing to continue', fields: { password: 'This password is too common or has appeared in a breach. Choose another.' } });
            } else if (response.status === 403 && onDetour()) {
                const access = await probe();
                if (access === 'verify' || access === 'enroll') navigate(`${destination(access, '/invite')}?return=%2Finvite`);
                else unavailable();
            } else unavailable();
        } catch { setFailure(NETWORK); }
        finally { setBusy(false); }
    }
    const detailed = invitation && ['form', 'existing', 'network', 'accepted'].includes(state);
    const heading = state === 'checking' ? 'Checking Your Invitation' : state === 'expired' ? 'Invitation Expired' : state === 'unavailable' ? 'Invitation Unavailable' : state === 'accepted' && invitation ? `Welcome to ${tenant(invitation)}` : state === 'existing' && invitation ? `Join ${invitation.org_name}` : 'Accept Your Invitation';
    return <AuthLayout aside={detailed ? <Aside invitation={invitation} /> : state === 'checking' ? <PlaceholderAside /> : undefined}><main className="auth-main"><h1>{heading}</h1>
        {state === 'checking' ? <div role="status"><p>This takes a moment.</p><div className="invite-skeleton" aria-hidden="true">{Array.from({ length: 4 }, (_, index) => <div key={index} />)}</div></div> : state === 'unavailable' ? <div className="auth-form"><p>This link has already been used, was withdrawn, or is not valid. Ask the person who invited you for a new one.</p><button onClick={() => navigate('/sign-in')}>Go to sign in</button></div> : state === 'expired' && expired ? <div className="auth-form"><p>Invitations last {expired.lifetime_days} {expired.lifetime_days === 1 ? 'day' : 'days'}. Ask the person who invited you to send a new one.</p><Box title="Nothing was changed">No account was created from this link.</Box><button onClick={() => navigate('/sign-in')}>Go to sign in</button></div> : state === 'accepted' && invitation ? <div className="auth-form"><p>{next === 'enroll' ? 'Your account is ready. One more step: your role needs two-step verification.' : 'Your account is ready.'}</p><Box title="Account created" done>{`You can sign in with ${invitation.email}.`}</Box><button className="auth-primary" onClick={() => navigate(next === 'enroll' ? '/sign-in/mfa/enroll' : '/')}>{next === 'enroll' ? 'Set up two-step verification' : 'Continue to XLR8 FLO'}</button></div> : <><p className="auth-subline">{state === 'existing' && invitation ? `You are signed in as ${invitation.email}.` : 'Choose a password to finish creating your account.'}</p><Summary failure={failure} />{invitation ? <form className="auth-form" noValidate onSubmit={event => void submit(event)}>{state === 'existing' ? <><Box title="Your other organizations are not affected">{`Accepting adds ${tenant(invitation)} to your account as a separate tenant. Its data stays separate from the others.`}</Box><button className="auth-primary" disabled={busy} aria-busy={busy}>Accept and join</button><button type="button" onClick={() => { clearToken(); navigate('/'); }}>Not now</button></> : <><div><label htmlFor="email">Email</label><input id="email" type="email" autoComplete="email" readOnly aria-readonly="true" value={invitation.email} /></div><div><label htmlFor="full_name">Full name</label><input id="full_name" required autoComplete="name" value={name} onChange={e => setName(e.target.value)} aria-invalid={Boolean(failure?.fields?.full_name)} aria-describedby={failure?.fields?.full_name ? 'full_name-error' : undefined} /><FieldError id="full_name" message={failure?.fields?.full_name} /></div><Password value={password} setValue={setPassword} label="Password" autoComplete="new-password" hint="At least 15 characters. A passphrase works well; pasting from a password manager is fine." error={failure?.fields?.password} /><div><label className="auth-check"><input id="accepted_terms" required type="checkbox" checked={terms} onChange={e => setTerms(e.target.checked)} aria-invalid={Boolean(failure?.fields?.accepted_terms)} aria-describedby={failure?.fields?.accepted_terms ? 'accepted_terms-error' : undefined} />I agree to the terms of service and privacy notice.</label><FieldError id="accepted_terms" message={failure?.fields?.accepted_terms} /></div><button className="auth-primary" disabled={busy} aria-busy={busy}>{busy ? 'Accepting' : 'Accept and create account'}</button><p>Already have an account? <button type="button" className="auth-link" onClick={() => { startDetour(); navigate('/sign-in?return=%2Finvite'); }}>Sign in to accept</button></p></>}</form> : <button onClick={() => { setFailure(null); setState('checking'); void load(() => true); }}>Try again</button>}</>}
    </main></AuthLayout>;
}
