const ORIGIN_SECRET_HEADER = "X-FLO-Origin-Secret";

export interface JobsTickEnvironment {
  CLOUD_RUN_ORIGIN: string;
  ORIGIN_SHARED_SECRET: string;
}

export function jobsTickRequest(
  origin: URL,
  environment: JobsTickEnvironment,
): Request {
  // The API applies double-submit CSRF to every unsafe method and requires an
  // Idempotency-Key on every state-changing POST (internal routes are not replayed, so a
  // fresh key per tick is correct). The cron is not a browser, but it takes part in the
  // same protocol with a one-request pair instead of asking the API to exempt it.
  const csrf = crypto.randomUUID();
  return new Request(new URL("/internal/jobs/tick", origin), {
    method: "POST",
    headers: {
      [ORIGIN_SECRET_HEADER]: environment.ORIGIN_SHARED_SECRET,
      "Idempotency-Key": crypto.randomUUID(),
      Cookie: `flo_csrf=${csrf}`,
      "X-CSRF-Token": csrf,
    },
    redirect: "manual",
  });
}

export async function tickJobs(
  origin: URL,
  environment: JobsTickEnvironment,
): Promise<void> {
  const response = await fetch(jobsTickRequest(origin, environment));
  if (!response.ok) {
    throw new Error(`job tick failed with HTTP ${response.status}`);
  }
}
