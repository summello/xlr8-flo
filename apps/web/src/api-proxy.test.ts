import { afterEach, describe, expect, it, vi } from "vitest";

import apiProxy, {
  cloudRunRequest,
  quotaRequest,
  type WorkerEnvironment,
} from "../worker/api-proxy";
import { jobsTickRequest } from "../../../infra/cron-worker/jobs-tick";

const environment: WorkerEnvironment = {
  CLOUD_RUN_ORIGIN: "https://flo-api-example-uc.a.run.app",
  ORIGIN_SHARED_SECRET: "test-origin-shared-secret-at-least-32-bytes",
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
    expect(upstream.redirect).toBe("manual");
    expect(upstream.headers.get("x-correlation-id")).toBe("test-correlation");
    expect(upstream.headers.get("x-flo-origin-secret")).toBe(
      environment.ORIGIN_SHARED_SECRET,
    );
    await expect(upstream.text()).resolves.toBe("probe");
  });

  it("overwrites a caller-supplied origin secret", () => {
    const upstream = cloudRunRequest(
      new Request("https://xlr8flo.summello.com/api/healthz", {
        headers: { "X-FLO-Origin-Secret": "attacker-controlled" },
      }),
      environment,
    );

    expect(upstream.headers.get("x-flo-origin-secret")).toBe(
      environment.ORIGIN_SHARED_SECRET,
    );
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
    "https://xlr8flo.summello.com/api/internal/jobs/tick",
  ])("fails closed outside the configured public API surface: %s", async (url) => {
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
      { ...environment, CLOUD_RUN_ORIGIN: origin },
    );

    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when the Worker secret binding is absent", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiProxy.fetch(
      new Request("https://xlr8flo.summello.com/api/healthz"),
      { ...environment, ORIGIN_SHARED_SECRET: "" },
    );

    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("authenticates the scheduled quota and job tick calls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    let scheduled: Promise<unknown> | undefined;

    apiProxy.scheduled(null, environment, {
      waitUntil(promise) {
        scheduled = promise;
      },
    });
    await scheduled;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const quota = fetchMock.mock.calls[0]?.[0] as Request;
    const jobs = fetchMock.mock.calls[1]?.[0] as Request;
    expect(quota.url).toBe(
      "https://flo-api-example-uc.a.run.app/internal/health/quota",
    );
    expect(quota.headers.get("x-flo-origin-secret")).toBe(
      environment.ORIGIN_SHARED_SECRET,
    );
    expect(jobs.url).toBe(
      "https://flo-api-example-uc.a.run.app/internal/jobs/tick",
    );
    expect(jobs.method).toBe("POST");
    expect(jobs.headers.get("x-flo-origin-secret")).toBe(
      environment.ORIGIN_SHARED_SECRET,
    );
    expect(quotaRequest(environment).headers.get("x-flo-origin-secret")).toBe(
      environment.ORIGIN_SHARED_SECRET,
    );
    expect(
      jobsTickRequest(new URL(environment.CLOUD_RUN_ORIGIN), environment).headers.get(
        "x-flo-origin-secret",
      ),
    ).toBe(environment.ORIGIN_SHARED_SECRET);
  });

  it("still starts the job tick when the independent quota call fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    let scheduled: Promise<unknown> | undefined;

    apiProxy.scheduled(null, environment, {
      waitUntil(promise) {
        scheduled = promise;
      },
    });

    await expect(scheduled).rejects.toThrow("quota health check failed");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const jobs = fetchMock.mock.calls[1]?.[0] as Request;
    expect(jobs.url).toContain("/internal/jobs/tick");
  });
});
