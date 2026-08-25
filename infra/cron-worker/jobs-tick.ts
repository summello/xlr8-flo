const ORIGIN_SECRET_HEADER = "X-FLO-Origin-Secret";

export interface JobsTickEnvironment {
  CLOUD_RUN_ORIGIN: string;
  ORIGIN_SHARED_SECRET: string;
}

export function jobsTickRequest(
  origin: URL,
  environment: JobsTickEnvironment,
): Request {
  return new Request(new URL("/internal/jobs/tick", origin), {
    method: "POST",
    headers: { [ORIGIN_SECRET_HEADER]: environment.ORIGIN_SHARED_SECRET },
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
