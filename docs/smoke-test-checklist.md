# FunHouse Deployment Smoke-Test Checklist (Spec 3.5)

Proves the Revenue_PWA syncs to the **live** Container_API end to end after a
deployment (Req 10). Run it after Step 8 of `docs/deployment-runbook.md`. Every
step states an **exact action** and an **unambiguous observable pass result** —
if the observed result differs, the step **FAILS** (Req 10.6).

**Prerequisites:** the deployment is live; you have the Terraform outputs
`cloudfront_domain` (PWA URL) and `apprunner_url` (API URL), and a valid test
login credential seeded in the database.

Record each result as **PASS** / **FAIL**. All six must PASS to accept the
deployment.

| # | Step | Action | Expected result (PASS) |
| --- | --- | --- | --- |
| 1 | **Load PWA over HTTPS** | In a browser, open `https://<cloudfront_domain>`. Then attempt `http://<cloudfront_domain>`. | The app loads over **HTTPS**; the plain `http://` request **redirects to `https://`**; the browser offers **Install** (manifest parsed, service worker registered — check DevTools ▸ Application). — Req 6.2, 6.4, 6.5 |
| 2 | **Login** | Authenticate through the PWA against the live API with valid test credentials. | Response is **200** and a **JWT is stored**; a protected view renders. An unauthenticated request to a protected endpoint returns **401**. — Req 10.2 |
| 3 | **Offline capture** | Open DevTools ▸ Network and set **Offline** (or disable the device network). Capture a record in the PWA (e.g. a metric / attendance entry). | The capture **succeeds locally**; the UI confirms an **offline/queued** state; the record is present in **IndexedDB** (DevTools ▸ Application ▸ IndexedDB) with a pending/unsynced flag. — Req 10.3 |
| 4 | **Sync** | Re-enable the network. Trigger sync (or allow background sync to run). | The PWA issues **`POST https://<apprunner_url>/sync`** returning **HTTP 200**, and the response reports the queued action as **`applied`** (not `rejected`/`conflict`). The record's local state flips to **synced**. — Req 10.4 |
| 5 | **Read-back through the API** | Query the just-synced record through the API (e.g. the relevant player/history/roster/metrics endpoint), authenticated. | The API returns the record with the **same values that were captured offline** in Step 3 (ids/fields match). — Req 10.5 |
| 6 | **CORS** | With DevTools ▸ Console + Network open, observe the cross-origin call the PWA (CloudFront origin) makes to the API (App Runner origin) during Steps 2 and 4. | The browser completes the cross-origin request **without any CORS error**; the preflight/response carries **`Access-Control-Allow-Origin`** matching the CloudFront origin. — Req 7.3 |

## Encryption spot-check (supports Req 7.4)

While running the above, confirm every hop is encrypted:

- Step 1/2/4 requests are all **`https://`** (no mixed-content warnings).
- The API is reachable only over HTTPS (a plain `http://<apprunner_url>` request
  is refused/redirected — App Runner has no plaintext listener).

## Result

- [ ] Step 1 — Load PWA over HTTPS — **PASS / FAIL**
- [ ] Step 2 — Login — **PASS / FAIL**
- [ ] Step 3 — Offline capture — **PASS / FAIL**
- [ ] Step 4 — Sync (`POST /sync` → 200, `applied`) — **PASS / FAIL**
- [ ] Step 5 — Read-back through the API — **PASS / FAIL**
- [ ] Step 6 — CORS (no error, allow-origin present) — **PASS / FAIL**

**Deployment is accepted only when all six steps PASS.**
