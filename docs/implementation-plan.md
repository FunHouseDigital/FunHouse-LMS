# FunHouse implementation plan (draft for founder approval)

Built from the four meetings in
[`docs/aws-architect-meeting-notes.md`](aws-architect-meeting-notes.md) and the
code on `main` (`dd95afb`). The latest PRD was not available, so anything in it
overrides this plan. Status: **draft, not approved**.

## Fixed dates and limits

| Item | Value | Source |
| --- | --- | --- |
| AWS architect support | About one month from Meeting B, so until roughly late November 2026 | Meeting B |
| Founder away | 29 November to 5 December 2026 (Cape Town) | Meeting D |
| Credits | US$10,000, ending around June or July 2027 | Meetings A, B, D |
| Phase 1 | Waiting on founder GO. Draft PR #52 is blocked by the founder's failed login on the lounge phone | Repo |
| Phase 2 (lessons, Bedrock) | Gated until Phase 1 GO | `CONTRIBUTING.md` |

## Recommended answers to the open decisions

| # | Decision | Recommendation | Why |
| --- | --- | --- | --- |
| D1 | What to build next | **A.** Get Phase 1 GO first. | One small step for the founder. Every later stage builds on a signed-off base, and nothing already tested has to be redone twice. |
| D2 | Container platform on AWS | **A.** Amazon ECS Express Mode on Fargate, in `af-south-1`. Confirm it is offered in `af-south-1` before starting. | App Runner is closed to new customers. ECS Express Mode is AWS's named replacement and matches the architects' design. |
| D3 | Sign-in | **A.** Keep the current sign-in for staff. Decide on Cognito when learner and parent logins are designed (Stage 5). | Works today and keeps the 2027 exit simple. Learner logins on shared devices need their own design first. |
| D4 | Credits spending rule | **B.** Use the credits, but each AWS service gets a row in the exit table, and an AWS Budgets alert keeps spending on track to last until June 2027. The monthly limit is the remaining credit balance divided by the months left: at most about US$1,250 a month if all US$10,000 is still there. Kiro usage is already drawing on the credits, so the real balance must be checked first. | Matches the architects' advice to use the credits, without running out early. |
| D5 | Session-end alerts | **A now, B later.** Timers save each session's end time on the phone, so they survive closing, locking and going offline. Sound and vibration play when the app is open. Test web push on the Huawei. Add a native Android wrapper only if the test shows it is needed. | A browser app cannot ring an alarm while the phone is locked and offline. |
| D6 | Learner and parent logins | Design in Stage 5 with parent consent under POPIA. Applies to lounge player accounts too, because every player is a minor. | Legal requirement for under-18s. |
| D7 | AWS prototype | Use it as a design reference only. Ask Mamadu for the code and screenshots. | This repo is the live product with Phase 1 evidence. |
| D8 | AWS region | All personal data stays in `af-south-1`. `us-east-1` only for tools that hold no learner or player data. Existing cross-region inference rule applies to Bedrock. | POPIA, and children's data. |
| D9 | Partner | Kiro keeps building the lounge. A partner, only if AWS funds it at no cash cost, builds the LMS teacher side at MVP level, using this repo and the PRD as the brief, with least-privilege access. | Partners are slow and may push unneeded changes (Meeting D). Lounge income cannot wait. |
| D10 | Lounge stations | Named stations: PS5-1, PS5-2, PS4, PC-1, PC-2, PC-3, PC-4 (founder's PC, overflow only). Founder to confirm names and whether PC prices match console prices. | The app must tell stations apart to show a timer for each. |
| D11 | How the AI tutor teaches | **A.** The tutor asks learners questions and never hands over answers or does their work. | Founder agreed in Meeting D. |
| D12 | Amazon Quick | **A.** Not now. Build the founder and impact dashboards in the app. Revisit with the free trial after Stage 3. | About a third of the credits per year for one user. |
| D13 | Shared devices and attendance | Keep the facilitator register as the attendance record. A learner login can add to it, not replace it. | 2 or 3 learners share each device. |

## Stages

Each stage is specced (requirements, design, tasks) before building, and each
ends with a check on the real lounge phone.

### Stage 1: Phase 1 sign-off and Kiro setup

1. Rotate the founder's password and log in on the lounge phone.
2. Rerun the role-change section of the field check, then founder GO.
3. Merge PR #52.
4. Add a steering file with the founder's working rules: follow the PRD, never
   assume, ask with options, plain language, no em dashes, South African English,
   offline-first, minors and POPIA, AI tutor asks questions.
5. Add a hook that suggests a new chat and writes a short carry-over summary.

### Stage 2: Lounge income (before the founder leaves on 29 November)

1. Named stations, including PCs (D10).
2. Station board: every station, free or busy, with a live timer for each busy one.
3. Timers keep the right time after closing, locking or going offline.
4. Sound and vibration at session end while the app is open.
5. Limit of 4 active subscriptions.
6. Daily cash target on the lounge dashboard.
7. Field test on the Huawei, including a web push test.

### Stage 3: Move to AWS `af-south-1` (while the architects can still help)

1. Replace the App Runner module with ECS Express Mode on Fargate.
2. RDS PostgreSQL, S3 + CloudFront, budget alerts, updated exit table.
3. Copy the Supabase data to RDS and test. Keep Vercel and Supabase running until
   the AWS copy passes the same checks, then switch.

### Stage 4: Players

1. Game catalogue with pictures and age ratings, per station type.
2. Player accounts with a digital loyalty card (10th visit: one hour free), with
   parent consent.

### Stage 5: Learning (Phase 2; needs the latest PRD; partner or Kiro per D9)

1. Lessons of about 45 minutes: create, assign to a learner or group with a due
   date, track results.
2. Lesson builder with sections: content, game, typing exercise on the theme.
3. Attendance register that works on shared devices.
4. Student and parent logins with consent.
5. Impact dashboard good enough to show funders.

### Stage 6: AI (2027, online)

1. Bedrock Knowledge Bases with the founder's teaching material.
2. AI drafts lessons, quizzes and themed typing sentences; the founder approves.
3. AI-assisted grading; the founder approves.
4. AI tutor that asks learners questions (D11).
5. Research: small AI models that run on the device with no internet.

## Not planned for now

- PS5 app and PlayStation integration (research only).
- Amazon GameLift Streams (no fit).
- Player pre-booking, online payment and QR check-in (after Stage 4).
- Amazon Quick (D12).
