import { APP_RELEASE_ID } from './release';

export const SYNTHETIC_CANARY = 'API Verification Canary v1' as const;
export const REHEARSAL_PAYMENT = 'Cash R0' as const;
export const PROHIBITED_ACTION = 'Entitlement draw' as const;

export interface RehearsalCard {
  number: 1 | 2 | 3 | 4 | 5;
  console: 'PS4' | 'PS5';
  duration: string;
  expectedCount: 2 | 4 | 6 | 8 | 10;
}

export const REHEARSAL_CARDS: readonly RehearsalCard[] = [
  { number: 1, console: 'PS5', duration: '20 min', expectedCount: 2 },
  { number: 2, console: 'PS4', duration: '60 min', expectedCount: 4 },
  { number: 3, console: 'PS5', duration: '120 min', expectedCount: 6 },
  { number: 4, console: 'PS4', duration: 'Custom 45 min', expectedCount: 8 },
  { number: 5, console: 'PS5', duration: 'Custom 90 min', expectedCount: 10 },
] as const;

/**
 * The order is authoritative for local progression. Persisted completion must
 * be an exact prefix, so restored state cannot skip a physical prerequisite.
 */
export const REHEARSAL_STEP_IDS = [
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
  'prepare-release-matched',
  'prepare-loyiso',
  'prepare-canary-selected',
  'prepare-history-recorded',
  'prepare-zero-observed',
  'preoffline-time-limit-set',
  'preoffline-connectivity-offline',
  'card-1',
  'card-2',
  'card-3',
  'card-4',
  'card-5',
  'offline-closed',
  'offline-relaunched',
  'offline-ten-remains',
  'offline-no-missing-duplicate',
  'reconcile-auto-or-one-retry',
  'reconcile-zero-observed',
  'reconcile-history-five',
  'reconcile-relaunch-stable',
  'operator-log-session',
  'operator-offline-saved',
  'operator-waiting-count',
  'operator-retry-location',
  'operator-history-location',
  'operator-sign-out',
  'operator-yes',
  'operator-no-paper',
  'operator-time-limit',
] as const;

export type RehearsalStepId = (typeof REHEARSAL_STEP_IDS)[number];
export type RehearsalQueueStage =
  | 'initial'
  | 'card-1'
  | 'card-2'
  | 'card-3'
  | 'card-4'
  | 'card-5'
  | 'durability'
  | 'reconciliation';
export type RehearsalFailureReason =
  | 'unsafe-status'
  | 'initial-count'
  | 'count-above-expected'
  | 'durability-count'
  | 'remaining-after-retry';

export interface RehearsalFailure {
  stage: RehearsalQueueStage;
  reason: RehearsalFailureReason;
}

export const REHEARSAL_PROGRESS_VERSION = 1 as const;
export const REHEARSAL_STORAGE_PREFIX = 'funhouse_field_acceptance_rehearsal:v1:' as const;

/** Contains no operator, player, device, credential, identifier, or free-text data. */
export interface RehearsalProgress {
  version: typeof REHEARSAL_PROGRESS_VERSION;
  completedStepIds: RehearsalStepId[];
  elapsedMs: number;
  timerStartedAt: number | null;
  timerRunning: boolean;
  failure: RehearsalFailure | null;
}

export type RehearsalProgressLoadResult =
  | { status: 'missing' }
  | { status: 'valid'; progress: RehearsalProgress }
  | { status: 'invalid' }
  | { status: 'unavailable' };

interface RehearsalStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function emptyRehearsalProgress(): RehearsalProgress {
  return {
    version: REHEARSAL_PROGRESS_VERSION,
    completedStepIds: [],
    elapsedMs: 0,
    timerStartedAt: null,
    timerRunning: false,
    failure: null,
  };
}

export function rehearsalStorageKey(releaseId: string = APP_RELEASE_ID): string {
  return `${REHEARSAL_STORAGE_PREFIX}${releaseId}`;
}

function browserStorage(): RehearsalStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const KNOWN_STEP_IDS = new Set<string>(REHEARSAL_STEP_IDS);
const PROGRESS_FIELDS = new Set([
  'version',
  'completedStepIds',
  'elapsedMs',
  'timerStartedAt',
  'timerRunning',
  'failure',
]);
const FAILURE_FIELDS = new Set(['stage', 'reason']);
const FAILURE_REASONS_BY_STAGE: Readonly<Record<RehearsalQueueStage, readonly RehearsalFailureReason[]>> = {
  initial: ['unsafe-status', 'initial-count'],
  'card-1': ['unsafe-status', 'count-above-expected'],
  'card-2': ['unsafe-status', 'count-above-expected'],
  'card-3': ['unsafe-status', 'count-above-expected'],
  'card-4': ['unsafe-status', 'count-above-expected'],
  'card-5': ['unsafe-status', 'count-above-expected'],
  durability: ['unsafe-status', 'durability-count'],
  reconciliation: ['unsafe-status', 'remaining-after-retry'],
};

export function nextRehearsalStep(progress: Pick<RehearsalProgress, 'completedStepIds'>): RehearsalStepId | null {
  return REHEARSAL_STEP_IDS[progress.completedStepIds.length] ?? null;
}

export function activeQueueStageForProgress(
  progress: Pick<RehearsalProgress, 'completedStepIds'>,
): RehearsalQueueStage | null {
  const next = nextRehearsalStep(progress);
  if (next === 'prepare-zero-observed') return 'initial';
  if (next?.startsWith('card-')) return next as RehearsalQueueStage;
  if (next === 'offline-ten-remains') return 'durability';
  if (next === 'reconcile-auto-or-one-retry' || next === 'reconcile-zero-observed') {
    return 'reconciliation';
  }
  return null;
}

function isValidFailure(
  value: unknown,
  progress: Pick<RehearsalProgress, 'completedStepIds'>,
): value is RehearsalFailure {
  if (!isRecord(value)) return false;
  const fields = Object.keys(value);
  if (fields.length !== FAILURE_FIELDS.size || !fields.every((field) => FAILURE_FIELDS.has(field))) {
    return false;
  }
  const stage = value.stage;
  const reason = value.reason;
  if (typeof stage !== 'string' || !(stage in FAILURE_REASONS_BY_STAGE)) return false;
  if (typeof reason !== 'string') return false;
  const typedStage = stage as RehearsalQueueStage;
  if (!FAILURE_REASONS_BY_STAGE[typedStage].includes(reason as RehearsalFailureReason)) return false;
  return activeQueueStageForProgress(progress) === typedStage;
}

export function isValidRehearsalProgress(value: unknown): value is RehearsalProgress {
  if (!isRecord(value)) return false;
  const fields = Object.keys(value);
  if (fields.length !== PROGRESS_FIELDS.size || !fields.every((field) => PROGRESS_FIELDS.has(field))) {
    return false;
  }
  if (value.version !== REHEARSAL_PROGRESS_VERSION) return false;
  if (!Array.isArray(value.completedStepIds)) return false;
  if (
    !value.completedStepIds.every(
      (step, index): step is RehearsalStepId =>
        typeof step === 'string' &&
        KNOWN_STEP_IDS.has(step) &&
        step === REHEARSAL_STEP_IDS[index],
    )
  ) return false;
  if (typeof value.elapsedMs !== 'number' || !Number.isFinite(value.elapsedMs) || value.elapsedMs < 0) {
    return false;
  }
  if (
    value.timerStartedAt !== null &&
    (typeof value.timerStartedAt !== 'number' ||
      !Number.isFinite(value.timerStartedAt) ||
      value.timerStartedAt < 0)
  ) {
    return false;
  }
  if (typeof value.timerRunning !== 'boolean') return false;
  if (value.timerRunning ? value.timerStartedAt === null : value.timerStartedAt !== null) return false;
  return value.failure === null || isValidFailure(value.failure, {
    completedStepIds: value.completedStepIds,
  });
}

export function loadRehearsalProgress(
  releaseId: string = APP_RELEASE_ID,
  storage: RehearsalStorage | null = browserStorage(),
): RehearsalProgressLoadResult {
  if (!storage) return { status: 'unavailable' };
  let raw: string | null;
  try {
    raw = storage.getItem(rehearsalStorageKey(releaseId));
  } catch {
    return { status: 'unavailable' };
  }
  if (raw === null) return { status: 'missing' };
  try {
    const parsed: unknown = JSON.parse(raw);
    return isValidRehearsalProgress(parsed)
      ? { status: 'valid', progress: parsed }
      : { status: 'invalid' };
  } catch {
    return { status: 'invalid' };
  }
}

export function saveRehearsalProgress(
  progress: RehearsalProgress,
  releaseId: string = APP_RELEASE_ID,
  storage: RehearsalStorage | null = browserStorage(),
): boolean {
  if (!storage || !isValidRehearsalProgress(progress)) return false;
  try {
    storage.setItem(rehearsalStorageKey(releaseId), JSON.stringify(progress));
    return true;
  } catch {
    return false;
  }
}

export function clearRehearsalProgress(
  releaseId: string = APP_RELEASE_ID,
  storage: RehearsalStorage | null = browserStorage(),
): boolean {
  if (!storage) return false;
  try {
    storage.removeItem(rehearsalStorageKey(releaseId));
    return true;
  } catch {
    return false;
  }
}

/** Reconstruct elapsed time without mutating persisted progress. */
export function elapsedRehearsalMs(progress: RehearsalProgress, now: number = Date.now()): number {
  if (!progress.timerRunning || progress.timerStartedAt === null) return progress.elapsedMs;
  return progress.elapsedMs + Math.max(0, now - progress.timerStartedAt);
}

export interface QueueObservation {
  loading: boolean;
  unsyncedCount: number;
  rejectedCount: number;
  blockedCount: number;
  quarantinedCount: number;
  stale: boolean;
}

export type QueueEvaluationState = 'loading' | 'pass' | 'wait' | 'stop';

export interface QueueEvaluation {
  state: QueueEvaluationState;
  message: string;
  stopReason?: RehearsalFailureReason;
}

function evaluateUnsafeSyncStatus(observation: QueueObservation): QueueEvaluation | null {
  const warningCount =
    observation.rejectedCount + observation.blockedCount + observation.quarantinedCount;
  if (observation.stale || warningCount > 0) {
    return {
      state: 'stop',
      stopReason: 'unsafe-status',
      message: 'STOP — observed sync status is unsafe: stale, rejected, blocked, or quarantined status is present. Do not continue.',
    };
  }
  return null;
}

/** Pure, payload-free evaluation of an observed positive queue checkpoint. */
export function evaluateQueueCheckpoint(
  observation: QueueObservation,
  expectedCount: number,
): QueueEvaluation {
  const unsafe = evaluateUnsafeSyncStatus(observation);
  if (unsafe) return unsafe;
  if (observation.loading) {
    return { state: 'loading', message: 'WAIT — observed counts are still loading.' };
  }
  if (observation.unsyncedCount === expectedCount) {
    return {
      state: 'pass',
      message: `PASS — observed waiting count is exactly ${expectedCount} with no warnings or stale status.`,
    };
  }
  if (observation.unsyncedCount > expectedCount) {
    return {
      state: 'stop',
      stopReason: 'count-above-expected',
      message: `STOP — observed waiting count ${observation.unsyncedCount} is above expected ${expectedCount}. Do not add another capture.`,
    };
  }
  return {
    state: 'wait',
    message: `WAIT — observed waiting count is ${observation.unsyncedCount}; expected exactly ${expectedCount}.`,
  };
}

/** Initial preparation requires an exactly empty, current queue before going offline. */
export function evaluateInitialQueuePreparation(
  observation: QueueObservation,
): QueueEvaluation {
  const unsafe = evaluateUnsafeSyncStatus(observation);
  if (unsafe) return unsafe;
  if (observation.loading) {
    return { state: 'loading', message: 'WAIT — initial observed counts are still loading.' };
  }
  if (observation.unsyncedCount === 0) {
    return {
      state: 'pass',
      message: 'PASS — initial waiting count is exactly 0 with no warnings or stale status.',
    };
  }
  return {
    state: 'stop',
    stopReason: 'initial-count',
    message: `STOP — initial waiting count must be exactly 0; observed ${observation.unsyncedCount}. Do not continue.`,
  };
}

/** After offline relaunch, every loaded count other than exactly ten is a STOP. */
export function evaluateDurabilityQueue(observation: QueueObservation): QueueEvaluation {
  const unsafe = evaluateUnsafeSyncStatus(observation);
  if (unsafe) return unsafe;
  if (observation.loading) {
    return { state: 'loading', message: 'WAIT — post-relaunch counts are still loading.' };
  }
  if (observation.unsyncedCount === 10) {
    return {
      state: 'pass',
      message: 'PASS — observed waiting count remained exactly 10 after relaunch with no warnings or stale status.',
    };
  }
  return {
    state: 'stop',
    stopReason: 'durability-count',
    message: `STOP — observed waiting count after relaunch is ${observation.unsyncedCount}; it must remain exactly 10. Preserve state and do not continue.`,
  };
}

/** Final reconciliation may wait during automatic sync, but one acknowledged retry is final. */
export function evaluateReconciliationQueue(
  observation: QueueObservation,
  retryAcknowledged: boolean,
): QueueEvaluation {
  const unsafe = evaluateUnsafeSyncStatus(observation);
  if (unsafe) return unsafe;
  if (observation.loading) {
    return { state: 'loading', message: 'WAIT — reconciliation counts are still loading.' };
  }
  if (observation.unsyncedCount === 0) {
    return {
      state: 'pass',
      message: 'PASS — final waiting count is exactly 0 with no warnings or stale status.',
    };
  }
  if (retryAcknowledged) {
    return {
      state: 'stop',
      stopReason: 'remaining-after-retry',
      message: `STOP — ${observation.unsyncedCount} item(s) remain waiting after automatic sync or the one allowed retry. Do not continue.`,
    };
  }
  return {
    state: 'wait',
    message: `WAIT — ${observation.unsyncedCount} item(s) remain while automatic sync is in progress.`,
  };
}
