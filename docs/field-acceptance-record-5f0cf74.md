# Phase 1 Field-Acceptance Record — Release `5f0cf74`

This is the working acceptance record for the release currently under test. It
instantiates the record template in
[`field-acceptance-checklist.md`](./field-acceptance-checklist.md) for production
`main` SHA `5f0cf74`.

> **Why `5f0cf74` and not `184cec0`?** The earlier record was pinned to
> `184cec0`. Merging that record (PR #43) advanced `main` to the merge commit
> `5f0cf74` and triggered fresh Vercel Production deployments, so the live app
> now shows **Release `5f0cf74`**. The delta from `184cec0` is documentation
> only (this record file); no application, API, or database behaviour changed.
> Because the gate requires the recorded SHA to equal current `main` and the
> on-device Release to match, all SHA-pinned automated evidence below was
> re-run against `5f0cf74`.
>
> **Do not merge this record to `main` until after the physical gate and final
> sign-off** — merging would again advance `main`, move the candidate SHA, and
> invalidate this evidence. Review it on the pull request; keep it on the branch.

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
Production main SHA (full 40 characters):               5f0cf74e12666b35f4fbed90c18037c6cd16e4ed
Visible app release (first 7 SHA characters):           5f0cf74
PWA production deployment link or ID:                   Vercel funhouse-revenue-pwa Production, commit 5f0cf74 — success, deployment id 5914640396 (https://vercel.com/fun-house-digital/funhouse-revenue-pwa/A9ULB7gWYkHDa2LPwGoafrRGo4YE); live bundle serves Release 5f0cf74
API production deployment link or ID:                   Vercel fun-house-lms Production, commit 5f0cf74 — success, deployment id 5914638496 (https://vercel.com/fun-house-digital/fun-house-lms/9KZ4yeurVCNyUedxRHarrx7b9oS1); live /health responds {"status":"ok"}
Verify Live API Role Access run link or ID:             https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31848380695
Verify Live PWA Browser first run link or ID:           https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31848425889 (mode: applied-or-skipped)
Verify Live PWA Browser replay run link or ID:          https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31848504235 (mode: skipped, stable-identity replay)
Prepare Phase 1 Field Acceptance run link or ID/date:   https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/31848383148 (2026-08-14 22:54 UTC)
Security Advisor evidence reference/date:               0 errors, 0 warnings, 4 info suggestions — observed 2026-08-14 (Africa/Johannesburg); DB unchanged since observation (184cec0 → 5f0cf74 delta is docs-only)
Last database migration, role, grant, or policy change date: [founder to confirm none occurred after 2026-08-14 preflight]
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
`main` SHA `5f0cf74`.

- [x] The recorded SHA is the current `main` SHA. — `5f0cf74e12666b35f4fbed90c18037c6cd16e4ed`.
- [x] Both Vercel Production deployments attached to that SHA completed
      successfully; neither build was skipped. — Vercel commit statuses
      `Vercel – fun-house-lms` and `Vercel – funhouse-revenue-pwa` are both
      `success` for `5f0cf74`; Production deployment objects `5914638496` (API)
      and `5914640396` (PWA) report state `success`. Kiro self-verified the live
      PWA bundle serves `Release 5f0cf74` and the live API `/health` responds
      `{"status":"ok"}`.
- [x] The app visibly shows **Release `5f0cf74`**, and those seven characters
      match the start of the recorded full SHA. — Confirmed in the live PWA
      bundle; operator re-confirms on-device before and after login in Section 2.
- [x] **Verify Live API Role Access** passed for that SHA after the API
      production deployment. — run 31848380695.
- [x] **Verify Live PWA Browser** passed twice for that same SHA after the
      role-access run. The first used `applied-or-skipped`; the second selected
      `skipped` and proved replay of the workflow's stable action identities.
      — runs 31848425889 then 31848504235.
- [x] The stable PWA and API origins above are unchanged and use HTTPS.
- [x] **Prepare Phase 1 Field Acceptance** passed for the recorded SHA. Its
      summary confirms 14/14 expected tables, 24/24 exact runtime-only policies,
      the fixed empty consent-function search path, and runtime least privilege.
      — run 31848383148.
- [x] The preflight and recorded Supabase Security Advisor observation are no
      more than seven days old. — Preflight 2026-08-14 22:54 UTC; Advisor
      observed 2026-08-14.
- [x] The recorded Supabase Security Advisor evidence reports zero errors and
      zero warnings. — 0 errors, 0 warnings (4 info-level suggestions, which do
      not block the gate), observed 2026-08-14.
- [ ] The founder confirms no relevant database change occurred after the
      preflight and Advisor observation. — **Founder to confirm.** (The
      `184cec0` → `5f0cf74` change is documentation only and touched no
      database object.)
- [ ] The operator has the approved `Loyiso` and second seeded-role
      password-manager entries. Neither value has been copied into this record,
      chat, a screenshot, or a workflow input. — **Operator/founder to confirm.**

**Prerequisite result:** Automated evidence **PASS** for `5f0cf74`. Two
founder/operator-only items remain open (founder confirmation of no
post-preflight database change, and password-manager entries available); the
prerequisite is not final **PASS** until those are confirmed.

## 2. Physical-device install, upgrade and role transition

Completed on the lounge device by the operator — see checklist Section 2.

### 2.1 Install and launch

- [ ] Installation or Add to Home Screen was available.
- [ ] Launch from the installed icon succeeded.
- [ ] Visible Release matched the candidate SHA (`5f0cf74`).
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

- [ ] Visible Release matched the candidate SHA (`5f0cf74`).
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
