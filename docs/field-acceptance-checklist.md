# Phase 1 Revenue PWA Field-Acceptance Checklist

This is the final gate between automated production verification and using the
Revenue PWA in the lounge with real learner data. It proves that the installed
app works on the actual field device, survives an offline close and relaunch,
and is practical at lounge pace without falling back to paper.

Run this rehearsal with **only** the retained synthetic player
`API Verification Canary v1`. Never enter a real learner's name or details
until every required result below is **PASS**.

## How to run this gate

This checklist is deliberately operator-friendly. The operator uses only the
normal app and device controls; no developer tools are required.

1. Kiro or the release operator completes the automated release evidence in
   Section 1.
2. The lounge operator completes Sections 2–5 on the actual device, one step at
   a time, and reports **PASS** or **FAIL** plus a short non-sensitive note.
3. Kiro records the results in a pull request without credentials, tokens,
   screenshots of player data, or personal information.
4. The founder completes the security confirmation and gate decision in
   Section 6.

A failed check is useful evidence, not permission to continue. Follow the stop
rules immediately.

## Non-sensitive acceptance record

Complete this block for the release under test:

```text
Test date (Africa/Johannesburg):
Production main SHA (full 40 characters):
Visible app release (first 7 SHA characters):
PWA production deployment link or ID:
API production deployment link or ID:
Verify Live API Role Access run link or ID:
Verify Live PWA Browser first run link or ID:
Verify Live PWA Browser replay run link or ID:
14-table and policy catalogue evidence reference/date:
Security Advisor evidence reference/date:
Last database migration, role, grant, or policy change date:
Stable PWA origin: https://funhouse-revenue-pwa.vercel.app
API origin: https://fun-house-lms.vercel.app
Device model:
Operating-system version:
Browser or installed-PWA version:
Test mode: clean install and existing-app upgrade
Operator role (do not record the person's name here):
Maximum acceptable time for five captures (set before testing):
Rehearsal start time (Africa/Johannesburg):
Rehearsal end time (Africa/Johannesburg):
History session count before rehearsal:
```

Never record a password, JWT, learner name, player identifier, device serial
number, telephone number, email address, or screenshot containing roster data.

## 1. Release and security prerequisites

Kiro or the release operator completes these before the physical rehearsal.

- [ ] The recorded SHA is the current `main` SHA.
- [ ] Both Vercel Production deployments attached to that SHA completed
      successfully; neither build was skipped.
- [ ] The app visibly shows **Release `<short-sha>`**, and those seven characters
      match the start of the recorded full SHA before and after login.
- [ ] **Verify Live API Role Access** passed for that SHA after the API
      production deployment.
- [ ] **Verify Live PWA Browser** passed twice for that same SHA after the
      role-access run. The first used `applied-or-skipped`; the second selected
      `skipped` and proved replay of the workflow's stable action identities.
      The physical operator must not manufacture a duplicate replay.
- [ ] The stable PWA and API origins above are unchanged and use HTTPS.
- [ ] The recorded catalogue evidence confirms all 14 tables have RLS enabled,
      not forced, and no table is owned by `funhouse_runtime`.
- [ ] The same evidence confirms the 24 reviewed policies target only
      `funhouse_runtime`, and the consent trigger function has the fixed empty
      search path.
- [ ] The recorded Supabase Security Advisor evidence reports zero errors and
      zero warnings.
- [ ] Catalogue/policy and Security Advisor observations are no more than seven
      days old. Rerun both after any migration or out-of-band role, membership,
      grant, ownership, function, or policy change regardless of age.
- [ ] The operator has the approved `Loyiso` and second seeded-role
      password-manager entries. Neither value has been copied into this record,
      chat, a screenshot, or a workflow input.

**Prerequisite result: PASS / FAIL**

A synthetic-only rehearsal may diagnose a device while a security prerequisite
is pending, but real learner data remains prohibited and the final gate cannot
pass.

## 2. Physical-device install, upgrade and role transition

Complete these checks on the device configuration that will be used in the
lounge. A clean installation on a fresh browser profile or fresh device is
mandatory. If any lounge device has run an earlier release, an in-place upgrade
on at least one such device is also mandatory; **N/A** is allowed only when no
prior FunHouse PWA installation exists anywhere in the rollout. Every device
approved for real use must show the accepted release before GO.

### 2.1 Install and launch

1. Open `https://funhouse-revenue-pwa.vercel.app` in the supported browser.
2. Confirm the browser offers **Install** or **Add to Home Screen**.
3. Install the app, close the browser tab, and launch **FunHouse Revenue** from
   its installed icon.
4. Find the small **Release** label at the bottom of the app. Confirm its seven
   characters match the start of the recorded production SHA.
5. Close the installed app completely, launch it again, and confirm the login
   screen and the same Release label appear without a blank page or endless
   loading state.

- [ ] Installation or Add to Home Screen was available.
- [ ] Launch from the installed icon succeeded.
- [ ] Visible Release matched the candidate SHA.
- [ ] Close and relaunch succeeded with the same Release.

### 2.2 Existing-app upgrade and seeded-role transition

If any rollout device already had FunHouse Revenue installed, use one of those
devices before replacing the app:

1. Close the app completely and reopen it twice so the latest service worker and
   app code can activate.
2. Confirm the visible Release matches the candidate SHA. A stale Release is a
   FAIL even if login works.
3. Sign in as `loyiso` using the password manager. Capitalisation and accidental
   spaces around the identifier must not matter.
4. Confirm login completes and the manager navigation appears.
5. Sign out. Before entering another credential, confirm no roster or protected
   manager screen remains visible.
6. Sign in as the approved seeded founder account using its separate
   password-manager entry. Confirm founder navigation appears and manager-only
   navigation is absent.
7. Sign out and sign back in as `Loyiso`; confirm manager navigation returns and
   founder-only navigation is absent.

- [ ] Existing app upgraded to the visible candidate Release. This may be marked
      **N/A** only when the rollout has no earlier installation.
- [ ] Manager → signed-out → founder → signed-out → manager transitions showed
      only the current role's navigation.
- [ ] No protected screen remained visible between accounts.

**Install, upgrade and role-transition result: PASS / FAIL**

## 3. Five-session offline durability rehearsal

Use only `API Verification Canary v1`. Keep all cash amounts at **R0** so this
rehearsal does not create reportable revenue. Do not choose **Entitlement draw**
because a field test must not consume a balance.

### 3.1 Prepare online

1. Sign in as `Loyiso` while online and confirm the visible Release still
   matches the candidate SHA.
2. Open **Players**, search for `API Verification Canary v1`, and confirm it is
   visible.
3. Open its history, record the existing **session count** in the acceptance
   record, then return to **Log Session** and confirm the synthetic player is
   available.
4. Wait for the exact status **Up to date — no items waiting to sync.** The
   starting waiting count must be zero, with no rejected, blocked, or
   quarantined warning.
5. Record the rehearsal start time immediately before switching offline.

- [ ] Visible Release matched the candidate SHA.
- [ ] Synthetic player was available in Players and Log Session.
- [ ] Existing history count and rehearsal start time were recorded.
- [ ] Starting waiting count was exactly zero with no sync warning.

### 3.2 Capture while offline

Turn off both Wi-Fi and mobile data, or enable flight mode. Do not merely move
away from the access point. Confirm the device reports that it is offline.

Capture these five sessions in order. For each one select **Cash**, enter `0`,
and confirm:

| Rehearsal session | Console | Duration | Payment |
| --- | --- | --- | --- |
| 1 | PS5 | 20 min | Cash R0 |
| 2 | PS4 | 60 min | Cash R0 |
| 3 | PS5 | 120 min | Cash R0 |
| 4 | PS4 | Custom 45 min | Cash R0 |
| 5 | PS5 | Custom 90 min | Cash R0 |

After every confirmation, verify that the app reports the capture as saved and
that the waiting count increases by exactly two: `2`, `4`, `6`, `8`, then `10`.
Each capture creates one session action and one R0 payment action.

- [ ] All five captures completed without connectivity.
- [ ] No capture was entered twice.
- [ ] Waiting counts were exactly 2, 4, 6, 8 and 10.
- [ ] The operator needed no developer assistance and did not use paper.

Record only aggregate usability evidence:

```text
Approximate time for all five captures:
Pre-set maximum acceptable time:
Completed within the pre-set maximum: YES / NO
Any label or step that caused hesitation (no learner details):
Operator assessment: usable at lounge pace / not usable at lounge pace
```

### 3.3 Close and relaunch while still offline

1. Keep the device offline.
2. Close the installed app completely by removing it from the device's recent
   apps; do not leave it suspended in the background.
3. Relaunch it from the installed icon.
4. Confirm the app shell opens offline and the waiting count is still present.
5. Confirm no saved capture disappeared and no duplicate appeared.

- [ ] Installed app relaunched while offline.
- [ ] All queued actions survived the close/relaunch.
- [ ] No missing or duplicate capture was observed.

**Offline durability result: PASS / FAIL**

## 4. Reconnect, reconcile and read back

1. Restore normal connectivity.
2. Wait for automatic sync. If it does not start, use the visible **Retry sync**
   action once.
3. Confirm the waiting count reaches zero and the app shows **Up to date — no
   items waiting to sync.**
4. Confirm there is no rejected, blocked, or quarantined item, then record the
   rehearsal end time.
5. Open **Players**, select `API Verification Canary v1`, and inspect history.
6. Confirm the history session count increased by exactly five. Identify the
   five new sessions by timestamps between the recorded rehearsal start/end
   times; each must appear once with the matching console, duration, Cash method,
   and R0 amount.
7. Close and reopen the app online, return to history, and confirm the count did
   not increase again.

- [ ] Sync completed automatically or after one visible retry.
- [ ] Final waiting count is zero with the exact up-to-date status.
- [ ] No action is rejected, blocked, or quarantined.
- [ ] History increased by exactly five sessions in the rehearsal window.
- [ ] All five new sessions and payments have the expected values.
- [ ] Another relaunch did not increase the count.

The relaunch check detects accidental duplicate capture; it is not an
idempotent network replay. Stable-identity replay is proven by the required
second same-SHA protected browser run in Section 1.

**Reconciliation result: PASS / FAIL**

## 5. Operator comprehension and recovery

Without developer help, ask the operator to show:

- [ ] where a new lounge session is logged;
- [ ] how to recognise that work is saved while offline;
- [ ] how to see whether anything is waiting to sync;
- [ ] where to retry sync after connectivity returns;
- [ ] where to find the synthetic player's history;
- [ ] how to sign out safely.

Ask one final question: **Can this replace paper for normal lounge session
capture without slowing the queue?**

```text
Operator answer: YES / NO
Non-sensitive reason:
```

This section is **PASS** only when all six demonstrations succeed without help,
no paper fallback occurred, all five captures completed within the maximum time
set before testing, and the operator answered **YES**. Otherwise it is **FAIL**.

**Operator-comprehension result: PASS / FAIL**

## Stop rules

Mark the rehearsal **FAIL** and stop before real learner use if any of these
occurs:

- login hangs, silently returns to the form, or repeatedly reports an unexplained
  connection/storage error;
- the installed app cannot open while offline after it was loaded online;
- a capture is missing, duplicated, or attributed to the wrong account;
- protected data appears before authentication or crosses an account boundary;
- the queue does not survive a close/relaunch;
- an action is rejected, blocked, quarantined, or remains waiting after one
  reconnect and one retry;
- history disagrees with console, duration, payment method, amount, or count;
- the operator needs paper or developer assistance to complete normal capture;
- any security prerequisite remains unresolved when real learner use is due to
  begin.

Do not repair production data manually and do not repeat captures merely to make
counts look correct. Record the failed section and a non-sensitive description,
then preserve the device state until the cause is understood.

## Rollback and retest

1. Stop the pilot; continue the prior approved operating process without adding
   real data to the failed release.
2. If a deployment caused the failure, promote the previous working Vercel
   deployment for only the affected component.
3. Fix the defect through a normal feature branch and reviewed pull request.
4. Require green CI, successful affected Vercel Production deployments, and
   same-SHA live API and browser verification.
5. Repeat the failed physical-device scenario, then rerun this entire checklist.

A headless browser pass alone does not override a physical-device failure.

## 6. Sign-off and gate decision

All section results and all prerequisites must be **PASS**.

```text
Prerequisites: PASS / FAIL
Install, upgrade and role transition: PASS / FAIL
Offline durability: PASS / FAIL
Reconciliation: PASS / FAIL
Operator comprehension: PASS / FAIL

Manager/operator approval (role and date only in this repository):
Founder approval (role and date only in this repository):
Final decision: GO / NO-GO
Accepted production SHA and visible Release:
Accepted device/OS/browser:
Acceptance date (Africa/Johannesburg):
```

### GO

A **GO** permits controlled Phase 1 use with real learner data on the accepted
release and device configuration. Update the README rollout status with the
accepted SHA, device/browser, date, and non-sensitive evidence link. Continue
monitoring sync health during the initial lounge sessions.

Only after this GO may a separate Phase 2 Lesson Engine requirements/design spec
begin.

### NO-GO

A **NO-GO** keeps real learner use and Phase 2 blocked. Follow the stop,
rollback, and retest procedure; do not weaken an acceptance criterion to close
the gate.
