import { vi } from "vitest";

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;

/** Mocked backend: routes by "METHOD /path". No network, no real data. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: { key: string; body: unknown }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api/, "");
    const key = `${init?.method ?? "GET"} ${path}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const h = routes[key] ?? Object.entries(routes).find(([k]) => k.endsWith("*") && key.startsWith(k.slice(0, -1)))?.[1];
    if (!h) return new Response(JSON.stringify({ error: { code: "http_error" } }), { status: 404 });
    const { status = 200, body } = await h(path, init);
    return new Response(JSON.stringify(body ?? {}), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { calls, count: (key: string) => calls.filter((c) => c.key === key).length };
}

export const result = (over: Record<string, unknown> = {}) => ({
  rank: 1, resource_type: "DocumentReference", resource_id: "doc-1", fhir_reference: "DocumentReference/doc-1",
  link: "/patients/p1/fhir", patient_id: "p1", record_date: "2024-01-02", score: 0.56,
  snippet: "Chest tightness on exertion.", source_record_id: "doc-1", ai_generated: false, ...over,
});

export const searchBody = (results: unknown[], kind = "semantic") => ({
  query: "chest", results, returned: results.length, candidates_considered: results.length, stale_results_dropped: 0,
  took_ms: 1, embedding: { model_identifier: "m", kind }, note: "n",
});

export const display = (id = "p1", name: string | null = "Alex Test", mrn = "000101") =>
  ({ patient_id: id, patient_name: name, mrn });

export const documentResource = {
  resourceType: "DocumentReference", id: "doc-1",
  context: { period: { start: "2024-01-02" }, related: [{ reference: "urn:uuid:dr-1", type: "DiagnosticReport" }] },
  content: [{ attachment: { contentType: "text/plain", data: btoa("Full note: chest tightness on exertion.") } }],
};

export const summaryBody = {
  summary: {
    patient_id: "p1", chief_concern: { text: "Chest tightness", evidence_quote: "chest tightness", source_record_ids: ["doc-1"] },
    key_diagnoses: [], anomalies: [], missing_information: ["No medications recorded"], summary_text: "Summary of the record.",
    source_record_ids: ["doc-1"], confidence_or_quality_indicator: { level: "medium", explanation: "Heuristic only." },
    generated_at: "2024-02-01T00:00:00Z", model_identifier: "gpt-test", disclaimer: "AI-generated. Requires clinician review.",
  },
  summary_id: "s1", is_stale: false, stale_reasons: [],
};
