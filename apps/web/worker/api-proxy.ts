const API_PREFIX = "/api";

export interface WorkerEnvironment {
  CLOUD_RUN_ORIGIN: string;
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
  return new Request(incoming, request);
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
};
