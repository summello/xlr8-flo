# Production deployment setup

The `deploy` job in `.github/workflows/ci.yml` releases `main` after every required CI job
passes. It builds the API image locally, blocks on the container scan, attaches an image SBOM,
pushes the scanned image, runs the migration job, deploys the API by digest, scans the SPA bundle,
and publishes it to Cloudflare Pages. Milestone branches build and test but never deploy.

Provisioning Neon, R2, Secret Manager, Artifact Registry, the Cloud Run migration job, and service
accounts belongs to E01-S06. This runbook records the deployment contract those resources must
satisfy.

## GitHub deployment configuration

Configure GitHub's GCP trust with Workload Identity Federation. Do not create or download a service
account key. The deploy job accepts only these GCP secrets:

- `GCP_WIF_PROVIDER`: full Workload Identity Provider resource name.
- `GCP_DEPLOY_SA`: deploy service account email trusted by that provider.
- `GCP_PROJECT`: GCP project id.

Configure these Cloudflare secrets:

- `CLOUDFLARE_API_TOKEN`: scoped to edit the `flo-web` Pages project.
- `CLOUDFLARE_ACCOUNT_ID`: account that owns the Pages project.

Set the GitHub repository variable `VITE_API_BASE_URL` to the public HTTPS API origin. This value is
public application configuration; no credential belongs in a `VITE_` variable because Vite embeds
it in browser assets. The deploy job rejects assets containing `sk-`, `-----BEGIN`, or the GCP
project id before Wrangler can publish them.

The GCP GitHub secrets contain resource identifiers, not credentials. The GitHub OIDC token is
short-lived and exchanged through WIF. A `credentials_json` input or service-account JSON secret is
not part of this deployment.

## Cloud Run contract

The `flo-api` service is deployed in `us-central1` with 1 GiB memory, 2 CPUs, concurrency 80, zero
minimum instances, and `DATABASE_URL` sourced from `flo-database-url:latest` in Secret Manager. The
workflow's resource check derives the Argon2 memory requirement from application defaults and fails
if these flags drift below it.

The image is pushed only after a blocking CRITICAL/HIGH Trivy scan. The deployment input is the
registry result in the form
`us-central1-docker.pkg.dev/PROJECT/flo/flo-api@sha256:DIGEST`; a mutable tag is never passed to
Cloud Run. The registry cleanup retains the three newest image versions to stay inside the 0.5 GB
free tier.

The deploy order is intentionally fixed:

1. Execute `flo-migrate` with `--wait`.
2. Deploy the new `flo-api` revision by digest only after the job exits successfully.

To verify the failure path, point a temporary branch of the migration job at a migration that exits
non-zero and run the workflow manually in an isolated GCP test project. The `Deploy API by immutable
digest` step must be skipped and the revision receiving traffic before the run must remain at 100%.
Revert the planted migration immediately and link both workflow runs in the milestone PR.

After a successful deployment, verify the immutable image and readiness:

```bash
gcloud run services describe flo-api --region us-central1 \
  --format='value(spec.template.spec.containers[0].image)'
curl --fail --silent --show-error https://API_DOMAIN/readyz
```

The first command must return an `@sha256:` reference. Readiness must return HTTP 200 with Neon and
R2 checks reported as `ok`.

## Cloudflare TLS

Attach the registered application domain to the `flo-web` Pages project and proxy the API hostname
through Cloudflare. In **SSL/TLS**, choose **Full (strict)** so Cloudflare validates the origin
certificate. In **Edge Certificates**, enable **Always Use HTTPS** and HSTS only after the custom
domain has a valid certificate. Use a conservative initial HSTS max-age, then raise it after the
domain is stable; include subdomains only when every subdomain is HTTPS-ready.

Verify the edge behavior from outside the Cloudflare and GCP networks:

```bash
curl --head http://APP_DOMAIN
curl --head https://APP_DOMAIN
```

The HTTP request must redirect to HTTPS. The HTTPS response must include
`Strict-Transport-Security`. Cloud Run's public endpoint is HTTPS-only; never expose the container's
internal cleartext port directly.

## Rollback

List revisions and identify the revision immediately before the current one:

```bash
gcloud run revisions list --service flo-api --region us-central1 \
  --sort-by='~metadata.creationTimestamp' \
  --format='table(metadata.name,status.conditions[0].status,metadata.creationTimestamp)'
```

Rollback is one traffic command against that previous revision:

```bash
gcloud run services update-traffic flo-api --region us-central1 \
  --to-revisions=PREVIOUS_REVISION=100
```

Confirm `/readyz` and the application smoke path after traffic moves. Rollback changes traffic only;
it does not reverse a database migration, so production migrations must remain backward-compatible
with the previously serving revision.
