import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import {
  REHEARSAL_STEP_IDS,
  emptyRehearsalProgress,
  rehearsalStorageKey,
  type RehearsalProgress,
  type RehearsalStepId,
} from '../domain/fieldAcceptanceRehearsal';
import { FieldAcceptanceRehearsal } from './FieldAcceptanceRehearsal';

const { mockRefresh, syncView } = vi.hoisted(() => ({
  mockRefresh: vi.fn(async () => {}),
  syncView: {
    loading: false,
    unsyncedCount: 0,
    blockedCount: 0,
    quarantinedCount: 0,
    synced: true,
    stale: false,
    rejected: [] as Array<{ entity: string; reason: string | null }>,
    lastSuccessfulSync: null as string | null,
    refresh: vi.fn(async () => {}),
  },
}));

vi.mock('../state/syncState', () => ({
  useSyncStatus: () => ({ ...syncView, refresh: mockRefresh }),
}));

function resetSyncView() {
  Object.assign(syncView, {
    loading: false,
    unsyncedCount: 0,
    blockedCount: 0,
    quarantinedCount: 0,
    synced: true,
    stale: false,
    rejected: [],
    lastSuccessfulSync: null,
  });
}

function storeProgress(progress: RehearsalProgress): void {
  localStorage.setItem(rehearsalStorageKey('local'), JSON.stringify(progress));
}

function storeBefore(nextStep: RehearsalStepId, overrides: Partial<RehearsalProgress> = {}): void {
  storeProgress({
    ...emptyRehearsalProgress(),
    completedStepIds: REHEARSAL_STEP_IDS.slice(0, REHEARSAL_STEP_IDS.indexOf(nextStep)),
    ...overrides,
  });
}

function storeThrough(step: RehearsalStepId, overrides: Partial<RehearsalProgress> = {}): void {
  storeProgress({
    ...emptyRehearsalProgress(),
    completedStepIds: REHEARSAL_STEP_IDS.slice(0, REHEARSAL_STEP_IDS.indexOf(step) + 1),
    ...overrides,
  });
}

describe('FieldAcceptanceRehearsal', () => {
  beforeEach(() => {
    localStorage.clear();
    mockRefresh.mockClear();
    resetSyncView();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders the exact safety constraints and five fixed card rows', () => {
    render(<FieldAcceptanceRehearsal />);

    const safety = screen.getByRole('note', { name: 'Synthetic-only safety rules' });
    expect(safety).toHaveTextContent('API Verification Canary v1');
    expect(safety).toHaveTextContent('Cash R0');
    expect(safety).toHaveTextContent('Entitlement draw');
    expect(safety).toHaveTextContent('Never use real learner data');
    expect(safety).toHaveTextContent(/no credentials, learner names, player identifiers, contact details, screenshots, or free text/i);
    expect(screen.getByText((_content, element) =>
      element?.tagName === 'STRONG' && element.textContent === 'Release local',
    )).toBeInTheDocument();
    expect(screen.getByText(/cannot prove GO/i)).toBeInTheDocument();

    const stopRules = screen.getByRole('note', { name: 'Critical stop rules' });
    expect(stopRules).toHaveTextContent(/login or storage failure/i);
    expect(stopRules).toHaveTextContent(/installed app cannot open offline/i);
    expect(stopRules).toHaveTextContent(/missing, duplicate, or wrong-account capture/i);
    expect(stopRules).toHaveTextContent(/protected data crosses an account boundary/i);
    expect(stopRules).toHaveTextContent(/queue does not survive relaunch/i);
    expect(stopRules).toHaveTextContent(/rejected, blocked, quarantined, or stale/i);
    expect(stopRules).toHaveTextContent(/history mismatch/i);
    expect(stopRules).toHaveTextContent(/paper or developer help/i);
    expect(stopRules).toHaveTextContent(/unresolved security prerequisite/i);
    expect(stopRules).toHaveTextContent(/Do not repeat captures or repair production rows/i);

    const table = screen.getByRole('table', { name: /exact synthetic rehearsal cards/i });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(6);
    const expectedRows = [
      ['1', 'PS5', '20 min', 'Cash R0', '2'],
      ['2', 'PS4', '60 min', 'Cash R0', '4'],
      ['3', 'PS5', '120 min', 'Cash R0', '6'],
      ['4', 'PS4', 'Custom 45 min', 'Cash R0', '8'],
      ['5', 'PS5', 'Custom 90 min', 'Cash R0', '10'],
    ];
    expectedRows.forEach((expected, index) => {
      const row = within(rows[index + 1]);
      expect(row.getByRole('rowheader')).toHaveTextContent(expected[0]);
      expect(row.getAllByRole('cell').slice(0, 4).map((cell) => cell.textContent)).toEqual(
        expected.slice(1),
      );
    });
  });

  it('renders install/account transitions before online and physical-offline preparation before cards', () => {
    render(<FieldAcceptanceRehearsal />);

    const installHeading = screen.getByRole('heading', { name: /1\. Install, relaunch/i });
    const onlineHeading = screen.getByRole('heading', { name: /2\. Prepare online/i });
    expect(installHeading.compareDocumentPosition(onlineHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    [
      /A clean Install or Add to Home Screen was available and completed/i,
      /An earlier installed app upgraded to this Release, or N\/A was confirmed only because no prior FunHouse PWA installation exists/i,
      /Launched FunHouse Revenue from its installed icon/i,
      /Visible Release matched local/i,
      /Fully closed and removed the app from recent apps.*same Release/i,
      /Signed in as Loyiso and manager navigation appeared/i,
      /Signed out of Loyiso.*no protected or stale account screen remained/i,
      /Signed in as Aya.*founder navigation appeared.*manager-only navigation was absent/i,
      /Signed out of Aya.*no protected or stale account screen remained/i,
      /Signed back in as Loyiso.*manager navigation returned.*founder-only navigation was absent/i,
    ].forEach((name) => expect(screen.getByRole('checkbox', { name })).toBeInTheDocument());

    const timeLimit = screen.getByRole('checkbox', { name: /Maximum acceptable time was set externally/i });
    const connectivity = screen.getByRole('checkbox', { name: /Wi-Fi and mobile data were physically off/i });
    const timer = screen.getByRole('heading', { name: 'Five-capture timer' });
    const table = screen.getByRole('table', { name: /exact synthetic rehearsal cards/i });
    expect(timeLimit.compareDocumentPosition(connectivity) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(connectivity.compareDocumentPosition(timer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(timer.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('offers no free-text, capture, or Retry sync controls', () => {
    const { container } = render(<FieldAcceptanceRehearsal />);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(container.querySelector('textarea')).toBeNull();
    expect(screen.queryByRole('button', { name: /retry sync/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /capture|confirm session|payment|history|retry sync|run api|sync now/i })).not.toBeInTheDocument();
  });

  it('enforces physical prerequisites in exact order and rejects completion rollback', () => {
    render(<FieldAcceptanceRehearsal />);

    const first = screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i });
    const release = screen.getByRole('checkbox', { name: /^Visible Release matched local\.$/i });
    const initialZero = screen.getByRole('checkbox', { name: /Observed the exact up-to-date status/i });
    const cardOne = screen.getByRole('checkbox', { name: 'Observed checkpoint 2' });
    expect(first).toBeEnabled();
    expect(release).toBeDisabled();
    expect(initialZero).toBeDisabled();
    expect(cardOne).toBeDisabled();

    fireEvent.click(first);
    expect(first).toBeChecked();
    expect(first).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /An earlier installed app upgraded/i })).toBeEnabled();
  });

  it('persists only enumerated, prefix-ordered progress across remount', () => {
    storeBefore('install-release-matched');
    const first = render(<FieldAcceptanceRehearsal />);
    const releaseCheck = screen.getByRole('checkbox', { name: /^Visible Release matched local\.$/i });
    fireEvent.click(releaseCheck);
    expect(releaseCheck).toBeChecked();

    const stored = localStorage.getItem(rehearsalStorageKey('local'));
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? '{}')).toEqual({
      version: 1,
      completedStepIds: REHEARSAL_STEP_IDS.slice(0, 4),
      elapsedMs: 0,
      timerStartedAt: null,
      timerRunning: false,
      failure: null,
    });

    first.unmount();
    render(<FieldAcceptanceRehearsal />);
    expect(screen.getByRole('checkbox', { name: /^Visible Release matched local\.$/i })).toBeChecked();
  });

  it('starts, pauses, and resets the visual-only local timer after offline preparation', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-01T00:00:00Z'));
    storeThrough('preoffline-connectivity-offline');
    render(<FieldAcceptanceRehearsal />);

    const output = screen.getByLabelText('Elapsed rehearsal time');
    expect(output).not.toHaveAttribute('aria-live');
    expect(output).toHaveTextContent('00:00:00');
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(output).toHaveTextContent('00:00:01');

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(output).toHaveTextContent('00:00:01');

    fireEvent.click(screen.getByRole('button', { name: 'Reset timer' }));
    expect(output).toHaveTextContent('00:00:00');
  });

  it('shows payload-free counts and exact card checkpoint progression', () => {
    storeBefore('card-1');
    Object.assign(syncView, { unsyncedCount: 2 });
    const view = render(<FieldAcceptanceRehearsal />);
    const counts = screen.getByRole('heading', { name: 'Observed sync counts' }).closest('section');
    expect(within(counts as HTMLElement).getByText('Waiting').nextElementSibling).toHaveTextContent('2');
    expect(screen.getByText('PASS — observed waiting count is exactly 2 with no warnings or stale status.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Observed checkpoint 2' }));
    Object.assign(syncView, { unsyncedCount: 4 });
    view.rerender(<FieldAcceptanceRehearsal />);
    expect(screen.getByText('PASS — observed waiting count is exactly 4 with no warnings or stale status.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Observed checkpoint 4' })).toBeEnabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('activates initial queue evaluation only after every prerequisite and latches unsafe status', async () => {
    storeBefore('prepare-zero-observed');
    Object.assign(syncView, { stale: true });
    const view = render(<FieldAcceptanceRehearsal />);

    const counts = screen.getByRole('heading', { name: 'Observed sync counts' }).closest('section');
    expect(within(counts as HTMLElement).getByText('Stale').nextElementSibling).toHaveTextContent('Yes');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/recorded unsafe-status at initial/i);
    expect(screen.getAllByRole('alert')).toHaveLength(1);

    Object.assign(syncView, { stale: false });
    view.rerender(<FieldAcceptanceRehearsal />);
    expect(screen.getByRole('alert')).toHaveTextContent(/Later readings cannot clear the failure/i);
    expect(screen.getByRole('checkbox', { name: /Observed the exact up-to-date status/i })).toBeDisabled();
  });

  it('classifies post-relaunch under-count as STOP and keeps it latched after a healthy refresh', async () => {
    storeBefore('offline-ten-remains');
    Object.assign(syncView, { unsyncedCount: 8 });
    const view = render(<FieldAcceptanceRehearsal />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/recorded durability-count at durability/i);
    const stored = JSON.parse(localStorage.getItem(rehearsalStorageKey('local')) ?? '{}');
    expect(stored.failure).toEqual({ stage: 'durability', reason: 'durability-count' });

    Object.assign(syncView, { unsyncedCount: 10 });
    view.rerender(<FieldAcceptanceRehearsal />);
    expect(screen.getByRole('alert')).toHaveTextContent(/Later readings cannot clear the failure/i);
    expect(screen.getByRole('checkbox', { name: /remained exactly 10 after relaunch/i })).toBeDisabled();
  });

  it('waits during reconciliation, then latches STOP after the one retry acknowledgement', async () => {
    storeBefore('reconcile-auto-or-one-retry');
    Object.assign(syncView, { unsyncedCount: 1 });
    render(<FieldAcceptanceRehearsal />);

    const reconciliation = screen.getByRole('heading', { name: /6\. Reconnect and reconcile/i }).closest('section');
    expect(within(reconciliation as HTMLElement).getByText(/WAIT — 1 item\(s\) remain/i)).toBeInTheDocument();
    fireEvent.click(within(reconciliation as HTMLElement).getByRole('checkbox', { name: /Automatic sync completed/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/recorded remaining-after-retry at reconciliation/i);
    expect(screen.getByRole('checkbox', { name: /Observed zero waiting/i })).toBeDisabled();
  });

  it('preserves invalid storage, blocks all progress, and requires confirmed reset', () => {
    const raw = '{not-json';
    localStorage.setItem(rehearsalStorageKey('local'), raw);
    render(<FieldAcceptanceRehearsal />);

    expect(screen.getByRole('alert')).toHaveTextContent(/stored rehearsal progress is invalid or incompatible/i);
    expect(localStorage.getItem(rehearsalStorageKey('local'))).toBe(raw);
    expect(screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Reset rehearsal progress' }));
    expect(localStorage.getItem(rehearsalStorageKey('local'))).toBe(raw);
    fireEvent.click(screen.getByRole('button', { name: 'Yes, reset rehearsal progress' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i })).toBeEnabled();
    expect(JSON.parse(localStorage.getItem(rehearsalStorageKey('local')) ?? '{}')).toEqual(emptyRehearsalProgress());
  });

  it('surfaces storage write failure as the only active STOP and prevents progress', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Denied', 'SecurityError');
    });
    render(<FieldAcceptanceRehearsal />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/rehearsal progress storage is unavailable/i);
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i })).toBeDisabled();
  });

  it('uses only the read-only refresh collaborator and never fetches or schedules work', () => {
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    render(<FieldAcceptanceRehearsal />);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh observed counts' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i }));

    expect(mockRefresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('requires two steps to reset and touches only the release-scoped key', async () => {
    localStorage.setItem('unrelated', 'keep');
    render(<FieldAcceptanceRehearsal />);
    fireEvent.click(screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i }));

    fireEvent.click(screen.getByRole('button', { name: 'Reset rehearsal progress' }));
    expect(screen.getByRole('group', { name: 'Confirm rehearsal reset' })).toBeInTheDocument();
    expect(localStorage.getItem(rehearsalStorageKey('local'))).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Yes, reset rehearsal progress' }));
    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /A clean Install or Add to Home Screen/i })).not.toBeChecked();
    });
    expect(localStorage.getItem('unrelated')).toBe('keep');
  });
});
