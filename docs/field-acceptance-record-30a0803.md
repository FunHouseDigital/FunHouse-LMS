# Phase 1 Field-Acceptance Record — Release `30a0803`

This is the working acceptance record for the release currently under test. It
instantiates the record template in
[`field-acceptance-checklist.md`](./field-acceptance-checklist.md) for production
`main` SHA `30a0803`.

> **Candidate history.** `184cec0` → `5f0cf74` (docs-only) → `59fc311` (installed-PWA
> session-lifecycle fix + smoke coverage) → **`30a0803`**. Since `59fc311` the
> following merged: the account-bound **founder password rotation** capability
> (#49 — `funhouse_pipeline/db/bootstrap_user.py`, a new `Rotate Live Founder
> Password` workflow, runbook) and the **in-app guided founder-reset helper**
> (#50 — a credential-free helper on the PWA login screen). #50 changes the PWA
> login screen, so all SHA-pinned automated evidence was re-run against
> `30a0803`; earlier evidence is superseded.
>
> **Do not merge this record to `main` until after the physical gate and final
> sign-off.** Merging advances `main`, redeploys, and moves the candidate SHA —
> which would invalidate this evidence and force another re-verification. Keep
> it on the branch; run the on-device rehearsal against `30a0803`.

Kiro has completed the automated release evidence and the read-only database
preflight (Section 1). The lounge operator completes Sections 2–5 on the actual
device, and the founder completes the security confirmation and gate decision in
Section 6.

**No passwords, JWTs, learner names, player identifiers, device serial numbers,
telephone numbers, email addresses, or roster screenshots may be added to this
file.**

## Non-sensitive acceptance record

```text
Test date (Africa/Johannesburg):                        [operator to complete on rehearsal day]
Production main SHA (full 40 characters):               30a0803cde8226d215811a839557bc334e13d0c2
Visible app release (first 7 SHA characters):           30a0803
PWA production deployment link or ID:                   Vercel funhouse-revenue-pwa Production, commit 30a0803 — success, deployment id 5935915745 (https://vercel.com/fun-house-digital/funhouse-revenue-pwa/9RpXknjn6w7RKBFtaie3NUUvwP8B); live bundle serves Release 30a0803 and includes the in-app founder-reset helper
API production deployment link or ID:                   Vercel fun-house-lms Production, commit 30a0803 — success, deployment id 5935918647 (https://vercel.com/fun-house-digital/fun-house-lms/FJeXfTfQNSdGGN4rTWkptA7rA7dB); live /health responds {"status":"ok"}
Verify Live API Role Access run link or ID:             https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31975367437
Verify Live PWA Browser first run link or ID:           https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31975414910 (mode: applied-or-skipped)
Verify Live PWA Browser replay run link or ID:          https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31975477906 (mode: skipped, stable-identity replay)
Prepare Phase 1 Field Acceptance run link or ID/date:   https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31975369914 (2026-08-16 22:05 UTC)
Security Advisor evidence reference/date:               0 errors, 0 warnings, 4 info suggestions — observed 2026-08-14 (Africa/Johannesburg), within the 7-day window; no database migration or policy change merged since (5f0cf74 → 30a0803 changes are PWA + offline pipeline tooling only), and the re-run preflight re-confirmed 14/14 tables and 24/24 policies. Founder to re-confirm 0/0 if any doubt.
Last database migration, role, grant, or policy change date: [founder to confirm none occurred after the 2026-08-16 preflight]
Stable PWA origin: https://funhouse-revenue-pwa.vercel.app
API origin: https://fun-house-lms.vercel.app
Device model:                                           [operator to complete]
Operating-system version:                               [operator to complete]
Browser or installed-PWA version:                       [operator to complete]
Test mode: clean install and existing-app upgrade       [operator to circle applicable modes]
Operator role (do not record the person's name here):   [operator to complete]
Maximum acceptable time for five captures (set before testing): [operator to set before testing]
Rehearsal start time (Africa/Johannesburg):             [operator to complete]
Rehearsal end time (Africa/Johannesburg):               [operator to complete]
History session count before rehearsal:                 [operator to complete]
```

## 1. Release and security prerequisites

Automated release and read-only database evidence — completed by Kiro against
`main` SHA `30a0803`.

- [x] The recorded SHA is the current `main` SHA. — `30a0803cde8226d215811a839557bc334e13d0c2`.
- [x] Both Vercel Production deployments attached to that SHA completed
      successfully; neither build was skipped. — Vercel commit statuses
      `Vercel – fun-house-lms` and `Vercel – funhouse-revenue-pwa` are both
      `success` for `30a0803`; Production deployment objects `5935918647` (API)
      and `5935915745` (PWA) report state `success`. Kiro self-verified the live
      PWA bundle serves `Release 30a0803` (and now includes the founder-reset
      helper) and the live API `/health` responds `{"status":"ok"}`.
- [x] The app visibly shows **Release `30a0803`**, and those seven characters
      match the start of the recorded full SHA. — Confirmed in the live PWA
      bundle; operator re-confirms on-device before and after login in Section 2.
- [x] **Verify Live API Role Access** passed for that SHA after the API
      production deployment. — run 31975367437 (2026-08-16 22:05 UTC).
- [x] **Verify Live PWA Browser** passed twice for that same SHA after the
      role-access run. The first used `applied-or-skipped`; the second selected
      `skipped` and proved replay of the workflow's stable action identities.
      Both runs also confirmed a new browser page restored the protected session
      from origin storage without another login. — runs 31975414910 (22:06) then
      31975477906 (22:07).
- [x] The stable PWA and API origins above are unchanged and use HTTPS.
- [x] **Prepare Phase 1 Field Acceptance** passed for the recorded SHA. Its
      summary confirms 14/14 expected tables, 24/24 exact runtime-only policies,
      the fixed empty consent-function search path, and runtime least privilege.
      — run 31975369914.
- [x] The preflight and recorded Supabase Security Advisor observation are no
      more than seven days old. — Preflight 2026-08-16 22:05 UTC; Advisor
      observed 2026-08-14.
- [x] The recorded Supabase Security Advisor evidence reports zero errors and
      zero warnings. — 0 errors, 0 warnings (4 info-level suggestions, which do
      not block the gate), observed 2026-08-14.
- [ ] The founder confirms no relevant database change occurred after the
      preflight and Advisor observation. — **Founder to confirm.** (The
      `59fc311` → `30a0803` changes are PWA and offline-pipeline tooling only
      and touched no database object; no migration was added — the schema
      remains migrations 001–010.)
- [ ] The operator has the approved `Loyiso` and second seeded-role
      password-manager entries. — **Loyiso available; Aya founder entry pending
      reset.** The founder resets it via the owner-role **Rotate Live Founder
      Password** workflow (#49), now guided from the app's login screen (#50),
      and stores the new value in the password manager before final GO. Neither
      value has been copied into this record, chat, a screenshot, or a workflow
      input.

**Prerequisite result:** Automated evidence **PASS** for `30a0803`. Two
founder/operator-only items remain open: (1) founder confirmation of no
post-preflight database change, and (2) the founder resetting and storing the
Aya credential. The prerequisite is not final **PASS** until those are
confirmed, development is frozen on this release, and the physical rehearsal is
complete.

## 2. Physical-device install, upgrade and role transition

Completed on the lounge device by the operator — see checklist Section 2.

### 2.1 Install and launch

- [ ] Installation or Add to Home Screen was available.
- [ ] Launch from the installed icon succeeded.
- [ ] Visible Release matched the candidate SHA (`30a0803`).
- [ ] Close and relaunch succeeded with the same Release.

### 2.2 Existing-app upgrade and seeded-role transition

- [ ] Existing app upgraded to the visible candidate Release (or **N/A** if the
      rollout has no earlier installation).
- [ ] Manager → signed-out → founder → signed-out → manager transitions showed
      only the current role's navigation.
- [ ] No protected screen remained visible between accounts.

**Install, upgrade and role-transition result:** PASS / FAIL — [operator]

## 3. Five-session offline durability rehearsal

Use only `API Verification Canary v1`, Cash **R0**, and never Entitlement draw.

### 3.1 Prepare online

- [ ] Visible Release matched the candidate SHA (`30a0803`).
- [ ] Synthetic player was available in Players and Log Session.
- [ ] Existing history count and rehearsal start time were recorded.
- [ ] Starting waiting count was exactly zero with no sync warning.

### 3.2 Capture while offline

- [ ] All five captures completed without connectivity.
- [ ] No capture was entered twice.
- [ ] Waiting counts were exactly 2, 4, 6, 8 and 10.
- [ ] The operator needed no developer assistance and did not use paper.

```text
Approximate time for all five captures:
Pre-set maximum acceptable time:
Completed within the pre-set maximum: YES / NO
Any label or step that caused hesitation (no learner details):
Operator assessment: usable at lounge pace / not usable at lounge pace
```

### 3.3 Close and relaunch while still offline

- [ ] Installed app relaunched while offline.
- [ ] All queued actions survived the close/relaunch.
- [ ] No missing or duplicate capture was observed.

**Offline durability result:** PASS / FAIL — [operator]

## 4. Reconnect, reconcile and read back

- [ ] Sync completed automatically or after one visible retry.
- [ ] Final waiting count is zero with the exact up-to-date status.
- [ ] No action is rejected, blocked, or quarantined.
- [ ] History increased by exactly five sessions in the rehearsal window.
- [ ] All five new sessions and payments have the expected values.
- [ ] Another relaunch did not increase the count.

**Reconciliation result:** PASS / FAIL — [operator]

## 5. Operator comprehension and recovery

- [ ] where a new lounge session is logged;
- [ ] how to recognise that work is saved while offline;
- [ ] how to see whether anything is waiting to sync;
- [ ] where to retry sync after connectivity returns;
- [ ] where to find the synthetic player's history;
- [ ] how to sign out safely.

```text
Operator answer: YES / NO
Non-sensitive reason:
```

**Operator-comprehension result:** PASS / FAIL — [operator]

## 6. Sign-off and gate decision

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
