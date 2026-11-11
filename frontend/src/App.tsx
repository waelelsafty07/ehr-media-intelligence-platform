import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getSearchStatus, searchRecords } from "./api/client";
import type { SearchResponse, SearchResult } from "./api/types";
import DetailDrawer from "./components/DetailDrawer";
import ResultCard from "./components/ResultCard";
import SearchForm, { EMPTY_FORM, toFilters, type FormValues } from "./components/SearchForm";

type SearchState =
  | { kind: "idle" } | { kind: "loading" } | { kind: "ok"; data: SearchResponse } | { kind: "error"; message: string };

export default function App() {
  const [form, setForm] = useState<FormValues>(EMPTY_FORM);
  const [state, setState] = useState<SearchState>({ kind: "idle" });
  const [mode, setMode] = useState<string | null>(null);
  const [selected, setSelected] = useState<SearchResult | null>(null);
  const ctl = useRef<AbortController | null>(null);

  useEffect(() => {
    getSearchStatus().then((s) => setMode(s.embedding.kind), () => undefined); // indicator only; failure is non-fatal
    return () => ctl.current?.abort();
  }, []);

  const run = useCallback(async (values: FormValues) => {
    ctl.current?.abort(); // a newer search supersedes any in flight: no stale results can overwrite fresh ones
    const c = new AbortController();
    ctl.current = c;
    setState({ kind: "loading" });
    try {
      const data = await searchRecords(values.query.trim(), toFilters(values), c.signal);
      if (c.signal.aborted) return;
      setMode(data.embedding.kind);
      setState({ kind: "ok", data });
    } catch (e) {
      if (c.signal.aborted || (e instanceof DOMException && e.name === "AbortError")) return;
      setState({ kind: "error", message: e instanceof ApiError ? e.message : "Something went wrong." });
    }
  }, []);

  const filterPatient = (patientId: string) => {
    const next = { ...form, patientId };
    setForm(next);
    if (next.query.trim()) void run(next);
  };

  const busy = state.kind === "loading";
  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <p role="note" className="bg-amber-100 px-4 py-2 text-center text-sm text-amber-900">
        Local demo with synthetic data only. No authentication or authorization: do not use with real patient data.
      </p>
      <main className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-2xl font-semibold">Clinical record search</h1>
          {mode && (
            <p className="text-sm text-slate-700" data-testid="mode">
              Search mode: <strong>{mode}</strong>
              {mode === "lexical" && " (keyword-style fallback, no semantic understanding)"}
            </p>
          )}
        </header>

        <SearchForm values={form} onChange={setForm} onSubmit={() => void run(form)} busy={busy} />

        <section aria-label="Results" aria-live="polite">
          {state.kind === "idle" && <p className="text-slate-700">Enter a query to search clinical documents and AI summaries.</p>}
          {state.kind === "loading" && <p role="status" className="text-slate-700">Searching…</p>}
          {state.kind === "error" && (
            <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-900">
              {state.message}{" "}
              <button type="button" onClick={() => void run(form)} className="underline focus-visible:outline-2 focus-visible:outline-blue-700">Retry</button>
            </div>
          )}
          {state.kind === "ok" && (
            <>
              <p className="mb-2 text-sm text-slate-700">
                {state.data.returned === 0 ? "No results" : `${state.data.returned} result${state.data.returned === 1 ? "" : "s"}`}
                {" "}for “{state.data.query}”. Similarity scores are not clinical relevance or confidence.
              </p>
              {state.data.returned === 0 ? (
                <p className="rounded border border-slate-300 bg-white p-4 text-slate-800">
                  No matching records. Try different words or relax the filters. Records must be indexed before they can be found.
                </p>
              ) : (
                <ol className="space-y-3">
                  {state.data.results.map((r) => (
                    <ResultCard key={`${r.resource_type}-${r.resource_id}`} result={r} onOpen={setSelected} onFilterPatient={filterPatient} />
                  ))}
                </ol>
              )}
            </>
          )}
        </section>
      </main>
      {selected && <DetailDrawer result={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
