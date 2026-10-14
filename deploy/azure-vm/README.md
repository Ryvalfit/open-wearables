# Hero Open Wearables on an Azure VM

Status: service active over HTTPS; Hero integration and device acceptance pending. VM + existing Azure
PostgreSQL was approved in the Hero release conversation on 2026-10-14.

## Architecture and initial capacity

One Linux amd64 VM, Standard_D2as_v6 (2 vCPU, 8 GiB) in North Europe.
Use Docker Engine and Compose >= 2.24, Python 3, util-linux/flock, OpenSSL and an
updated OS CA bundle. The Compose file is standalone, not an upstream override.

Public API: `https://wearables.herofitness.es`, through Caddy on TCP 80/443.
The portal binds only to `127.0.0.1:3400`. API, worker, singleton beat, Svix and
authenticated Redis communicate on the Compose network without published ports.
Flower, local PostgreSQL, XML/S3 workers and object storage are omitted. XML import
must stay disabled until its storage and dedicated worker are explicitly added.
CPU limits are per-service caps, not reservations; containers share the VM.
Memory caps total 6.75 GiB for running services; validate headroom under real imports.
No API source changes or upstream migration changes are needed for this topology.

## Azure prerequisites (not executed by these scripts)

- Static Standard public IPv4 for the VM, SSH keys, no password authentication.
- VM on a **nondelegated** subnet of `vnet-rvf-prod-neu-data`, or a peered VNet.
  Never place it in `snet-psql-delegated`. Pick a free subnet after checking current
  prefixes, routing and NSGs. Ensure the PostgreSQL private DNS zone is linked to
  the VM VNet. Existing PostgreSQL has public access disabled; retain that setting.
- Inbound TCP 80/443 public; SSH only from administrator IPs/VPN. No rules exposing
  Redis, Svix, portal or the database. Preserve other workloads' NSG rules.
- Permit required outbound HTTPS, DNS, PostgreSQL 5432 and provider APIs.
- Existing PostgreSQL: `psql-rvf-prod-neu.postgres.database.azure.com`, version 16,
  resource group `rg-rvf-prod-neu-data`. Inspect current connections, CPU and storage
  before loading real health history; no server resize is part of this preparation.
- Existing ACR `acrrvfdbmigprodneu`; grant the VM identity scoped image-pull access
  appropriate for the registry RBAC mode. Use short-lived `az login --identity` /
  `az acr login` before pulling. Do not enable registry admin credentials.
- Reuse Key Vault `kv-rvf-prod-neu` for the independent secrets. Restrict read access
  to the VM identity; materialize root/operator-owned env files with mode 600.
  Existing backend identity is separate and needs its own OW integration secrets.
- Persist Docker's data directory on appropriately sized durable managed storage,
  preferably a separate data disk, mounted **before** starting containers. Confirm
  ownership and Docker service ordering. Do not reconfigure another workload's disk.
- OS security updates, disk/certificate expiry alerts, external API uptime monitoring,
  container restart alerts and bounded logs. No high availability with one VM.

## IONOS DNS and HTTPS

After Azure allocates the static IPv4, create this DNS record in IONOS:

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| A | `wearables` | VM static public IPv4 | 300 seconds initially |

Do not modify the root domain, mail records or other subdomains. Remove a conflicting
record for **this exact hostname** only after reviewing its current use. An AAAA
record must not point elsewhere; do not create AAAA unless IPv6 is configured.
If CAA records restrict issuers, permit Caddy's selected ACME issuer. Check DNS
propagation and TCP 80/443 reachability before expecting certificate issuance.
Caddy automatically obtains/renews HTTPS certificates and redirects HTTP. Persist
its volumes across updates. No IONOS API key or DNS challenge is required for this
single hostname with public HTTP/TLS ACME challenges. A scoped IONOS key can automate
the A-record later, but is not stored or consumed by this deployment.

## Create two databases and roles

An authorized PostgreSQL administrator performs this once via the private network.
Use an interactive `psql` connection with `sslmode=verify-full` and the trusted CA
bundle. Do not put administrator credentials in the OW configuration.

```sql
CREATE ROLE hero_open_wearables LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE hero_svix LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password hero_open_wearables
\password hero_svix
CREATE DATABASE open_wearables OWNER hero_open_wearables;
CREATE DATABASE svix OWNER hero_svix;
REVOKE ALL ON DATABASE open_wearables FROM PUBLIC;
REVOKE ALL ON DATABASE svix FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE open_wearables TO hero_open_wearables;
GRANT CONNECT, TEMPORARY ON DATABASE svix TO hero_svix;
\connect open_wearables
GRANT USAGE, CREATE ON SCHEMA public TO hero_open_wearables;
\connect svix
GRANT USAGE, CREATE ON SCHEMA public TO hero_svix;
```

Review existing objects before executing: these commands intentionally fail on
name collisions rather than modifying an existing role/database. Generate independent
64-character hex passwords using `openssl rand -hex 32`. The OW upstream builds its
DB URI without percent-encoding; the hex format avoids reserved URI characters.
Azure may keep public schema ownership with azure_pg_admin even in a new database;
the explicit schema grants above are required for both migration runners. Verify
USAGE/CREATE on each owning database before starting Svix or OW bootstrap.
The two roles own only their own databases; neither receives server administration
or Hero schema permissions. Runtime API startup does not create databases or migrate.
The owner roles also perform maintenance migrations in this first deployment.

Microsoft recommends hostname/certificate verification and the current Microsoft RSA
Root CA 2017 and DigiCert Global Root G2 roots. Verify the host CA bundle contains
these roots before using the bound bundle for OW/libpq and Svix. Do not weaken TLS to
work around DNS/certificate failures.

## Build reviewed images

From a full, clean checkout of `codex/azure-0.9.0`, with an authorized Azure CLI
session, run:

```sh
bash deploy/azure-vm/build-images.sh
```

This queues two sequential ACR builds and publishes commit-tagged backend/frontend
images. It uses `git archive` to exclude ignored local files; the backend Docker
context additionally excludes local env/provider-key files. It writes immutable
references to `deploy/azure-vm/release-images.env`. Record the source SHA, ACR build
results and digests before deploying. No images are built on the production VM.
The upstream Dockerfiles still use mutable base-image tags; runtime digest pinning
fixes the deployed artifact, not bit-for-bit reproducibility of a later rebuild.
A failed build does not change production. Initial images were built in ACR from source 91462a6b0e1b54d7032bc2ae41e13244868ecf10
using immutable remote Git contexts; runs cg7 and cg8 succeeded. Their immutable
references are recorded in infrastructure-record.json and .env.example. The local
archive build helper remains available for subsequent reviewed releases.

## Configure the VM

Install the reviewed fork files under `/opt/hero-open-wearables` or another
operator-owned directory. Keep secrets outside Docker build contexts. In its
`deploy/azure-vm` directory:

```sh
umask 077
cp .env.example .env
cp backend.env.example backend.env
cp svix.env.example svix.env
cp redis.env.example redis.env
chmod 600 .env backend.env svix.env redis.env
```

Fill the image refs from `release-images.env`, the certificate contact email and
independently generated secrets. `SECRET_KEY` uses `openssl rand -hex 64`; other
passwords/JWT secret use `openssl rand -hex 32`. The Redis password must match in
all three runtime files; the Svix JWT secret must match OW and Svix. Svix's database
password is separate. Do not use the server JWT secret as Hero's webhook endpoint
signing secret. Add only approved provider credentials. Configure Resend and its
verified sender if administrator invitations are needed.

Files use literal unquoted values with no interpolation or inline comments. Do not
`source` them into a shell. Never run `docker compose config` without `--quiet` on
real configuration in shared logs: its expanded output contains secrets.

```sh
python3 preflight.py
docker compose --env-file .env -f compose.yml config --quiet
```

## Deploy and accept

Before the first deploy, confirm private DNS/TLS connectivity, both databases and
roles, current backups and VM storage. Before any update, take/verify recoverable
backups, record existing digests and migration revisions, and review schema
compatibility. The deploy script requires these preparations; it does not attest them.

```sh
bash deploy.sh
```

The script locks maintenance, validates configuration, pulls images before stopping
services, stops beat/workers/API/portal/proxy and Svix, starts Redis/Svix, then runs
explicit OW migrations and upstream initialization before starting the application.
Svix performs its own migrations at its controlled start. Bootstrap verifies the
OW database/role, PostgreSQL 16 and encrypted connection before applying migrations.
Data migrations fail deployment instead of silently continuing. The API runs without
upstream auto-bootstrap. This procedure deliberately has a maintenance interruption;
a failed migration leaves producers stopped for diagnosis, never an automatic
schema rollback. Stop/inspect failed containers before retrying.

`--wait` proves configured health checks only; worker/beat/portal/proxy being running
is not proof of queue consumption, HTTPS, database readiness or signed delivery.
Verify:

- HTTPS certificate/redirect and root API response from outside Azure.
- Open portal through `ssh -N -L 3400:127.0.0.1:3400 <operator>@<VM-IP>` and visit
  `http://localhost:3400`; log in naturally, then change the seeded admin password.
  `ADMIN_PASSWORD` changes do not rotate an existing account. SSH authenticates portal
  access; portal/API credentials still protect administration operations.
- Create the Hero SDK application/API key and signed webhook endpoint at
  `https://api.ryvalfit.com/api/open-wearables/webhook`, restricted to events supported
  by Hero's `OPEN_WEARABLES_SUBSCRIBED_EVENTS` contract.
- Configure Hero's API/public URLs to `https://wearables.herofitness.es`, app ID,
  app secret, backend API key and **Svix endpoint signing secret** via its own secret
  configuration. These steps are not performed by this fork's deployment scripts.
- Real iOS/Android device sync, queued task consumption, single scheduled producer,
  signed delivery/retries, Hero idempotency, persisted history and authorization.
- Provider OAuth callbacks, revocation and deletion. Samsung stays gated by its
  approval; the fork does not establish provider/store/native redistribution approval.
- Watch CPU/RAM, Redis memory, queue depth/age, DB connections/storage and failed
  containers under a representative history import. Adjust before releasing 5.0.

## Backups, rollback and later extraction

PostgreSQL currently has 7-day PITR retention without geo redundancy. Verify restores
of **both** new databases, including Svix delivery state. Azure PITR restores a
server; restoring selected databases into this shared server needs a reviewed
selective recovery procedure. Never restore over Hero to recover OW.

Redis uses persistent AOF, `everysec`, and `noeviction`: pressure rejects writes
instead of silently evicting queued work. Persistence is not a backup; a crash can
still lose the last second of writes. Add off-VM encrypted backups of Redis and Caddy
state and demonstrate restoration before production acceptance. Cleanly stop writers
and Redis before taking a filesystem-level snapshot; do not copy live AOF files
as if they were a consistent backup. Restoring only PostgreSQL can leave queues and
delivery state inconsistent: plan replay/reconciliation with Hero's idempotency.
Do not run `docker compose down -v` or prune production volumes.

Retain the previous fork revision, immutable image refs and env files securely.
Only reuse previous images if they are compatible with the current OW/Svix schemas;
restore code/config via the controlled deployment, never downgrade schemas blindly.
A recovery or rollback exercise is still required before live health ingestion.

Workers/API can later move to Container Apps using the same images, existing
PostgreSQL and domain. Update service URLs/Redis routing, private connectivity,
secrets, capacity and health checks. Move singleton beat with stop-before-start.
Redis on the VM remains a VM dependency until its state/queue are migrated or its
recovery requirements are met. Never expose its current plaintext Docker-network
connection on a public network to enable extraction; configure private routing and
TLS as needed. Prefer extraction based on observed queue/CPU pressure or uptime
requirements rather than container count alone.

## Verification boundaries

Test decision: no new product unit suites; one scoped workflow protects the new
production configuration refusal boundary (three cases) and PostgreSQL 16 migration
compatibility. Builds and database checks run serially, with a 40-minute job limit.

Scoped CI validates production configuration using disposable generated values,
builds upstream images sequentially and applies OW migrations/initialization to
isolated PostgreSQL 16. This is not an Azure network, Svix TLS, device SDK or production
backup/restore check. Record actual CI results and deployment evidence separately.

References: [Azure PostgreSQL TLS](https://learn.microsoft.com/en-us/azure/postgresql/security/security-tls-how-to-connect),
[private networking](https://learn.microsoft.com/en-gb/azure/postgresql/network/concepts-networking-private),
[ACR CLI](https://learn.microsoft.com/en-us/cli/azure/acr),
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).


## Provisioned infrastructure (2026-10-14)

- Resource group: `rg-rvf-prod-neu-wearables`.
- VM: `vm-rvf-wearables-prod-neu`, Ubuntu 24.04, 2 vCPU / 8 GiB, Trusted Launch.
- Static public IPv4: `20.234.120.170`; private IPv4: `10.10.3.4`.
- New nondelegated subnet: `10.10.3.0/26` in the existing data VNet.
- OS disk: 64 GiB; durable Docker data disk: 128 GiB, Standard SSD LRS.
- Only TCP 80/443 are allowed from the Internet. SSH requires private-network access.
- System-assigned identity has ACR pull and read access to seven dedicated OW
  Key Vault entries (six credentials and the selected administrator/contact email). Temporary read access to Hero's administrator connection
  is removed after database preparation; no Hero credential is copied into OW config.
- Backend/frontend ACR publication and VM identity-based digest pulls are verified.
- IONOS `A wearables → 20.234.120.170`, TTL 300, verified through Google/Cloudflare.
  Public HTTPS and HTTP redirect verified; certificate expires 2027-01-12.
- PostgreSQL private DNS, verified TLS, scoped logins and reciprocal database
  isolation are verified. The server initially had 50 slots and rejected normal
  connections during a shared usage peak. With explicit user approval,
  max_connections was increased to 100 and the server restarted; SQL confirms
  the active value and Azure reports Ready without pending changes. Hero's public
  GraphQL endpoint also responded successfully after restart. Monitor actual pool
  usage during OW activation; this does not replace workload/backup acceptance.
- All seven service containers are running; API/Redis/Svix health checks pass.
  Worker ping and the four selected queues verified; one beat scheduler runs.
  OW migrations are at a7c3e9f1b2d4. Administrator login and protected endpoint
  rejection verified; private portal serves the public API runtime URL correctly.
  Real device ingestion and Hero config have not been activated. Production
  monitoring and backup/restore acceptance remain pending.

VM host bootstrap is captured in cloud-init.yml. Infrastructure/image metadata is
captured in infrastructure-record.json; neither file contains credentials. Failed v5
allocation attempts created no VMs; the final v6 VM is the only new compute instance.


## Activation fixes and evidence

The pinned Svix image uses a default command rather than an entrypoint; the Compose
service explicitly sets entrypoint=["svix-server"] before --run-migrations. This
was verified by actual container startup, Svix migrations and its health check.
Azure kept public schema ownership with azure_pg_admin on both new databases; the
owning roles needed explicit USAGE/CREATE grants before migration. No grants were
made on Hero schemas. Temporary administrator-secret read access was removed after
the grants, and no administrator password is in the OW runtime files.

Acceptance measured on 2026-10-14: public root HTTP200 / expected JSON, HTTP-to-HTTPS
redirect, valid certificate/hostname, anonymous administrator endpoint HTTP401,
seeded administrator login, loopback portal HTML/runtime URL, Celery ping/queues
and committed Alembic head. These checks do not attest real SDK ingestion, provider
OAuth credentials or signed delivery into Hero. No Hero release pipeline, app-store
submission or dev/main merge was performed as part of service activation.

The administrator/contact email is stored in OW-ADMIN-EMAIL and the bootstrap
password in OW-ADMIN-PASSWORD in the existing Key Vault. Use the private-network
SSH tunnel described above for portal access; no public SSH or portal port opened.
Changing the portal password does not automatically rotate the bootstrap Key Vault
value, and editing ADMIN_PASSWORD does not reset an existing developer account.
