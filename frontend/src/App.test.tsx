import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { clearPatientDisplayCache } from "./hooks/usePatientDisplay";
import { display, documentResource, mockApi, result, searchBody, summaryBody } from "./test-utils";

const status = { "GET /search/status": () => ({ body: { embedding: { model_identifier: "m", kind: "semantic", available: true } } }) };

beforeEach(() => clearPatientDisplayCache());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function submit(q = "chest") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Search query"), q);
  await user.click(screen.getByRole("button", { name: "Search" }));
  return user;
}

describe("search", () => {
  it("shows the mode indicator and submits the query", async () => {
    const api = mockApi({ ...status, "POST /search": () => ({ body: searchBody([result()]) }), "GET /patients/p1/display": () => ({ body: display() }) });
    render(<App />);
    expect(await screen.findByTestId("mode")).toHaveTextContent("semantic");
    await submit();
    expect(await screen.findByText("Chest tightness on exertion.")).toBeInTheDocument();
    expect(api.calls.find((c) => c.key === "POST /search")?.body).toEqual({ query: "chest" });
  });

  it("renders a ranked card with name, MRN, type, date, similarity and snippet, and a result count", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result(), result({ rank: 2, resource_id: "d2", patient_id: "p2", score: 0.4 })]) }),
      "GET /patients/p1/display": () => ({ body: display() }), "GET /patients/p2/display": () => ({ body: display("p2", "Sam Other", "000222") }) });
    render(<App />);
    await submit();
    expect(await screen.findByText("Alex Test")).toBeInTheDocument();
    expect(screen.getByText("Sam Other")).toBeInTheDocument();
    expect(screen.getByText(/MRN: 000101/)).toBeInTheDocument();
    expect(screen.getByText(/2 results for/)).toBeInTheDocument();
    expect(screen.getByText("0.56")).toBeInTheDocument();
    expect(screen.getAllByText("2024-01-02").length).toBe(2);
    expect(screen.getAllByText("Clinical document").length).toBe(2);
    expect(screen.getAllByText(/not clinical relevance or confidence/).length).toBeGreaterThan(0);
  });

  it("looks a patient up once even when several results share the patient", async () => {
    const api = mockApi({ ...status, "POST /search": () => ({ body: searchBody([result(), result({ rank: 2, resource_id: "d2" })]) }),
      "GET /patients/p1/display": () => ({ body: display() }) });
    render(<App />);
    await submit();
    await screen.findAllByText("Alex Test");
    expect(api.count("GET /patients/p1/display")).toBe(1);
  });

  it("keeps the result and shows a fallback when the display lookup fails", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result()]) }), "GET /patients/p1/display": () => ({ status: 500, body: { error: { code: "internal_error" } } }) });
    render(<App />);
    await submit();
    expect(await screen.findByText("Patient details unavailable")).toBeInTheDocument();
    expect(screen.getByText("Chest tightness on exertion.")).toBeInTheDocument();
  });

  it("sends patient, resource-type and date filters", async () => {
    const api = mockApi({ ...status, "POST /search": () => ({ body: searchBody([]) }) });
    render(<App />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search query"), "cough");
    await user.type(screen.getByLabelText("Patient ID"), "p9");
    await user.click(screen.getByLabelText("AI summaries"));
    await user.type(screen.getByLabelText("Date from"), "2024-01-01");
    await user.type(screen.getByLabelText("Date to"), "2024-12-31");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText(/No matching records/);
    expect(api.calls.find((c) => c.key === "POST /search")?.body).toEqual({
      query: "cough", patient_id: "p9", resource_types: ["ClinicalSummary"], date_from: "2024-01-01", date_to: "2024-12-31" });
  });

  it("'Only this patient' applies the patient filter and re-searches", async () => {
    const api = mockApi({ ...status, "POST /search": () => ({ body: searchBody([result()]) }), "GET /patients/p1/display": () => ({ body: display() }) });
    render(<App />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: /Only this patient/ }));
    await waitFor(() => expect(api.count("POST /search")).toBe(2));
    expect(screen.getByLabelText("Patient ID")).toHaveValue("p1");
    expect(api.calls.filter((c) => c.key === "POST /search")[1].body).toMatchObject({ patient_id: "p1" });
  });

  it("validates a blank query and an inverted date range without calling the API", async () => {
    const api = mockApi({ ...status });
    render(<App />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Enter a search query.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search query"), "x");
    await user.type(screen.getByLabelText("Date from"), "2024-05-01");
    await user.type(screen.getByLabelText("Date to"), "2024-01-01");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/must not be after/);
    expect(api.count("POST /search")).toBe(0);
  });

  it("shows an empty state when nothing matches", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([]) }) });
    render(<App />);
    await submit();
    expect(await screen.findByText(/No matching records/)).toBeInTheDocument();
    expect(screen.getByText(/No results for/)).toBeInTheDocument();
  });

  it("disables submit while loading and shows a loading indicator", async () => {
    let release: (v: unknown) => void = () => undefined;
    const gate = new Promise((r) => { release = r; });
    mockApi({ ...status, "POST /search": async () => { await gate; return { body: searchBody([]) }; } });
    render(<App />);
    await submit();
    expect(await screen.findByRole("button", { name: "Searching…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Searching…");
    release(null);
    expect(await screen.findByRole("button", { name: "Search" })).toBeEnabled();
  });

  it("shows a safe error with retry, without leaking server details", async () => {
    let n = 0;
    mockApi({ ...status, "POST /search": () => (++n === 1
      ? { status: 503, body: { error: { code: "embedding_provider_unavailable", message: "SECRET internal path /srv/x" } } }
      : { body: searchBody([]) }) });
    render(<App />);
    const user = await submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Semantic search is unavailable/);
    expect(alert).not.toHaveTextContent("SECRET");
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/No matching records/)).toBeInTheDocument();
  });

  it("reports a network failure safely", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("boom"); }));
    render(<App />);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server.");
  });

  it("marks lexical mode clearly", async () => {
    mockApi({ "GET /search/status": () => ({ body: { embedding: { model_identifier: "h", kind: "lexical", available: true } } }) });
    render(<App />);
    expect(await screen.findByTestId("mode")).toHaveTextContent(/lexical.*no semantic understanding/);
  });
});

describe("detail drawer", () => {
  it("opens a document with full text and FHIR references, and closes with Escape", async () => {
    const api = mockApi({ ...status, "POST /search": () => ({ body: searchBody([result()]) }), "GET /patients/p1/display": () => ({ body: display() }),
      "GET /patients/p1/fhir/resources/DocumentReference/doc-1": () => ({ body: documentResource }) });
    render(<App />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: /View details/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Full note: chest tightness on exertion\./)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "DocumentReference/doc-1" }))
      .toHaveAttribute("href", "/api/patients/p1/fhir/resources/DocumentReference/doc-1");
    expect(within(dialog).getByRole("link", { name: "DiagnosticReport/dr-1" }))
      .toHaveAttribute("href", "/api/patients/p1/fhir/resources/DiagnosticReport/dr-1");
    expect(api.calls.map((c) => c.key)).not.toContain("GET /patients/p1/fhir"); // the whole Bundle is never requested
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /View details/ })).toHaveFocus();
  });

  it("closes with the Close button", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result()]) }), "GET /patients/p1/display": () => ({ body: display() }),
      "GET /patients/p1/fhir/resources/DocumentReference/doc-1": () => ({ body: documentResource }) });
    render(<App />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: /View details/ }));
    await user.click(await screen.findByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("labels AI summaries, shows the disclaimer, source records and quotes", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result({ resource_type: "ClinicalSummary", resource_id: "s1", ai_generated: true, fhir_reference: null })]) }),
      "GET /patients/p1/display": () => ({ body: display() }), "GET /patients/p1/summary": () => ({ body: summaryBody }) });
    render(<App />);
    const user = await submit();
    expect(await screen.findByText("AI-generated")).toBeInTheDocument(); // badge on the card
    await user.click(screen.getByRole("button", { name: /View details/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Summary of the record.")).toBeInTheDocument();
    expect(within(dialog).getByText(/AI-generated\. Requires clinician review\.$/, { selector: "p.bg-amber-100" })).toBeInTheDocument();
    expect(within(dialog).getAllByText(/AI-generated\. Requires clinician review\./).length).toBeGreaterThanOrEqual(2); // banner + backend disclaimer
    expect(within(dialog).getByText("DocumentReference/doc-1")).toBeInTheDocument();
    expect(within(dialog).getByText(/Source quote: “chest tightness”/)).toBeInTheDocument();
  });

  it("handles unavailable FHIR resources gracefully and keeps the snippet", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result()]) }), "GET /patients/p1/display": () => ({ body: display() }),
      "GET /patients/p1/fhir/resources/DocumentReference/doc-1": () => ({ status: 409, body: { error: { code: "fhir_not_available" } } }) });
    render(<App />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: /View details/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/FHIR resources have not been generated/);
    expect(dialog).toHaveTextContent(/Search snippet: “Chest tightness on exertion\.”/);
  });

  it("handles a FHIR resource that is missing or not the patient's", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result({ resource_id: "gone" })]) }), "GET /patients/p1/display": () => ({ body: display() }),
      "GET /patients/p1/fhir/resources/DocumentReference/gone": () => ({ status: 404, body: { error: { code: "fhir_resource_not_found" } } }) });
    render(<App />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: /View details/ }));
    expect(await screen.findByText(/no longer available for this patient/)).toBeInTheDocument();
  });

  it("handles a missing summary (404)", async () => {
    mockApi({ ...status, "POST /search": () => ({ body: searchBody([result({ resource_type: "ClinicalSummary", ai_generated: true })]) }),
      "GET /patients/p1/display": () => ({ body: display() }),
      "GET /patients/p1/summary": () => ({ status: 404, body: { error: { code: "summary_not_found" } } }) });
    render(<App />);
    const user = await submit();
    await user.click(await screen.findByRole("button", { name: /View details/ }));
    expect(await screen.findByText(/No AI summary has been generated/)).toBeInTheDocument();
  });
});
