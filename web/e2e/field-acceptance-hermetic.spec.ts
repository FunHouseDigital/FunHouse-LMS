import {
  chromium,
  expect,
  test,
  type BrowserContext,
  type Page,
  type Route,
  type TestInfo,
} from '@playwright/test';

const PWA_ORIGIN = 'http://127.0.0.1:4173';
const API_ORIGIN = 'http://localhost:8000';
const SYNTHETIC_PLAYER_ID = '10000000-0000-4000-8000-000000000001';
const SYNTHETIC_PLAYER_NAME = 'Synthetic Field Canary';
const SYNTHETIC_LOCATION_ID = '20000000-0000-4000-8000-000000000001';
const SYNTHETIC_IDENTIFIER = 'synthetic-manager';
const SYNTHETIC_PASSWORD = 'test-only-passphrase';

interface WireAction {
  client_id: string;
  entity: string;
  created_at: string;
  payload: Record<string, unknown>;
}

interface SyntheticApiState {
  appliedClientIds: Set<string>;
  syncBatches: WireAction[][];
  syncRequestCount: number;
  sessions: Array<Record<string, unknown>>;
  payments: Array<Record<string, unknown>>;
  unexpectedApiRequests: string[];
  unexpectedExternalRequests: string[];
}

interface LocalStateSummary {
  queueCount: number;
  sessionCount: number;
  paymentCount: number;
  unsyncedCount: number;
  terminalCount: number;
  queueRowsSealed: boolean;
  localRowsSealed: boolean;
  businessDataAbsentFromRawRows: boolean;
  uniqueQueueIds: boolean;
  queueIdentity: string[];
  sessionKeyProtected: boolean;
  ownerKeysProtected: boolean;
  sessionOwner: string | null;
  ownerKeyIds: string[];
}

const CARDS = [
  { console: 'PS5', duration: 20, custom: false },
  { console: 'PS4', duration: 60, custom: false },
  { console: 'PS5', duration: 120, custom: false },
  { console: 'PS4', duration: 45, custom: true },
  { console: 'PS5', duration: 90, custom: true },
] as const;

function makeSyntheticJwt(): { token: string; expiresAt: string } {
  const issuedAt = Math.floor(Date.now() / 1000);
  const expiresAt = issuedAt + 60 * 60;
  const encode = (value: unknown): string =>
    Buffer.from(JSON.stringify(value)).toString('base64url');
  return {
    token: `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({
      sub: 'synthetic-field-manager',
      role: 'manager',
      location_id: SYNTHETIC_LOCATION_ID,
      school_id: null,
      iat: issuedAt,
      exp: expiresAt,
    })}.synthetic-signature`,
    expiresAt: new Date(expiresAt * 1000).toISOString(),
  };
}

function jsonHeaders(): Record<string, string> {
  return {
    'access-control-allow-origin': PWA_ORIGIN,
    'access-control-allow-credentials': 'true',
    'access-control-allow-headers': 'authorization,content-type',
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'content-type': 'application/json',
  };
}

async function fulfilJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, headers: jsonHeaders(), body: JSON.stringify(body) });
}

async function installSyntheticApi(
  context: BrowserContext,
  state: SyntheticApiState,
): Promise<void> {
  await context.route(`${API_ORIGIN}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();

    if (method === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: jsonHeaders() });
      return;
    }

    if (method === 'POST' && url.pathname === '/auth/login') {
      const body = request.postDataJSON() as { identifier?: unknown; password?: unknown };
      if (body.identifier !== SYNTHETIC_IDENTIFIER || body.password !== SYNTHETIC_PASSWORD) {
        await fulfilJson(route, { detail: 'Invalid credentials' }, 401);
        return;
      }
      const jwt = makeSyntheticJwt();
      await fulfilJson(route, {
        access_token: jwt.token,
        token_type: 'bearer',
        expires_at: jwt.expiresAt,
      });
      return;
    }

    if (method === 'GET' && url.pathname === '/players') {
      await fulfilJson(route, [
        {
          id: SYNTHETIC_PLAYER_ID,
          first_name: 'Synthetic',
          last_name: 'Field Canary',
          birth_date: null,
          grade: null,
          school_id: null,
          location_id: SYNTHETIC_LOCATION_ID,
          consent_status: 'granted',
          active: true,
        },
      ]);
      return;
    }

    if (method === 'GET' && url.pathname === '/products') {
      await fulfilJson(route, []);
      return;
    }

    if (
      method === 'GET' &&
      url.pathname === `/players/${encodeURIComponent(SYNTHETIC_PLAYER_ID)}/entitlements`
    ) {
      await fulfilJson(route, []);
      return;
    }

    if (
      method === 'GET' &&
      url.pathname === `/players/${encodeURIComponent(SYNTHETIC_PLAYER_ID)}/history`
    ) {
      await fulfilJson(route, {
        player_id: SYNTHETIC_PLAYER_ID,
        sessions: state.sessions,
        payments: state.payments,
        entitlement_draws: [],
      });
      return;
    }

    if (method === 'POST' && url.pathname === '/sync') {
      state.syncRequestCount += 1;
      const body = request.postDataJSON() as { actions?: unknown };
      const actions = Array.isArray(body.actions) ? body.actions as WireAction[] : [];
      state.syncBatches.push(actions);

      const results = actions.map((action) => {
        const seen = state.appliedClientIds.has(action.client_id);
        if (!seen) {
          state.appliedClientIds.add(action.client_id);
          if (action.entity === 'session') {
            state.sessions.push({
              id: `server-${action.client_id}`,
              ...action.payload,
              school_id: null,
              logged_by: SYNTHETIC_IDENTIFIER,
              location_id: SYNTHETIC_LOCATION_ID,
            });
          } else if (action.entity === 'payment') {
            state.payments.push({
              id: `server-${action.client_id}`,
              ...action.payload,
              logged_by: SYNTHETIC_IDENTIFIER,
              location_id: SYNTHETIC_LOCATION_ID,
            });
          }
        }
        return {
          client_id: action.client_id,
          entity: action.entity,
          status: seen ? 'skipped' : 'applied',
          record_id: `server-${action.client_id}`,
          reason: null,
        };
      });
      await fulfilJson(route, { results });
      return;
    }

    state.unexpectedApiRequests.push(`${method} ${url.pathname}`);
    await fulfilJson(route, { detail: 'Unexpected synthetic API request' }, 404);
  });

  // Registered last so it runs first. Allowed loopback requests fall through to
  // the synthetic API route or preview server; every other network destination
  // is denied before DNS or transport can leave the runner.
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === PWA_ORIGIN || url.origin === API_ORIGIN) {
      await route.fallback();
      return;
    }
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      state.unexpectedExternalRequests.push(route.request().url());
      await route.abort('blockedbyclient');
      return;
    }
    await route.fallback();
  });
}

async function launchApp(
  profilePath: string,
  state: SyntheticApiState,
  offline: boolean,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await chromium.launchPersistentContext(profilePath, {
    baseURL: PWA_ORIGIN,
    headless: true,
    locale: 'en-ZA',
    timezoneId: 'Africa/Johannesburg',
    serviceWorkers: 'allow',
    offline,
  });
  await installSyntheticApi(context, state);
  const page = context.pages()[0] ?? await context.newPage();
  return { context, page };
}

async function ensureServiceWorkerControl(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  if (!await page.evaluate(() => navigator.serviceWorker.controller !== null)) {
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect.poll(
      () => page.evaluate(() => navigator.serviceWorker.controller !== null),
      { message: 'The production app shell never became service-worker controlled' },
    ).toBe(true);
  }
}

async function readLocalState(page: Page): Promise<LocalStateSummary> {
  return page.evaluate(async ({ playerId }) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('funhouse-revenue');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const readAll = (storeName: string): Promise<Array<Record<string, unknown>>> =>
      new Promise((resolve, reject) => {
        const request = db.transaction(storeName, 'readonly').objectStore(storeName).getAll();
        request.onsuccess = () => resolve(request.result as Array<Record<string, unknown>>);
        request.onerror = () => reject(request.error);
      });
    const readOne = (storeName: string, key: IDBValidKey): Promise<Record<string, unknown> | undefined> =>
      new Promise((resolve, reject) => {
        const request = db.transaction(storeName, 'readonly').objectStore(storeName).get(key);
        request.onsuccess = () => resolve(request.result as Record<string, unknown> | undefined);
        request.onerror = () => reject(request.error);
      });
    const [queue, sessions, payments, sessionKeyEntry, sessionOwnerEntry, ownerKeys] = await Promise.all([
      readAll('sync_queue'),
      readAll('sessions'),
      readAll('payments'),
      readOne('meta', 'session_key'),
      readOne('meta', 'session_owner'),
      readAll('owner_data_keys'),
    ]);
    db.close();

    const hasEnvelope = (row: Record<string, unknown>): boolean => {
      const envelope = row.envelope as Record<string, unknown> | undefined;
      return Boolean(
        envelope &&
        envelope.version === 1 &&
        typeof envelope.key_id === 'string' &&
        typeof envelope.iv === 'string' &&
        typeof envelope.ciphertext === 'string',
      );
    };
    const isProtectedAesKey = async (value: unknown): Promise<boolean> => {
      if (!(value instanceof CryptoKey)) return false;
      const algorithm = value.algorithm as AesKeyAlgorithm;
      if (
        value.type !== 'secret' ||
        value.extractable ||
        algorithm.name !== 'AES-GCM' ||
        algorithm.length !== 256 ||
        [...value.usages].sort().join(',') !== 'decrypt,encrypt'
      ) return false;
      try {
        await crypto.subtle.exportKey('raw', value);
        return false;
      } catch {
        return true;
      }
    };
    const ownerKeyChecks = await Promise.all(
      ownerKeys.map((row) => isProtectedAesKey(row.key)),
    );
    const raw = JSON.stringify({ queue, sessions, payments });
    const queueIds = queue.map((row) => String(row.client_id));
    return {
      queueCount: queue.length,
      sessionCount: sessions.length,
      paymentCount: payments.length,
      unsyncedCount: queue.filter((row) => row.status === 'unsynced').length,
      terminalCount: queue.filter((row) => row.status === 'applied' || row.status === 'skipped').length,
      queueRowsSealed: queue.every((row) => hasEnvelope(row) && !('entity' in row) && !('payload' in row)),
      localRowsSealed: [...sessions, ...payments].every((row) =>
        hasEnvelope(row) && !('player_id' in row) && !('duration_minutes' in row) && !('amount_cents' in row)
      ),
      businessDataAbsentFromRawRows:
        !raw.includes(playerId) &&
        !raw.includes('PS5') &&
        !raw.includes('PS4') &&
        !raw.includes('cash'),
      uniqueQueueIds: new Set(queueIds).size === queueIds.length,
      queueIdentity: queue
        .map((row) => `${String(row.client_id)}|${String(row.created_at)}`)
        .sort(),
      sessionKeyProtected: await isProtectedAesKey(sessionKeyEntry?.value),
      ownerKeysProtected:
        ownerKeys.length === 1 &&
        ownerKeyChecks.length === 1 &&
        ownerKeyChecks.every(Boolean),
      sessionOwner: typeof sessionOwnerEntry?.value === 'string' ? sessionOwnerEntry.value : null,
      ownerKeyIds: ownerKeys
        .map((row) => `${String(row.owner_key_id)}|${String(row.key_id)}`)
        .sort(),
    };
  }, { playerId: SYNTHETIC_PLAYER_ID });
}

async function selectSyntheticPlayer(page: Page): Promise<void> {
  await page.getByLabel('Search players').fill(SYNTHETIC_PLAYER_NAME);
  const playerButton = page
    .getByRole('list', { name: 'Players' })
    .getByRole('button', { name: SYNTHETIC_PLAYER_NAME, exact: true });
  await expect(playerButton).toHaveCount(1);
  await playerButton.click();
}

async function captureCard(
  page: Page,
  card: (typeof CARDS)[number],
  expectedWaiting: number,
): Promise<void> {
  await page.getByRole('radio', { name: card.console }).check();
  if (card.custom) {
    await page.getByLabel('Custom minutes').fill(String(card.duration));
  } else {
    await page.getByRole('button', { name: `${card.duration} min`, exact: true }).click();
  }
  await page.getByRole('radio', { name: 'Cash' }).check();
  await page.getByLabel('Cash amount').fill('0');
  await page.getByRole('button', { name: 'Confirm session' }).click();
  await expect(
    page.getByLabel('Sync status').getByText(
      `Offline — ${expectedWaiting} items are saved on this device and will sync when connected.`,
      { exact: true },
    ),
  ).toBeVisible();
}

async function openSyntheticHistory(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Players' }).click();
  await page.getByLabel('Search players by name').fill(SYNTHETIC_PLAYER_NAME);
  const name = page
    .getByRole('list', { name: 'Player roster' })
    .getByText(SYNTHETIC_PLAYER_NAME, { exact: true });
  await expect(name).toHaveCount(1);
  await name.locator('xpath=ancestor::li[1]').getByRole('link').click();
  await expect(page.getByRole('heading', { name: 'Sessions (5)' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Payments (5)' })).toBeVisible();
}

async function assertRenderedHistory(page: Page): Promise<void> {
  const sessionRows = page.getByRole('list', { name: 'Sessions' }).locator('li');
  const paymentRows = page.getByRole('list', { name: 'Payments' }).locator('li');
  await expect(sessionRows).toHaveCount(5);
  await expect(paymentRows).toHaveCount(5);

  const sessionText = await sessionRows.allTextContents();
  for (const card of CARDS) {
    expect(
      sessionText.filter((text) =>
        text.includes(`Reference${card.console}`) && text.includes(`Duration${card.duration} min`)
      ),
      `${card.console}/${card.duration} must appear exactly once in history`,
    ).toHaveLength(1);
  }
  for (const text of await paymentRows.allTextContents()) {
    expect(text).toContain('R0.00');
    expect(text).toContain('Cash');
  }
}

async function triggerOnlineFlushAndWait(page: Page): Promise<void> {
  await page.evaluate(() => new Promise<void>((resolve, reject) => {
    const summary = document.querySelector<HTMLElement>('[data-field="sync-summary"]');
    if (!summary) {
      reject(new Error('Sync summary is unavailable'));
      return;
    }
    let sawSyncing = summary.textContent?.startsWith('Syncing ') ?? false;
    const timeout = window.setTimeout(() => {
      observer.disconnect();
      reject(new Error('Online-triggered empty flush did not complete'));
    }, 10_000);
    const inspect = (): void => {
      const text = summary.textContent ?? '';
      if (text.startsWith('Syncing ')) sawSyncing = true;
      if (sawSyncing && text === 'Up to date — no items waiting to sync.') {
        window.clearTimeout(timeout);
        observer.disconnect();
        resolve();
      }
    };
    const observer = new MutationObserver(inspect);
    observer.observe(summary, { childList: true, subtree: true, characterData: true });
    window.dispatchEvent(new Event('online'));
    inspect();
  }));
}

function newApiState(): SyntheticApiState {
  return {
    appliedClientIds: new Set(),
    syncBatches: [],
    syncRequestCount: 0,
    sessions: [],
    payments: [],
    unexpectedApiRequests: [],
    unexpectedExternalRequests: [],
  };
}

async function profilePath(testInfo: TestInfo): Promise<string> {
  return testInfo.outputPath('chromium-profile');
}

test('five offline sessions survive restart, reconcile once, and remain idempotent', async ({}, testInfo) => {
  const state = newApiState();
  const persistentProfile = await profilePath(testInfo);
  let activeContext: BrowserContext | null = null;

  try {
    // Online preparation uses the real login and cache-hydration paths with a
    // local synthetic API. The structurally coherent token is test-only and no
    // request can reach a remote service.
    let launched = await launchApp(persistentProfile, state, false);
    activeContext = launched.context;
    let page = launched.page;
    await page.goto(`${PWA_ORIGIN}/`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible();
    await ensureServiceWorkerControl(page);

    await page.getByLabel('Identifier').fill(SYNTHETIC_IDENTIFIER);
    await page.getByLabel('Password').fill(SYNTHETIC_PASSWORD);
    await page.getByRole('button', { name: 'Log in' }).click();
    await expect(page).toHaveURL(`${PWA_ORIGIN}/log-session`);
    await expect(page.getByRole('heading', { name: 'Log Session' })).toBeVisible();
    await expect(page.getByLabel('Reference data status')).toContainText(
      'Players and products are available offline',
    );
    await selectSyntheticPlayer(page);

    await activeContext.setOffline(true);
    for (const [index, card] of CARDS.entries()) {
      await captureCard(page, card, (index + 1) * 2);
      expect(state.syncRequestCount, 'Offline capture attempted a sync request').toBe(0);
    }

    const captured = await readLocalState(page);
    expect(captured).toMatchObject({
      queueCount: 10,
      sessionCount: 5,
      paymentCount: 5,
      unsyncedCount: 10,
      terminalCount: 0,
      queueRowsSealed: true,
      localRowsSealed: true,
      businessDataAbsentFromRawRows: true,
      uniqueQueueIds: true,
      sessionKeyProtected: true,
      ownerKeysProtected: true,
    });
    expect(captured.queueIdentity).toHaveLength(10);
    expect(captured.sessionOwner).not.toBeNull();
    expect(captured.ownerKeyIds).toHaveLength(1);

    // Close the entire Chromium context and relaunch the same on-disk profile
    // while offline. This proves the service-worker shell, IndexedDB CryptoKeys,
    // encrypted auth, cache, records, and queue survive a process lifecycle.
    await activeContext.close();
    activeContext = null;
    launched = await launchApp(persistentProfile, state, true);
    activeContext = launched.context;
    page = launched.page;
    await page.goto(`${PWA_ORIGIN}/`, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(`${PWA_ORIGIN}/log-session`);
    await expect(page.getByRole('heading', { name: 'Log Session' })).toBeVisible();
    await expect(
      page.getByLabel('Sync status').getByText(
        'Offline — 10 items are saved on this device and will sync when connected.',
        { exact: true },
      ),
    ).toBeVisible();
    await page.getByLabel('Search players').fill(SYNTHETIC_PLAYER_NAME);
    await expect(
      page.getByRole('list', { name: 'Players' })
        .getByRole('button', { name: SYNTHETIC_PLAYER_NAME, exact: true }),
    ).toHaveCount(1);
    const restored = await readLocalState(page);
    expect(restored).toMatchObject({
      queueCount: 10,
      sessionCount: 5,
      paymentCount: 5,
      unsyncedCount: 10,
      terminalCount: 0,
      sessionKeyProtected: true,
      ownerKeysProtected: true,
    });
    expect(restored.queueIdentity).toEqual(captured.queueIdentity);
    expect(restored.sessionOwner).toBe(captured.sessionOwner);
    expect(restored.ownerKeyIds).toEqual(captured.ownerKeyIds);

    // Reconnect. Automatic online sync gets one bounded chance; the visible
    // retry control is used at most once, matching the field checklist.
    await activeContext.setOffline(false);
    try {
      await expect.poll(() => state.syncRequestCount, { timeout: 8_000 }).toBe(1);
    } catch {
      await page.getByRole('button', { name: 'Retry sync' }).click();
      await expect.poll(() => state.syncRequestCount).toBe(1);
    }
    await expect(
      page.getByLabel('Sync status').getByText(
        'Up to date — no items waiting to sync.',
        { exact: true },
      ),
    ).toBeVisible();

    expect(state.syncBatches).toHaveLength(1);
    const transmitted = state.syncBatches[0];
    expect(transmitted).toHaveLength(10);
    expect(new Set(transmitted.map((action) => action.client_id)).size).toBe(10);
    expect(
      transmitted.map((action) => `${action.client_id}|${action.created_at}`).sort(),
    ).toEqual(restored.queueIdentity);
    for (const action of transmitted) {
      expect(Object.keys(action).sort()).toEqual(['client_id', 'created_at', 'entity', 'payload']);
      expect(action.payload.player_id).toBe(SYNTHETIC_PLAYER_ID);
    }
    const sessions = transmitted.filter((action) => action.entity === 'session');
    const payments = transmitted.filter((action) => action.entity === 'payment');
    expect(sessions).toHaveLength(5);
    expect(payments).toHaveLength(5);
    expect(
      sessions.map((action) => `${action.payload.reference}/${action.payload.duration_minutes}`).sort(),
    ).toEqual(CARDS.map((card) => `${card.console}/${card.duration}`).sort());
    for (const action of sessions) {
      expect(Object.keys(action.payload).sort()).toEqual([
        'duration_minutes', 'ended_at', 'player_id', 'reference', 'session_type', 'started_at',
      ]);
      expect(action.payload.session_type).toBe('lounge');
      expect(action.payload.started_at).toBe(action.created_at);
      expect(new Date(String(action.payload.ended_at)).getTime()).toBe(
        new Date(action.created_at).getTime() + Number(action.payload.duration_minutes) * 60_000,
      );
    }
    for (const action of payments) {
      expect(Object.keys(action.payload).sort()).toEqual([
        'amount_cents', 'method', 'paid_at', 'player_id',
      ]);
      expect(action.payload.method).toBe('cash');
      expect(action.payload.amount_cents).toBe(0);
      expect(action.payload.paid_at).toBe(action.created_at);
    }
    expect(state.sessions).toHaveLength(5);
    expect(state.payments).toHaveLength(5);

    expect(await readLocalState(page)).toMatchObject({
      queueCount: 10,
      sessionCount: 5,
      paymentCount: 5,
      unsyncedCount: 0,
      terminalCount: 10,
      queueRowsSealed: true,
      localRowsSealed: true,
    });
    await openSyntheticHistory(page);
    await assertRenderedHistory(page);
    await expect(page.getByRole('alert')).toHaveCount(0);

    // A second browser-process lifecycle must not retransmit terminal actions
    // or duplicate server history.
    await activeContext.close();
    activeContext = null;
    const syncRequestsBeforeSecondRestart = state.syncRequestCount;
    launched = await launchApp(persistentProfile, state, false);
    activeContext = launched.context;
    page = launched.page;
    await page.goto(`${PWA_ORIGIN}/`, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(`${PWA_ORIGIN}/log-session`);
    await openSyntheticHistory(page);
    await assertRenderedHistory(page);
    await triggerOnlineFlushAndWait(page);
    expect(state.syncRequestCount).toBe(syncRequestsBeforeSecondRestart);
    const secondRestart = await readLocalState(page);
    expect(secondRestart).toMatchObject({
      queueCount: 10,
      unsyncedCount: 0,
      terminalCount: 10,
      sessionKeyProtected: true,
      ownerKeysProtected: true,
    });
    expect(secondRestart.queueIdentity).toEqual(captured.queueIdentity);
    expect(secondRestart.sessionOwner).toBe(captured.sessionOwner);
    expect(secondRestart.ownerKeyIds).toEqual(captured.ownerKeyIds);
    expect(state.sessions).toHaveLength(5);
    expect(state.payments).toHaveLength(5);
    expect(state.unexpectedApiRequests).toEqual([]);
    expect(state.unexpectedExternalRequests).toEqual([]);
  } finally {
    await activeContext?.close();
  }
});