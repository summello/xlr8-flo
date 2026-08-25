const API_PREFIX = "/api";
const ORIGIN_SECRET_HEADER = "X-FLO-Origin-Secret";

export interface WorkerEnvironment {
  CLOUD_RUN_ORIGIN: string;
  ORIGIN_SHARED_SECRET: string;
}

interface WorkerExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
}

function originSharedSecret(environment: WorkerEnvironment): string {
  if (environment.ORIGIN_SHARED_SECRET.length < 32) {
    throw new TypeError("ORIGIN_SHARED_SECRET must be configured");
  }
  return environment.ORIGIN_SHARED_SECRET;
}

function parseCloudRunOrigin(value: string): URL {
  const origin = new URL(value);
  const isOriginOnly =
    origin.pathname === "/" && origin.search === "" && origin.hash === "";
  if (
    origin.protocol !== "https:" ||
    !origin.hostname.endsWith(".run.app") ||
    origin.username !== "" ||
    origin.password !== "" ||
    !isOriginOnly
  ) {
    throw new TypeError("CLOUD_RUN_ORIGIN must be an HTTPS Cloud Run origin");
  }
  return origin;
}

export function cloudRunRequest(request: Request, environment: WorkerEnvironment): Request {
  const incoming = new URL(request.url);
  if (incoming.pathname !== API_PREFIX && !incoming.pathname.startsWith(`${API_PREFIX}/`)) {
    throw new TypeError("request is outside the API route");
  }

  const origin = parseCloudRunOrigin(environment.CLOUD_RUN_ORIGIN);
  incoming.protocol = origin.protocol;
  incoming.host = origin.host;
  incoming.pathname = incoming.pathname.slice(API_PREFIX.length) || "/";
  const upstream = new Request(new Request(incoming, request), { redirect: "manual" });
  upstream.headers.set(ORIGIN_SECRET_HEADER, originSharedSecret(environment));
  return upstream;
}

export function quotaRequest(environment: WorkerEnvironment): Request {
  const origin = parseCloudRunOrigin(environment.CLOUD_RUN_ORIGIN);
  return new Request(new URL("/internal/health/quota", origin), {
    headers: { [ORIGIN_SECRET_HEADER]: originSharedSecret(environment) },
    redirect: "manual",
  });
}

async function checkQuota(environment: WorkerEnvironment): Promise<void> {
  const response = await fetch(quotaRequest(environment));
  if (!response.ok) {
    throw new Error(`quota health check failed with HTTP ${response.status}`);
  }
}

export default {
  async fetch(request: Request, environment: WorkerEnvironment): Promise<Response> {
    try {
      return await fetch(cloudRunRequest(request, environment));
    } catch (error) {
      if (error instanceof TypeError) {
        return new Response("Service unavailable", { status: 503 });
      }
      throw error;
    }
  },
  scheduled(
    _controller: unknown,
    environment: WorkerEnvironment,
    context: WorkerExecutionContext,
  ): void {
    context.waitUntil(checkQuota(environment));
  },
};
