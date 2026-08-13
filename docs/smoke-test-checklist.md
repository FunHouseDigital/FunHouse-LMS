# FunHouse Production PWA Smoke-Test Checklist

Proves the Revenue PWA syncs to the live Container API end to end after a
deployment. Record each result as **PASS** or **FAIL**. All six steps must pass
to accept the browser deployment.

## Prerequisites

Record the two exact HTTPS origins, without paths or trailing slashes:

```text
PWA_ORIGIN=https://funhouse-revenue-pwa.vercel.app
API_ORIGIN=https://fun-house-lms.vercel.app
```

For the current Vercel + Supabase rollout:

- The PWA project was built from repository root directory `web` with
  `VITE_API_BASE_URL=https://fun-house-lms.vercel.app`.
- The API project's `FUNHOUSE_CORS_ORIGINS` contains the exact `PWA_ORIGIN`.
- Run **Verify Live API Role Access** from `main` after the latest API
  deployment. Record the workflow run's `head_sha` and require it to match the
  API's Vercel Production deployment SHA.
- You have the seeded manager (`Loyiso`) password from the dedicated
  `LOYISO_BOOTSTRAP_PASSWORD` password-manager entry. Never write the password
  in this checklist, browser screenshots, workflow inputs, or chat.
- The retained synthetic player `API Verification Canary v1` is visible to the
  manager. Use only this player for the smoke capture; never use a real learner.

The same checks also apply to a future CloudFront/App Runner deployment by
substituting its PWA and API origins.

## Protected browser automation

The manually dispatched **Verify Live PWA Browser** workflow runs the automated
parts of all six checks from `main`. It requires the workflow SHA to remain the
current `main` commit, verifies successful Vercel-bot Production deployments and
approved project status targets attached to that SHA, reads
`LOYISO_BOOTSTRAP_PASSWORD` only from the protected `production` environment,
and writes only to `API Verification Canary v1`. Stable action identities make reruns idempotent. It publishes no browser
artifacts, credentials, JWTs, roster data, or record identifiers.

Headless automation verifies the installability prerequisites—manifest metadata,
loadable and decodable icon assets, HTTPS, deep-link fallback and an activated
service worker—and fails on observed mixed-content or CORS browser diagnostics.
Browser or operating-system presentation of the **Install** prompt remains a
manual check. Do not mark Step 1 fully accepted until that prompt is confirmed on a target
field device.

## Browser checks

| # | Step | Exact action | Expected result (PASS) |
| --- | --- | --- | --- |
| 1 | **HTTPS and installability** | Open `PWA_ORIGIN`, then refresh the deep link `PWA_ORIGIN/login`. In browser DevTools, open **Application → Manifest** and **Service Workers**. | Both routes load over HTTPS; the manifest has no errors and names **FunHouse Revenue**; `sw.js` is activated for scope `/`; the browser offers **Install** or reports the app as installable. No mixed-content warning appears. |
| 2 | **Login and protected read** | Sign in as `Loyiso`. Open **Players** and search for `API Verification Canary v1`. In DevTools **Network**, inspect the login and players requests. | `POST API_ORIGIN/auth/login` returns `200`; a JWT-backed session is established; `GET API_ORIGIN/players` returns `200`; the protected Players view renders the synthetic canary. No request is sent to the PWA origin as though it were the API. |
| 3 | **Offline capture** | While still signed in, open **Log Session** and make sure the canary appears. In DevTools **Network**, select **Offline**. Select `API Verification Canary v1`, `PS5`, `20 min`, **Cash**, amount `0`, then confirm. | Capture succeeds without a network response; the UI confirms it was saved; **Sync status** says one or more items are saved/waiting; the actions are present in **Application → IndexedDB** with an unsynced status. No plaintext password or JWT appears in IndexedDB. |
| 4 | **Reconnect and sync** | Clear the **Offline** setting. Click **Retry sync** if automatic sync does not start. Watch the Network panel. | The PWA sends `POST API_ORIGIN/sync`, receives `200`, and every canary action reports `applied` on first creation or `skipped` only when replaying the stable synthetic action identity; none reports `rejected`. Sync status returns to **Up to date — no items waiting to sync.** |
| 5 | **Read-back** | Open **Players**, select `API Verification Canary v1`, and inspect its history. | The API-backed history returns `200` and contains the newly captured `PS5`, 20-minute session and its zero-value cash payment. Values match the offline capture. |
| 6 | **CORS** | In DevTools **Console** and **Network**, inspect the cross-origin login, players, and sync traffic. Open the sync request's response headers and its OPTIONS preflight when shown. | No CORS error appears. The API response/preflight includes `Access-Control-Allow-Origin` equal to the exact `PWA_ORIGIN`; it is not `*`. All PWA and API requests use HTTPS. |

## Result

- [ ] Step 1 — HTTPS, deep-link fallback, manifest, service worker, installability — **PASS / FAIL**
- [ ] Step 2 — Login and protected player read — **PASS / FAIL**
- [ ] Step 3 — Offline synthetic session capture in IndexedDB — **PASS / FAIL**
- [ ] Step 4 — Reconnect and `POST /sync` → `200` / `applied|skipped` — **PASS / FAIL**
- [ ] Step 5 — Synthetic session read-back — **PASS / FAIL**
- [ ] Step 6 — Exact-origin CORS and HTTPS — **PASS / FAIL**

**Deployment is accepted only when all six steps pass.** If a step fails, stop
before using the PWA with real learner data and record the failed step and HTTP
status. Preserve the working API and any established PWA deployment, then roll
back only the component changed. When validating a replacement PWA project or
new environment, keep its generated hostname unpublished; remove any alias or
disable the replacement project if needed, and remove its exact API CORS origin
if abandoning that release.
