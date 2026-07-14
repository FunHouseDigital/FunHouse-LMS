import { describe, it, expect } from 'vitest';

// Trivial smoke test to prove Vitest + TypeScript + jsdom + fake-indexeddb
// are wired up correctly (tasks.md task 1.1).
describe('toolchain smoke test', () => {
  it('runs a trivial assertion', () => {
    expect(1 + 1).toBe(2);
  });

  it('has a jsdom document available', () => {
    expect(typeof document).toBe('object');
    expect(document.createElement('div')).toBeTruthy();
  });

  it('has fake-indexeddb installed on the global scope', () => {
    expect(typeof indexedDB).toBe('object');
  });
});
