import { afterEach, describe, expect, it, vi } from "vitest";

import apiProxy, {
  cloudRunRequest,
  type WorkerEnvironment,
} from "../worker/api-proxy";

const environment: WorkerEnvironment = {
  CLOUD_RUN_ORIGIN: "https://flo-api-example-uc.a.run.app",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Cloudflare API proxy", () => {
  it("strips /api while preserving the request and query", async () => {
    const request = new Request(
      "https://xlr8flo.summello.com/api/healthz?probe=edge",
      {
        method: "POST",
        headers: { "X-Correlation-ID": "test-correlation" },
        body: "probe",
      },
    );

    const upstream = cloudRunRequest(request, environment);

    expect(upstream.url).toBe(
      "https://flo-api-example-uc.a.run.app/healthz?probe=edge",
    );
    expect(upstream.method).toBe("POST");
    expect(upstream.headers.get("x-correlation-id")).toBe("test-correlation");
    await expect(upstream.text()).resolves.toBe("probe");
  });

  it("returns the upstream response without adding CORS headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiProxy.fetch(
      new Request("https://xlr8flo.summello.com/api/healthz"),
      environment,
    );

    expect(response.status).toBe(200);
    expect(response.headers.has("access-control-allow-origin")).toBe(false);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each([
    "https://xlr8flo.summello.com/",
    "https://xlr8flo.summello.com/apiary",
  ])("fails closed outside the configured /api route: %s", async (url) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiProxy.fetch(new Request(url), environment);

    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "http://flo-api-example-uc.a.run.app",
    "https://example.com",
    "https://flo-api-example-uc.a.run.app/unexpected-path",
  ])("fails closed for an invalid Cloud Run origin: %s", async (origin) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiProxy.fetch(
      new Request("https://xlr8flo.summello.com/api/healthz"),
      { CLOUD_RUN_ORIGIN: origin },
    );

    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
