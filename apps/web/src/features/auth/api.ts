import { apiClient } from '../../api/client';
import type { components } from '../../api/generated/schema';
export type Enrollment = components['schemas']['MfaEnrollmentResponse'];
export type Session = components['schemas']['SessionResponse'];
export type Access = 'verified' | 'signin' | 'verify' | 'enroll' | 'error';
export function safeReturn(value: string | null): string {
    if (!value || !value.startsWith('/') || value.startsWith('//') || (value.includes('\\') || [...value].some(char => char.charCodeAt(0) <= 32)))
        return '/';
    const parsed = new URL(value, window.location.origin);
    return parsed.origin === window.location.origin && !parsed.pathname.startsWith('/sign-in') ? parsed.pathname + parsed.search + parsed.hash : '/';
}
export async function probe(): Promise<Access> {
    try {
        const { response, error } = await apiClient.GET('/api/v1/auth/sessions');
        if (response.status === 200)
            return 'verified';
        if (response.status === 401)
            return 'signin';
        if (response.status === 403 && response.headers.get('WWW-Authenticate') === 'mfa' && error?.type?.endsWith('/mfa-verification-required'))
            return 'verify';
        if (response.status === 403 && response.headers.get('WWW-Authenticate') === 'mfa-enroll' && error?.type?.endsWith('/mfa-enrollment-required'))
            return 'enroll';
    }
    catch { /* Transport failure is never evidence of authentication. */ }
    return 'error';
}
export function writeHeaders() {
    const csrf = document.cookie.split('; ').find(value => value.startsWith('flo_csrf='))?.slice('flo_csrf='.length) ?? '';
    return { 'X-CSRF-Token': decodeURIComponent(csrf), 'Idempotency-Key': crypto.randomUUID() };
}
export function destination(access: Access, back: string): string {
    return access === 'verified' ? back : access === 'verify' ? '/sign-in/mfa' : access === 'enroll' ? '/sign-in/mfa/enroll' : '/sign-in';
}
