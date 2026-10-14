# Hero Azure deployment baseline

This fork prepares a reproducible Open Wearables deployment for Hero 5.0.
No Azure infrastructure has been provisioned by this preparation.

## Source and update policy

- Upstream: https://github.com/the-momentum/open-wearables.
- Release branch: `codex/azure-0.9.0`.
- Baseline: tag `0.9.0`, commit `ff8527a52ad8a96cd1ebe8c19344295c934ae9dc`.
- Hero integration uses iOS SDK `0.15.0` and Android SDK `v0.13.1`.
- The fork's `main` is an upstream tracking reference, not a deployment target.
- Preserve upstream licensing and applied migration history. Review upstream
  upgrades separately; do not automatically merge upstream main into a release.
- Source patches must have a demonstrated compatibility need and scoped checks.
- Pin production images by digest. `deployment-baseline.json` intentionally has
  no image digests until images have been selected, built and verified.

## Azure work still required

Target topology: an HTTPS API, private Celery worker, a single Celery beat
scheduler, a protected administration portal, PostgreSQL databases for OW and
Svix, authenticated Redis, and private self-hosted Svix. Flower is optional.
Choose capacity, network and cost before provisioning. Do not scale persistent
workers or beat to zero without an explicit reactivation mechanism. Avoid
concurrent beat instances during revision changes.

Keep the OW and Svix databases separate from the Hero application database.
Provision database roles and backups explicitly. Upstream API startup runs
Alembic and attempts Svix database creation; prepare a controlled bootstrap and
migration step suitable for managed PostgreSQL and restricted runtime roles.
Validate PostgreSQL and Redis TLS, certificates, credentials and private routing.

Enable `OUTGOING_WEBHOOKS_ENABLED=true` (upstream defaults to false), configure
Svix event types and the signed Hero receiver, and verify retry/idempotency.
Use managed identities and Key Vault for deployment/runtime secrets. Do not
commit credentials, health payloads, admin passwords or environment files.
Raw S3 payload storage, SDK S3 offload and Apple XML import are not prerequisites
for the initial device SDK integration.

## Hero connection contract

The Hero backend needs these settings, supplied outside source control:

- `OPEN_WEARABLES_API_URL`: service API reachable by the Hero backend.
- `OPEN_WEARABLES_PUBLIC_URL`: HTTPS service API reachable by mobile devices.
- `OPEN_WEARABLES_API_KEY`: backend API key.
- `OPEN_WEARABLES_APP_ID` and `OPEN_WEARABLES_APP_SECRET`: SDK application.
- `OPEN_WEARABLES_WEBHOOK_SECRET`: Svix endpoint signing secret, distinct from
  the Svix server JWT secret.

The receiver path is `/api/open-wearables/webhook` on the Hero backend. Register
only events supported by Hero's `OPEN_WEARABLES_SUBSCRIBED_EVENTS` contract.
The service provides each deployment for one organization; Hero owns user/team
isolation and sharing permissions. Never expose the backend API key or app
secret to a mobile app in place of user-scoped SDK credentials.

## Deployment acceptance

Before store review, demonstrate device SDK ingestion, signed webhook delivery,
workout and metric persistence, recovery of missing events, permissions and
revocation, disconnect with retain/delete options, and account deletion. Record
source SHAs, image digests, database migration state and backup/restore evidence.
The new Hero backend must remain compatible with already distributed 4.x apps.
Hero 5.0 and every white-label app require their own store declarations and
native validation; this fork does not provide or attest those approvals.

## Rollback

Retain previous image digests and deployment configuration. Verify old code is
compatible with any schema expansion before using it for rollback. Never undo
an application deployment by destructively deleting imported health data or
rewriting an applied migration.
