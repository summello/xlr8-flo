import { apiClient } from '../../api/client';
import type { components } from '../../api/generated/schema';
import { writeHeaders } from '../auth/api';
export type Invitation = components['schemas']['InvitationValid'];
export type Expired = components['schemas']['InvitationExpired'];
export type Acceptance = components['schemas']['InvitationAccept'];
export async function lookup(token: string) {
    return apiClient.GET('/api/v1/invitations/by-token', { headers: { 'X-Invitation-Token': token } });
}
export async function accept(token: string, body: Acceptance, key: string) {
    if (!document.cookie.split('; ').some(value => value.startsWith('flo_csrf=')))
        await apiClient.GET('/api/v1/auth/csrf');
    return apiClient.POST('/api/v1/invitations/accept', { body, headers: { ...writeHeaders(), 'Idempotency-Key': key, 'X-Invitation-Token': token } });
}
