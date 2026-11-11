import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getDocumentReference, searchRecords } from "./client";
import { documentResource, mockApi, searchBody } from "../test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("omits empty filters from the request body", async () => {
    const api = mockApi({ "POST /search": () => ({ body: searchBody([]) }) });
    await searchRecords("q", { patient_id: "", resource_types: [], date_from: "" });
    expect(api.calls[0].body).toEqual({ query: "q" });
  });

  it("maps HTTP errors to safe messages and never exposes the server's message text", async () => {
    mockApi({ "POST /search": () => ({ status: 500, body: { error: { code: "internal_error", message: "Traceback /srv/secret" } } }) });
    const err = await searchRecords("q", {}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
    expect(err.message).not.toContain("secret");
  });

  it("tolerates a non-JSON error body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>oops</html>", { status: 502 })));
    const err = await searchRecords("q", {}).catch((e) => e);
    expect(err.code).toBe("http_error");
  });

  it("propagates an abort (used to cancel superseded searches) instead of reporting an error", async () => {
    vi.stubGlobal("fetch", vi.fn((_u: unknown, init?: RequestInit) => new Promise((_res, rej) => {
      init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    })));
    const c = new AbortController();
    const p = searchRecords("q", {}, c.signal);
    c.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });

  it("fetches one DocumentReference through the focused endpoint, never the Bundle", async () => {
    const api = mockApi({ "GET /patients/p1/fhir/resources/DocumentReference/doc-1": () => ({ body: documentResource }) });
    const d = await getDocumentReference("p1", "doc-1");
    expect(d).toEqual({ reference: "DocumentReference/doc-1", text: "Full note: chest tightness on exertion.",
      contentType: "text/plain", date: "2024-01-02", related: ["DiagnosticReport/dr-1"] });
    expect(api.calls.map((c) => c.key)).toEqual(["GET /patients/p1/fhir/resources/DocumentReference/doc-1"]);
  });

  it("returns null on fhir_resource_not_found but rethrows other errors", async () => {
    mockApi({ "GET /patients/p1/fhir/resources/DocumentReference/gone": () => ({ status: 404, body: { error: { code: "fhir_resource_not_found" } } }),
      "GET /patients/p1/fhir/resources/DocumentReference/bad": () => ({ status: 409, body: { error: { code: "fhir_not_available" } } }) });
    expect(await getDocumentReference("p1", "gone")).toBeNull();
    await expect(getDocumentReference("p1", "bad")).rejects.toMatchObject({ code: "fhir_not_available" });
  });
});
