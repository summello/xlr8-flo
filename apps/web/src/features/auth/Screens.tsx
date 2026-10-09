import { Buildings, ChartLine, CheckCircle, CircleNotch, ShieldCheck, Warning, XCircle } from '@phosphor-icons/react';
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import qrcode from 'qrcode-generator';
import { apiClient } from '../../api/client';
import { destination, probe, safeReturn, writeHeaders, type Enrollment } from './api';
import OrganizationChooser from './OrganizationChooser';
import Backdrop from './Backdrop';
import '../../styles/auth.css';
const UNIFORM = 'The email or password is incorrect, or the account is locked. Try again or reset your password.';
const NETWORK = 'Nothing was submitted. Your email is kept. Check your connection and try again.';
const PROBE_FAILURE = { title: 'We could not reach the server', body: 'Your input is kept. Check your connection and try again.' };
type Props = {
    path: string;
    probeFailed?: boolean;
    navigate: (path: string) => void;
};
type Failure = {
    title: string;
    body?: string;
    fields?: Record<string, string>;
};
function Summary({ failure }: {
    failure: Failure | null;
}) {
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => { if (failure)
        ref.current?.focus(); }, [failure]);
    if (!failure)
        return null;
    return <div className="auth-summary" role="alert" tabIndex={-1} ref={ref}><Warning aria-hidden="true"/><strong>{failure.title}</strong>{failure.body && <p>{failure.body}</p>}{failure.fields && <ul>{Object.entries(failure.fields).map(([id, message]) => <li key={id}><a href={`#${id}`} onClick={event => { event.preventDefault(); document.getElementById(id)?.focus(); }}>{message}</a></li>)}</ul>}</div>;
}
function FieldError({ id, message }: {
    id: string;
    message?: string;
}) {
    return message ? <p id={`${id}-error`} className="auth-field-error"><XCircle aria-hidden="true"/>{message}</p> : null;
}
function Password({ value, setValue, label, error }: {
    value: string;
    setValue: (value: string) => void;
    label: string;
    error?: string;
}) {
    const [shown, setShown] = useState(false);
    return <div><label htmlFor="password">{label}</label><div className="auth-password"><input id="password" autoComplete="current-password" type={shown ? 'text' : 'password'} value={value} onChange={e => setValue(e.target.value)} aria-invalid={Boolean(error)} aria-describedby={error ? 'password-error' : undefined}/><button type="button" aria-pressed={shown} onClick={() => setShown(!shown)}>{shown ? 'Hide' : 'Show'}</button></div><FieldError id="password" message={error}/></div>;
}
function BusyButton({ busy, children }: {
    busy: boolean;
    children: ReactNode;
}) {
    return <button className="auth-primary" disabled={busy} aria-busy={busy} type="submit">{busy && <CircleNotch className="auth-spinner" aria-hidden="true"/>}{busy ? 'Signing in' : children}</button>;
}
function Qr({ uri }: {
    uri: string;
}) {
    const qr = qrcode(0, 'M');
    qr.addData(uri);
    qr.make();
    const size = qr.getModuleCount();
    const path: string[] = [];
    for (let row = 0; row < size; row++)
        for (let col = 0; col < size; col++)
            if (qr.isDark(row, col))
                path.push(`M${col + 4},${row + 4}h1v1h-1z`);
    return <svg className="auth-qr" viewBox={`0 0 ${size + 8} ${size + 8}`} role="img" aria-label="QR code for your authenticator app"><path d={path.join('')}/></svg>;
}
export default function Screens({ path, navigate, probeFailed }: Props) {
    const back = safeReturn(new URLSearchParams(window.location.search).get('return'));
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [code, setCode] = useState('');
    const [busy, setBusy] = useState(false);
    const [failure, setFailure] = useState<Failure | null>(() => new URLSearchParams(window.location.search).has('ended') ? { title: 'Your session ended', body: 'Sign in again to pick up where you left off.' } : null);
    // All enrollment material stays in component memory. Reload discards it and returns to password confirmation.
    const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
    const [confirmed, setConfirmed] = useState(false);
    const [saved, setSaved] = useState(false);
    const [notice, setNotice] = useState('');
    const isChoose = path === '/sign-in/organization';
    const isEnroll = path === '/sign-in/mfa/enroll';
    const isLogin = path === '/sign-in';
    const heading = isChoose ? 'Choose an Organization' : isLogin ? 'Sign In' : isEnroll ? confirmed ? 'Save Your Recovery Codes' : 'Secure Your Account' : 'Two-Step Verification';
    const subline = isChoose ? 'Your account belongs to more than one. Pick where to work. You can switch later from the menu.' : isLogin ? 'Use your work email and password.' : isEnroll ? confirmed ? 'Each code works once if you lose your device. We show them only now.' : 'Your organization requires a second step. It takes a minute.' : 'Enter the 6-digit code from your authenticator app.';
    async function finish() {
        const access = await probe();
        if (access === 'error') {
            setFailure({ title: 'We could not reach the server', body: 'Your input is kept. Check your connection and try again.' });
            return;
        }
        const target = destination(access, back);
        navigate(target + (access === 'verified' ? '' : `?return=${encodeURIComponent(back)}`));
    }
    async function submit(event: FormEvent) {
        event.preventDefault();
        setFailure(null);
        const fields: Record<string, string> = {};
        if (isLogin && !email)
            fields.email = 'Enter your email address.';
        if ((isLogin || (isEnroll && !enrollment)) && !password)
            fields.password = 'Enter your password.';
        if (!isLogin && (!isEnroll || enrollment) && !/^\d{6}$/.test(code))
            fields.code = 'Enter the 6-digit verification code.';
        if (Object.keys(fields).length) {
            setFailure({ title: `Fix ${Object.keys(fields).length} things to continue`, fields });
            return;
        }
        setBusy(true);
        try {
            if (!document.cookie.split("; ").some(value => value.startsWith("flo_csrf=")))
                await apiClient.GET("/api/v1/auth/csrf");
            if (isLogin) {
                const { response } = await apiClient.POST('/api/v1/auth/login', { body: { email, password }, headers: writeHeaders() });
                setPassword('');
                if (response.status !== 204) {
                    setFailure({ title: 'We could not sign you in', body: response.status === 401 ? UNIFORM : 'Something went wrong on our side. Nothing was changed and your email is kept. Try again.' });
                    return;
                }
                await finish();
            }
            else if (isEnroll && !enrollment) {
                const { response, data } = await apiClient.POST('/api/v1/auth/mfa/enroll', { body: { password }, headers: writeHeaders() });
                setPassword('');
                if (response.status === 200 && data)
                    setEnrollment(data);
                else
                    setFailure({ title: 'We could not confirm your password', body: response.status === 401 ? 'That password is not correct.' : 'Your setup was not changed. Try again.' });
            }
            else {
                const result = isEnroll ? await apiClient.POST('/api/v1/auth/mfa/confirm', { body: { code }, headers: writeHeaders() }) : await apiClient.POST('/api/v1/auth/mfa/verify', { body: { code }, headers: writeHeaders() });
                if (result.response.status !== 204) {
                    setFailure({ title: 'We could not verify your code', fields: { code: 'That code is not valid or has expired. Enter the newest code.' } });
                    return;
                }
                if (isEnroll) {
                    const access = await probe();
                    if (access === 'verified')
                        setConfirmed(true);
                    else if (access === 'error')
                        setFailure({ title: 'We could not reach the server', body: 'Check your connection and try again.' });
                    else
                        navigate(`${destination(access, back)}?return=${encodeURIComponent(back)}`);
                }
                else
                    await finish();
            }
        }
        catch {
            setFailure({ title: 'We could not reach the server', body: NETWORK });
        }
        finally {
            setPassword('');
            setBusy(false);
        }
    }
    async function copy(value: string) {
        try {
            await navigator.clipboard.writeText(value);
            setNotice('Copied.');
        }
        catch {
            setNotice('Copy did not complete. Select the text and copy it, or download the codes.');
        }
    }
    function download() {
        if (!enrollment)
            return;
        const url = URL.createObjectURL(new Blob([enrollment.recovery_codes.join('\n')], { type: 'text/plain' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = 'xlr8flo-recovery-codes.txt';
        link.click();
        URL.revokeObjectURL(url);
    }
    return <div className="auth-root"><Backdrop /><div className="auth-card"><aside className="auth-aside"><p className="auth-brand"><Buildings aria-hidden="true"/>XLR8 FLO</p><h2>One Record for Every Capital Dollar</h2><p className="auth-aside-copy">Plan, approve and buy with one traceable system of record.</p><ul className="auth-points"><li><ChartLine aria-hidden="true"/><span>Plan With Real Budgets<small>Reserved, committed and actual in one ledger.</small></span></li><li><ShieldCheck aria-hidden="true"/><span>Approve With Confidence<small>Every decision is recorded and cannot be edited.</small></span></li><li><CheckCircle aria-hidden="true"/><span>Buy Through a Completed RFQ<small>No purchase order without one.</small></span></li></ul></aside><main className="auth-main"><h1>{heading}</h1><p className="auth-subline">{subline}</p><Summary failure={failure ?? (probeFailed ? PROBE_FAILURE : null)}/>{isChoose ? <OrganizationChooser back={back} navigate={navigate} /> : confirmed && enrollment ? <div className="auth-form"><div className="auth-codes" role="list" aria-label="Recovery codes">{enrollment.recovery_codes.map(value => <span key={value} role="listitem">{value}</span>)}</div><div className="auth-actions"><button onClick={() => void copy(enrollment.recovery_codes.join('\n'))}>Copy codes</button><button onClick={download}>Download</button></div><label className="auth-check"><input type="checkbox" checked={saved} onChange={e => setSaved(e.target.checked)}/>I saved these codes somewhere safe.</label><button className="auth-primary" disabled={!saved || busy} onClick={() => void finish()}>Continue to XLR8 FLO</button></div> : <form className="auth-form" noValidate onSubmit={event => void submit(event)}>{isLogin && <div><label htmlFor="email">Email</label><input id="email" type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} aria-invalid={Boolean(failure?.fields?.email)} aria-describedby={failure?.fields?.email ? 'email-error' : undefined}/><FieldError id="email" message={failure?.fields?.email}/></div>}{(isLogin || (isEnroll && !enrollment)) && <Password value={password} setValue={setPassword} label={isLogin ? 'Password' : 'Confirm your password'} error={failure?.fields?.password}/>}{isEnroll && !enrollment && <p>We ask again before showing your setup key.</p>}{isLogin && <button className="auth-link" type="button" onClick={() => setNotice('Password reset is planned. Contact your administrator for help.')}>Forgot your password?</button>}{enrollment && <ol><li>Scan this code with an authenticator app.<Qr uri={enrollment.otpauth_uri}/><p>Or enter this key by hand</p><code className="auth-key">{enrollment.secret.match(/.{1,4}/g)?.join(' ')}</code><button type="button" onClick={() => void copy(enrollment.secret)}>Copy key</button></li><li>Enter the 6-digit code it shows.</li></ol>}{!isLogin && (!isEnroll || enrollment) && <div><label htmlFor="code">Verification code</label><input className="auth-code" id="code" inputMode="numeric" maxLength={6} autoComplete="one-time-code" autoFocus value={code} onChange={e => setCode(e.target.value)} aria-invalid={Boolean(failure?.fields?.code)} aria-describedby={failure?.fields?.code ? 'code-error' : undefined}/><FieldError id="code" message={failure?.fields?.code}/></div>}<BusyButton busy={busy}>{isLogin ? 'Sign in' : isEnroll ? enrollment ? 'Turn on and continue' : 'Continue' : 'Verify'}</BusyButton>{!isLogin && <><button className="auth-link" type="button" onClick={() => setNotice('Recovery-code sign-in is planned. Contact your administrator for help.')}>Use a recovery code</button><button className="auth-link" type="button" onClick={() => navigate(`/sign-in?return=${encodeURIComponent(back)}`)}>Back to sign in</button></>}</form>}<p role="status">{notice}</p></main></div></div>;
}
