import { expect, test, type APIResponse, type Response } from '@playwright/test';

const PWA_ORIGIN = 'https://funhouse-revenue-pwa.vercel.app';
const API_ORIGIN = 'https://fun-house-lms.vercel.app';
const CANARY_NAME = 'API Verification Canary v1';

// Stable identity makes the production write rerunnable: the first run applies
// these rows and later runs safely return skipped for the same natural keys.
const SMOKE_TIME = '2026-08-13T19:00:00.000Z';
const SESSION_CLIENT_ID = 'f0000000-0000-4000-8000-000000000031';
const PAYMENT_CLIENT_ID = 'f0000000-0000-4000-8000-000000000032';
const CLIENT_IDS = [SESSION_CLIENT_ID, PAYMENT_CLIENT_ID] as const;

function requireCondition(condition: boolean, message: string): void {
  expect(condition, message).toBe(true);
}

function isApiResponse(
  response: Response,
  pathname: string | RegExp,
  method: string,
): boolean {
  const url = new URL(response.url());
  const pathMatches =
    typeof pathname === 'string' ? url.pathname === pathname : pathname.test(url.pathname);
  return url.origin === API_ORIGIN && pathMatches && response.request().method() === method;
}

async function requireBrowserCors(response: Response, label: string): Promise<void> {
  expect(response.status(), `${label} did not return HTTP 200`).toBe(200);
  const headers = await response.allHeaders();
  expect(
    headers['access-control-allow-origin'],
    `${label} did not allow the approved PWA origin`,
  ).toBe(PWA_ORIGIN);
  expect(
    headers['access-control-allow-credentials'],
    `${label} did not allow credentialed browser requests`,
  ).toBe('true');
}

function requireNoCache(response: APIResponse, label: string): void {
  expect(response.status(), `${label} did not load`).toBe(200);
  const directives = String(response.headers()['cache-control'] ?? '')
    .toLowerCase()
    .split(',')
    .map((directive) => directive.trim());
  requireCondition(
    directives.includes('max-age=0') && directives.includes('must-revalidate'),
    `${label} must require immediate revalidation`,
  );
}

test('production PWA: offline synthetic capture, sync and read-back', async ({
  context,
  page,
  request,
}) => {
  const password = process.env.LOYISO_BOOTSTRAP_PASSWORD;
  requireCondition(
    typeof password === 'string' && password.length > 0,
    'LOYISO_BOOTSTRAP_PASSWORD is required',
  );
  requireCondition(
    !password!.includes('\n') && !password!.includes('\r'),
    'LOYISO_BOOTSTRAP_PASSWORD must be a single line',
  );

  let insecureRequestObserved = false;
  let browserSecurityDiagnosticObserved = false;
  let apiPathFetchedFromPwaOrigin = false;
  let syncRequestCount = 0;
  const isSecurityDiagnostic = (message: string): boolean =>
    /mixed content|blocked by cors policy|cross-origin request blocked/i.test(message);
  page.on('console', (message) => {
    if (
      (message.type() === 'warning' || message.type() === 'error') &&
      isSecurityDiagnostic(message.text())
    ) {
      browserSecurityDiagnosticObserved = true;
    }
  });
  page.on('requestfailed', (browserRequest) => {
    const url = new URL(browserRequest.url());
    if (url.protocol === 'http:') insecureRequestObserved = true;
    if (isSecurityDiagnostic(browserRequest.failure()?.errorText ?? '')) {
      browserSecurityDiagnosticObserved = true;
    }
  });
  page.on('request', (browserRequest) => {
    const url = new URL(browserRequest.url());
    if (url.protocol === 'http:') insecureRequestObserved = true;
    if (url.origin === API_ORIGIN && url.pathname === '/sync') syncRequestCount += 1;

    const isFetch = ['fetch', 'xhr'].includes(browserRequest.resourceType());
    const looksLikeApiPath =
      url.pathname === '/auth/login' ||
      url.pathname === '/sync' ||
      url.pathname === '/products' ||
      url.pathname === '/players' ||
      /^\/players\/[^/]+\/(?:entitlements|history)$/.test(url.pathname);
    if (isFetch && url.origin === PWA_ORIGIN && looksLikeApiPath) {
      apiPathFetchedFromPwaOrigin = true;
    }
  });

  // Step 1: HTTPS shell/deep link, manifest metadata and active scoped worker.
  const rootResponse = await page.goto(`${PWA_ORIGIN}/`, { waitUntil: 'domcontentloaded' });
  expect(rootResponse?.status(), 'PWA root did not load').toBe(200);
  const deepLinkResponse = await page.goto(`${PWA_ORIGIN}/login`, {
    waitUntil: 'domcontentloaded',
  });
  expect(deepLinkResponse?.status(), 'Login deep link did not load').toBe(200);
  const refreshedDeepLink = await page.reload({ waitUntil: 'domcontentloaded' });
  expect(refreshedDeepLink?.status(), 'Login deep-link refresh failed').toBe(200);
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible();

  const manifestResponse = await request.get(`${PWA_ORIGIN}/manifest.webmanifest`);
  requireNoCache(manifestResponse, 'Web app manifest');
  const manifest = (await manifestResponse.json()) as Record<string, unknown>;
  requireCondition(
    manifest.name === 'FunHouse Revenue' &&
      manifest.short_name === 'FunHouse' &&
      manifest.start_url === '/' &&
      manifest.scope === '/' &&
      manifest.display === 'standalone' &&
      manifest.background_color === '#0b0b0f' &&
      manifest.theme_color === '#0b0b0f',
    'Web app manifest metadata did not match the approved production contract',
  );
  const icons = Array.isArray(manifest.icons)
    ? (manifest.icons as Array<Record<string, unknown>>)
    : [];
  const requiredIcons = [
    {
      icon: icons.find(
        (icon) =>
          icon.sizes === '192x192' && !String(icon.purpose ?? '').includes('maskable'),
      ),
      width: 192,
      height: 192,
    },
    {
      icon: icons.find(
        (icon) =>
          icon.sizes === '512x512' && !String(icon.purpose ?? '').includes('maskable'),
      ),
      width: 512,
      height: 512,
    },
    {
      icon: icons.find(
        (icon) =>
          icon.sizes === '512x512' && String(icon.purpose ?? '').includes('maskable'),
      ),
      width: 512,
      height: 512,
    },
  ];
  requireCondition(
    requiredIcons.every(({ icon }) => typeof icon?.src === 'string'),
    'Web app manifest icon metadata was incomplete',
  );
  const iconAssets = requiredIcons.map(({ icon, width, height }) => {
    const url = new URL(String(icon!.src), PWA_ORIGIN);
    requireCondition(url.origin === PWA_ORIGIN, 'A manifest icon was not same-origin');
    return { url: url.href, width, height };
  });
  for (const asset of iconAssets) {
    const iconResponse = await request.get(asset.url);
    expect(iconResponse.status(), 'A manifest icon did not load').toBe(200);
    expect(
      String(iconResponse.headers()['content-type'] ?? ''),
      'A manifest icon did not return an image content type',
    ).toMatch(/^image\//i);
  }
  const decodedIcons = await page.evaluate(async (assets) => {
    return Promise.all(
      assets.map(
        (asset) =>
          new Promise<boolean>((resolve) => {
            const image = new Image();
            image.onload = () =>
              resolve(image.naturalWidth === asset.width && image.naturalHeight === asset.height);
            image.onerror = () => resolve(false);
            image.src = asset.url;
          }),
      ),
    );
  }, iconAssets);
  requireCondition(
    decodedIcons.every(Boolean),
    'A manifest icon could not be decoded at its declared dimensions',
  );

  const workerResponse = await request.get(`${PWA_ORIGIN}/sw.js`);
  requireNoCache(workerResponse, 'Service worker');
  const worker = await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    const active = registration.active;
    if (!active) throw new Error('Service worker registration had no active worker');
    if (active.state !== 'activated') {
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => {
          active.removeEventListener('statechange', onStateChange);
          reject(new Error('Service worker activation timed out'));
        }, 10_000);
        const onStateChange = (): void => {
          if (active.state === 'activated') {
            window.clearTimeout(timeout);
            active.removeEventListener('statechange', onStateChange);
            resolve();
          } else if (active.state === 'redundant') {
            window.clearTimeout(timeout);
            active.removeEventListener('statechange', onStateChange);
            reject(new Error('Service worker became redundant before activation'));
          }
        };
        active.addEventListener('statechange', onStateChange);
        onStateChange();
      });
    }
    return { scope: registration.scope, state: active.state };
  });
  expect(worker.scope, 'Service worker scope changed').toBe(`${PWA_ORIGIN}/`);
  expect(worker.state, 'Service worker was not activated').toBe('activated');

  // Step 6 (deterministic preflight half): exact-origin credentialed CORS.
  const preflight = await request.fetch(`${API_ORIGIN}/sync`, {
    method: 'OPTIONS',
    headers: {
      Origin: PWA_ORIGIN,
      'Access-Control-Request-Method': 'POST',
      'Access-Control-Request-Headers': 'authorization,content-type',
    },
  });
  requireCondition(preflight.status() >= 200 && preflight.status() < 300, 'CORS preflight failed');
  const preflightHeaders = preflight.headers();
  expect(preflightHeaders['access-control-allow-origin'], 'CORS origin was not exact').toBe(
    PWA_ORIGIN,
  );
  expect(preflightHeaders['access-control-allow-credentials'], 'CORS credentials changed').toBe(
    'true',
  );
  requireCondition(
    String(preflightHeaders['access-control-allow-methods']).includes('POST'),
    'CORS preflight did not allow POST',
  );
  const allowedHeaders = String(preflightHeaders['access-control-allow-headers']).toLowerCase();
  requireCondition(
    allowedHeaders.includes('authorization') && allowedHeaders.includes('content-type'),
    'CORS preflight did not allow required headers',
  );

  // Step 2: manager login and protected canary read. Arm all response waits
  // before submit so fast production responses cannot race the assertions.
  const loginResponsePromise = page.waitForResponse((response) =>
    isApiResponse(response, '/auth/login', 'POST'),
  );
  const playersResponsePromise = page.waitForResponse((response) =>
    isApiResponse(response, '/players', 'GET'),
  );
  const productsResponsePromise = page.waitForResponse((response) =>
    isApiResponse(response, '/products', 'GET'),
  );

  await page.getByLabel('Identifier').fill('Loyiso');
  await page.getByLabel('Password').fill(password!);
  await page.getByRole('button', { name: 'Log in' }).click();

  const [loginResponse, playersResponse, productsResponse] = await Promise.all([
    loginResponsePromise,
    playersResponsePromise,
    productsResponsePromise,
  ]);
  await requireBrowserCors(loginResponse, 'Login');
  await requireBrowserCors(playersResponse, 'Player roster');
  await requireBrowserCors(productsResponse, 'Product catalogue');
  await expect(page).toHaveURL(`${PWA_ORIGIN}/log-session`);
  await expect(page.getByRole('heading', { name: 'Log Session' })).toBeVisible();

  await page.getByRole('link', { name: 'Players' }).click();
  const playerSearch = page.getByLabel('Search players by name');
  await expect(playerSearch).toBeVisible();
  await playerSearch.fill(CANARY_NAME);
  const roster = page.getByRole('list', { name: 'Player roster' });
  const canaryNames = roster.getByText(CANARY_NAME, { exact: true });
  await expect(canaryNames).toHaveCount(1);
  const canaryRow = canaryNames.locator('xpath=ancestor::li[1]');
  const canaryLinks = canaryRow.getByRole('link');
  await expect(canaryLinks).toHaveCount(1);
  const canaryHref = await canaryLinks.getAttribute('href');
  requireCondition(
    typeof canaryHref === 'string' && /^\/players\/[^/]+$/.test(canaryHref),
    'Synthetic canary did not resolve to one server-backed player',
  );

  // Hydrate and select the canary while online, then perform capture offline.
  await page.getByRole('link', { name: 'Log Session' }).click();
  await page.getByLabel('Search players').fill(CANARY_NAME);
  const playerList = page.getByRole('list', { name: 'Players' });
  const canaryButton = playerList.getByRole('button', { name: CANARY_NAME, exact: true });
  await expect(canaryButton).toHaveCount(1);
  const entitlementResponsePromise = page.waitForResponse((response) =>
    isApiResponse(response, /^\/players\/[^/]+\/entitlements$/, 'GET'),
  );
  await canaryButton.click();
  await requireBrowserCors(await entitlementResponsePromise, 'Entitlement balance');

  await page.clock.setFixedTime(new Date(SMOKE_TIME));
  await page.evaluate((stableIds) => {
    const ids = [...stableIds];
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      value: (): `${string}-${string}-${string}-${string}-${string}` => {
        const next = ids.shift();
        if (!next) throw new Error('Stable smoke action identity exhausted');
        return next as `${string}-${string}-${string}-${string}-${string}`;
      },
    });
  }, CLIENT_IDS);

  await context.setOffline(true);
  const syncRequestsBeforeCapture = syncRequestCount;
  await page.getByRole('radio', { name: 'PS5' }).check();
  await page.getByRole('button', { name: '20 min', exact: true }).click();
  await page.getByRole('radio', { name: 'Cash' }).check();
  await page.getByLabel('Cash amount').fill('0');
  await page.getByRole('button', { name: 'Confirm session' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Session logged' })).toBeVisible();
  await expect(
    page.getByLabel('Sync status').getByText(
      'Offline — 2 items are saved on this device and will sync when connected.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry sync' })).toBeDisabled();
  expect(syncRequestCount, 'Offline capture attempted a sync request').toBe(syncRequestsBeforeCapture);

  const localState = await page.evaluate(
    async ({ ids, suppliedPassword, smokeTime }) => {
      const db = await new Promise<IDBDatabase>((resolve, reject) => {
        const open = indexedDB.open('funhouse-revenue');
        open.onsuccess = () => resolve(open.result);
        open.onerror = () => reject(open.error);
      });
      const getAll = (storeName: string): Promise<unknown[]> =>
        new Promise((resolve, reject) => {
          const request = db.transaction(storeName, 'readonly').objectStore(storeName).getAll();
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
      const getOne = (storeName: string, key: IDBValidKey): Promise<unknown> =>
        new Promise((resolve, reject) => {
          const request = db.transaction(storeName, 'readonly').objectStore(storeName).get(key);
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });

      const [queueValues, sessionRow, paymentRow, sessionMeta] = await Promise.all([
        getAll('sync_queue'),
        getOne('sessions', ids[0]),
        getOne('payments', ids[1]),
        getOne('meta', 'session'),
      ]);
      const queue = queueValues as Array<Record<string, unknown>>;
      const expectedQueue = queue.filter((row) => ids.includes(String(row.client_id)));
      const entities = expectedQueue.map((row) => String(row.entity)).sort();
      const queueValid =
        expectedQueue.length === 2 &&
        expectedQueue.every(
          (row) =>
            row.status === 'unsynced' && row.attempt_count === 0 && row.created_at === smokeTime,
        ) &&
        entities.join(',') === 'payment,session';

      const session = sessionRow as Record<string, unknown> | undefined;
      const payment = paymentRow as Record<string, unknown> | undefined;
      const recordsValid =
        session?.client_id === ids[0] &&
        session?.session_type === 'lounge' &&
        session?.console === 'PS5' &&
        session?.duration_minutes === 20 &&
        session?.started_at === smokeTime &&
        payment?.client_id === ids[1] &&
        payment?.method === 'cash' &&
        payment?.amount_cents === 0;

      const meta = sessionMeta as
        | { value?: { iv?: unknown; ciphertext?: unknown } }
        | undefined;
      const encryptedSession =
        typeof meta?.value?.iv === 'string' && typeof meta?.value?.ciphertext === 'string';

      const allValues: unknown[] = [];
      for (const storeName of Array.from(db.objectStoreNames)) {
        allValues.push(...(await getAll(storeName)));
      }
      db.close();
      const serialised = JSON.stringify(allValues);
      const noPassword = !serialised.includes(suppliedPassword);
      const noJwt = !/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(serialised);
      return { queueValid, recordsValid, encryptedSession, noPassword, noJwt };
    },
    { ids: CLIENT_IDS, suppliedPassword: password!, smokeTime: SMOKE_TIME },
  );
  requireCondition(localState.queueValid, 'Offline queue did not contain two safe unsynced actions');
  requireCondition(localState.recordsValid, 'Offline local session/payment records were incomplete');
  requireCondition(localState.encryptedSession, 'The persisted login session was not encrypted');
  requireCondition(localState.noPassword, 'A plaintext password was found in IndexedDB');
  requireCondition(localState.noJwt, 'A plaintext JWT was found in IndexedDB');

  // Step 4: reconnect and flush. Automatic online sync gets a bounded chance;
  // otherwise use the visible manual retry control.
  let syncResponse: Response | null = null;
  const automaticSync = page
    .waitForResponse((response) => isApiResponse(response, '/sync', 'POST'), {
      timeout: 8_000,
    })
    .catch(() => null);
  await context.setOffline(false);
  syncResponse = await automaticSync;
  if (!syncResponse) {
    const retry = page.getByRole('button', { name: 'Retry sync' });
    await expect(retry).toBeEnabled();
    const manualSync = page.waitForResponse((response) =>
      isApiResponse(response, '/sync', 'POST'),
    );
    await retry.click();
    syncResponse = await manualSync;
  }
  await requireBrowserCors(syncResponse, 'Offline queue sync');

  const syncBody = (await syncResponse.json()) as {
    results?: Array<{ client_id?: unknown; status?: unknown }>;
  };
  const syncResults = Array.isArray(syncBody.results) ? syncBody.results : [];
  const matchingResults = syncResults.filter((result) =>
    CLIENT_IDS.includes(String(result.client_id) as (typeof CLIENT_IDS)[number]),
  );
  requireCondition(
    matchingResults.length === 2 &&
      matchingResults.every(
        (result) => result.status === 'applied' || result.status === 'skipped',
      ),
    'Sync did not safely apply or idempotently skip both synthetic actions',
  );
  await expect(
    page.getByLabel('Sync status').getByText('Up to date — no items waiting to sync.'),
  ).toBeVisible();

  const reconciled = await page.evaluate(async (ids) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const open = indexedDB.open('funhouse-revenue');
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
    const rows = await Promise.all(
      ids.map(
        (id) =>
          new Promise<Record<string, unknown> | undefined>((resolve, reject) => {
            const request = db.transaction('sync_queue', 'readonly').objectStore('sync_queue').get(id);
            request.onsuccess = () => resolve(request.result as Record<string, unknown> | undefined);
            request.onerror = () => reject(request.error);
          }),
      ),
    );
    db.close();
    return rows.every((row) => row?.status === 'applied' || row?.status === 'skipped');
  }, CLIENT_IDS);
  requireCondition(reconciled, 'Local queue did not reconcile both synthetic actions');

  // Step 5: server-backed read-back of the exact deterministic synthetic rows.
  await page.getByRole('link', { name: 'Players' }).click();
  await page.getByLabel('Search players by name').fill(CANARY_NAME);
  const readBackRoster = page.getByRole('list', { name: 'Player roster' });
  const readBackName = readBackRoster.getByText(CANARY_NAME, { exact: true });
  await expect(readBackName).toHaveCount(1);
  const readBackLink = readBackName.locator('xpath=ancestor::li[1]').getByRole('link');
  await expect(readBackLink).toHaveCount(1);
  const historyResponsePromise = page.waitForResponse((response) =>
    isApiResponse(response, /^\/players\/[^/]+\/history$/, 'GET'),
  );
  await readBackLink.click();
  const historyResponse = await historyResponsePromise;
  await requireBrowserCors(historyResponse, 'Player history');
  const history = (await historyResponse.json()) as {
    sessions?: Array<Record<string, unknown>>;
    payments?: Array<Record<string, unknown>>;
  };
  const timeMatches = (value: unknown): boolean => {
    if (typeof value !== 'string') return false;
    return new Date(value).toISOString() === SMOKE_TIME;
  };
  requireCondition(
    Array.isArray(history.sessions) &&
      history.sessions.some(
        (row) =>
          row.reference === 'PS5' &&
          row.duration_minutes === 20 &&
          timeMatches(row.started_at),
      ),
    'Exact synthetic session was not returned by player history',
  );
  requireCondition(
    Array.isArray(history.payments) &&
      history.payments.some(
        (row) => row.method === 'cash' && row.amount_cents === 0 && timeMatches(row.paid_at),
      ),
    'Exact synthetic payment was not returned by player history',
  );

  const sessionRendered = await page
    .getByRole('list', { name: 'Sessions' })
    .locator('li')
    .evaluateAll((rows) =>
      rows.some((row) => row.textContent?.includes('PS5') && row.textContent.includes('20 min')),
    );
  const paymentRendered = await page
    .getByRole('list', { name: 'Payments' })
    .locator('li')
    .evaluateAll((rows) =>
      rows.some((row) => row.textContent?.includes('R0.00') && row.textContent.includes('Cash')),
    );
  requireCondition(sessionRendered, 'Synthetic PS5 session was not rendered');
  requireCondition(paymentRendered, 'Synthetic zero-value cash payment was not rendered');

  // Step 6 (browser half): actual credentialed responses passed exact-origin
  // CORS, all traffic stayed on HTTPS, and no API fetch targeted the PWA host.
  requireCondition(!insecureRequestObserved, 'An insecure HTTP browser request was observed');
  requireCondition(
    !browserSecurityDiagnosticObserved,
    'A mixed-content or CORS browser diagnostic was observed',
  );
  requireCondition(
    !apiPathFetchedFromPwaOrigin,
    'An API fetch was incorrectly sent to the PWA origin',
  );

  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page).toHaveURL(`${PWA_ORIGIN}/login`);
});
