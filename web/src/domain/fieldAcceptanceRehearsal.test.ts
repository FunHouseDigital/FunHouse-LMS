import { describe, expect, it } from 'vitest';
import {
  PROHIBITED_ACTION,
  REHEARSAL_CARDS,
  REHEARSAL_PAYMENT,
  REHEARSAL_STEP_IDS,
  REHEARSAL_STORAGE_PREFIX,
  SYNTHETIC_CANARY,
  activeQueueStageForProgress,
  clearRehearsalProgress,
  elapsedRehearsalMs,
  emptyRehearsalProgress,
  evaluateDurabilityQueue,
  evaluateInitialQueuePreparation,
  evaluateQueueCheckpoint,
  evaluateReconciliationQueue,
  loadRehearsalProgress,
  nextRehearsalStep,
  rehearsalStorageKey,
  saveRehearsalProgress,
  type RehearsalProgress,
  type RehearsalStepId,
} from './fieldAcceptanceRehearsal';

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

const cleanObservation = {
  loading: false,
  unsyncedCount: 0,
  rejectedCount: 0,
  blockedCount: 0,
  quarantinedCount: 0,
  stale: false,
};

function prefixThrough(id: RehearsalStepId): RehearsalStepId[] {
  return REHEARSAL_STEP_IDS.slice(0, REHEARSAL_STEP_IDS.indexOf(id) + 1);
}

describe('field acceptance rehearsal domain', () => {
  it('defines the exact synthetic safety constants and five cards/checkpoints', () => {
    expect(SYNTHETIC_CANARY).toBe('API Verification Canary v1');
    expect(REHEARSAL_PAYMENT).toBe('Cash R0');
    expect(PROHIBITED_ACTION).toBe('Entitlement draw');
    expect(REHEARSAL_CARDS).toEqual([
      { number: 1, console: 'PS5', duration: '20 min', expectedCount: 2 },
      { number: 2, console: 'PS4', duration: '60 min', expectedCount: 4 },
      { number: 3, console: 'PS5', duration: '120 min', expectedCount: 6 },
      { number: 4, console: 'PS4', duration: 'Custom 45 min', expectedCount: 8 },
      { number: 5, console: 'PS5', duration: 'Custom 90 min', expectedCount: 10 },
    ]);
    expect(REHEARSAL_STEP_IDS).toEqual(expect.arrayContaining([
      'install-clean-install-completed',
      'install-existing-upgrade-completed-or-na',
      'install-launched-from-icon',
      'install-release-matched',
      'install-reopened-same-release',
      'install-loyiso-manager-navigation',
      'install-loyiso-signout-cleared',
      'install-aya-founder-navigation-manager-absent',
      'install-aya-signout-cleared',
      'install-loyiso-returned-manager-founder-absent',
      'preoffline-time-limit-set',
      'preoffline-connectivity-offline',
    ]));
    expect(REHEARSAL_STEP_IDS).not.toContain('offline-connectivity-off');
  });

  it('uses the exact release-scoped storage prefix', () => {
    expect(REHEARSAL_STORAGE_PREFIX).toBe('funhouse_field_acceptance_rehearsal:v1:');
    expect(rehearsalStorageKey('abc1234')).toBe(
      'funhouse_field_acceptance_rehearsal:v1:abc1234',
    );
    expect(rehearsalStorageKey('release-a')).not.toBe(rehearsalStorageKey('release-b'));
  });

  it('round-trips valid progress and rejects unknown, skipped, or invalid fields', () => {
    const storage = new MemoryStorage();
    const progress: RehearsalProgress = {
      ...emptyRehearsalProgress(),
      completedStepIds: prefixThrough('prepare-release-matched'),
      elapsedMs: 1234,
    };
    expect(saveRehearsalProgress(progress, 'release', storage)).toBe(true);
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'valid', progress });

    storage.setItem(rehearsalStorageKey('release'), JSON.stringify({
      ...progress,
      completedStepIds: ['unknown-step'],
    }));
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'invalid' });

    storage.setItem(rehearsalStorageKey('release'), JSON.stringify({
      ...progress,
      completedStepIds: ['install-clean-install-completed', 'prepare-zero-observed'],
    }));
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'invalid' });

    storage.setItem(rehearsalStorageKey('release'), JSON.stringify({
      ...progress,
      elapsedMs: -1,
    }));
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'invalid' });

    storage.setItem(rehearsalStorageKey('release'), JSON.stringify({
      ...progress,
      notes: 'must never survive validation',
    }));
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'invalid' });
  });

  it('distinguishes missing, invalid, and unavailable storage without overwriting', () => {
    const storage = new MemoryStorage();
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'missing' });

    storage.setItem(rehearsalStorageKey('release'), '{not-json');
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'invalid' });
    expect(storage.getItem(rehearsalStorageKey('release'))).toBe('{not-json');

    storage.setItem(rehearsalStorageKey('release'), JSON.stringify({
      ...emptyRehearsalProgress(),
      version: 2,
    }));
    expect(loadRehearsalProgress('release', storage)).toEqual({ status: 'invalid' });

    const denied = {
      getItem: () => { throw new DOMException('Denied', 'SecurityError'); },
      setItem: () => { throw new DOMException('Denied', 'SecurityError'); },
      removeItem: () => { throw new DOMException('Denied', 'SecurityError'); },
    };
    expect(loadRehearsalProgress('release', denied)).toEqual({ status: 'unavailable' });
    expect(loadRehearsalProgress('release', null)).toEqual({ status: 'unavailable' });
    expect(saveRehearsalProgress(emptyRehearsalProgress(), 'release', denied)).toBe(false);
    expect(clearRehearsalProgress('release', denied)).toBe(false);
  });

  it('enforces an exact prerequisite prefix and derives only the active queue stage', () => {
    const progress = emptyRehearsalProgress();
    expect(nextRehearsalStep(progress)).toBe('install-clean-install-completed');
    expect(activeQueueStageForProgress(progress)).toBeNull();

    progress.completedStepIds = REHEARSAL_STEP_IDS.slice(
      0,
      REHEARSAL_STEP_IDS.indexOf('prepare-zero-observed'),
    );
    expect(nextRehearsalStep(progress)).toBe('prepare-zero-observed');
    expect(activeQueueStageForProgress(progress)).toBe('initial');

    progress.completedStepIds = REHEARSAL_STEP_IDS.slice(
      0,
      REHEARSAL_STEP_IDS.indexOf('offline-ten-remains'),
    );
    expect(activeQueueStageForProgress(progress)).toBe('durability');
  });

  it('accepts only a payload-free STOP marker matching the active stage', () => {
    const storage = new MemoryStorage();
    const progress: RehearsalProgress = {
      ...emptyRehearsalProgress(),
      completedStepIds: REHEARSAL_STEP_IDS.slice(
        0,
        REHEARSAL_STEP_IDS.indexOf('offline-ten-remains'),
      ),
      failure: { stage: 'durability', reason: 'durability-count' },
    };
    expect(saveRehearsalProgress(progress, 'release', storage)).toBe(true);

    expect(saveRehearsalProgress({
      ...progress,
      failure: { stage: 'initial', reason: 'initial-count' },
    }, 'wrong-stage', storage)).toBe(false);
    expect(saveRehearsalProgress({
      ...progress,
      failure: { stage: 'durability', reason: 'remaining-after-retry' },
    }, 'wrong-reason', storage)).toBe(false);
  });

  it('reconstructs elapsed time for a running timer after relaunch', () => {
    const running: RehearsalProgress = {
      ...emptyRehearsalProgress(),
      elapsedMs: 2500,
      timerRunning: true,
      timerStartedAt: 10_000,
    };
    expect(elapsedRehearsalMs(running, 14_250)).toBe(6750);
    expect(elapsedRehearsalMs(running, 9000)).toBe(2500);
    expect(elapsedRehearsalMs({ ...running, timerRunning: false, timerStartedAt: null }, 99_000)).toBe(2500);
  });

  it('clears only the selected release rehearsal key', () => {
    const storage = new MemoryStorage();
    storage.setItem('funhouse_session_active', 'keep-auth-marker');
    storage.setItem('unrelated-cache', 'keep-cache');
    saveRehearsalProgress(emptyRehearsalProgress(), 'release-a', storage);
    saveRehearsalProgress(emptyRehearsalProgress(), 'release-b', storage);

    expect(clearRehearsalProgress('release-a', storage)).toBe(true);
    expect(storage.getItem(rehearsalStorageKey('release-a'))).toBeNull();
    expect(storage.getItem(rehearsalStorageKey('release-b'))).not.toBeNull();
    expect(storage.getItem('funhouse_session_active')).toBe('keep-auth-marker');
    expect(storage.getItem('unrelated-cache')).toBe('keep-cache');
  });

  it('never passes while loading and passes only the exact warning-free count', () => {
    expect(evaluateQueueCheckpoint({ ...cleanObservation, loading: true }, 0).state).toBe('loading');
    expect(evaluateQueueCheckpoint({ ...cleanObservation, loading: true, unsyncedCount: 2 }, 2).state).not.toBe('pass');
    expect(evaluateQueueCheckpoint({ ...cleanObservation, unsyncedCount: 4 }, 4).state).toBe('pass');
    expect(evaluateQueueCheckpoint({ ...cleanObservation, unsyncedCount: 2 }, 4).state).toBe('wait');
    expect(evaluateQueueCheckpoint({ ...cleanObservation, unsyncedCount: 6 }, 4)).toMatchObject({
      state: 'stop',
      stopReason: 'count-above-expected',
    });
  });

  it.each([
    ['rejected', { rejectedCount: 1 }],
    ['blocked', { blockedCount: 1 }],
    ['quarantined', { quarantinedCount: 1 }],
    ['stale', { stale: true }],
  ])('returns STOP for any %s status, including at an exact count', (_label, warning) => {
    const result = evaluateQueueCheckpoint({
      ...cleanObservation,
      unsyncedCount: 2,
      ...warning,
    }, 2);
    expect(result).toMatchObject({ state: 'stop', stopReason: 'unsafe-status' });
    expect(result.message).toMatch(/^STOP/);
  });

  it('makes any nonzero initial count an immediate STOP and never passes while loading', () => {
    expect(evaluateInitialQueuePreparation(cleanObservation).state).toBe('pass');
    expect(evaluateInitialQueuePreparation({ ...cleanObservation, unsyncedCount: 1 })).toMatchObject({
      state: 'stop',
      stopReason: 'initial-count',
    });
    expect(evaluateInitialQueuePreparation({ ...cleanObservation, loading: true }).state).toBe('loading');
    expect(evaluateInitialQueuePreparation({ ...cleanObservation, stale: true }).state).toBe('stop');
  });

  it('treats every loaded post-relaunch count other than ten as STOP', () => {
    expect(evaluateDurabilityQueue({ ...cleanObservation, loading: true }).state).toBe('loading');
    expect(evaluateDurabilityQueue({ ...cleanObservation, unsyncedCount: 10 }).state).toBe('pass');
    for (const unsyncedCount of [0, 8, 11]) {
      expect(evaluateDurabilityQueue({ ...cleanObservation, unsyncedCount })).toMatchObject({
        state: 'stop',
        stopReason: 'durability-count',
      });
    }
    expect(evaluateDurabilityQueue({ ...cleanObservation, unsyncedCount: 10, stale: true }).state).toBe('stop');
  });

  it('waits during automatic reconciliation, then stops remaining work after acknowledgement', () => {
    const remaining = { ...cleanObservation, unsyncedCount: 1 };
    expect(evaluateReconciliationQueue(remaining, false).state).toBe('wait');
    expect(evaluateReconciliationQueue(remaining, true)).toMatchObject({
      state: 'stop',
      stopReason: 'remaining-after-retry',
    });
    expect(evaluateReconciliationQueue(cleanObservation, true).state).toBe('pass');
    expect(evaluateReconciliationQueue({ ...remaining, loading: true }, true).state).toBe('loading');
  });

  it.each([
    { rejectedCount: 1 },
    { blockedCount: 1 },
    { quarantinedCount: 1 },
    { stale: true },
  ])('always stops reconciliation for warning or stale status: %o', (unsafe) => {
    expect(evaluateReconciliationQueue({ ...cleanObservation, ...unsafe }, false).state).toBe('stop');
  });
});
