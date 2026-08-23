# Phase 1 Field-Acceptance Record — Release `0b6df3d`

This is the working acceptance record for the release currently deployed and
under test. It instantiates the record template in
[`field-acceptance-checklist.md`](./field-acceptance-checklist.md) for production
`main` SHA `0b6df3d`.

> **Why this supersedes the `30a0803` record.** The `30a0803` record was pinned
> to that SHA, but the docs re-pin itself was merged to `main` (PR #51), which
> advanced `main` to `0b6df3d` and — because both Vercel projects always build
> on every push to `main` (`ignoreCommand: "exit 1"`, the deliberate PR #34
> behaviour that keeps the API and PWA at the same SHA) — redeployed the PWA.
> The live installed app therefore now shows **Release `0b6df3d`**, not
> `30a0803`. The `30a0803` → `0b6df3d` change is a **single docs file** (this
> record's predecessor) with **no code and no database migration**, so the
> deployed bundle is functionally identical; only the embedded release label
> moved. The candidate is re-pinned to the SHA the lounge device will actually
> display, and all SHA-pinned automated evidence was re-run against `0b6df3d`.
>
> **Do not merge this record to `main` until after the physical gate and final
> sign-off.** Merging advances `main`, redeploys both projects, and moves the
> visible Release — which is exactly what invalidated the previous record.
> Keep this record on its branch; run the on-device rehearsal against
> `0b6df3d`. See
> [`CONTRIBUTING.md`](../CONTRIBUTING.md) → *Field-acceptance records must not
> be merged before GO*.

Kiro has completed the automated release evidence and the read-only database
preflight (Section 1) against `0b6df3d`. The lounge operator completes Sections
2–5 on the actual device, and the founder completes the security confirmation
and gate decision in Section 6.

**No passwords, JWTs, learner names, player identifiers, device serial numbers,
telephone numbers, email addresses, or roster screenshots may be added to this
file.**

## Non-sensitive acceptance record

```text
Test date (Africa/Johannesburg):                        [operator to complete on rehearsal day]
Production main SHA (full 40 characters):               0b6df3da1debbc3a7e882cc1a0f407448f5d2e9c
Visible app release (first 7 SHA characters):           0b6df3d
PWA production deployment link or ID:                   Vercel funhouse-revenue-pwa Production, commit 0b6df3d — success (https://vercel.com/fun-house-digital/funhouse-revenue-pwa/72ZoNEoHKU52yWNAVyWqGWcGJUpe); live bundle serves Release 0b6df3d and includes the in-app founder-reset helper
API production deployment link or ID:                   Vercel fun-house-lms Production, commit 0b6df3d — success (https://vercel.com/fun-house-digital/fun-house-lms/ABtrMg42LQHUZkZFAJ8j4Gk3cqwi); live /health responds {"status":"ok"}
Verify Live API Role Access run link or ID:             https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32635822427
Verify Live PWA Browser first run link or ID:           https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32635848777 (mode: applied-or-skipped)
Verify Live PWA Browser replay run link or ID:          https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32635916243 (mode: skipped, stable-identity replay)
Prepare Phase 1 Field Acceptance run link or ID/date:   https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32635825075 (2026-08-23 11:11 UTC)
Security Advisor evidence reference/date:               0 errors, 0 warnings, 4 info suggestions — observed 2026-08-14 (Africa/Johannesburg); no database migration or policy change has merged since (30a0803 → 0b6df3d is a docs-only change), and the 2026-08-23 preflight re-confirmed 14/14 tables and 24/24 runtime-only policies. Founder to re-confirm 0/0 if any doubt or if the observation is older than seven days on the rehearsal day.
Last database migration, role, grant, or policy change date: [founder to confirm none occurred after the 2026-08-23 preflight — schema remains migrations 001–010]
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
`main` SHA `0b6df3d`.

- [x] The recorded SHA is the current `main` SHA. — `0b6df3da1debbc3a7e882cc1a0f407448f5d2e9c`.
- [x] Both Vercel Production deployments attached to that SHA completed
      successfully; neither build was skipped. — Vercel commit statuses
      `Vercel – fun-house-lms` and `Vercel – funhouse-revenue-pwa` are both
      `success` for `0b6df3d`. Kiro self-verified the live PWA bundle serves
      `Release 0b6df3d` (and includes the founder-reset helper) and the live API
      `/health` responds `{"status":"ok"}`.
- [x] The app visibly shows **Release `0b6df3d`**, and those seven characters
      match the start of the recorded full SHA. — Confirmed in the live PWA
      bundle; operator re-confirms on-device before and after login in Section 2.
- [x] **Verify Live API Role Access** passed for that SHA after the API
      production deployment. — run 32635822427 (2026-08-23 11:11 UTC).
- [x] **Verify Live PWA Browser** passed twice for that same SHA after the
      role-access run. The first used `applied-or-skipped`; the second selected
      `skipped` and proved replay of the workflow's stable action identities. —
      runs 32635848777 (11:11) then 32635916243 (11:13). The physical operator
      must not manufacture a duplicate replay.
- [x] The stable PWA and API origins above are unchanged and use HTTPS.
- [x] **Prepare Phase 1 Field Acceptance** passed for the recorded SHA. Its
      summary confirms 14/14 expected tables, 24/24 exact runtime-only policies,
      the fixed empty consent-function search path, and runtime least privilege.
      — run 32635825075 (2026-08-23 11:11 UTC).
- [x] The preflight and recorded Supabase Security Advisor observation are no
      more than seven days old. — Preflight 2026-08-23; Advisor observed
      2026-08-14. **If the rehearsal day is later than 2026-08-21, the founder
      re-observes Security Advisor so the observation is within seven days.**
- [x] The recorded Supabase Security Advisor evidence reports zero errors and
      zero warnings. — 0 errors, 0 warnings (4 info-level suggestions, which do
      not block the gate), observed 2026-08-14.
- [ ] The founder confirms no relevant database change occurred after the
      preflight and Advisor observation. — **Founder to confirm.** (The
      `30a0803` → `0b6df3d` change is a single docs file and touched no database
      object; no migration was added — the schema remains migrations 001–010.)
- [ ] The operator has the approved `Loyiso` and second seeded-role
      password-manager entries. — **Loyiso available.** The **Rotate Live Founder
      Password** workflow succeeded for this release
      ([run 32061953897](https://github.com/FunHouseDigital/FunHouse-LMS/actions/runs/32061953897),
      2026-08-17), so the Aya founder credential has been reset; the **founder
      confirms the reset value is stored in the password manager** before final
      GO. Neither value has been copied into this record, chat, a screenshot, or
      a workflow input.

**Prerequisite result:** Automated evidence **PASS** for `0b6df3d`. Two
founder-only items remain open: (1) founder confirmation of no post-preflight
database change, and (2) founder confirmation that the reset Aya credential is
stored in the password manager. The prerequisite is not final **PASS** until
those are confirmed, development is frozen on this release, and the physical
rehearsal is complete.

A synthetic-only rehearsal may diagnose a device while a security prerequisite
is pending, but real learner data remains prohibited and the final gate cannot
pass.

## 2. Physical-device install, upgrade and role transition

Completed on the lounge device by the operator — see checklist Section 2.

### 2.1 Install and launch

- [ ] Installation or Add to Home Screen was available.
- [ ] Launch from the installed icon succeeded.
- [ ] Visible Release matched the candidate SHA (`0b6df3d`).
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

- [ ] Visible Release matched the candidate SHA (`0b6df3d`).
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
