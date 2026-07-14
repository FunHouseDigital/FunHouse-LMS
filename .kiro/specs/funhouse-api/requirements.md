# Requirements Document

## Introduction

This spec defines the **FunHouse Container API** — Spec 2 of the FunHouse Operating System (PRD §12, "Spec 2: Schema + API"). It builds a single portable HTTP API, packaged as one Docker container, on top of the Phase 0 Data Foundation (the 14-table PostgreSQL schema, idempotent seed, and deterministic Load logic already delivered in `funhouse_pipeline/`).

The API is the **sync target** for an offline-first Progressive Web App (PWA) that will be built in a later spec. Field devices (a lounge tablet, a facilitator's phone) write records locally first and sync opportunistically. This API accepts those queued writes, applies them deterministically and idempotently, records every applied action in an audit ledger, and exposes the read/write resource endpoints the PWA needs (players, sessions, entitlements, payments, products, revenue summary, alerts).

The API carries its own authentication (self-managed JWT + bcrypt, no AWS Cognito), enforces role-based access control at the API layer, and honors POPIA-by-design constraints because most data subjects are minors. It is portable by construction: runnable on AWS (App Runner / ECS) now or any VPS later, depending only on PostgreSQL.

This spec covers the API layer only. The PWA frontend (service worker, IndexedDB, screens) is Spec 3, and lesson generation / SMS / parent summaries are Phases 2–3 — both are out of scope here.

## Glossary

- **FunHouse_API**: The single portable HTTP API service defined by this spec; the container that field devices sync against and read from.
- **Auth_Service**: The FunHouse_API component that authenticates users and issues and verifies JSON Web Tokens (JWTs).
- **JWT**: A signed JSON Web Token issued by the Auth_Service that carries the authenticated user's identity (user id), role, and location scope, plus an expiry claim.
- **Token_Verifier**: The FunHouse_API middleware component that validates the JWT on each protected request and rejects missing, malformed, or expired tokens.
- **RBAC_Enforcer**: The FunHouse_API component that authorizes each request against the caller's role and scope before any data is read or written.
- **Role**: One of `founder`, `manager`, or `facilitator`, stored in `users.role`, determining data-access scope.
- **Founder**: A user with role `founder` (e.g. Aya) who is authorized to access data across all locations and schools.
- **Manager**: A user with role `manager` (e.g. Loyiso) who is authorized to access data for the lounge location assigned to that user only.
- **Facilitator**: A user with role `facilitator` who is authorized to access data for the school assigned to that user only.
- **Scope**: The set of records a caller may access, expressed as a `location_id` and, for facilitators, a `school_id`.
- **Sync_Service**: The FunHouse_API component that accepts a batch of offline-created records and applies them server-side.
- **Sync_Batch**: A request payload containing an ordered queue of Sync_Actions submitted by one device in a single sync attempt.
- **Sync_Action**: One offline-created write within a Sync_Batch, targeting a known entity (session, attendance, player, payment, entitlement, or consent) and carrying a `client_id`, `created_at`, and the record payload.
- **client_id**: A device-supplied stable identifier for a Sync_Action, used to make re-sending the same action idempotent (no duplicate rows).
- **created_at (device-origin)**: The device-side creation timestamp on a Sync_Action, used to establish device-origin ordering and to resolve last-write-wins conflicts.
- **Last_Write_Wins (LWW)**: The MVP conflict rule: when two Sync_Actions target the same record, the one with the later device-origin `created_at` determines the final stored value.
- **Idempotency_Key**: The client-supplied id or natural key used to detect that an incoming Sync_Action has already been applied, so re-applying it is a no-op.
- **sync_log**: The append-only audit table (`sync_log`) recording, for every applied action, the entity, record id, action type, acting user, device, and timestamps.
- **Entitlement_Engine**: The deterministic FunHouse_API component that creates entitlements, draws down (decrements) units on a session, resets recurring allowances, and answers balance queries.
- **Entitlement**: A row in `entitlements` representing what a player is entitled to under a product (e.g. Holiday Special 3 hrs/week), tracking remaining units and validity window.
- **Digital_Signature**: The pairing of the acting user (`logged_by`) with a server timestamp recorded when an Entitlement is decremented by a session, serving as the accountable record of who drew the entitlement and when.
- **Revenue_Reporter**: The FunHouse_API component that computes the revenue summary across three streams.
- **Revenue_Stream**: One of the three revenue categories: `pay_per_use`, `subscription`, and `school_contracts`.
- **Alerts_Engine**: The deterministic (IF-statement, non-AI) FunHouse_API component that computes rule-based operational alerts.
- **Alert**: A deterministic finding produced by the Alerts_Engine (e.g. no session in 7 days, entitlement expiring, subscription due, unsynced device older than 5 days).
- **POPIA**: South Africa's Protection of Personal Information Act; the API is designed to comply because most subjects are minors.
- **Prohibited_Field**: Personal data the system must never store — national identity numbers and physical addresses (per Phase 0 POPIA filter).

## Requirements

### Requirement 1: User Authentication and JWT Issuance

**User Story:** As a FunHouse staff member, I want to log in with my credentials and receive a token, so that I can make authenticated requests from my device.

#### Acceptance Criteria

1. WHEN a login request is received with an identifier and a password matching a stored `users` row, THE Auth_Service SHALL return a JWT containing the user id, the user role, and the user location scope.
2. WHEN a login request is received with an identifier and password, THE Auth_Service SHALL verify the password against the bcrypt hash stored in `users.password_hash`.
3. IF a login request contains an identifier that matches no `users` row, THEN THE Auth_Service SHALL reject the request with an authentication-failure response and SHALL NOT issue a JWT.
4. IF a login request contains a password that does not match the stored bcrypt hash, THEN THE Auth_Service SHALL reject the request with an authentication-failure response and SHALL NOT issue a JWT.
5. WHEN the Auth_Service issues a JWT, THE Auth_Service SHALL include an expiry claim set to a configured lifetime.
6. WHEN a new user password is stored, THE Auth_Service SHALL hash the password with bcrypt before persisting it to `users.password_hash` and SHALL persist only the hash.
7. IF a login request omits the identifier or the password, THEN THE Auth_Service SHALL reject the request with a validation-error response.

### Requirement 2: Token Verification and Session Expiry

**User Story:** As a security-conscious operator, I want tokens to be verified on every request and to expire, so that access to minors' data is time-limited and protected.

#### Acceptance Criteria

1. WHEN a request to a protected endpoint is received with a valid unexpired JWT, THE Token_Verifier SHALL authenticate the request and make the token's user id, role, and scope available to downstream authorization.
2. IF a request to a protected endpoint arrives without a JWT, THEN THE Token_Verifier SHALL reject the request with an authentication-required response.
3. IF a request to a protected endpoint arrives with a JWT whose signature is invalid, THEN THE Token_Verifier SHALL reject the request with an authentication-failure response.
4. IF a request to a protected endpoint arrives with a JWT whose expiry claim is in the past relative to the current server time, THEN THE Token_Verifier SHALL reject the request with an authentication-failure response.
5. WHERE an endpoint is designated as public (the login endpoint and health check), THE Token_Verifier SHALL allow the request without a JWT.
6. THE Token_Verifier SHALL require a valid unexpired JWT for every protected endpoint under all circumstances.
7. IF more than one rejection condition applies to a request (missing, invalid, or expired JWT), THEN THE Token_Verifier SHALL reject the request.

### Requirement 3: Role-Based Access Control

**User Story:** As the founder, I want each role to see only the data it is entitled to, so that a manager cannot read another location's data and a facilitator cannot read another school's data.

#### Acceptance Criteria

1. WHEN a request from a founder is authorized, THE RBAC_Enforcer SHALL grant access to records across all locations and all schools.
2. WHEN a request from a manager is authorized, THE RBAC_Enforcer SHALL restrict access to records whose `location_id` equals the manager's assigned location.
3. WHEN a request from a facilitator is authorized, THE RBAC_Enforcer SHALL restrict access to records whose `location_id` equals the facilitator's assigned location AND whose `school_id` equals the facilitator's assigned school.
4. WHEN a collection read is authorized, THE RBAC_Enforcer SHALL exclude records outside the caller's scope from the response.
5. IF a request attempts to write a record outside the caller's scope, THEN THE RBAC_Enforcer SHALL reject the write with an authorization-failure response and SHALL NOT persist the write.
6. WHEN the RBAC_Enforcer authorizes a read query, THE RBAC_Enforcer SHALL constrain the query by `location_id` and, for facilitators, additionally by `school_id`.
7. IF a request directly reads a specific record outside the caller's scope, THEN THE RBAC_Enforcer SHALL respond with an authorization-failure response.

### Requirement 4: Idempotent Batch Sync

**User Story:** As a field device operating offline, I want to submit my queue of locally-created records and have them applied exactly once, so that re-syncing after a dropped connection never creates duplicates.

#### Acceptance Criteria

1. WHEN a Sync_Batch is received, THE Sync_Service SHALL apply each Sync_Action to its target entity server-side and SHALL return a per-action result indicating applied, skipped, or rejected.
2. WHEN a Sync_Action carries a `client_id` or natural key that matches an already-applied action, THE Sync_Service SHALL treat the action as a no-op and SHALL NOT create a duplicate row.
3. WHEN the same Sync_Batch is submitted more than once, THE Sync_Service SHALL produce the same final database state as submitting it once.
4. WHEN the Sync_Service applies a Sync_Action, THE Sync_Service SHALL record the acting user in `logged_by` where the target table carries that column and SHALL append a `sync_log` entry referencing the entity, record id, and action.
5. IF a Sync_Action fails to apply, THEN THE Sync_Service SHALL isolate that failure to the single action, SHALL record the failure in the per-action result, and SHALL continue applying the remaining actions in the Sync_Batch.
6. WHERE a Sync_Action targets an entity supported by Phase 0 Load semantics, THE Sync_Service SHALL reuse the deterministic dedup and natural-key idempotency rules from Phase 0 Load.
7. IF a Sync_Action targets a record outside the submitting user's scope, THEN THE Sync_Service SHALL reject that action and SHALL NOT persist it.

### Requirement 5: Last-Write-Wins Conflict Resolution

**User Story:** As an operator whose devices sometimes edit the same record, I want a predictable conflict rule, so that concurrent offline edits resolve deterministically.

#### Acceptance Criteria

1. WHEN two Sync_Actions target the same existing record with different device-origin `created_at` values, THE Sync_Service SHALL retain the values from the Sync_Action with the later `created_at`.
2. IF an incoming Sync_Action targets a record whose stored device-origin `created_at` is later than the incoming action's `created_at`, THEN THE Sync_Service SHALL preserve the stored values and SHALL record the action as skipped.
3. WHEN the Sync_Service resolves any conflict, including a tie-break, THE Sync_Service SHALL append a `sync_log` entry recording the resolved action and the acting device.
4. WHEN two Sync_Actions target the same record with equal device-origin `created_at` values, THE Sync_Service SHALL apply a deterministic tie-break using the `client_id` ordering so that the outcome is reproducible.

### Requirement 6: Player Roster and Registration

**User Story:** As a facilitator, I want to view the player roster and register new players with their consents, so that every child in my care has a compliant record.

#### Acceptance Criteria

1. WHEN a scoped roster read is requested, THE FunHouse_API SHALL return the players within the caller's scope.
2. WHEN a player registration request is received with a first name, THE FunHouse_API SHALL create a player row scoped to the caller's `location_id`.
3. WHEN a player is registered with one or more of the four consent types, THE FunHouse_API SHALL append one `consents` row per supplied consent type.
4. WHEN the FunHouse_API appends a consent, THE FunHouse_API SHALL append a new `consents` row and SHALL NOT update or delete any existing `consents` row.
5. WHEN a player registration request matches an existing player by the Phase 0 dedup key, THE FunHouse_API SHALL resolve to the existing player row rather than create a duplicate.
6. IF a player registration request omits a first name, THEN THE FunHouse_API SHALL reject the request with a validation-error response.
7. WHEN a player history read is requested, THE FunHouse_API SHALL return that player's sessions, payments, and entitlement draws within the caller's scope.
8. WHERE none of the requested player's history falls within the caller's scope, THE FunHouse_API SHALL return an empty history result.
9. IF a player registration request supplies zero consent types, THEN THE FunHouse_API SHALL reject the request with a validation-error response.

### Requirement 7: Session Logging

**User Story:** As a lounge operator, I want to log a session with the player, console, duration, and how it was paid, so that usage and revenue are captured.

#### Acceptance Criteria

1. WHEN a session-log request is received with a player, session details, and a duration, THE FunHouse_API SHALL create a `sessions` row scoped to the caller's `location_id` with `logged_by` set to the acting user.
2. WHERE a session-log request specifies payment, THE FunHouse_API SHALL associate the session with a `payments` record.
3. WHERE a session-log request specifies an entitlement draw, THE FunHouse_API SHALL decrement the referenced Entitlement via the Entitlement_Engine.
4. IF a session-log request references a player outside the caller's scope, THEN THE FunHouse_API SHALL reject the request with an authorization-failure response.
5. WHEN the FunHouse_API creates a session, THE FunHouse_API SHALL append a `sync_log` entry referencing the session record and the acting user.
6. IF the authorization check for a session-log request cannot be completed, THEN THE FunHouse_API SHALL reject the request rather than allow it to proceed.

### Requirement 8: Entitlement Draw and Digital Signature

**User Story:** As a manager, I want entitlements to decrement when drawn and to show who drew them, so that punch-card balances are accurate and accountable.

#### Acceptance Criteria

1. WHEN an Entitlement is created from a sell action, THE Entitlement_Engine SHALL create an `entitlements` row with its remaining units and validity window derived from the product rules.
2. WHEN a session draws from an Entitlement, THE Entitlement_Engine SHALL decrement the Entitlement's remaining units by the drawn amount.
3. WHEN the Entitlement_Engine decrements an Entitlement, THE Entitlement_Engine SHALL record the acting user in `logged_by` and a server timestamp as the Digital_Signature of the draw.
4. IF a draw is requested against an Entitlement with insufficient remaining units, THEN THE Entitlement_Engine SHALL reject the draw and SHALL leave the remaining units unchanged.
5. IF a draw is requested against an Entitlement whose status is not active, THEN THE Entitlement_Engine SHALL reject the draw and SHALL leave the remaining units unchanged.
6. WHEN an Entitlement balance query is received for a player, THE Entitlement_Engine SHALL return the current remaining units and validity window for that player's entitlements within the caller's scope.
7. WHEN a player history is read, THE FunHouse_API SHALL include each Entitlement draw with its acting user and timestamp.
8. THE Entitlement_Engine SHALL record the Digital_Signature only when an Entitlement's remaining units are actually decremented.
9. IF the Digital_Signature cannot be recorded after a decrement, THEN THE Entitlement_Engine SHALL roll back the decrement so that the remaining units are unchanged.

### Requirement 9: Recurring Entitlement Reset

**User Story:** As a manager selling a weekly allowance product, I want the allowance to reset on schedule without rollover, so that "3 hrs/week, resets Sunday, no rollover" behaves as sold.

#### Acceptance Criteria

1. WHERE an Entitlement's product rules specify a recurring allowance, THE Entitlement_Engine SHALL reset the remaining units to the product's per-period allowance at the start of each period.
2. WHERE an Entitlement's product rules specify no rollover, THE Entitlement_Engine SHALL discard any unused units from the prior period automatically at the start of each period.
3. WHEN a draw is evaluated against a recurring Entitlement, THE Entitlement_Engine SHALL apply the reset for the current period before computing remaining units.
4. WHEN the Entitlement_Engine computes a reset, THE Entitlement_Engine SHALL derive the period boundary deterministically from the product rules without any AI or model call.

### Requirement 10: Payments and Products

**User Story:** As an operator, I want to record payments and read the seeded product catalog, so that sales and pricing are captured against the correct product.

#### Acceptance Criteria

1. WHEN a payment-record request is received with a player and an amount, THE FunHouse_API SHALL create a `payments` row with the amount stored as integer cents and `logged_by` set to the acting user.
2. WHERE a payment-record request references a product, THE FunHouse_API SHALL associate the payment with the referenced `products` row.
3. WHEN a product catalog read is requested, THE FunHouse_API SHALL return the seeded products within the caller's scope.
4. WHEN the FunHouse_API records a payment, THE FunHouse_API SHALL append a `sync_log` entry referencing the payment record and the acting user.
5. IF a payment-record request omits the amount, THEN THE FunHouse_API SHALL reject the request with a validation-error response.

### Requirement 11: Three-Stream Revenue Summary

**User Story:** As the founder, I want a revenue summary split into pay-per-use, subscription, and school-contract streams, so that I can see where money comes from.

#### Acceptance Criteria

1. WHEN a revenue summary is requested, THE Revenue_Reporter SHALL return totals for the `pay_per_use`, `subscription`, and `school_contracts` streams separately.
2. WHEN the Revenue_Reporter computes the `pay_per_use` and `subscription` streams, THE Revenue_Reporter SHALL sum the payments attributable to products of the corresponding type within the caller's scope.
3. WHILE no payment exists for the school-contract stream, THE Revenue_Reporter SHALL report the `school_contracts` stream total as R0.
4. WHEN the Revenue_Reporter computes any stream total, THE Revenue_Reporter SHALL express monetary amounts in integer cents consistently with the stored payment amounts.
5. WHEN a revenue summary is requested by a manager or facilitator, THE Revenue_Reporter SHALL restrict the summary to the caller's scope.

### Requirement 12: Deterministic Rule-Based Alerts

**User Story:** As a manager, I want deterministic operational alerts, so that I can act on lapsed players, expiring entitlements, due subscriptions, and stale devices without relying on AI.

#### Acceptance Criteria

1. WHEN an alerts query is received, THE Alerts_Engine SHALL evaluate all alert rules deterministically using conditional logic and SHALL NOT invoke any AI or model call.
2. WHERE a player has no session within the preceding 7 days, THE Alerts_Engine SHALL include a no-recent-session Alert for that player.
3. WHERE an Entitlement's validity window ends within the configured expiry horizon, THE Alerts_Engine SHALL include an entitlement-expiring Alert for that Entitlement.
4. WHERE a subscription is due, THE Alerts_Engine SHALL include a subscription-due Alert.
5. WHERE a device's most recent sync is older than 5 days, THE Alerts_Engine SHALL include an unsynced-device Alert for that device.
6. WHEN the Alerts_Engine returns alerts to a manager or facilitator, THE Alerts_Engine SHALL restrict the alerts to the caller's scope.

### Requirement 13: Portability and Deployment Constraints

**User Story:** As the founder wary of vendor lock-in, I want the API to run anywhere PostgreSQL runs, so that I can move off AWS without a rewrite.

#### Acceptance Criteria

1. THE FunHouse_API SHALL be packaged as a single Docker container image runnable on AWS App Runner or ECS and on any VPS.
2. THE FunHouse_API SHALL require a PostgreSQL database as its mandatory and only persistence dependency and SHALL NOT depend on AWS Cognito, DynamoDB, Pinpoint, or Lambda-as-application-architecture.
3. THE Auth_Service SHALL run inside the FunHouse_API container and SHALL NOT depend on any external identity provider.
4. WHERE a database connection is configured, THE FunHouse_API SHALL connect using a standard PostgreSQL connection string so that the database host is interchangeable.
5. WHERE Lambda functions are used only for auxiliary tasks (such as image processing or background jobs), THE FunHouse_API SHALL keep its core application logic within the container.

### Requirement 14: POPIA-by-Design Safeguards

**User Story:** As a data controller for minors, I want the API to minimize collection, log every access, and secure data in transit, so that we meet POPIA obligations.

#### Acceptance Criteria

1. IF a write request payload contains a Prohibited_Field (national identity number or physical address), THEN THE FunHouse_API SHALL strip the Prohibited_Field before persistence so that no Prohibited_Field is stored.
2. WHEN the FunHouse_API performs any write, THE FunHouse_API SHALL record the acting user in `logged_by` where the target table carries that column and SHALL append a `sync_log` entry.
3. THE FunHouse_API SHALL serve all traffic over TLS.
4. WHEN the FunHouse_API records a consent, THE FunHouse_API SHALL append to the `consents` ledger only and SHALL NOT update or delete existing consent rows.
5. THE FunHouse_API SHALL issue JWTs that expire, so that authenticated sessions are time-limited.
6. IF a `sync_log` entry cannot be appended for a write, THEN THE FunHouse_API SHALL fail the entire write request so that no business data is persisted without its audit entry.
7. IF TLS is unavailable, THEN THE FunHouse_API SHALL reject the request rather than serve it over an unencrypted connection.

### Requirement 15: Location Scoping for Scale

**User Story:** As an operator planning for 500 players across multiple locations, I want every query scoped by location, so that the system stays correct and fast as it grows.

#### Acceptance Criteria

1. WHEN the FunHouse_API executes a resource read query, THE FunHouse_API SHALL scope the query by `location_id`.
2. WHERE a resource is school-associated, THE FunHouse_API SHALL additionally scope the query by `school_id`.
3. WHEN the FunHouse_API creates a resource row, THE FunHouse_API SHALL set the row's `location_id` to the caller's scope.
