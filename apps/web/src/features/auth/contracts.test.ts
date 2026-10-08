import { readFileSync, readdirSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { apiClient } from '../../api/client';
import { probe, safeReturn } from './api';
function literals(source: string) { return source.match(/\b\d+(?:\.\d+)?(?:px|ms|s)\b/g) ?? []; }
const directory = new URL('.', import.meta.url);
describe('auth contracts', () => {
    it('uses named properties instead of raw dimensions or durations in sources', () => {
        for (const file of readdirSync(directory).filter(file => /\.(tsx|ts)$/.test(file) && !file.endsWith('.test.ts'))) {
            expect(literals(readFileSync(new URL(file, directory), 'utf8')), file).toEqual([]);
        }
    });
    it('sign-in route sources also use named properties', () => {
        const routes = new URL('../../routes/', import.meta.url);
        for (const file of readdirSync(routes).filter(file => file.startsWith('sign-in') && file.endsWith('.tsx'))) expect(literals(readFileSync(new URL(file, routes), 'utf8')), file).toEqual([]);
    });
    it('catches a planted source literal', () => {
        expect(literals('style={{height: "44px", animationDuration: "7s"}}')).toEqual(['44px', '7s']);
    });
    it('rejects open redirects including URL parser backslashes', () => {
        vi.stubGlobal('window', { location: { origin: 'https://flo.test' } });
        for (const value of ['https://evil', '//evil', 'javascript:', '/\\evil', '/sign-in'])
            expect(safeReturn(value)).toBe('/');
        expect(safeReturn('/budget?tab=1')).toBe('/budget?tab=1');
        vi.unstubAllGlobals();
    });
    it.each([
        [200, '', '', 'verified'], [401, '', '', 'signin'],
        [403, 'mfa', '/mfa-verification-required', 'verify'],
        [403, 'mfa-enroll', '/mfa-enrollment-required', 'enroll'],
        [403, 'mfa', '/other', 'error'], [500, '', '', 'error'],
    ])('probe interprets %s %s %s', async (status, header, type, expected) => {
        const mock = vi.spyOn(apiClient, 'GET').mockResolvedValue({ response: new Response(null, { status: Number(status), headers: { 'WWW-Authenticate': String(header) } }), error: { type: String(type) } } as never);
        expect(await probe()).toBe(expected);
        mock.mockRestore();
    });
});
