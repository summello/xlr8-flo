# Production environment setup

This runbook provisions the production contract used by `.github/workflows/ci.yml`: one GCP
project, Workload Identity Federation (WIF), Neon PostgreSQL 17 through its pooled endpoint,
private Cloudflare R2 storage, one Secret Manager secret per credential, Artifact Registry,
Cloud Run, Cloudflare Pages, and the single public origin `xlr8flo.summello.com`.

Run these steps from an operator workstation, never from a story worktree. Commands use placeholders
and resource identifiers only. Secret values are read directly from the macOS keychain into the
receiving provider; they are never placed in a file, command argument, shell variable, or terminal
output. Do not enable shell tracing while provisioning.

## 1. Record the non-secret identifiers

Choose these values once and substitute them consistently:

| Placeholder | Required value |
|---|---|
| `GCP_PROJECT_ID` | Globally unique GCP project id |
| `GCP_PROJECT_NUMBER` | Numeric project number shown after project creation |
| `GITHUB_OWNER/GITHUB_REPOSITORY` | Repository allowed to exchange GitHub OIDC tokens |
| `CLOUDFLARE_ACCOUNT_ID` | Account that owns the zone, R2, Worker, and Pages project |
| `R2_ACCOUNT_ID` | Account id used in `https://R2_ACCOUNT_ID.r2.cloudflarestorage.com` |
| `INITIAL_IMAGE_DIGEST` | A scanned `us-central1-docker.pkg.dev/...@sha256:...` API image |

The fixed resource names are:

- region `us-central1`
- Artifact Registry repository `flo`, image `flo-api`
- Cloud Run service `flo-api`, migration job `flo-migrate`
- runtime service account `flo-runtime`, deploy service account `flo-deploy`
- R2 bucket `flo-attachments`
- Pages project `flo-web`
- Secret Manager secrets `flo-database-url`, `flo-origin-shared-secret`,
  `flo-r2-access-key-id`, `flo-r2-secret-access-key`, and `flo-resend-api-key`

## 2. Create and secure the GCP project

1. In the GCP console, create `GCP_PROJECT_ID`, attach the production billing account, and set a
   budget notification. A billing account is required to activate Cloud Run even while usage stays
   inside the free tier.
2. Select the project and enable the required APIs:

```bash
gcloud config set project GCP_PROJECT_ID
gcloud services enable \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  monitoring.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  sts.googleapis.com
```

3. Create the Docker repository and the two service accounts:

```bash
gcloud artifacts repositories create flo \
  --repository-format=docker \
  --location=us-central1 \
  --description='XLR8 FLO production images'

gcloud iam service-accounts create flo-runtime \
  --display-name='XLR8 FLO Cloud Run runtime'
gcloud iam service-accounts create flo-deploy \
  --display-name='XLR8 FLO GitHub deployer'
```

4. Grant the runtime identity only the non-secret read roles needed by the quota collectors:

```bash
gcloud projects add-iam-policy-binding GCP_PROJECT_ID \
  --member='serviceAccount:flo-runtime@GCP_PROJECT_ID.iam.gserviceaccount.com' \
  --role='roles/monitoring.viewer'
gcloud projects add-iam-policy-binding GCP_PROJECT_ID \
  --member='serviceAccount:flo-runtime@GCP_PROJECT_ID.iam.gserviceaccount.com' \
  --role='roles/artifactregistry.reader'
```

5. Grant the deploy identity permission to update Cloud Run and push images. Grant service-account
   use on `flo-runtime` at that account, not project-wide:

```bash
gcloud projects add-iam-policy-binding GCP_PROJECT_ID \
  --member='serviceAccount:flo-deploy@GCP_PROJECT_ID.iam.gserviceaccount.com' \
  --role='roles/run.admin'
gcloud projects add-iam-policy-binding GCP_PROJECT_ID \
  --member='serviceAccount:flo-deploy@GCP_PROJECT_ID.iam.gserviceaccount.com' \
  --role='roles/artifactregistry.writer'
gcloud iam service-accounts add-iam-policy-binding \
  flo-runtime@GCP_PROJECT_ID.iam.gserviceaccount.com \
  --member='serviceAccount:flo-deploy@GCP_PROJECT_ID.iam.gserviceaccount.com' \
  --role='roles/iam.serviceAccountUser'
```

No service-account JSON key is created or downloaded.

## 3. Configure GitHub Workload Identity Federation

1. Create a dedicated pool and GitHub OIDC provider. The repository condition is mandatory; it
   prevents an unrelated repository from presenting a valid GitHub token to this provider.

```bash
gcloud iam workload-identity-pools create github \
  --location=global \
  --display-name='GitHub Actions'

gcloud iam workload-identity-pools providers create-oidc github \
  --location=global \
  --workload-identity-pool=github \
  --display-name='GitHub repository provider' \
  --issuer-uri='https://token.actions.githubusercontent.com' \
  --attribute-mapping='google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref' \
  --attribute-condition="assertion.repository == 'GITHUB_OWNER/GITHUB_REPOSITORY'"
```

2. Allow only that repository principal set to impersonate `flo-deploy`:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  flo-deploy@GCP_PROJECT_ID.iam.gserviceaccount.com \
  --role='roles/iam.workloadIdentityUser' \
  --member='principalSet://iam.googleapis.com/projects/GCP_PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/GITHUB_OWNER/GITHUB_REPOSITORY'
```

3. Record the provider resource name without exposing a credential:

```bash
gcloud iam workload-identity-pools providers describe github \
  --location=global \
  --workload-identity-pool=github \
  --format='value(name)'
```

## 4. Create Neon and capture only the pooled URL

1. In the Neon console, create the production project on PostgreSQL 17 in the region closest to
   `us-central1`. Create the production database and a least-privilege application role.
2. Open **Connect**, select the application role and database, enable **Pooled connection**, and
   require TLS. The hostname must contain `-pooler.` and the URL must include `sslmode=require`.
   A hostname without `-pooler` is the session endpoint and is forbidden: the API rejects it at
   startup. Transaction pooling is compatible with tenant RLS because the application uses
   `SET LOCAL`; session-scoped `SET` is blocked by CI.
3. Store the complete pooled URL in the macOS keychain without printing it:

```bash
security add-generic-password -U -a "$USER" -s FLO_PROD_DATABASE_URL -w
```

Paste the value only at the hidden keychain prompt.

## 5. Create the private R2 bucket and scoped token

1. In Cloudflare **R2 Object Storage**, create bucket `flo-attachments` in the automatic location.
2. Keep public development URLs disabled, attach no public custom domain, and confirm the bucket
   has no public access policy.
3. Create an R2 API token scoped to **Object Read & Write** on the single `flo-attachments` bucket.
   Do not grant account-wide bucket administration.
4. At the one-time credential display, place each value in its own keychain item:

```bash
security add-generic-password -U -a "$USER" -s FLO_PROD_R2_ACCESS_KEY_ID -w
security add-generic-password -U -a "$USER" -s FLO_PROD_R2_SECRET_ACCESS_KEY -w
```

5. Record the non-secret S3 endpoint as
   `https://R2_ACCOUNT_ID.r2.cloudflarestorage.com`. The runtime uses region `auto` and bucket
   `flo-attachments`.
6. Create a random origin-authentication value of at least 32 bytes in a password manager, then
   paste it only at this hidden keychain prompt:

```bash
security add-generic-password -U -a "$USER" -s FLO_PROD_ORIGIN_SHARED_SECRET -w
```

   This value is shared only by Cloud Run and the Cloudflare Worker. It is never sent to a browser.

7. Create a Resend API key restricted to sending mail for the production account and place it in
   the keychain without printing it. The checked-in runtime configuration selects Resend; local
   development selects SMTP and Mailpit instead.

```bash
security add-generic-password -U -a "$USER" -s FLO_PROD_RESEND_API_KEY -w
```

## 6. Create one Secret Manager secret per value

Create exactly five secret resources, then pipe each keychain value directly into a new version:

```bash
gcloud secrets create flo-database-url --replication-policy=automatic
gcloud secrets create flo-origin-shared-secret --replication-policy=automatic
gcloud secrets create flo-r2-access-key-id --replication-policy=automatic
gcloud secrets create flo-r2-secret-access-key --replication-policy=automatic
gcloud secrets create flo-resend-api-key --replication-policy=automatic

security find-generic-password -a "$USER" -s FLO_PROD_DATABASE_URL -w \
  | gcloud secrets versions add flo-database-url --data-file=-
security find-generic-password -a "$USER" -s FLO_PROD_ORIGIN_SHARED_SECRET -w \
  | gcloud secrets versions add flo-origin-shared-secret --data-file=-
security find-generic-password -a "$USER" -s FLO_PROD_R2_ACCESS_KEY_ID -w \
  | gcloud secrets versions add flo-r2-access-key-id --data-file=-
security find-generic-password -a "$USER" -s FLO_PROD_R2_SECRET_ACCESS_KEY -w \
  | gcloud secrets versions add flo-r2-secret-access-key --data-file=-
security find-generic-password -a "$USER" -s FLO_PROD_RESEND_API_KEY -w \
  | gcloud secrets versions add flo-resend-api-key --data-file=-
```

Grant `flo-runtime` access on each secret resource, not all secrets in the project:

```bash
for secret_name in flo-database-url flo-origin-shared-secret flo-r2-access-key-id flo-r2-secret-access-key flo-resend-api-key; do
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member='serviceAccount:flo-runtime@GCP_PROJECT_ID.iam.gserviceaccount.com' \
    --role='roles/secretmanager.secretAccessor'
done
```

The application reads process environment injected by Cloud Run. It has no secrets-file setting
and never calls Secret Manager directly. Rotation of the origin secret must update both the GCP
secret version and the Cloudflare Worker secret binding before the old Cloud Run revision is
retired. Never edit an ordinary config file.

## 7. Bootstrap the Cloud Run migration job

The deploy workflow updates this job to the newly scanned image digest before every execution. It
must exist before the first `main` deploy. `INITIAL_IMAGE_DIGEST` must be an immutable, scanned
application image containing the migration command delivered by the deployment story.

```bash
gcloud run jobs create flo-migrate \
  --region=us-central1 \
  --image=INITIAL_IMAGE_DIGEST \
  --service-account=flo-runtime@GCP_PROJECT_ID.iam.gserviceaccount.com \
  --set-secrets=DATABASE_URL=flo-database-url:latest \
  --command=alembic \
  --args=upgrade,head \
  --max-retries=0 \
  --task-timeout=10m
```

Describe the job and confirm the image contains `@sha256:`, the runtime account is `flo-runtime`,
and `DATABASE_URL` is a `secretKeyRef`; no secret value should appear:

```bash
gcloud run jobs describe flo-migrate --region=us-central1 --format=yaml
```

## 8. Configure GitHub deployment settings

In **Repository settings → Secrets and variables → Actions**, create only these repository secrets:

| Secret | Value |
|---|---|
| `GCP_WIF_PROVIDER` | Full WIF provider resource name from step 3 |
| `GCP_DEPLOY_SA` | `flo-deploy@GCP_PROJECT_ID.iam.gserviceaccount.com` |
| `GCP_PROJECT` | `GCP_PROJECT_ID` |
| `CLOUDFLARE_API_TOKEN` | Token scoped to Workers scripts/routes and the `flo-web` Pages project |
| `CLOUDFLARE_ACCOUNT_ID` | Owning Cloudflare account id |

Create these repository variables:

| Variable | Value |
|---|---|
| `R2_ENDPOINT_URL` | `https://R2_ACCOUNT_ID.r2.cloudflarestorage.com` |
| `CLOUD_RUN_ORIGIN` | The `https://...run.app` origin after the first service creation |

The GCP values are identifiers, not credentials. A `credentials_json` input or service-account key
is not part of this deployment. Do not add either one, the Neon URL, or either R2 key to GitHub.

## 9. Create Cloudflare Pages, Worker access, and DNS

1. In Cloudflare Pages, create project `flo-web`. Production assets are uploaded by Wrangler; do
   not connect a second automatic Git build.
2. Create a Cloudflare API token limited to this account with Pages edit, Workers Scripts edit,
   Workers Routes edit, and Zone read. Store it only in the GitHub secret from step 8.
3. Attach `xlr8flo.summello.com` as the `flo-web` custom domain. If Cloudflare does not create it
   automatically, add a proxied CNAME record named `xlr8flo` targeting `flo-web.pages.dev`.
4. The checked-in `infra/cloudflare/wrangler.toml` deploys the more-specific
   `xlr8flo.summello.com/api/*` Worker route. All unmatched paths continue to Pages. `workers_dev`
   stays disabled; do not create a second public API hostname.
5. From a clean checkout with `apps/web` dependencies installed, copy the same origin secret into
   the Worker's encrypted `ORIGIN_SHARED_SECRET` binding without printing it or writing a file:

```bash
security find-generic-password -a "$USER" -s FLO_PROD_ORIGIN_SHARED_SECRET -w \
  | npx wrangler secret put ORIGIN_SHARED_SECRET --config infra/cloudflare/wrangler.toml
```

   `wrangler.toml` declares this binding as required, so later deploys fail closed if it is absent.
   Wrangler preserves encrypted secret bindings across ordinary code deploys. The Worker overwrites
   any caller-supplied `X-FLO-Origin-Secret` header on every proxied request.
6. The same Worker has a one-minute Cron Trigger and calls `/internal/health/quota` followed by
   `POST /internal/jobs/tick` from its scheduled handler with the encrypted binding. The tick is
   bounded to 50 seconds and drains the Postgres `SKIP LOCKED` queue and transactional outbox; no
   daemon or Redis service exists. This is the scheduler selected by D-08; do not create a second
   Google Cloud Scheduler job. Confirm **Workers & Pages → flo-api-proxy →
   Triggers** shows `* * * * *` and **Settings → Variables and Secrets** shows
   `ORIGIN_SHARED_SECRET` as encrypted.
7. In **SSL/TLS**, select **Full (strict)**. Enable **Always Use HTTPS**. After the Pages certificate
   is active and HTTPS has been stable, enable HSTS with a conservative max-age; include subdomains
   only when every subdomain is HTTPS-ready.

Cloud Run ingress remains `all` because a Cloudflare Worker is not a Google load balancer. The
shared-secret dependency returns the same HTTP 404 for every request to the default `run.app` URL
that lacks the Worker header, closing that bypass without a paid load balancer. Do not switch to
`internal-and-cloud-load-balancing`; it would break every `/api/*` request.

## 10. First release and service configuration

Push to `main` only through the milestone pull request. The deploy job authenticates through WIF,
scans the local image, pushes it, resolves its digest, updates and executes `flo-migrate`, and then
deploys `flo-api` with:

- 1 GiB memory, 2 CPUs, concurrency 80, minimum instances 0
- runtime identity `flo-runtime`
- five Secret Manager references, never literal secret environment values
- R2 endpoint/bucket/region and GCP resource identifiers as ordinary non-secret variables
- immutable `@sha256:` image reference

If this is the first service creation, copy its HTTPS `run.app` origin into the GitHub repository
variable `CLOUD_RUN_ORIGIN`, then re-run the failed deploy job. Do not put that hostname in a
`VITE_` variable or browser bundle.

The fixed release order is:

1. Scan the built image and generate its SBOM.
2. Push it and resolve the immutable digest.
3. Update and execute `flo-migrate --wait`.
4. Deploy `flo-api` only after the migration exits zero.
5. Run `prune_registry.py`, which deletes older versions, lists again, and fails unless the expected
   three newest available digests remain.
6. Deploy the path-scoped Worker and Pages assets.

## 11. Verify Neon, R2, quotas, registry, and secret handling

Run these checks from the operator workstation after a successful deployment.

### Immutable image and pooled Neon

```bash
gcloud run services describe flo-api --region=us-central1 \
  --format='value(spec.template.spec.containers[0].image)'
curl --fail --silent --show-error https://xlr8flo.summello.com/api/readyz
```

The image must contain `@sha256:`. Readiness must return HTTP 200 with `database` and `storage`
reported as `ok`. In Neon **Monitoring → Connections**, verify application connections use the
pooled endpoint. The application config guard independently rejects a Neon hostname without
`-pooler`.

### R2 round-trip through the Storage port

Create a one-off job using the serving digest and the same secret references. It runs
`python -m flo.kernel.storage`, which writes random bytes through the `Storage` port, reads and
compares them, and deletes the object in a `finally` block. It prints no SDK exception details.

```bash
gcloud run jobs create flo-storage-smoke \
  --region=us-central1 \
  --image="$(gcloud run services describe flo-api --region=us-central1 --format='value(spec.template.spec.containers[0].image)')" \
  --service-account=flo-runtime@GCP_PROJECT_ID.iam.gserviceaccount.com \
  --set-secrets=S3_ACCESS_KEY_ID=flo-r2-access-key-id:latest,S3_SECRET_ACCESS_KEY=flo-r2-secret-access-key:latest \
  --set-env-vars="S3_ENDPOINT_URL=https://R2_ACCOUNT_ID.r2.cloudflarestorage.com,S3_BUCKET=flo-attachments,S3_REGION=auto" \
  --command=python \
  --args=-m,flo.kernel.storage \
  --max-retries=0
gcloud run jobs execute flo-storage-smoke --region=us-central1 --wait
gcloud run jobs delete flo-storage-smoke --region=us-central1 --quiet
```

The execution must print `storage round-trip passed and the smoke object was deleted`. Confirm the
R2 bucket has no `operator-smoke/` object afterward.

### Quota document and alert actions

```bash
curl --fail --silent --show-error \
  https://xlr8flo.summello.com/api/internal/health/quota
```

The document must contain exactly `neon_storage`, `cloud_run_requests`, `r2_storage`, and
`artifact_registry`, each with `current`, `limit`, `unit`, and `percentage`. A crossed policy also
appears in `alerts` and emits a structured warning containing its pre-agreed action and cost:

| Metric | Alerts | Action | Cost |
|---|---:|---|---:|
| Neon storage, 512 MB | 60 / 80 / 90 % | Archive audit partitions to R2 first; then Neon Launch | $19/mo |
| Cloud Run, 2M requests/month | 60 / 80 / 90 % | Still free; set `min-instances=1` for latency | ~$8–15/mo |
| R2 storage, 10 GB | 60 / 80 / 90 % | Pay-as-you-go | ~$0.15/mo per 10 GB |
| Artifact Registry, 0.5 GB | 80 % | Prune to 3 images (automate first) | ~$0 |

The Cloudflare Cron Trigger calls this endpoint every minute through the scheduled Worker handler.
The edge Worker adds the same encrypted header for manual and scheduled calls; the value never
appears in this command, the response, or Worker logs.

### Registry pruning after four deploys

After four successful deploys, list the versions:

```bash
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/GCP_PROJECT_ID/flo/flo-api \
  --sort-by='~UPDATE_TIME' \
  --format='table(version,updateTime,imageSizeBytes)'
```

Exactly three digests must remain. The deploy job itself verifies the same condition after every
delete; a successful delete command that leaves an image behind makes the job fail.

### Secret non-disclosure

```bash
gcloud run services describe flo-api --region=us-central1 --format=yaml
gcloud run jobs describe flo-migrate --region=us-central1 --format=yaml
```

The documents may show secret resource names and versions, but never values. Search the deployment
logs and API error bodies for neither the Neon hostname/user nor either R2 credential. Do not run
`env`, `printenv`, shell tracing, SDK wire logging, or a command that renders a secret version. API
collector failures return a generic RFC 9457 503 and log only the failed metric name.

Resolve the non-secret `run.app` URL and prove direct callers cannot discover either the public or
internal API paths. Both calls must return HTTP 404 and must not open a Neon connection; the edge
request with the Worker's correct binding must still return HTTP 200:

```bash
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  "$(gcloud run services describe flo-api --region=us-central1 --format='value(status.url)')/healthz"
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -H 'X-FLO-Origin-Secret: deliberately-wrong' \
  "$(gcloud run services describe flo-api --region=us-central1 --format='value(status.url)')/internal/health/quota"
curl --fail --silent --show-error \
  https://xlr8flo.summello.com/api/internal/health/quota
```

## 12. Edge verification

From outside the Cloudflare and GCP networks:

```bash
curl --head http://xlr8flo.summello.com
curl --head https://xlr8flo.summello.com
curl --fail --silent --show-error https://xlr8flo.summello.com/api/healthz
```

The HTTP request must redirect to HTTPS, the HTTPS response must include
`Strict-Transport-Security`, and the API request must return the Cloud Run health document through
the same origin. Same-origin responses must not contain CORS headers.

## 13. Rollback

### API traffic

List revisions and identify the revision immediately before the current one:

```bash
gcloud run revisions list --service=flo-api --region=us-central1 \
  --sort-by='~metadata.creationTimestamp' \
  --format='table(metadata.name,status.conditions[0].status,metadata.creationTimestamp)'
```

Move all traffic to that revision:

```bash
gcloud run services update-traffic flo-api --region=us-central1 \
  --to-revisions=PREVIOUS_REVISION=100
```

Confirm `/readyz`, `/internal/health/quota`, and the application smoke path. Traffic rollback does
not reverse a database migration, so every production migration must remain backward-compatible
with the previously serving revision.

### Pages and Worker

In Cloudflare **Workers & Pages → flo-web → Deployments**, select the previous successful production
deployment and choose **Rollback to this deployment**. If the Worker caused the incident, redeploy
the last known-good repository commit with its checked-in `infra/cloudflare/wrangler.toml`; do not
edit the route ad hoc in the dashboard. Re-run all three edge checks after rollback.

### Failed migration

The workflow uses `gcloud run jobs execute flo-migrate --wait` before `gcloud run deploy`. A
non-zero migration therefore skips the new service revision and leaves existing traffic unchanged.
Inspect the job execution, correct the migration in a new commit, and redeploy. Never bypass the job
or manually point traffic at the unserved image.
