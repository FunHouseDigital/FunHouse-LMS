# Requirements Document

## Introduction

This spec defines **Spec 3.5 — Deployment**: the first production deployment of the already-built FunHouse Operating System to AWS. It is derived **strictly from PRD Section 3.1 (Portable Core)** and its "post-credits migration table." Its scope is **infrastructure, deployment, secrets, cost, and operability** — not new application features.

Three completed components are deployed as-is; their internals are out of scope for this spec:

- **Phase 0 pipeline** (`funhouse_pipeline/`) — the Python ETL, the 14-table PostgreSQL schema, idempotent migrations (`funhouse_pipeline/db/migrations.py`, `funhouse_pipeline/sql/*.sql`), and the seed (`db/seed.py`). It already archives raw files to S3 in `af-south-1` over TLS, establishing the POPIA data-residency precedent.
- **Container API** (`funhouse_api/`) — a FastAPI app already packaged as a single Docker container, depending only on PostgreSQL for persistence, carrying self-managed JWT auth and TLS-required middleware, and reading all configuration from environment variables (see `funhouse_api/config.py`). This container is the sync target for the PWA.
- **Revenue PWA** (`web/`) — a static Vite/React build producing `dist/` (with a service worker and manifest) that syncs to the Container API over HTTPS.

The deployment obeys a hard **portability rule**: nothing beyond PRD §3.1. No load balancers, no Kubernetes/EKS, no ECS clusters, no additional managed services, and no monitoring beyond CloudWatch defaults. Every AWS component must have a documented non-AWS equivalent so that standing up a second location or migrating away in June 2027 is a re-run, not a memory exercise. Total AWS burn at Location 1 scale must stay under a documented cost ceiling.

Phase 2 (the Lesson Engine, which uses Amazon Bedrock) is **gated behind Phase 1 field acceptance and is out of build scope here.** This spec only captures the **data-residency and documentation rule** that governs any future cross-region model inference; it does not build AI features.

Where a genuinely necessary component might exceed §3.1 (for example, some minimal supporting resource App Runner requires), this spec does not silently add it. Such items are captured as explicit open questions/assumptions (see the Assumptions and Open Questions section) so the founder can decide with the cost and migration implications visible.

## Glossary

- **FunHouse_OS**: The already-built FunHouse Operating System (Phase 0 pipeline, Container API, and Revenue PWA) being deployed to production by this spec.
- **Portable_Core**: The set of components defined in PRD §3.1 that this deployment is limited to; nothing outside this set may be provisioned.
- **Deployment**: The complete provisioned production environment plus the committed reproducible setup, runbook, and smoke-test checklist that this spec delivers.
- **af-south-1**: The AWS Cape Town region; the sole permitted region for all data at rest, consistent with the FunHouse POPIA posture.
- **Data_At_Rest**: Any persisted data, including the database contents, object-storage objects, backups, and any logs that contain data.
- **RDS_PostgreSQL**: The AWS RDS for PostgreSQL managed database instance that hosts the FunHouse schema; portable equivalent of any managed or self-hosted PostgreSQL server.
- **Automated_Backups**: The RDS-managed automatic backup feature retaining point-in-time recovery snapshots of RDS_PostgreSQL.
- **Migration_Runner**: The existing repository migration entry point (`run_migrations`, backed by `funhouse_pipeline/db/migrations.py` and `funhouse_pipeline/sql/*.sql`) used to apply the schema; hand-authored SQL is not used.
- **App_Runner**: AWS App Runner, the container hosting platform that runs the existing Container API image; portable equivalent of any Docker host or VPS.
- **Container_API**: The existing single Docker container (`funhouse_api/`) deployed as the sync target; its only persistence dependency is PostgreSQL.
- **Secrets_Store**: AWS Secrets Manager or AWS SSM Parameter Store, holding runtime secrets (database credentials, JWT secret, and similar); portable equivalent of any environment or secret store.
- **Static_Site_Hosting**: The combination of an S3 bucket and a CloudFront distribution serving the static PWA build over HTTPS; portable equivalent of any object store plus CDN or static host.
- **Revenue_PWA**: The static `web/` build (`dist/`, including service worker and manifest) served by Static_Site_Hosting.
- **Reproducible_Setup**: The committed infrastructure-as-code or documented setup scripts that provision the Deployment idempotently and repeatably.
- **Deployment_Runbook**: The committed end-to-end operating document a non-founder can follow to stand up, operate, and tear down the Deployment.
- **Smoke_Test_Checklist**: The committed checklist that proves the Revenue_PWA syncs to the live Container_API end to end (login, offline capture, sync, and read-back via the API).
- **Migration_Equivalence_Table**: The committed table mapping each AWS component used by the Deployment to its documented non-AWS equivalent, per the PRD post-credits migration table.
- **Cost_Ceiling**: The target upper bound on total monthly AWS spend at Location 1 scale, set at under US$80 per month.
- **Cost_Estimate**: The documented estimated monthly cost per component whose sum is compared against the Cost_Ceiling.
- **Cross_Region_Inference**: Any model-inference call whose processing region differs from af-south-1, relevant only to the gated, out-of-scope Phase 2 Lesson Engine.
- **POPIA**: South Africa's Protection of Personal Information Act; the deployment is designed to comply because most data subjects are minors.
- **Location_1**: The initial single FunHouse location whose production traffic scale defines the Cost_Ceiling and instance-sizing baseline.

## Requirements

### Requirement 1: Data Residency in af-south-1

**User Story:** As the FunHouse data controller, I want all persisted data to live only in af-south-1, so that the deployment stays consistent with our established POPIA posture.

#### Acceptance Criteria

1. THE Deployment SHALL provision all Data_At_Rest — including RDS_PostgreSQL storage, Static_Site_Hosting object storage, Automated_Backups, and any logs that contain data — in the af-south-1 region.
2. WHERE a component of the Deployment persists data, THE Deployment SHALL configure that component to reside in af-south-1.
3. IF a required capability is unavailable in af-south-1, THEN THE Deployment SHALL record the gap as an explicit open question for founder decision rather than provision Data_At_Rest in another region.
4. THE Deployment SHALL document that the af-south-1 residency decision follows the existing Phase 0 POPIA precedent.

### Requirement 2: RDS PostgreSQL Provisioning and Backups

**User Story:** As the founder, I want the database on a small, backed-up managed PostgreSQL instance, so that operations are reliable and recoverable without over-spending.

#### Acceptance Criteria

1. THE Deployment SHALL provision RDS_PostgreSQL as the single datastore for the FunHouse schema and SHALL NOT provision any second datastore.
2. THE Deployment SHALL select the smallest instance class that supports Location_1 scale for RDS_PostgreSQL.
3. THE Deployment SHALL enable Automated_Backups on RDS_PostgreSQL.
4. WHERE Automated_Backups are enabled, THE Deployment SHALL document the configured backup retention period.
5. THE Deployment SHALL provision RDS_PostgreSQL in af-south-1.

### Requirement 3: Schema Application via Existing Migration Runner

**User Story:** As an operator, I want the production schema created by running the repository's own migration scripts, so that production matches the tested code and stays idempotent.

#### Acceptance Criteria

1. WHEN the production schema is applied to RDS_PostgreSQL, THE Deployment SHALL apply it by executing the existing Migration_Runner (`run_migrations`) and SHALL NOT apply hand-authored SQL.
2. WHEN the Migration_Runner is executed more than once against RDS_PostgreSQL, THE Deployment SHALL produce the same final schema state as executing it once.
3. THE Deployment_Runbook SHALL document the exact command and configuration used to run the Migration_Runner against RDS_PostgreSQL.

### Requirement 4: Container API Hosting on App Runner over HTTPS

**User Story:** As the founder, I want the existing API container hosted on App Runner and reachable only over HTTPS, so that the sync target runs without extra infrastructure and stays encrypted in transit.

#### Acceptance Criteria

1. THE Deployment SHALL host the existing Container_API image on App_Runner.
2. THE Deployment SHALL configure App_Runner to serve the Container_API over HTTPS only.
3. IF a request to the Container_API arrives over an unencrypted connection, THEN THE Deployment SHALL cause the request to be redirected or rejected so that no traffic is served unencrypted.
4. THE Deployment SHALL supply the Container_API's configuration through platform-provided environment values read at runtime, consistent with `funhouse_api/config.py`.
5. THE Deployment SHALL connect the Container_API to RDS_PostgreSQL as its only persistence dependency using a standard PostgreSQL connection string.
6. WHERE the Container_API connects to RDS_PostgreSQL, THE Deployment SHALL configure the connection to use TLS in transit.

### Requirement 5: Secrets Management

**User Story:** As a security-conscious operator, I want database credentials and the JWT secret stored in a managed secret store, so that secrets are never committed to the repository.

#### Acceptance Criteria

1. THE Deployment SHALL store the Container_API runtime secrets (database credentials and JWT secret at minimum) in the Secrets_Store.
2. THE Deployment SHALL NOT store any secret in the repository source code or in any environment file committed to the repository.
3. WHEN the Container_API starts, THE Deployment SHALL provide its secrets from the Secrets_Store to the container at runtime.
4. WHERE a secret is referenced by the Reproducible_Setup, THE Deployment SHALL reference the Secrets_Store entry rather than an inline secret value.
5. THE Deployment SHALL provision the Secrets_Store entries in af-south-1.

### Requirement 6: PWA Hosting on S3 and CloudFront

**User Story:** As a field user, I want the PWA served fast and over HTTPS, so that I can install and use it reliably on my device.

#### Acceptance Criteria

1. THE Deployment SHALL host the static Revenue_PWA build (`web/dist/`) on Static_Site_Hosting composed of an S3 bucket and a CloudFront distribution.
2. THE Deployment SHALL serve the Revenue_PWA over HTTPS.
3. THE Deployment SHALL provision the Static_Site_Hosting object storage (the S3 bucket) in af-south-1.
4. WHEN the Revenue_PWA build is deployed, THE Deployment SHALL publish the service worker and manifest so that the PWA is installable.
5. IF a request for the Revenue_PWA arrives over an unencrypted connection, THEN THE Deployment SHALL redirect the request to HTTPS.

### Requirement 7: End-to-End Connectivity

**User Story:** As an operator, I want the PWA, the API, and the database to communicate securely end to end, so that a device can sync captured data all the way to storage.

#### Acceptance Criteria

1. THE Deployment SHALL enable the Revenue_PWA to reach the Container_API over HTTPS.
2. THE Deployment SHALL enable the Container_API to reach RDS_PostgreSQL over a TLS-encrypted PostgreSQL connection.
3. WHERE the Revenue_PWA origin differs from the Container_API origin, THE Deployment SHALL configure Cross-Origin Resource Sharing on the Container_API to permit the Revenue_PWA origin.
4. THE Deployment SHALL serve every network hop between the Revenue_PWA, the Container_API, and RDS_PostgreSQL over an encrypted connection.

### Requirement 8: Reproducible Setup

**User Story:** As the founder planning a second location and a possible 2027 migration, I want the environment defined as committed infrastructure-as-code or setup scripts, so that recreating it is a re-run rather than a memory exercise.

#### Acceptance Criteria

1. THE Deployment SHALL provide the Reproducible_Setup as infrastructure-as-code or as documented setup scripts committed to the repository.
2. WHEN the Reproducible_Setup is executed more than once, THE Reproducible_Setup SHALL produce the same provisioned state as executing it once.
3. THE Reproducible_Setup SHALL provision every AWS component the Deployment depends on: RDS_PostgreSQL, App_Runner, Static_Site_Hosting, and the Secrets_Store entries.
4. THE Reproducible_Setup SHALL be runnable to stand up a second location without hand-authored, undocumented steps.

### Requirement 9: Deployment Runbook

**User Story:** As a non-founder operator, I want a step-by-step runbook, so that I can stand up, operate, and tear down the deployment end to end without tribal knowledge.

#### Acceptance Criteria

1. THE Deployment SHALL provide a Deployment_Runbook committed to the repository.
2. THE Deployment_Runbook SHALL document, in order, the steps to provision the Deployment, apply the schema via the Migration_Runner, deploy the Container_API, and publish the Revenue_PWA.
3. THE Deployment_Runbook SHALL document how secrets are created in and consumed from the Secrets_Store.
4. THE Deployment_Runbook SHALL document how to tear down the Deployment.
5. THE Deployment_Runbook SHALL be written so that a non-founder can follow it end to end without additional undocumented knowledge.

### Requirement 10: Smoke-Test Checklist

**User Story:** As an operator validating a release, I want a smoke-test checklist proving the PWA syncs to the live API end to end, so that I can confirm the deployment works before relying on it.

#### Acceptance Criteria

1. THE Deployment SHALL provide a Smoke_Test_Checklist committed to the repository.
2. THE Smoke_Test_Checklist SHALL include a step that authenticates against the live Container_API (login).
3. THE Smoke_Test_Checklist SHALL include a step that captures a record in the Revenue_PWA while offline.
4. THE Smoke_Test_Checklist SHALL include a step that syncs the offline-captured record from the Revenue_PWA to the live Container_API.
5. THE Smoke_Test_Checklist SHALL include a step that reads the synced record back through the Container_API to confirm the data is present.
6. THE Smoke_Test_Checklist SHALL define the expected observable result for each step so that pass or fail is unambiguous.

### Requirement 11: Cost Ceiling and Per-Component Estimate

**User Story:** As the founder, I want the deployment costed per component against a ceiling, so that I know monthly AWS burn stays under budget at Location 1 scale.

#### Acceptance Criteria

1. THE Deployment SHALL include a Cost_Estimate stating the estimated monthly cost of each AWS component at Location_1 scale.
2. THE Deployment SHALL document that the sum of the Cost_Estimate stays under the Cost_Ceiling of US$80 per month at Location_1 scale.
3. IF the summed Cost_Estimate meets or exceeds the Cost_Ceiling, THEN THE Deployment SHALL record the overage as an explicit open question for founder decision.
4. THE Cost_Estimate SHALL cover RDS_PostgreSQL, App_Runner, Static_Site_Hosting, and the Secrets_Store.

### Requirement 12: Portability and No-Extra-Services Boundary

**User Story:** As the founder wary of vendor lock-in, I want the deployment limited to the portable core with documented non-AWS equivalents, so that I can migrate away without a rewrite.

#### Acceptance Criteria

1. THE Deployment SHALL provision only the components defined in the Portable_Core and SHALL NOT provision any component beyond PRD §3.1.
2. THE Deployment SHALL NOT provision a load balancer (ALB or NLB), Kubernetes or EKS, an ECS cluster, or any additional managed service beyond RDS_PostgreSQL, App_Runner, Static_Site_Hosting, and the Secrets_Store.
3. THE Deployment SHALL NOT configure monitoring beyond CloudWatch defaults.
4. THE Deployment SHALL provide a Migration_Equivalence_Table mapping each AWS component used to a documented non-AWS equivalent.
5. THE Migration_Equivalence_Table SHALL include an equivalent for RDS_PostgreSQL, App_Runner, Static_Site_Hosting, and the Secrets_Store.
6. IF a component genuinely required to run the Deployment would exceed the Portable_Core, THEN THE Deployment SHALL record it as an explicit open question with its cost and migration implications rather than provision it silently.

### Requirement 13: Cross-Region Model-Inference Residency Rule

**User Story:** As the data controller, I want a documented residency rule for any future cross-region model inference, so that the gated Phase 2 Lesson Engine cannot silently move minors' data out of af-south-1.

#### Acceptance Criteria

1. THE Deployment SHALL keep all Data_At_Rest in af-south-1 regardless of any model-inference configuration.
2. IF Cross_Region_Inference is ever required, THEN THE Deployment SHALL require an explicitly documented, POPIA-justified decision that names the chosen inference region.
3. WHERE Cross_Region_Inference is documented, THE decision SHALL state which data transits the region boundary and why the arrangement remains POPIA-defensible.
4. THE Deployment SHALL record that Phase 2 (the Lesson Engine using Amazon Bedrock) is gated and out of the current build scope, so that this requirement governs the residency and documentation rule only and not the building of any AI feature now.

## Assumptions and Open Questions

These items may require a component or decision at or near the edge of PRD §3.1. Per the portability rule, they are surfaced for founder decision rather than resolved silently. Each notes the cost and migration implication.

1. **App Runner supporting resources:** App_Runner may require a minimal supporting resource to pull the container image and to reach RDS_PostgreSQL (for example, a private image source or VPC connectivity for the database). Decision needed on whether any such resource is acceptable within §3.1, with its cost and migration implication documented.
2. **Database network exposure:** A decision is needed on whether RDS_PostgreSQL is reached privately (implying additional networking) or over a TLS-secured public endpoint restricted by security group, and the cost/portability trade-off of each.
3. **Custom domains and TLS certificates:** Whether the Container_API and Revenue_PWA use custom domains (and managed certificates) or platform-default HTTPS endpoints, and the migration implication of each choice.
4. **CloudFront regional footprint:** CloudFront is a global edge service; the decision confirms that only Data_At_Rest (the S3 origin) must remain in af-south-1 while edge caching is acceptable, or records an alternative.
5. **Cost ceiling headroom:** Confirmation that the smallest RDS instance plus App Runner minimums plus Static_Site_Hosting plus the Secrets_Store fit under the US$80/month Cost_Ceiling at Location_1 scale, and how to proceed if they do not.
