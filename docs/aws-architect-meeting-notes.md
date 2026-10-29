# AWS architect meeting notes and build impact

Source: transcript of the second meeting between Aya Yokwana (founder) and the AWS
team: Mamadu Bah and Chichi Ogugu (solutions architects) and Guyu Ye (senior
solutions architect, scaling and social impact). Meeting held on a Thursday at
21:00 SAST. The exact date is not in the transcript.

This file records what the meeting changed, checks each point against the code on
`main` (`dd95afb`), and lists the decisions the founder still has to make. Nothing
here changes the build by itself.

## 1. Agreed in the meeting

| Topic | What was said |
| --- | --- |
| Hosting target | Move off the current hosting to AWS `af-south-1` (Cape Town). |
| Credits approach | Founder now wants to use as many AWS services as are useful while credits last. Credits end around June or July 2027. Plan to migrate out gradually, starting about a month before they end. AWS asked that portability stays in mind. |
| Near-term priority | Finish the lounge manager app. Then start the teacher side of the LMS. |
| AI tutor and course creation | Bedrock AI tutor and AI course creation are 2027 work. |
| Proposed AWS architecture | Route 53 (DNS), ACM (free auto-renewing HTTPS certificates), CloudFront + S3 (PWA), ECR + ECS on Fargate (API container), RDS PostgreSQL (database, automatic backups), KMS (encryption at rest), TLS in transit, database reachable only by the API. |
| Sign-in | AWS proposed Amazon Cognito. It is free under 10,000 monthly active users. |
| Offline timer | The session countdown must keep running when the internet drops, the browser closes or the phone locks, like the phone's clock app. It syncs when the connection returns. AWS said this belongs in the app's own code (service worker + IndexedDB), not in an AWS service. AppSync was mentioned as an option. |
| Vibration alerts | AWS could not get a browser-installed app to vibrate or notify reliably on Android or iPhone. They will keep researching and may suggest a proper app. Alerts must reach both the founder and the lounge manager, including discrepancy alerts. |
| Player app (new idea) | Players pre-book and pay online on their own phones, then show a QR code on arrival. Usage logs automatically. Founder wants to build towards it. |
| Learner check-in (new) | Learners log in to the platform. Logging in marks them present and opens the lesson of the day. Replaces the manual sign-in page and Claude-based tracking. |
| Education direction (new) | Shift from general digital literacy to a pathway that gets learners aged 15 to 17 ready for a first AWS certificate by 18, leading to work readiness. |
| Course creation method | Founder builds lessons using a fixed teaching "spine" (Finland + Singapore approaches) and wants the platform to hold her source material and flag outdated content. The AWS feature she could not name is most likely Amazon Bedrock Knowledge Bases. |
| Subscription rule | Maximum 4 people per subscription (already built). Maximum 4 subscriptions at a time because of limited consoles (not built). |

## 2. Actions from the meeting

AWS team:

1. Send the slides and architecture diagram.
2. Build a demo of the lounge manager app on the proposed AWS architecture.
3. Run enablement sessions. The founder asked for a Kiro session first (steering files).
4. Prepare a cost breakdown using the AWS Pricing Calculator.
5. Bring partner options to the next meeting.
6. Possibly run an enablement session for the learners.
7. Guyu to tell Anya (AWS education lead, K-12 and Restart) that Aya will reach out.
8. Book the next meeting for a Thursday or Friday, next week or the week after.

Founder:

1. Send screenshots of every screen in the current app.
2. Email Anya.
3. Rebook the AWS foundational certificate course.

## 3. Corrections to what was said in the meeting

- The meeting described the API as "running on Azure". The repo has no Azure
  anywhere. Production is Vercel (API and PWA as two projects) with Supabase
  PostgreSQL. See `docs/deployment-runbook.md`.
- The repo already contains a complete, never-run AWS `af-south-1` path
  (`infra/`, `.github/workflows/deploy.yml`). It uses App Runner, not ECS.

## 4. Facts checked after the meeting

| Item | Finding | Effect on the build |
| --- | --- | --- |
| App Runner | AWS closed App Runner to new customers from 30 April 2026 and recommends Amazon ECS Express Mode instead ([AWS notice](https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html)). The repo records no completed AWS deploy, so the account is probably not an existing App Runner customer. | The current Terraform API module will most likely fail to deploy. Switching to ECS on Fargate matches what the architects proposed, but it breaks the deployment spec's "no ECS, no load balancer" rule. A load balancer adds a fixed monthly cost. |
| Cognito pricing | The Lite and Essentials plans include 10,000 free monthly active users for direct sign-in ([AWS feature plans](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html)). | Cost is not the blocker. The blocker is that the repo bans Cognito (`funhouse_api/auth/service.py`, `tests/test_smoke_config.py`) for portability. |
| Timer that survives closing | Possible in the current PWA. Save the session's end time on the phone, then recalculate the time left from the clock every time the app opens. Works offline. | Can be built now. No live countdown exists today: sessions are logged after the fact (`web/src/domain/captures/session.ts`). |
| Alarm while the phone is locked and offline | A web app cannot schedule a notification for later. The browser feature that would allow it (Notification Triggers) never shipped. Web Push needs the internet and a server. | A reliable offline, locked-phone vibration needs a native Android app. The existing PWA could be wrapped (for example with Capacitor) and use Android's local alarms. |
| Lounge phone | Phase 1 testing used a Huawei Nova 14. Recent Huawei phones ship without Google services. Chrome's web push on Android relies on Google's push service, so web push will probably not arrive on this phone even when online. Needs a test on the device. Huawei's battery saving can also close background apps. | Points further towards a native app for the lounge phone. |

## 5. Conflicts with existing rules that need a founder decision

| # | Decision | Current rule in the repo | Options |
| --- | --- | --- | --- |
| D1 | What to build next | Phase 1 is waiting on GO (draft PR #52 is blocked by Aya's failed login). Merging anything new to `main` restarts the release evidence. Phase 2 is gated behind GO. | A: get Phase 1 GO first (rotate Aya's password, rerun the role section). B: start the live session timer now and accept a new release candidate. C: start the AWS move first. |
| D2 | Container platform on AWS | App Runner only; ECS and load balancers banned (`.kiro/specs/deployment/design.md`). | A: switch to ECS Express Mode on Fargate (AWS's replacement, matches the architects). B: plain ECS on Fargate set up by hand in Terraform. C: stay on Vercel + Supabase for now. |
| D3 | Sign-in | Self-managed passwords and tokens; Cognito banned. | A: keep the current sign-in (simple to move away from in 2027). B: move to Cognito (password reset by email or SMS, learner accounts, but harder to leave in 2027). |
| D4 | Credits spending rule | Portable core only, under US$80 per month. | A: keep the rule. B: relax it until the 2027 exit, with every AWS service listed in the exit table. |
| D5 | Session-end alerts | None built. | A: in-app countdown + sound and vibration while the app is open (PWA, now). B: add a native Android wrapper for locked or offline alarms (later). C: wait for the AWS team's research. |
| D6 | Learner logins | Only staff log in. Learners are records, not users. | Needs a design: learner accounts for under-18s require parent consent under POPIA. |

## 6. New work to add to the plan

Listed only. None of this is specced yet.

1. Live session countdown that survives closing, locking and going offline (lounge manager app).
2. Session-end and discrepancy alerts to the founder and the lounge manager.
3. Limit of 4 active subscriptions.
4. Move to AWS `af-south-1` using the chosen container platform.
5. Learner login that records attendance and opens the lesson of the day.
6. Teacher side of the LMS.
7. Player app: pre-booking, online payment (needs a South African payment provider) and QR check-in.
8. Certificate-readiness curriculum for ages 15 to 17 (product and content work).
9. 2027: Bedrock AI tutor and course creation using Bedrock Knowledge Bases.
10. Kiro steering file with the founder's working rules (always follow the PRD, never assume, ask with options, plain language, no em dashes, South African English).

## 7. Missing inputs

- The PRD and user journey are not in the repo. Specs cite "PRD §3.1" only.
- Notes from the first AWS meeting are not in the repo.
