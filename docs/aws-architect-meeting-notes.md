# AWS architect meeting notes and build impact

A running record of the founder's meetings with the AWS team, checked against the
code on `main` (`dd95afb`). Each meeting has its own section. The open decisions
and the new-work list at the end combine all meetings so far. Nothing here changes
the build by itself.

People:

- Aya Yokwana: founder ("super admin" in the app).
- Mamadu Bah and Chichi Ogugu: AWS solutions architects.
- Guyu Ye: AWS senior solutions architect, scaling and social impact.
- Loyiso ("Lloyd"): lounge manager. Male.
- Anya: AWS education lead (K-12 strategy, AWS Restart).

Meetings recorded so far:

| Meeting | When | Main topics |
| --- | --- | --- |
| Meeting A | A Thursday, 21:00 SAST (date not in transcript) | Proposed AWS architecture, offline timer and alerts, Cognito, education direction |
| Meeting B | The Thursday after a missed week, 21:00 SAST (date not in transcript) | Offline vs online split, AWS prototype demo, lesson creation flow, PS5 idea, Kiro features, partners, region |
| Meeting C | Friday, most likely 23 October 2026 (the following Friday was "the 30th"), evening SAST | Games specialist advice, partner candidate and its LMS demo, lounge equipment, age-appropriate games |
| Meeting D | Late October 2026 (date not in transcript). Peer founder catch-up, not AWS staff | Partner funding reality, credits, Amazon Quick, AI teaching approach, shared devices, on-device AI, learner age range |

The transcript of the first introductory meeting (before Meeting A) is not
recorded here.

---

## Meeting A

### A1. Agreed

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

### A2. Actions

AWS team: send slides and diagram; build a demo; run enablement sessions (Kiro
first); cost breakdown with the AWS Pricing Calculator; partner options; possibly
a learner enablement session; Guyu to introduce Anya.

Founder: send screenshots of every app screen (done by Meeting B); email Anya;
rebook the AWS foundational certificate course.

### A3. Corrections

- The meeting described the API as "running on Azure". The repo has no Azure
  anywhere. Production is Vercel (API and PWA as two projects) with Supabase
  PostgreSQL. See `docs/deployment-runbook.md`.
- The repo already contains a complete, never-run AWS `af-south-1` path
  (`infra/`, `.github/workflows/deploy.yml`). It uses App Runner, not ECS.

### A4. Facts checked after the meeting

| Item | Finding | Effect on the build |
| --- | --- | --- |
| App Runner | AWS closed App Runner to new customers from 30 April 2026 and recommends Amazon ECS Express Mode instead ([AWS notice](https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html)). The repo records no completed AWS deploy, so the account is probably not an existing App Runner customer. | The current Terraform API module will most likely fail to deploy. Switching to ECS on Fargate matches what the architects proposed, but it breaks the deployment spec's "no ECS, no load balancer" rule. A load balancer adds a fixed monthly cost. |
| Cognito pricing | The Lite and Essentials plans include 10,000 free monthly active users for direct sign-in ([AWS feature plans](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html)). | Cost is not the blocker. The blocker is that the repo bans Cognito (`funhouse_api/auth/service.py`, `tests/test_smoke_config.py`) for portability. |
| Timer that survives closing | Possible in the current PWA. Save the session's end time on the phone, then recalculate the time left from the clock every time the app opens. Works offline. | Can be built now. No live countdown exists today: sessions are logged after the fact (`web/src/domain/captures/session.ts`). |
| Alarm while the phone is locked and offline | A web app cannot schedule a notification for later. The browser feature that would allow it (Notification Triggers) never shipped. Web Push needs the internet and a server. | A reliable offline, locked-phone vibration needs a native Android app. The existing PWA could be wrapped (for example with Capacitor) and use Android's local alarms. |
| Lounge phone | Phase 1 testing used a Huawei Nova 14. Recent Huawei phones ship without Google services. Chrome's web push on Android relies on Google's push service, so web push will probably not arrive on this phone even when online. Needs a test on the device. Huawei's battery saving can also close background apps. | Points further towards a native app for the lounge phone. |

---

## Meeting B

### B1. Agreed or stated

| Topic | What was said | Compared with the repo |
| --- | --- | --- |
| Power cuts | Aya missed the previous week because of an electricity outage. | Confirms offline-first matters for the founder's side too, not only the lounge. |
| Works offline | Open the installed app; move between cached screens; cached lessons and reference data; record new work (sessions, new players, new learners) into the sync queue; see the "waiting to sync" queue. | Matches the current PWA design. Lessons are not cached yet because there are no lessons in the app. |
| Needs online | First install and first open; first login; AI tutor answers (Bedrock); content not yet cached; changes made on other devices; final balances and the founder dashboard. | Matches the current design. |
| AWS prototype | Built by Mamadu and Chichi, hosted on S3 + CloudFront, styled like the current app. Three areas: Gaming Lounge, Learning Space, Founder Dashboard (with an Impact dashboard). Four logins: founder, lounge manager, student, parent. "Not final", built to get feedback. | The repo has founder, manager and facilitator roles. Student and parent logins do not exist. |
| Gaming Lounge screen | Today's cash, session count and a target; log a session (player, station, duration, payment method); players list; add player. Aya found it simple and easy to learn. | Logging, players and payments exist. A daily target does not. |
| Station board (new) | One screen showing every station, which are free, and a live timer with time left for each occupied station. | Not built. Extends the live countdown requirement from one timer to one per station. |
| Player accounts and loyalty (new) | Players get accounts. Loyalty card in the app replaces printed cards: after 9 visits, the 10th gives an hour free. Loyalty discounts also possible. | Not built. Player accounts link to the Meeting A player app idea. |
| Game catalogue (new) | Show the available games with pictures when logging a session, to upsell beyond FIFA. | Not built. |
| Lounge manager access | Loyiso sees Gaming Lounge, players and Learning Space. No founder dashboard. | Matches the current `manager` role scope. Learning Space access for the manager needs confirming against the current RBAC. |
| Founder access | Founder is the "super admin" and sees everything. | Matches the current `founder` role (unrestricted). |
| Student login | Student sees only learning: lessons, games, typing game, internet safety lesson. | Not built. |
| Learning Space | Dashboard with lessons, minutes and learners; assign a lesson to a learner or cohort with a due date; typing game with results. | Learners, attendance and typing/homework/quiz metrics exist. Lesson assignment, cohorts and due dates do not. |
| Typing and games | The typing game currently comes from an outside website. Later Aya wants her own games in the platform. She wants Bedrock to help create simple learning games. | Not built. Bedrock is Phase 2 and gated. |
| Lesson creation flow (new, detailed) | 1. Create lesson. 2. Pick the grade. 3. See previous lessons for that grade and choose to continue one. 4. AI reads past results (for example "struggled with variables") and suggests doubling down, continuing or starting a new subject. 5. Build sections: content, a game, a typing exercise using sentences on the lesson's theme. 6. The attendance register is set up. 7. Lesson is ready. Also see analytics and attendance for old lessons. No fixed curriculum yet. | Not built. AI parts need Bedrock + Knowledge Bases. Founder reviews AI drafts before publishing. AWS to research a cost-effective model. |
| Impact dashboard | Cash, sessions and skills learned together. Aya: "this is where it all comes together". | Not built. |
| One system or two | A mentor suggested separating gaming and education. Aya strongly prefers one ecosystem: gaming income funds education. Not ruled out for the future. | Current repo is one system. |
| Two groups of users | Walk-in gamers play any PlayStation game, nothing educational. After-school classes at schools: Monday esports (building a team), Tuesday lessons. In classes, every game is tied to a coding or computational concept. Some walk-in players are also learners. | One player can be both a gamer and a learner. Matters for the data model. |
| PS5 idea (new, exploratory) | Run the FunHouse app on a PS5 like Spotify, and link to PlayStation games through official APIs. Long term: one PS5 in each rural school running the FunHouse app. Mamadu will test opening the web app on his PS5. AWS will ask a games specialist. Aya wants only routes that do not break Sony's rules. | Research only. See B4. |
| Partners | AWS partners are contractors that AWS can fund or subsidise, not co-founders. Aya is open to one if the cost and return make sense. She turned down someone at a conference who wanted to be paid from her credits. | No code impact. |
| Credits | Aya mentioned 10,000 in credits (assumed US$). | Budget input for D4. |
| AWS region | Aya's AWS console is set to `us-east-1` (N. Virginia) for everything, because some things did not work in `af-south-1`. AWS expects the lounge and LMS to be hosted in South Africa. | The deploy workflow targets `af-south-1`. Anything Aya created by hand in `us-east-1` is outside that. Some services (likely Bedrock models) are not in `af-south-1`; the deployment spec already has a cross-region inference rule. |
| Timeline | About one month left in the AWS engagement. Goal: working, and bringing in income. | Supports building the income-related lounge features first. |

### B2. How Aya uses Kiro today (from the meeting)

Kiro Web, plain English. Each session: ask what was done last, how far along it is
and what is next; then "continue with the PRD, ask me questions, offer feasible
options, never assume". Lets Kiro choose the model. Checks Kiro's summary with
Claude against the PRD, then tests the live site by hand.

Chichi recommended:

- **Steering files** for rules Aya repeats. Chichi's demo steering said: always
  offline-first; most users are minors, so consent is required; encrypted access;
  POPIA; and "not for now" rules such as no Cognito and no Bedrock yet.
- **Skills** for repeated multi-step workflows, with "ask me before you submit" steps.
- **Custom agents** for repeated checks.
- **Hooks** for automatic checks. Aya wants Kiro to tell her when a chat is getting
  too long and a new one should start.

### B3. Actions

AWS team:

1. Meet a games specialist about PlayStation integration and building learning games.
2. Mamadu to try the web app on his PS5.
3. Find AWS partners that work in South Africa, with cost and return on investment.
4. Map a workflow for the combined system and long-term services, cost-optimised.
5. Send Kiro resources (skills, steering).
6. Next meeting: the following Thursday, 21:00 SAST.

Founder:

1. Try a steering file and a hook in Kiro, and give Chichi feedback.
2. Share any PS5 integration ideas found.

### B4. Facts to check (not yet verified)

| Item | Note |
| --- | --- |
| PS5 web app | The PS5 has no official general-purpose web browser. Apps on the PlayStation Store go through Sony's developer programme. Mamadu's test will confirm. |
| PlayStation game data | Any link to PlayStation games must use official Sony routes. Unofficial or modified consoles are out of scope. |
| Bedrock in Cape Town | Which models run in `af-south-1` and which need cross-region inference. Needed before any AI lesson work. |

---

## Meeting C

### C1. Agreed or stated

| Topic | What was said | Compared with the repo |
| --- | --- | --- |
| Games specialist advice | Games that use AWS mostly use it for data pipelines, leaderboards and stats, not to run the game. Amazon GameLift Streams (streaming games to players) does not fit FunHouse. The Godot game engine ([godotengine.org](https://godotengine.org)) was mentioned as an option. | No code impact. |
| How to build learning games | AWS recommends building the typing and coding games inside the LMS with Kiro and Amazon Bedrock, so the work uses credits instead of cash. | Our reading: Kiro writes the game code; Bedrock could generate content such as typing sentences on the lesson theme. To confirm. Bedrock is Phase 2 and gated. |
| PS5 app | No update on Mamadu's PS5 test. The games advice points away from a PlayStation app for now. | Stays research only. |
| Founder's Kiro steering and hooks | Not tried yet. Aya builds on weekends and has been updating the PRD with ideas from these meetings. | The PRD has changed since the specs were written. The latest version is needed. |
| Partner candidate | AWS contacted "HaviLabs" (spelling unconfirmed; an AWS social impact programme partner) and is waiting for a reply. The only open question is whether they can work on a South African project. AWS checked them for: AI Services and Small Business competencies; validations for CloudFront, Lambda, CloudFormation, RDS and EC2; several professional AWS certifications. | If a partner builds the LMS and lounge, who builds what has to be decided (D9). |
| Partner process | 1. Partner replies. 2. AWS discovery call with the partner. 3. Partner sends an overview. 4. Joint call with Aya: demo, scope, cost, and what happens when the work moves to other AWS accounts after the credits. | Waiting on the partner. |
| Partner's LMS demo ("Learnify") | Upload a document and AI drafts the modules and a quiz checked against it; a person approves. Enrol by role with deadlines. 17 languages; web, iOS and Android apps. Certificates with a validity date and verification link. Agents remind before expiry, escalate to a manager and draft actions for approval but never grant anything. A tutor answers only from the course. Exportable audit trail. Append-only consent ledger, data rights requests, retention and breach workflows (built for India's privacy law, data kept in Mumbai). Own branding. | Aya will borrow ideas for an African context, not copy it. The repo already has an append-only consent table (`funhouse_pipeline/sql/002_consents_append_only.sql`). |
| Why the LMS matters | Schools cannot pay, so Aya needs funders to subsidise the product, and funders need to see it working. The LMS is also a showcase for funding. AI should cut lesson creation (4 to 5 hours) and grading (4 to 5 hours). Aya is also seeking a partner to teach AI to teachers and children. | Raises the priority of a demonstrable LMS and impact dashboard. |
| Lounge equipment | 1 PS4, 2 PS5s and 3 PCs. A 4th PC (Aya's own) is used only when it is very busy. PCs have Steam, but there is little demand for PC games. | The app offers only `PS5` or `PS4` (`web/src/domain/captures/session.ts` line 16). It has no PC option and cannot tell the two PS5s apart. The station board needs named stations. |
| Age-appropriate games | No violent games such as Call of Duty, because most players are under 18 and parents object. Games are added based on demand. | Not recorded anywhere. A game catalogue should hold each game's age rating. |
| First hire | A part-time funding intern, 7 hours a month. | Unknown whether they need app access. |
| Funding tools | Chichi shared an AI tool for finding funders. Aya already uses Kindora with Claude. | No code impact. |
| Next meeting | Not fixed. Mamadu will contact Aya when the partner replies. Friday 30 October was suggested. Chichi is away at AfroTech the week after. | |

### C2. Actions

AWS team: wait for the partner, or try another partner for a faster reply; hold a
discovery call; set up the joint call; send a follow-up email.

Founder: try a new Kindora search for funding; continue building on weekends;
(still open from Meeting B) try a steering file and a hook.

---

## Meeting D

Catch-up with Pera Barrett, a fellow founder from the AWS social impact
accelerator (New Zealand; runs Shoebox Christmas and Kaha Create). Personal and
political conversation is left out.

### D-1. Stated

| Topic | What was said | Effect on the build |
| --- | --- | --- |
| Infrastructure at home | No tap water on the day of the meeting; electricity cuts; the government blocked cheaper rural Starlink internet. Aya: an installed app must work "100% offline and 150% online". | Reconfirms offline-first as the top rule. |
| Who plays at the lounge | Open only outside school hours. All customers are school children; adults are not allowed, by choice. The lounge players are meant to be the same children Aya later teaches at school. | Every player is a minor. Player accounts, loyalty cards and any player app need parent consent under POPIA. |
| One ecosystem | The Village Capital EdTech mentor (Nigeria) also advised separating gaming and education. Aya still prefers one ecosystem. | No change. Stays one system. |
| First hire | Starts next month: 7 hours a month (6 work + 1 meeting), six-month contract, funding applications. She runs her own EdTech company in another province. Must tell Aya before applying for anything. | No app access assumed. |
| How partners are paid | Partners are paid from AWS funding buckets and receive a share of the customer's AWS spend for a period. A partner once pushed Pera to swap a working database for an AWS one with no problem to solve. Pera's partner (RevStar) scoped about US$30,000 of work, took a long time, then said AWS funding rules had changed and it might not deliver. Meetings across time zones and giving admin access add overhead. Funding comes at levels: proof of concept, MVP, or production. | A partner build is uncertain and slow. Keep the lounge build moving with Kiro. If a partner is used, agree the level (MVP or production) in writing, give least-privilege access, and reject changes that solve no real problem. |
| Credits | The AWS architects told Aya to aim to use all US$10,000 of credits, because that usually leads to more. Aya now calls building on Vercel and Supabase "a mistake". | Supports moving to AWS. |
| Amazon Quick | Pera recommends Amazon Quick: an AI assistant over your own data that answers questions and builds dashboards. He quoted about US$250 a month. | Checked ([Quick pricing](https://aws.amazon.com/quick/pricing/)): dashboards over your own AWS data are in the Professional (US$20 per user per month) and Enterprise (US$40) plans, each plus a US$250 per account monthly fee. One Professional user is about US$3,240 a year, roughly a third of the credits. A 30-day free Enterprise trial exists. Free and Plus plans are for individual AI assistant use. Region availability for South African personal data is not checked. |
| AWS funding buckets | AWS has themed funding (for example promoting a new region, education, rural connectivity). | Worth asking Mamadu whether an `af-south-1` or education bucket could fund a partner. |
| Education pathway | Now starts at age 11 or 12 (Meeting A said 15 to 17), aiming at AWS certificates and job readiness by 18. A 4 to 8 year programme. Target: 1,000 learners if funded. AWS staff in Cape Town offered free 3-hour lessons; Aya will cut them into 45-minute chunks with gaming. | Lessons should be about 45 minutes. Plan for about 1,000 learners. |
| Community champions (idea) | Train a few adults close to the children (for example 5 adults for 200 children each) through free AWS Skill Builder pathways, then have them run the programme. Many teachers are overwhelmed or uninterested in tech. | Fits the existing facilitator role. |
| How AI should teach | Pera's school talks: when learners let AI answer for them, they stop learning. Teach learners to make AI ask them questions instead. Aya agreed fully. | The future AI tutor should ask learners questions and never hand over answers or do their work. |
| Devices | Only 6 devices; learners work 2 or 3 to a device. With 20 devices Aya could run the AWS classes. AWS (and other companies) donate refurbished devices; Pera got 12 laptops about 4 to 5 months after asking. | One device is shared by several learners. Attendance cannot come from logins alone. |
| On-device AI | Small open-source models (Pera uses Gemma) run on a laptop with no internet. Laptops that run large models are expensive now but will get cheaper. Aya wants an AI tutor that works offline. | Future option: offline AI on the device. Bedrock needs the internet. |
| Sustainability | Pera's advice: build things that keep running if the founder is away or ill. | The lounge app should let Loyiso run a full day without Aya. |
| Next accelerator cohort | Possibly in Cape Town. Aya may offer to speak as an alumna. | No code impact. |
| Google for Startups South Africa | Aya and a co-founder are in the accelerator for a nursing venture (it appears separate from FunHouse). Pitch in Cape Town, 29 November to 5 December 2026, possibly about US$30,000. | Aya is away 29 November to 5 December. |

### D-2. Actions (founder)

1. Look at Mozilla Foundation and AWS Imagine Grant funding.
2. Look into Amazon Quick.
3. Ask AWS who handles refurbished device donations in South Africa.
4. Ask the AWS social impact team about attending the next cohort as an alumna.

---

## Open decisions (all meetings)

| # | Decision | Current rule in the repo | Options |
| --- | --- | --- | --- |
| D1 | What to build next | Phase 1 is waiting on GO (draft PR #52 is blocked by Aya's failed login). Merging anything new to `main` restarts the release evidence. Phase 2 is gated behind GO. | A: get Phase 1 GO first. B: start the live station timers now and accept a new release candidate. C: start the AWS move first. |
| D2 | Container platform on AWS | App Runner only; ECS and load balancers banned (`.kiro/specs/deployment/design.md`). | A: ECS Express Mode on Fargate. B: plain ECS on Fargate in Terraform. C: stay on Vercel + Supabase for now. |
| D3 | Sign-in | Self-managed passwords and tokens; Cognito banned. | A: keep the current sign-in. B: move to Cognito. Student and parent logins (Meeting B) make this decision more important. |
| D4 | Credits spending rule | Portable core only, under US$80 per month. | A: keep the rule. B: relax it until the 2027 exit, with every AWS service listed in the exit table. |
| D5 | Session-end alerts | None built. | A: in-app timers + sound and vibration while the app is open. B: add a native Android wrapper for locked or offline alarms. C: wait for AWS research. |
| D6 | Learner and parent logins | Only staff log in. | Needs a design. Under-18s need parent consent under POPIA. The prototype's parent login may be the place to give consent. |
| D7 | AWS prototype vs this repo | This repo is the live product. | A: keep building this repo and use the prototype as a design reference. B: adopt the prototype's code. Needs a look at the prototype code first. |
| D8 | AWS region | Deploy targets `af-south-1`. | Confirm `af-south-1` for all personal data, and decide what (if anything) may run in `us-east-1`. |
| D9 | Who builds what, if a partner is hired | Everything is built in this repo with Kiro. | A: Kiro keeps building the lounge (income now); the partner builds the LMS, using this repo and the PRD as the brief. B: the partner builds both, and this repo becomes the reference. C: no partner; Kiro builds both. Depends on the partner's reply and cost. |
| D10 | Lounge stations | Choices are `PS5` or `PS4` only. | List the real stations by name (for example PS5-1, PS5-2, PS4, PC-1 to PC-3, plus Aya's PC as overflow) and whether PC sessions use the same prices. |
| D11 | How the AI tutor teaches | No AI tutor yet. | A: the tutor asks learners questions and never hands over answers or does their work (Meeting D). B: a normal question-and-answer tutor. |
| D12 | Amazon Quick | Not used. | A: not now; build the founder and impact dashboards in the app. B: try the 30-day free trial once data is on AWS. C: subscribe now. |
| D13 | Shared devices and attendance | Facilitators record attendance in the app. | Several learners share one device, so a login cannot be the only proof of attendance. Keep the facilitator register as the record; a learner login can add to it. |

Recommendations for every decision are in [`docs/implementation-plan.md`](implementation-plan.md).

## New work (all meetings, not yet specced)

Lounge (income):

1. Station board with a live timer per named station (2 PS5s, 1 PS4, 3 PCs, plus an overflow PC) that survives closing, locking and going offline.
2. Session-end and discrepancy alerts to the founder and the lounge manager.
3. Limit of 4 active subscriptions.
4. Daily cash target on the lounge dashboard.
5. Game catalogue with pictures and age ratings in session logging, per platform (PlayStation or PC/Steam).
6. Player accounts with a digital loyalty card (10th visit: one hour free).
7. Player app: pre-booking, online payment (needs a South African payment provider) and QR check-in.

Learning:

8. Student login: logging in records attendance and opens the lesson of the day.
9. Parent login (consent and progress).
10. Lessons: create, assign to a learner or cohort with a due date, track results.
11. Lesson creation flow with sections (content, game, themed typing exercise) and an attendance register. Ideas from the partner demo: upload source material, AI drafts the lesson and quiz checked against it, founder approves.
11a. AI-assisted grading, with the founder approving results.
12. Lesson analytics and attendance history.
13. In-platform typing game and learning games.
14. Certificate-readiness curriculum for ages 15 to 17 (content work).

Founder and platform:

15. Impact dashboard (cash, sessions, skills learned).
16. Move to AWS `af-south-1` on the chosen container platform.
17. 2027: Bedrock AI tutor (answers only from FunHouse course material), AI lesson drafting and AI game content with Knowledge Bases, founder review before publishing. Agents may remind and draft, never act without approval.
17a. Impact dashboard good enough to show funders.
18. Research: FunHouse app on PS5 and official PlayStation integration.

Kiro setup:

19. Steering file with the founder's working rules (follow the PRD, never assume, ask with options, plain language, no em dashes, South African English, offline-first, minors and POPIA).
20. Hook that reminds Aya when to start a new chat, with a summary to carry over.

## Missing inputs

- The PRD and user journey are not in the repo. Specs cite "PRD §3.1" only.
- Transcript of the first introductory meeting.
- The AWS prototype code or screenshots.
- The latest PRD. Aya has updated it with ideas from these meetings.
- Transcript of the final meeting.
