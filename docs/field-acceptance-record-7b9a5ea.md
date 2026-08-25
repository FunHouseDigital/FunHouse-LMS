# Phase 1 Field-Acceptance Record — Release `7b9a5ea`

This is the working acceptance record for the release currently deployed and
under test. It instantiates the record template in
[`field-acceptance-checklist.md`](./field-acceptance-checklist.md) for production
`main` SHA `7b9a5ea1ccf1b7bb84db06fa151e109a26179ab9`.

> **Why this supersedes the `0b6df3d` record.** PR #53 intentionally merged the
> field-acceptance rehearsal assistant to `main`, advancing the candidate to
> `7b9a5ea`. Both Vercel projects deploy every push to `main`, and the PWA bakes
> the commit SHA into its visible Release label. The live app therefore now
> shows **Release `7b9a5ea`**. All SHA-pinned automated evidence was rerun against
> that exact production release after both deployments succeeded.
>
> **Do not merge this record to `main` until after the physical gate and final
> sign-off.** Merging advances `main`, redeploys both projects, and moves the
> visible Release. Keep this record on its branch and run the on-device
> rehearsal against `7b9a5ea`. See
> [`CONTRIBUTING.md`](../CONTRIBUTING.md) → *Field-acceptance records must not
> be merged before GO*.

Kiro has completed the automated release evidence and read-only database
preflight (Section 1) against `7b9a5ea`. The founder confirmed a current
Security Advisor result of 0 errors and 0 warnings on 2026-08-25. The lounge
operator completes Sections 2–5 on the actual device, and the founder confirms
no later relevant database change and records the gate decision in Section 6.

The in-app rehearsal assistant is a convenience guide only. The authoritative
requirements and final decision remain in
[`field-acceptance-checklist.md`](./field-acceptance-checklist.md) and this
record.

**No passwords, JWTs, learner names, player identifiers, device serial numbers,
telephone numbers, email addresses, or roster screenshots may be added to this
file.**

## Non-sensitive acceptance record

```text
Test date (Africa/Johannesburg):                        [operator to complete on rehearsal day]
Production main SHA (full 40 characters):               7b9a5ea1ccf1b7bb84db06fa151e109a26179ab9
Visible app release (first 7 SHA characters):           7b9a5ea
PWA production deployment link or ID:                   Vercel funhouse-revenue-pwa Production, commit 7b9a5ea — success (https://vercel.com/fun-house-digital/funhouse-revenue-pwa/3cUfbwXYkXDM9G96m6fXwKxgPKs2); live bundle serves Release 7b9a5ea and includes the read-only field-acceptance rehearsal assistant
API production deployment link or ID:                   Vercel fun-house-lms Production, commit 7b9a5ea — success (https://vercel.com/fun-house-digital/fun-house-lms/5TEvuCP2Fw9oRngLNgD3yuj2iFbQ); live /health responds {"status":"ok"}
Verify Live API Role Access run link or ID:             https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32890655986
Verify Live PWA Browser first run link or ID:           https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32890730880 (mode: applied-or-skipped)
Verify Live PWA Browser replay run link or ID:          https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32890985837 (mode: skipped, stable-identity replay)
Prepare Phase 1 Field Acceptance run link or ID/date:   https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32890657288 (2026-08-25 19:37 UTC)
Security Advisor evidence reference/date:               0 errors, 0 warnings and 4 info suggestions — founder-observed 2026-08-25 (Africa/Johannesburg); no screenshot, project identifier or exported report was retained.
Last database migration, role, grant, or policy change date: [founder to confirm none occurred after the 2026-08-25 preflight — schema remains migrations 001–010]
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
`main` SHA `7b9a5ea1ccf1b7bb84db06fa151e109a26179ab9`.

- [x] The recorded SHA is the current `main` SHA. —
      `7b9a5ea1ccf1b7bb84db06fa151e109a26179ab9`.
- [x] Both Vercel Production deployments attached to that SHA completed
      successfully; neither build was skipped. — Vercel commit statuses
      `Vercel – fun-house-lms` and `Vercel – funhouse-revenue-pwa` are both
      `success` for `7b9a5ea`. Kiro self-verified the live PWA bundle serves
      `Release 7b9a5ea` and the live API `/health` responds `{"status":"ok"}`.
- [x] The app visibly shows **Release `7b9a5ea`**, and those seven characters
      match the start of the recorded full SHA. — Confirmed in the live PWA
      bundle and both protected browser runs; the operator re-confirms on-device
      before and after login in Section 2.
- [x] **Verify Live API Role Access** passed for that SHA after the API
      production deployment. — run 32890655986 (2026-08-25 19:37 UTC).
- [x] **Verify Live PWA Browser** passed twice for that same SHA after the
      role-access run. The first used `applied-or-skipped`; the second selected
      `skipped` and proved replay of the workflow's stable action identities. —
      runs 32890730880 then 32890985837. The physical operator must not
      manufacture a duplicate replay.
- [x] The stable PWA and API origins above are unchanged and use HTTPS.
- [x] **Prepare Phase 1 Field Acceptance** passed for the recorded SHA. Its
      summary confirms 14/14 expected tables, 24/24 exact runtime-only policies,
      the fixed empty consent-function search path, and runtime least privilege.
      — run 32890657288 (2026-08-25 19:37 UTC).
- [x] The preflight and recorded Supabase Security Advisor observation are no
      more than seven days old. — Database preflight and founder observation
      both completed on 2026-08-25.
- [x] The recorded Supabase Security Advisor evidence reports zero errors and
      zero warnings. — Founder observed 0 errors, 0 warnings and 4 info-level
      suggestions on 2026-08-25; no screenshot, project identifier or exported
      report was retained.
- [ ] The founder confirms no relevant database change occurred after the
      preflight and Advisor observation. — **Founder to confirm.** No migration
      was added by PR #53; the repository schema remains migrations 001–010.
- [x] The operator has the approved `Loyiso` and second seeded-role
      password-manager entries. — The founder previously confirmed the Aya
      credential was saved without sharing its value. The Rotate Live Founder
      Password workflow succeeded
      ([run 32766576106](https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32766576106),
      2026-08-24), and current-release API role verification run 32890655986
      authenticated founder, manager and facilitator. No credential, token or
      roster data was logged or copied into this record, chat, a screenshot or
      a workflow input.

**Prerequisite result:** Exact-release automated evidence **PASS** for
`7b9a5ea`, the seeded credentials are ready, and the current Security Advisor
result is 0 errors and 0 warnings. One founder confirmation remains open:
confirmation that no relevant database change occurred after the 2026-08-25
preflight and Advisor observation. The prerequisite is not final **PASS** until
that is confirmed, development is frozen on this release, and the physical
rehearsal is complete.

A synthetic-only rehearsal may diagnose a device while a security prerequisite
is pending, but real learner data remains prohibited and the final gate cannot
pass.

## 2. Physical-device install, upgrade and role transition

Completed on the lounge device by the operator — see checklist Section 2.

### 2.1 Install and launch

- [ ] Installation or Add to Home Screen was available.
- [ ] Launch from the installed icon succeeded.
- [ ] Visible Release matched the candidate SHA (`7b9a5ea`).
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
Use the in-app **Field acceptance** guide for ordered convenience checks while
keeping this record authoritative.

### 3.1 Prepare online

- [ ] Visible Release matched the candidate SHA (`7b9a5ea`).
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
