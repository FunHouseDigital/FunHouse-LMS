# Requirements Document

## Introduction

The Revenue PWA is the offline-first Progressive Web App frontend for the FunHouse Operating System (PRD §7, "Phase 1: Revenue PWA"). It is the tool Loyiso (a **manager**) uses phone-first at the lounge to log sessions, sell products, register players, and track the day's takings, and the tool Aya (a **founder**) uses on any device to view revenue, log school sessions, enter metrics, and read operational alerts.

The application is an installable PWA built with React + Vite + TypeScript, using Workbox (via `vite-plugin-pwa`) for the service worker and IndexedDB for local persistence. Every core capture function must work with zero connectivity; the internet is only for syncing. All writes are captured locally and flushed to the Spec 2 FunHouse Container API through its idempotent, last-write-wins `POST /sync` endpoint. Read views (revenue summary, alerts, roster) are served by the same API when online and from cached data when offline.

Because most subjects are minors, the app captures guardian consent at registration and protects minors' data held on the device (POPIA by design). The keystone habit from Phase 1 (PRD §12) is that **no lounge session is recorded anywhere except this PWA**, so the Log Session flow must be fast and reliable enough to fully replace paper.

This spec covers the PWA client only. It consumes the endpoints already delivered by Spec 2 (`POST /auth/login`, `GET /players`, `GET /players/{id}/entitlements`, `GET /players/{id}/history`, `GET /revenue/summary`, `GET /alerts`, `POST /sync`). No backend or API changes are in scope; any genuinely missing endpoint is recorded as a dependency/assumption rather than specified here.

## Glossary

- **Revenue_PWA**: The complete offline-first Progressive Web App client described by this document.
- **Container_API**: The Spec 2 FunHouse Container API that the Revenue_PWA authenticates against and syncs to over HTTPS.
- **Service_Worker**: The Workbox-generated service worker that caches the application shell and content and mediates network access.
- **App_Shell**: The static HTML, CSS, JavaScript, and icon assets required to render the Revenue_PWA user interface.
- **Local_Store**: The IndexedDB database on the device that persists all local reads and writes (sessions, attendance, registrations, payments, entitlements, consents, cached roster, and reference data).
- **Sync_Queue**: The ordered, durable list in the Local_Store of offline actions awaiting transmission to the Container_API.
- **Sync_Engine**: The Revenue_PWA component that transmits queued actions to `POST /sync`, records per-action results, and updates sync status. It uses the Background Sync API when available.
- **Sync_Action**: A single queued write with fields `client_id`, `entity`, `created_at`, and `payload`, matching the Container_API `POST /sync` action contract. `entity` is one of `player`, `consent`, `session`, `attendance`, `payment`, `entitlement`.
- **Auth_Manager**: The Revenue_PWA component that performs login, stores the JWT, attaches it to API requests, and handles token expiry.
- **Entitlement_Calculator**: The Revenue_PWA component that computes an optimistic local entitlement balance by applying queued offline draws to the last cached server balance, mirroring the deterministic server rules.
- **Session_Logger**: The Log Session capture flow used by a manager at the lounge.
- **Sell_Module**: The purchase-capture flow that records pay-per-use payments, subscriptions, and Holiday Special passes.
- **Registration_Module**: The player registration flow that captures player details and guardian consent.
- **Attendance_Module**: The school session and attendance capture flow used by a founder.
- **Metrics_Module**: The grid used to enter TypingBird metrics into `student_metrics`.
- **Revenue_Dashboard**: The founder view that renders the three revenue streams.
- **Alerts_View**: The view that renders deterministic operational alerts read from `GET /alerts`.
- **JWT**: The signed JSON Web Token issued by `POST /auth/login` and used to authenticate API and sync requests.
- **Manager_Role**: A user whose JWT role is `manager` (e.g. Loyiso); sees the lounge capture screens scoped to their own location.
- **Founder_Role**: A user whose JWT role is `founder` (e.g. Aya); sees the dashboards, school sessions, metrics, and alerts across all locations.
- **Unsynced_Item**: A Sync_Action in the Sync_Queue that has not yet received an `applied` or `skipped` result from the Container_API.
- **Consent_Type**: One of the four guardian consent categories captured at registration.
- **Entitlement_Balance**: The remaining units, product, and validity window for a player's active entitlement, as returned by `GET /players/{id}/entitlements`.

## Requirements

### Requirement 1: Authentication and JWT handling

**User Story:** As a staff member, I want to log in with my identifier and password, so that the app can securely identify me and authorize my API and sync requests.

#### Acceptance Criteria

1. WHEN a user submits a non-empty identifier and a non-empty password on the login screen, THE Auth_Manager SHALL send a `POST /auth/login` request to the Container_API with those credentials.
2. WHEN the Container_API returns a `200` login response, THE Auth_Manager SHALL store the returned `access_token` and `expires_at` in device-protected storage.
3. IF the Container_API returns a `401` login response, THEN THE Auth_Manager SHALL display a generic "invalid credentials" message and SHALL NOT store a token.
4. WHEN the user submits the login form with an empty identifier or an empty password, THE Auth_Manager SHALL block submission and display a field-level validation message.
5. WHILE a stored JWT is present and its `expires_at` is in the future, THE Auth_Manager SHALL attach the JWT as a bearer token to every Container_API request and to every `POST /sync` request.
6. IF the stored JWT is absent or its `expires_at` is at or before the current device time, THEN THE Auth_Manager SHALL route the user to the login screen and SHALL retain queued Unsynced_Items in the Local_Store.
7. IF a Container_API request returns a `401` response due to token rejection, THEN THE Auth_Manager SHALL clear the stored JWT and prompt the user to re-login.

### Requirement 2: Role-gated navigation

**User Story:** As a staff member, I want the app to show only the screens my role permits, so that I see the tools relevant to my job.

#### Acceptance Criteria

1. WHEN a user with Manager_Role is authenticated, THE Revenue_PWA SHALL make the Log Session, Players, Today, and Sell screens available for navigation.
2. WHEN a user with Founder_Role is authenticated, THE Revenue_PWA SHALL make the Revenue Dashboard, Attendance & Sessions, Metrics Entry, and Alerts screens available for navigation.
3. WHILE no valid JWT is present, THE Revenue_PWA SHALL restrict navigation to the login screen only.
4. WHERE a user's role does not include a screen, THE Revenue_PWA SHALL exclude that screen from the navigation controls.

### Requirement 3: Installability and offline app shell

**User Story:** As a lounge operator, I want to install the app and open it without signal, so that I can capture activity anywhere at any time.

#### Acceptance Criteria

1. THE Revenue_PWA SHALL provide a web app manifest that declares name, icons, start URL, and standalone display mode so the application is installable to the device home screen.
2. WHEN the Service_Worker installs, THE Service_Worker SHALL precache the App_Shell assets.
3. WHILE the device has no network connectivity, WHEN the user launches the installed Revenue_PWA, THE Service_Worker SHALL serve the App_Shell from cache and render the interface.
4. WHEN a new App_Shell version is deployed and activated, THE Service_Worker SHALL serve the updated assets on the next launch.

### Requirement 4: Local persistence of all writes

**User Story:** As a lounge operator, I want every capture I make to be saved on the device immediately, so that nothing is lost when there is no signal.

#### Acceptance Criteria

1. WHEN the user completes any capture action (session, attendance, registration, consent, payment, or entitlement create or draw), THE Revenue_PWA SHALL write the resulting record to the Local_Store before confirming success to the user.
2. WHEN a record is written to the Local_Store, THE Revenue_PWA SHALL create a corresponding Sync_Action in the Sync_Queue with a `client_id`, the `entity` type, a `created_at` timestamp set to the device time of capture, and a `payload` carrying the record fields.
3. THE Revenue_PWA SHALL assign each Sync_Action a `client_id` that is unique across all actions created on the device.
4. WHILE the device is offline, THE Revenue_PWA SHALL allow the user to complete session, attendance, registration, consent, payment, and entitlement captures using only Local_Store data.
5. WHEN the Revenue_PWA is relaunched after being closed, THE Revenue_PWA SHALL load previously persisted records and any pending Sync_Queue entries from the Local_Store regardless of network status.

### Requirement 5: Sync queue transmission to the API

**User Story:** As a lounge operator, I want my saved captures to reach the server whenever a connection appears, so that the central record stays complete without any manual effort.

#### Acceptance Criteria

1. WHEN network connectivity becomes available and pending Sync_Actions exist, THE Sync_Engine SHALL transmit the pending Sync_Actions to `POST /sync` as a batch.
2. WHERE the Background Sync API is available, THE Sync_Engine SHALL register a background sync so the Sync_Queue is flushed when connectivity is restored even if the app is not in the foreground.
3. WHEN `POST /sync` returns per-action results, THE Sync_Engine SHALL match each result to its Sync_Action by `client_id` and update that action's local status to the returned status (`applied`, `skipped`, or `rejected`).
4. WHEN a Sync_Action receives an `applied` or `skipped` result, THE Sync_Engine SHALL remove that action from the set of Unsynced_Items.
5. IF a `POST /sync` request fails due to a network error, THEN THE Sync_Engine SHALL retain all affected Sync_Actions in the Sync_Queue for a later attempt.
6. IF a Sync_Action receives a `rejected` result, THEN THE Sync_Engine SHALL retain the rejected action's record locally and record the returned rejection reason for that action.
7. THE Sync_Engine SHALL preserve each Sync_Action's original device-origin `created_at` and `client_id` on every transmission attempt.

### Requirement 6: Sync status and unsynced-items visibility

**User Story:** As a lounge operator, I want to see how many captures are still waiting to sync and whether my device has fallen behind, so that I can trust the record and act if something is wrong.

#### Acceptance Criteria

1. THE Revenue_PWA SHALL display an unsynced-items badge showing the current count of Unsynced_Items.
2. WHEN the count of Unsynced_Items changes, THE Revenue_PWA SHALL update the unsynced-items badge to the new count.
3. WHEN the Sync_Queue contains zero Unsynced_Items, THE Revenue_PWA SHALL display a synced state indicating no pending items.
4. IF the device's most recent successful sync completed strictly more than 5 full days before the current device time, THEN THE Revenue_PWA SHALL display a stale-device warning, and THE Revenue_PWA SHALL be permitted to display the synced state and the stale-device warning at the same time.
5. WHEN a Sync_Action has a `rejected` status, THE Revenue_PWA SHALL surface that action to the user with its recorded rejection reason.

### Requirement 7: Log Session capture flow

**User Story:** As a manager at the lounge, I want to log a gaming session in a few taps, so that recording play is faster than writing on paper.

#### Acceptance Criteria

1. WHEN the manager opens the Log Session screen, THE Session_Logger SHALL present controls to select a player, a console, a duration, and a payment method.
2. WHEN the manager selects a player, THE Session_Logger SHALL allow selection by search or from a recent-players list sourced from the Local_Store.
3. THE Session_Logger SHALL offer console options of PS5 and PS4.
4. THE Session_Logger SHALL offer duration presets of 20 minutes, 1 hour, and 2 hours, and SHALL accept a custom duration in minutes.
5. THE Session_Logger SHALL offer a payment method of either a cash amount or a draw from the selected player's entitlement.
6. WHILE the manager has selected a player and a duration and a payment method, THE Session_Logger SHALL enable a confirm control.
7. WHEN the manager confirms a session, THE Session_Logger SHALL write a `session` record (and, for an entitlement payment, an `entitlement` draw action; for a cash payment, a `payment` record) to the Local_Store and enqueue the corresponding Sync_Action(s).
8. WHEN the manager confirms a session, THE Session_Logger SHALL complete the capture without requiring network connectivity.

### Requirement 8: Optimistic entitlement balance before confirm

**User Story:** As a manager, I want to see a player's remaining entitlement balance before I draw from it, so that I never oversell against an entitlement while offline.

#### Acceptance Criteria

1. WHEN the manager selects a player whose entitlement can pay for a session, THE Entitlement_Calculator SHALL display that player's remaining balance, unit total, and reset or validity date before the confirm control is used.
2. THE Entitlement_Calculator SHALL compute the displayed balance by applying all pending entitlement draw Sync_Actions for that player to the last cached server Entitlement_Balance.
3. WHEN a manager confirms an entitlement draw offline, THE Entitlement_Calculator SHALL immediately reduce the displayed remaining balance by the drawn amount.
4. IF the optimistic remaining balance for a player is less than the requested draw amount, THEN THE Entitlement_Calculator SHALL prevent selecting the entitlement payment method for that amount.
5. WHEN an updated Entitlement_Balance is retrieved from `GET /players/{id}/entitlements`, THE Entitlement_Calculator SHALL replace the cached server balance for that player with the retrieved value.
6. IF the optimistic remaining balance for a player is negative, THEN THE Entitlement_Calculator SHALL prevent an entitlement draw, including a zero-amount draw.

### Requirement 9: Player roster and player detail

**User Story:** As a manager, I want to see the players and their status, so that I can find a player quickly and understand their standing.

#### Acceptance Criteria

1. WHEN the manager opens the Players screen, THE Revenue_PWA SHALL display each in-scope player with their name, entitlement balance, last visit date, and entitlement status.
2. WHILE the device is offline, THE Revenue_PWA SHALL render the Players roster from the last cached `GET /players` data in the Local_Store.
3. WHEN the manager selects a player, THE Revenue_PWA SHALL display that player's session, payment, and entitlement history, including locally captured records not yet synced.
4. WHEN the manager searches the roster by name, THE Revenue_PWA SHALL display only players whose name matches the search text.
5. WHEN the manager opens the Players screen and no in-scope players exist, THE Revenue_PWA SHALL display an empty roster.

### Requirement 10: Player registration and guardian consent

**User Story:** As a manager, I want to register a new player and capture guardian consent on-screen, so that we comply with POPIA before the player participates.

#### Acceptance Criteria

1. WHEN the manager starts "Add player", THE Registration_Module SHALL present fields for player name and guardian phone and controls for four Consent_Type toggles.
2. THE Registration_Module SHALL require a non-empty player name before registration can be submitted.
3. THE Registration_Module SHALL require an on-screen guardian confirmation action before registration can be submitted.
4. WHEN the manager submits a registration, THE Registration_Module SHALL write a `player` record and one `consent` record per captured Consent_Type to the Local_Store and enqueue the corresponding Sync_Actions.
5. THE Registration_Module SHALL restrict collected personal fields to the player name, guardian phone, and the four Consent_Type values, and SHALL NOT collect national ID numbers or residential addresses.
6. WHEN a registration is captured offline, THE Registration_Module SHALL complete the registration and consent capture without network connectivity.

### Requirement 11: Today summary

**User Story:** As a manager, I want a snapshot of today's takings and activity, so that I know how the day is tracking against target.

#### Acceptance Criteria

1. WHEN the manager opens the Today screen, THE Revenue_PWA SHALL display the running cash total captured on the device for the current day.
2. WHEN the manager opens the Today screen, THE Revenue_PWA SHALL display the count of sessions logged on the device for the current day.
3. THE Revenue_PWA SHALL display the current day's cash total relative to the R550 monthly pace target.
4. THE Today screen SHALL display the current count of Unsynced_Items, displaying a zero count when no Unsynced_Items exist.
5. THE Revenue_PWA SHALL compute the Today totals from Local_Store records so the screen renders while offline.

### Requirement 12: Sell flow

**User Story:** As a manager, I want to record a sale, so that a player gains the entitlement they paid for and the payment is captured.

#### Acceptance Criteria

1. WHEN the manager opens the Sell screen, THE Sell_Module SHALL offer product options of pay-per-use cash, a new subscription, and a Holiday Special pass.
2. WHEN the manager sells a new subscription, THE Sell_Module SHALL record the R350 subscription price and SHALL allow selecting up to four members to include.
3. WHEN the manager completes a sale, THE Sell_Module SHALL write a `payment` record and, for a subscription or Holiday Special, an `entitlement` create action to the Local_Store and enqueue the corresponding Sync_Actions.
4. IF the manager attempts to add more than four members to a subscription, THEN THE Sell_Module SHALL prevent adding the fifth member.
5. WHEN a sale is captured offline, THE Sell_Module SHALL complete the sale without network connectivity.

### Requirement 13: Revenue dashboard

**User Story:** As a founder, I want one dashboard showing all three revenue streams, so that I can see how the business is performing across time and location.

#### Acceptance Criteria

1. WHEN the founder opens the Revenue Dashboard, THE Revenue_Dashboard SHALL display the pay-per-use, subscription, and school-contract revenue streams read from `GET /revenue/summary`.
2. THE Revenue_Dashboard SHALL display the school-contract stream even when its value is R0.
3. WHEN the founder selects a daily, weekly, or monthly period, THE Revenue_Dashboard SHALL display the revenue streams for the selected period.
4. WHEN the founder selects a location, THE Revenue_Dashboard SHALL display the revenue streams for the selected location.
5. WHILE the device is offline, THE Revenue_Dashboard SHALL render the last cached revenue summary and SHALL indicate that the data is cached.

### Requirement 14: Attendance and school sessions

**User Story:** As a founder, I want to log school sessions by type and mark attendance quickly, so that programme delivery is recorded even without signal.

#### Acceptance Criteria

1. WHEN the founder starts a school session, THE Attendance_Module SHALL require selecting a session type of lesson, kit, or esports.
2. WHEN the founder selects a class roster, THE Attendance_Module SHALL display each roster member with a tap-to-toggle attendance control.
3. WHERE the session type is kit, THE Attendance_Module SHALL present a kit-module quick field; WHERE the session type is esports, THE Attendance_Module SHALL present a match-particulars quick field; WHERE the session type is lesson, THE Attendance_Module SHALL present a lesson-reference quick field.
4. WHEN the founder confirms a school session, THE Attendance_Module SHALL write a `session` record and one `attendance` record per marked-present roster member to the Local_Store and enqueue the corresponding Sync_Actions.
5. WHEN a school session is captured offline, THE Attendance_Module SHALL complete the capture without network connectivity.

### Requirement 15: Metrics entry

**User Story:** As a founder, I want a fast grid to enter TypingBird numbers, so that I can transfer the paper table quickly and accurately.

#### Acceptance Criteria

1. WHEN the founder opens Metrics Entry, THE Metrics_Module SHALL display a grid with columns for student name, words-per-minute, and accuracy.
2. WHEN the founder enters a words-per-minute value or an accuracy value for a student row, THE Metrics_Module SHALL accept only non-negative numeric input for that field.
3. WHEN the founder saves a metrics row, THE Metrics_Module SHALL write a `student_metrics` record to the Local_Store and enqueue the corresponding Sync_Action.
4. WHEN a metrics row is captured offline, THE Metrics_Module SHALL complete the entry without network connectivity.

### Requirement 16: Operational alerts

**User Story:** As a founder, I want to see deterministic operational alerts, so that I can act on inactivity, expiring entitlements, due payments, and stale devices.

#### Acceptance Criteria

1. WHEN the founder opens the Alerts screen and the device is online, THE Alerts_View SHALL retrieve alerts from `GET /alerts` and display each alert's type and subject.
2. THE Alerts_View SHALL render the alert types no-session-in-7-days, entitlement-expiring, subscription-payment-due, and unsynced-device-older-than-5-days as returned by the Container_API.
3. WHILE the device is offline, THE Alerts_View SHALL display the last cached alerts and SHALL indicate that the data is cached.
4. THE Alerts_View SHALL display alerts as received from the Container_API without recomputing alert rules on the client.

### Requirement 17: POPIA-aligned on-device protection

**User Story:** As a data controller, I want minors' data on the device to be protected, so that we meet our POPIA obligations by design.

#### Acceptance Criteria

1. WHEN the Revenue_PWA writes a record containing personal data to the Local_Store, THE Revenue_PWA SHALL store the personal-data payload in encrypted form.
2. WHILE no user is authenticated, THE Revenue_PWA SHALL withhold display of stored personal data until a user authenticates.
3. WHEN the authenticated session's JWT `expires_at` is reached, THE Auth_Manager SHALL require re-authentication before personal data is displayed again, WHERE a re-authentication grace period of up to 30 seconds MAY maintain access to already-displayed personal data while re-authentication completes.
4. THE Revenue_PWA SHALL transmit all Container_API and sync requests over HTTPS.
5. THE Revenue_PWA SHALL limit stored personal fields to those required by the capture flows and SHALL NOT persist national ID numbers or residential addresses.
