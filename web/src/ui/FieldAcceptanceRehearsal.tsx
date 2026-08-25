import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  PROHIBITED_ACTION,
  REHEARSAL_CARDS,
  REHEARSAL_PAYMENT,
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
  saveRehearsalProgress,
  type QueueEvaluation,
  type RehearsalFailure,
  type RehearsalProgress,
  type RehearsalProgressLoadResult,
  type RehearsalStepId,
} from '../domain/fieldAcceptanceRehearsal';
import { APP_RELEASE_ID } from '../domain/release';
import { useSyncStatus } from '../state/syncState';

interface CheckItem {
  id: RehearsalStepId;
  label: string;
}

const INSTALL_AND_ACCOUNT_CHECKS: readonly CheckItem[] = [
  {
    id: 'install-clean-install-completed',
    label: 'A clean Install or Add to Home Screen was available and completed on a fresh browser profile or device.',
  },
  {
    id: 'install-existing-upgrade-completed-or-na',
    label: 'An earlier installed app upgraded to this Release, or N/A was confirmed only because no prior FunHouse PWA installation exists anywhere in the rollout.',
  },
  { id: 'install-launched-from-icon', label: 'Launched FunHouse Revenue from its installed icon.' },
  { id: 'install-release-matched', label: `Visible Release matched ${APP_RELEASE_ID}.` },
  { id: 'install-reopened-same-release', label: 'Fully closed and removed the app from recent apps, then reopened it to the same Release.' },
  { id: 'install-loyiso-manager-navigation', label: 'Signed in as Loyiso and manager navigation appeared.' },
  { id: 'install-loyiso-signout-cleared', label: 'Signed out of Loyiso; no protected or stale account screen remained.' },
  { id: 'install-aya-founder-navigation-manager-absent', label: 'Signed in as Aya; founder navigation appeared and manager-only navigation was absent.' },
  { id: 'install-aya-signout-cleared', label: 'Signed out of Aya; no protected or stale account screen remained.' },
  { id: 'install-loyiso-returned-manager-founder-absent', label: 'Signed back in as Loyiso; manager navigation returned and founder-only navigation was absent.' },
];

const PREPARATION_CHECKS: readonly CheckItem[] = [
  { id: 'prepare-release-matched', label: 'Visible release matched the accepted candidate.' },
  { id: 'prepare-loyiso', label: 'Signed in as Loyiso while online.' },
  { id: 'prepare-canary-selected', label: `Selected only ${SYNTHETIC_CANARY}.` },
  { id: 'prepare-history-recorded', label: 'Existing session count was recorded externally in the authoritative acceptance record.' },
  { id: 'prepare-zero-observed', label: 'Observed the exact up-to-date status, zero waiting, and no warnings.' },
];

const PRE_OFFLINE_CHECKS: readonly CheckItem[] = [
  { id: 'preoffline-time-limit-set', label: 'Maximum acceptable time was set externally before testing.' },
  { id: 'preoffline-connectivity-offline', label: 'Wi-Fi and mobile data were physically off (or flight mode was enabled), and the device reported offline.' },
];

const OFFLINE_CHECKS: readonly CheckItem[] = [
  { id: 'offline-closed', label: 'The installed app was fully closed and removed from recent apps.' },
  { id: 'offline-relaunched', label: 'The app was relaunched from its installed icon while still offline.' },
  { id: 'offline-ten-remains', label: 'The observed waiting count remained exactly 10 after relaunch.' },
  { id: 'offline-no-missing-duplicate', label: 'No saved capture was missing or duplicated.' },
];

const RECONCILE_CHECKS: readonly CheckItem[] = [
  { id: 'reconcile-auto-or-one-retry', label: 'Automatic sync completed, or Retry sync was used no more than once through the existing sync surface.' },
  { id: 'reconcile-zero-observed', label: 'Observed zero waiting, the exact up-to-date status, and no warnings.' },
  { id: 'reconcile-history-five', label: 'History was checked externally and increased by exactly five with the expected values.' },
  { id: 'reconcile-relaunch-stable', label: 'After another online relaunch, history did not increase again.' },
];

const OPERATOR_CHECKS: readonly CheckItem[] = [
  { id: 'operator-log-session', label: 'Operator showed where a new lounge session is logged.' },
  { id: 'operator-offline-saved', label: 'Operator showed how to recognise that work is saved offline.' },
  { id: 'operator-waiting-count', label: 'Operator showed where to see items waiting to sync.' },
  { id: 'operator-retry-location', label: 'Operator showed where to retry sync after connectivity returns.' },
  { id: 'operator-history-location', label: `Operator showed where to find ${SYNTHETIC_CANARY} history.` },
  { id: 'operator-sign-out', label: 'Operator showed how to sign out safely.' },
  { id: 'operator-yes', label: 'Operator answered YES: this can replace paper without slowing the queue.' },
  { id: 'operator-no-paper', label: 'No paper fallback or developer assistance was used.' },
  { id: 'operator-time-limit', label: 'All five captures completed within the maximum set before testing.' },
];

function formatElapsed(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':');
}

function QueueCheck({
  evaluation,
  announceStop = false,
}: {
  evaluation: QueueEvaluation;
  announceStop?: boolean;
}) {
  return (
    <p
      className="field-acceptance-queue-check"
      data-state={evaluation.state}
      role={evaluation.state === 'stop' && announceStop ? 'alert' : undefined}
    >
      {evaluation.message}
    </p>
  );
}

function initialProgress(load: RehearsalProgressLoadResult): RehearsalProgress {
  return load.status === 'valid' ? load.progress : emptyRehearsalProgress();
}

function latchedEvaluation(failure: RehearsalFailure): QueueEvaluation {
  return {
    state: 'stop',
    message: `STOP — this run recorded ${failure.reason} at ${failure.stage}. Later readings cannot clear the failure; preserve state or use the confirmed reset to begin a new rehearsal.`,
  };
}

export function FieldAcceptanceRehearsal() {
  const syncStatus = useSyncStatus();
  const [loadResult] = useState<RehearsalProgressLoadResult>(() => loadRehearsalProgress());
  const [storageStatus, setStorageStatus] = useState<RehearsalProgressLoadResult['status']>(loadResult.status);
  const [progress, setProgress] = useState<RehearsalProgress>(() => initialProgress(loadResult));
  const [clockNow, setClockNow] = useState(() => Date.now());
  const [confirmingReset, setConfirmingReset] = useState(false);
  const persistedProgress = useRef(progress);
  const skipNextSave = useRef(false);

  const storageBlocked = storageStatus === 'invalid' || storageStatus === 'unavailable';
  const runStopped = progress.failure !== null;

  useEffect(() => {
    if (storageBlocked) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    if (saveRehearsalProgress(progress)) {
      persistedProgress.current = progress;
      setStorageStatus('valid');
      return;
    }
    setStorageStatus('unavailable');
    if (progress !== persistedProgress.current) {
      skipNextSave.current = true;
      setProgress(persistedProgress.current);
    }
  }, [progress, storageBlocked]);

  useEffect(() => {
    if (!progress.timerRunning) return undefined;
    setClockNow(Date.now());
    const timer = window.setInterval(() => setClockNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [progress.timerRunning]);

  const observation = useMemo(() => ({
    loading: syncStatus.loading,
    unsyncedCount: syncStatus.unsyncedCount,
    rejectedCount: syncStatus.rejected.length,
    blockedCount: syncStatus.blockedCount,
    quarantinedCount: syncStatus.quarantinedCount,
    stale: syncStatus.stale,
  }), [syncStatus.blockedCount, syncStatus.loading, syncStatus.quarantinedCount, syncStatus.rejected.length, syncStatus.stale, syncStatus.unsyncedCount]);

  const initialEvaluation = evaluateInitialQueuePreparation(observation);
  const durabilityEvaluation = evaluateDurabilityQueue(observation);
  const retryAcknowledged = progress.completedStepIds.includes('reconcile-auto-or-one-retry');
  const reconciliationEvaluation = evaluateReconciliationQueue(observation, retryAcknowledged);
  const nextStep = nextRehearsalStep(progress);
  const activeQueueStage = activeQueueStageForProgress(progress);
  const activeQueueEvaluation = useMemo<QueueEvaluation | null>(() => {
    if (activeQueueStage === 'initial') return initialEvaluation;
    if (activeQueueStage === 'durability') return durabilityEvaluation;
    if (activeQueueStage === 'reconciliation') return reconciliationEvaluation;
    if (activeQueueStage?.startsWith('card-')) {
      const card = REHEARSAL_CARDS.find((candidate) => `card-${candidate.number}` === activeQueueStage);
      return card ? evaluateQueueCheckpoint(observation, card.expectedCount) : null;
    }
    return null;
  }, [activeQueueStage, durabilityEvaluation, initialEvaluation, observation, reconciliationEvaluation]);

  useEffect(() => {
    if (
      storageStatus !== 'valid' ||
      progress.failure !== null ||
      activeQueueStage === null ||
      activeQueueEvaluation?.state !== 'stop' ||
      activeQueueEvaluation.stopReason === undefined
    ) return;
    const failure: RehearsalFailure = {
      stage: activeQueueStage,
      reason: activeQueueEvaluation.stopReason,
    };
    setProgress((current) => current.failure === null ? { ...current, failure } : current);
  }, [activeQueueEvaluation, activeQueueStage, progress.failure, storageStatus]);

  const toggleStep = useCallback((id: RehearsalStepId) => {
    if (storageBlocked || runStopped || id !== nextStep) return;
    setProgress((current) => ({
      ...current,
      completedStepIds: [...current.completedStepIds, id],
    }));
  }, [nextStep, runStopped, storageBlocked]);

  const queueStepPasses = (id: RehearsalStepId): boolean => {
    if (id === 'prepare-zero-observed') return initialEvaluation.state === 'pass';
    if (id === 'offline-ten-remains') return durabilityEvaluation.state === 'pass';
    if (id === 'reconcile-zero-observed') return reconciliationEvaluation.state === 'pass';
    if (id.startsWith('card-')) {
      const card = REHEARSAL_CARDS.find((candidate) => `card-${candidate.number}` === id);
      return card !== undefined && evaluateQueueCheckpoint(observation, card.expectedCount).state === 'pass';
    }
    return true;
  };

  const stepDisabled = (id: RehearsalStepId): boolean =>
    storageBlocked ||
    runStopped ||
    progress.completedStepIds.includes(id) ||
    id !== nextStep ||
    !queueStepPasses(id);

  const renderChecks = (items: readonly CheckItem[]) => items.map((item) => (
    <label className="field-acceptance-check-row" key={item.id}>
      <input
        type="checkbox"
        checked={progress.completedStepIds.includes(item.id)}
        disabled={stepDisabled(item.id)}
        onChange={() => toggleStep(item.id)}
      />
      <span>{item.label}</span>
    </label>
  ));

  const timerReady = progress.completedStepIds.includes('preoffline-connectivity-offline');

  const startTimer = () => {
    if (progress.timerRunning || storageBlocked || runStopped || !timerReady) return;
    const now = Date.now();
    setClockNow(now);
    setProgress((current) => ({ ...current, timerRunning: true, timerStartedAt: now }));
  };

  const pauseTimer = () => {
    if (storageBlocked || runStopped) return;
    const now = Date.now();
    setProgress((current) => ({
      ...current,
      elapsedMs: elapsedRehearsalMs(current, now),
      timerRunning: false,
      timerStartedAt: null,
    }));
    setClockNow(now);
  };

  const resetTimer = () => {
    if (storageBlocked || runStopped) return;
    setProgress((current) => ({
      ...current,
      elapsedMs: 0,
      timerRunning: false,
      timerStartedAt: null,
    }));
    setClockNow(Date.now());
  };

  const resetAll = () => {
    if (!clearRehearsalProgress()) {
      setStorageStatus('unavailable');
      setConfirmingReset(false);
      return;
    }
    const resetProgress = emptyRehearsalProgress();
    if (!saveRehearsalProgress(resetProgress)) {
      setStorageStatus('unavailable');
      setConfirmingReset(false);
      return;
    }
    skipNextSave.current = true;
    persistedProgress.current = resetProgress;
    setProgress(resetProgress);
    setClockNow(Date.now());
    setStorageStatus('valid');
    setConfirmingReset(false);
  };

  const elapsed = elapsedRehearsalMs(progress, clockNow);
  const visibleActiveEvaluation = progress.failure
    ? latchedEvaluation(progress.failure)
    : activeQueueEvaluation;
  const initialCheckpointAcknowledged = progress.completedStepIds.includes('prepare-zero-observed');
  const durabilityCheckpointAcknowledged = progress.completedStepIds.includes('offline-ten-remains');
  const reconciliationCheckpointAcknowledged = progress.completedStepIds.includes('reconcile-zero-observed');

  return (
    <section aria-label="Field acceptance rehearsal" data-screen-body="field-acceptance">
      <h1>Field acceptance rehearsal</h1>
      <p className="screen-intro">
        A read-only convenience guide for the authoritative Phase 1 physical-device rehearsal. It observes only payload-free queue counts and never captures, syncs, retries sync, or decides GO.
      </p>

      <div className="field-acceptance-safety" role="note" aria-label="Synthetic-only safety rules">
        <h2>Synthetic only — stop before real learner data</h2>
        <p>
          Use exactly <strong>{SYNTHETIC_CANARY}</strong>, <strong>{REHEARSAL_PAYMENT}</strong>, and never <strong>{PROHIBITED_ACTION}</strong>. Never use real learner data.
        </p>
        <p>
          This assistant stores no credentials, learner names, player identifiers, contact details, screenshots, or free text.
        </p>
      </div>

      <div className="field-acceptance-safety" role="note" aria-label="Critical stop rules">
        <h2>Critical STOP rules</h2>
        <p>Stop immediately and preserve state if any of these occurs:</p>
        <ul>
          <li>login or storage failure;</li>
          <li>the installed app cannot open offline;</li>
          <li>a missing, duplicate, or wrong-account capture;</li>
          <li>protected data crosses an account boundary;</li>
          <li>the queue does not survive relaunch;</li>
          <li>any rejected, blocked, quarantined, or stale status, or anything still waiting after one reconnect and one retry;</li>
          <li>a history mismatch;</li>
          <li>the operator needs paper or developer help; or</li>
          <li>an unresolved security prerequisite.</li>
        </ul>
        <p><strong>Do not repeat captures or repair production rows.</strong></p>
      </div>

      <div className="field-acceptance-release" role="note">
        <strong>Release <code>{APP_RELEASE_ID}</code></strong>
        <span>This must match the accepted candidate. Local builds may say <code>local</code>.</span>
      </div>

      <div className="field-acceptance-restored-warning" role="note">
        Restored checklist state is convenience only. It is not authoritative evidence, cannot prove GO, and does not replace the field-acceptance checklist or founder sign-off.
      </div>

      {storageBlocked ? (
        <p className="field-acceptance-queue-check" data-state="stop" role="alert">
          {storageStatus === 'invalid'
            ? 'STOP — stored rehearsal progress is invalid or incompatible. It has not been overwritten. Preserve state or use the confirmed reset to begin a new rehearsal.'
            : 'STOP — rehearsal progress storage is unavailable. Preserve state, do not continue, and resolve the storage failure.'}
        </p>
      ) : progress.failure ? (
        <p className="field-acceptance-queue-check" data-state="stop" role="alert">
          {latchedEvaluation(progress.failure).message}
        </p>
      ) : null}

      <section className="field-acceptance-panel" aria-labelledby="install-account-heading">
        <h2 id="install-account-heading">1. Install, relaunch, and verify account transitions</h2>
        <p>Complete each check in order on the physical device before online rehearsal preparation.</p>
        <div className="field-acceptance-checks">{renderChecks(INSTALL_AND_ACCOUNT_CHECKS)}</div>
      </section>

      <section className="field-acceptance-panel" aria-labelledby="observed-counts-heading">
        <h2 id="observed-counts-heading">Observed sync counts</h2>
        <p>Read-only values from the current account’s durable sync status. Refresh does not retry or trigger sync.</p>
        <dl className="field-acceptance-counts">
          <div><dt>Waiting</dt><dd>{syncStatus.loading ? 'Loading' : syncStatus.unsyncedCount}</dd></div>
          <div><dt>Rejected</dt><dd>{syncStatus.rejected.length}</dd></div>
          <div><dt>Blocked</dt><dd>{syncStatus.blockedCount}</dd></div>
          <div><dt>Quarantined</dt><dd>{syncStatus.quarantinedCount}</dd></div>
          <div><dt>Stale</dt><dd>{syncStatus.stale ? 'Yes' : 'No'}</dd></div>
        </dl>
        <button type="button" className="button-secondary" onClick={() => void syncStatus.refresh()}>
          Refresh observed counts
        </button>
      </section>

      <section className="field-acceptance-panel" aria-labelledby="online-preparation-heading">
        <h2 id="online-preparation-heading">2. Prepare online</h2>
        <div className="field-acceptance-checks">{renderChecks(PREPARATION_CHECKS)}</div>
        {activeQueueStage === 'initial' && visibleActiveEvaluation ? (
          <QueueCheck evaluation={visibleActiveEvaluation} announceStop={!storageBlocked && !runStopped} />
        ) : (
          <p className="field-acceptance-queue-check" data-state="recorded">
            {initialCheckpointAcknowledged
              ? 'Initial zero checkpoint acknowledged. The live count now applies only when the next queue stage is active.'
              : 'Initial zero checkpoint is not active until all preceding physical and online checks are acknowledged.'}
          </p>
        )}
      </section>

      <section className="field-acceptance-panel" aria-labelledby="pre-offline-heading">
        <h2 id="pre-offline-heading">3. Set the limit and go physically offline</h2>
        <div className="field-acceptance-checks">{renderChecks(PRE_OFFLINE_CHECKS)}</div>
        <div className="field-acceptance-timer-block" aria-labelledby="rehearsal-timer-heading">
          <h3 id="rehearsal-timer-heading">Five-capture timer</h3>
          <output className="field-acceptance-timer" aria-label="Elapsed rehearsal time">
            {formatElapsed(elapsed)}
          </output>
          <div className="field-acceptance-actions">
            <button type="button" onClick={startTimer} disabled={progress.timerRunning || storageBlocked || runStopped || !timerReady}>Start</button>
            <button type="button" className="button-secondary" onClick={pauseTimer} disabled={!progress.timerRunning || storageBlocked || runStopped}>Pause</button>
            <button type="button" className="button-secondary" onClick={resetTimer} disabled={storageBlocked || runStopped}>Reset timer</button>
          </div>
        </div>
      </section>

      <section className="field-acceptance-panel" aria-labelledby="offline-cards-heading">
        <h2 id="offline-cards-heading">4. Five fixed offline cards</h2>
        <p>
          For each row, use the normal <strong>Log Session</strong> screen, then return here and refresh or observe the waiting count. This assistant does not create the session or payment.
        </p>
        <div className="field-acceptance-table-scroll">
          <table className="field-acceptance-table">
            <caption>Exact synthetic rehearsal cards and waiting-count checkpoints</caption>
            <thead>
              <tr><th scope="col">Card</th><th scope="col">Console</th><th scope="col">Duration</th><th scope="col">Payment</th><th scope="col">Expected waiting</th><th scope="col">Acknowledgement</th></tr>
            </thead>
            <tbody>
              {REHEARSAL_CARDS.map((card) => {
                const stepId = `card-${card.number}` as RehearsalStepId;
                const evaluation = evaluateQueueCheckpoint(observation, card.expectedCount);
                const isActive = activeQueueStage === stepId;
                const isCompleted = progress.completedStepIds.includes(stepId);
                const displayedEvaluation = isActive && progress.failure
                  ? latchedEvaluation(progress.failure)
                  : evaluation;
                return (
                  <tr key={card.number}>
                    <th scope="row">{card.number}</th>
                    <td>{card.console}</td>
                    <td>{card.duration}</td>
                    <td>{REHEARSAL_PAYMENT}</td>
                    <td>{card.expectedCount}</td>
                    <td>
                      <label className="field-acceptance-card-check">
                        <input
                          type="checkbox"
                          checked={isCompleted}
                          disabled={stepDisabled(stepId)}
                          onChange={() => toggleStep(stepId)}
                        />
                        <span>Observed checkpoint {card.expectedCount}</span>
                      </label>
                      {isCompleted ? (
                        <p className="field-acceptance-queue-check" data-state="recorded">
                          Checkpoint {card.expectedCount} acknowledged locally. The live count now applies only when the next card is active.
                        </p>
                      ) : isActive ? (
                        <QueueCheck evaluation={displayedEvaluation} announceStop={!storageBlocked && !runStopped} />
                      ) : (
                        <p className="field-acceptance-queue-check" data-state="recorded">
                          Checkpoint {card.expectedCount} is not active until every preceding check is acknowledged.
                        </p>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="field-acceptance-panel" aria-labelledby="offline-durability-heading">
        <h2 id="offline-durability-heading">5. Close and relaunch offline</h2>
        <div className="field-acceptance-checks">{renderChecks(OFFLINE_CHECKS)}</div>
        {activeQueueStage === 'durability' && visibleActiveEvaluation ? (
          <QueueCheck evaluation={visibleActiveEvaluation} announceStop={!storageBlocked && !runStopped} />
        ) : (
          <p className="field-acceptance-queue-check" data-state="recorded">
            {durabilityCheckpointAcknowledged
              ? 'Offline durability checkpoint acknowledged.'
              : 'Offline durability checkpoint is not active until the app has been closed and relaunched offline.'}
          </p>
        )}
      </section>

      <section className="field-acceptance-panel" aria-labelledby="reconcile-heading">
        <h2 id="reconcile-heading">6. Reconnect and reconcile</h2>
        <div className="field-acceptance-checks">{renderChecks(RECONCILE_CHECKS)}</div>
        {activeQueueStage === 'reconciliation' && visibleActiveEvaluation ? (
          <QueueCheck evaluation={visibleActiveEvaluation} announceStop={!storageBlocked && !runStopped} />
        ) : (
          <p className="field-acceptance-queue-check" data-state="recorded">
            {reconciliationCheckpointAcknowledged
              ? 'Final zero checkpoint acknowledged.'
              : 'Reconciliation checkpoint is not active until offline durability and duplicate checks are acknowledged.'}
          </p>
        )}
      </section>

      <section className="field-acceptance-panel" aria-labelledby="operator-heading">
        <h2 id="operator-heading">7. Operator comprehension</h2>
        <div className="field-acceptance-checks">{renderChecks(OPERATOR_CHECKS)}</div>
      </section>

      <section className="field-acceptance-panel field-acceptance-reset" aria-labelledby="reset-rehearsal-heading">
        <h2 id="reset-rehearsal-heading">Reset local convenience state</h2>
        <p>Only this release’s rehearsal checklist, timer, and payload-free STOP marker will be reset. Auth, queue, captures, and cached reference data are untouched.</p>
        {confirmingReset ? (
          <div className="field-acceptance-reset-confirm" role="group" aria-label="Confirm rehearsal reset">
            <button type="button" onClick={resetAll}>Yes, reset rehearsal progress</button>
            <button type="button" className="button-secondary" onClick={() => setConfirmingReset(false)}>Keep progress</button>
          </div>
        ) : (
          <button type="button" className="button-secondary" onClick={() => setConfirmingReset(true)}>Reset rehearsal progress</button>
        )}
      </section>
    </section>
  );
}

export default FieldAcceptanceRehearsal;
