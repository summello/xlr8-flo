import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { captureToken, clearToken, onDetour, startDetour } from './token';

describe('invitation bearer memory', () => {
    beforeEach(() => { clearToken(); vi.unstubAllGlobals(); });
    it('captures once, replaces history and survives the SPA detour only', () => {
        const location = { pathname: '/invite/disposable-fixture' };
        const replaceState = vi.fn(() => { location.pathname = '/invite'; });
        vi.stubGlobal('window', { location, history: { replaceState } });
        expect(captureToken()).toBe('disposable-fixture');
        expect(replaceState).toHaveBeenCalledExactlyOnceWith(null, '', '/invite');
        startDetour(); expect(onDetour()).toBe(true);
        location.pathname = '/sign-in';
        expect(captureToken()).toBe('disposable-fixture');
        location.pathname = '/invite';
        expect(captureToken()).toBe('disposable-fixture');
        clearToken(); expect(captureToken()).toBeNull(); expect(onDetour()).toBe(false);
    });
    it('bare route never invents a token', () => {
        vi.stubGlobal('window', { location: { pathname: '/invite' } });
        expect(captureToken()).toBeNull();
    });
    it('invitation sources use named dimensions and durations', () => {
        const directories = [new URL('.', import.meta.url), new URL('../../routes/', import.meta.url)];
        for (const directory of directories) for (const file of readdirSync(directory).filter(file => /\.(tsx|ts)$/.test(file) && !file.endsWith('.test.ts') && (directory === directories[0] || file.startsWith('invite')))) {
            expect(readFileSync(new URL(file, directory), 'utf8').match(/\b\d+(?:\.\d+)?(?:px|ms|s)\b/g) ?? [], file).toEqual([]);
        }
    });
});
