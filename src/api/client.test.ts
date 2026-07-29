import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiEnabled,
  fetchCase,
  fetchCaseById,
  isInvalidMfaCode,
  postCaseClaim,
  REQUEST_TIMEOUT_MS,
} from "./client";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("api base URL", () => {
  it("is disabled when VITE_API_URL is unset", () => {
    expect(apiEnabled()).toBe(false);
  });

  it("targets an absolute base with credentials", async () => {
    vi.stubEnv("VITE_API_URL", "http://host:8100/");
    expect(apiEnabled()).toBe(true);
    await fetchCase();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://host:8100/api/case");
    expect(init.credentials).toBe("include");
  });

  it("treats VITE_API_URL=/ as same-origin relative requests", async () => {
    vi.stubEnv("VITE_API_URL", "/");
    expect(apiEnabled()).toBe(true);
    await fetchCase();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/case");
  });

  it("targets explicit case routes", async () => {
    vi.stubEnv("VITE_API_URL", "/");
    await fetchCaseById("case 3407");
    await postCaseClaim("case 3407", {
      sourceDocumentId: "src-fdr",
      text: "Claim text",
      pageRef: "p. 1",
      quote: "Quote text",
      confidence: 0.8,
      evidenceQuality: "primary",
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/cases/case%203407");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/cases/case%203407/claims");
  });

  it("aborts a request that never settles", async () => {
    vi.useFakeTimers();
    vi.stubEnv("VITE_API_URL", "/");
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        }),
    );

    const timedOut = expect(fetchCase()).rejects.toMatchObject({
      message: "Case service request timed out — try again.",
      status: null,
    });
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
    await timedOut;
  });
});

describe("API error classification", () => {
  it("recognizes current and legacy invalid-MFA responses without swallowing lockouts", () => {
    expect(isInvalidMfaCode(new ApiError("Invalid MFA code.", 422))).toBe(true);
    expect(isInvalidMfaCode(new ApiError("Invalid MFA code.", 401))).toBe(true);
    expect(
      isInvalidMfaCode(new ApiError("Too many invalid MFA codes — try again shortly.", 429)),
    ).toBe(false);
  });
});
