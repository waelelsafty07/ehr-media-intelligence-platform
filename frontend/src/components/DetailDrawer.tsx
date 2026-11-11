import { useEffect, useRef, useState } from "react";
import { ApiError, fhirResourcePath, getDocumentReference, getSummary } from "../api/client";
import type { DocumentDetail, SearchResult, SummaryResponse } from "../api/types";
import { usePatientDisplay } from "../hooks/usePatientDisplay";

type Load<T> = { kind: "loading" } | { kind: "ok"; data: T } | { kind: "error"; message: string } | { kind: "missing"; message: string };

function errorState(e: unknown): Load<never> {
  if (e instanceof ApiError && (e.status === 404 || e.status === 409)) return { kind: "missing", message: e.message };
  return { kind: "error", message: e instanceof ApiError ? e.message : "Something went wrong." };
}

export default function DetailDrawer({ result, onClose }: { result: SearchResult; onClose: () => void }) {
  const display = usePatientDisplay(result.patient_id);
  const isSummary = result.resource_type === "ClinicalSummary";
  const [doc, setDoc] = useState<Load<DocumentDetail | null>>({ kind: "loading" });
  const [sum, setSum] = useState<Load<SummaryResponse>>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<Element | null>(document.activeElement);

  useEffect(() => {
    const ctl = new AbortController();
    const swallow = (e: unknown) => !(e instanceof DOMException && e.name === "AbortError");
    if (isSummary) {
      setSum({ kind: "loading" });
      getSummary(result.patient_id, ctl.signal).then((data) => setSum({ kind: "ok", data }), (e) => swallow(e) && setSum(errorState(e)));
    } else {
      setDoc({ kind: "loading" });
      getDocumentReference(result.patient_id, result.resource_id, ctl.signal).then(
        (data) => setDoc(data ? { kind: "ok", data } : { kind: "missing", message: "This FHIR resource is no longer available for this patient." }),
        (e) => swallow(e) && setDoc(errorState(e)));
    }
    return () => ctl.abort();
  }, [result.patient_id, result.resource_id, isSummary, attempt]);

  useEffect(() => {
    closeRef.current?.focus();
    const opener = openerRef.current as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
    if (e.key !== "Tab") return; // simple focus trap inside the dialog
    const nodes = e.currentTarget.querySelectorAll<HTMLElement>("a[href],button:not([disabled])");
    if (!nodes.length) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const state = isSummary ? sum : doc;
  const retry = () => setAttempt((n) => n + 1);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/50" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="drawer-title" onKeyDown={onKeyDown} onClick={(e) => e.stopPropagation()}
        className="h-full w-full overflow-y-auto bg-white p-5 shadow-xl sm:max-w-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="drawer-title" className="text-lg font-semibold text-slate-900">
              {isSummary ? "AI clinical summary" : "Clinical document"}
            </h2>
            <p className="text-sm text-slate-700">
              {display.kind === "ok" ? `${display.data.patient_name ?? "Name not recorded"} · MRN ${display.data.mrn}` :
                display.kind === "loading" ? "Loading patient…" : "Patient details unavailable"}
              {" · "}{result.record_date ?? "Date not recorded"}
            </p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose}
            className="rounded border border-slate-400 px-3 py-1 text-sm focus-visible:outline-2 focus-visible:outline-blue-700">Close</button>
        </div>

        {state.kind === "loading" && <p role="status" className="mt-4 text-slate-700">Loading details…</p>}
        {(state.kind === "error" || state.kind === "missing") && (
          <div role="alert" className="mt-4 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">
            {state.message}
            {state.kind === "error" && (
              <button type="button" onClick={retry} className="ml-2 underline focus-visible:outline-2 focus-visible:outline-blue-700">Retry</button>
            )}
          </div>
        )}

        {!isSummary && doc.kind === "ok" && doc.data && <DocumentBody d={doc.data} patientId={result.patient_id} />}
        {isSummary && sum.kind === "ok" && <SummaryBody r={sum.data} />}
        {(state.kind === "error" || state.kind === "missing") && (
          <p className="mt-3 text-sm text-slate-700">Search snippet: “{result.snippet}”</p>
        )}
      </div>
    </div>
  );
}

function DocumentBody({ d, patientId }: { d: DocumentDetail; patientId: string }) {
  return (
    <section className="mt-4 space-y-3" aria-label="Document">
      <p className="text-sm text-slate-700">Document date: {d.date ?? "not recorded"}</p>
      {d.text ? (
        <pre className="whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm text-slate-900">{d.text}</pre>
      ) : (
        <p className="text-sm text-slate-700">The document text is not available as plain text.</p>
      )}
      <h3 className="text-sm font-semibold text-slate-900">FHIR resources</h3>
      <ul className="list-inside list-disc text-sm text-slate-800">
        {[d.reference, ...d.related].map((ref) => {
          const [type, id] = ref.split("/");
          return (
            <li key={ref}>
              <a className="text-blue-800 underline focus-visible:outline-2 focus-visible:outline-blue-700" target="_blank" rel="noreferrer"
                href={`/api${fhirResourcePath(patientId, type, id)}`}>{ref}</a>
              {ref !== d.reference && " (related)"}
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-slate-600">Links open the individual FHIR R4 resource as JSON.</p>
    </section>
  );
}

function SummaryBody({ r }: { r: SummaryResponse }) {
  const s = r.summary;
  return (
    <section className="mt-4 space-y-4" aria-label="AI summary">
      <p className="rounded bg-amber-100 px-3 py-2 text-sm font-medium text-amber-900">AI-generated. Requires clinician review.</p>
      {r.is_stale && (
        <p role="note" className="rounded border border-amber-400 px-3 py-2 text-sm text-amber-900">
          This summary may be out of date ({r.stale_reasons.join(", ")}).
        </p>
      )}
      <p className="text-sm text-slate-900">{s.summary_text}</p>
      {s.chief_concern && (
        <div><h3 className="text-sm font-semibold text-slate-900">Chief concern</h3>
          <p className="text-sm text-slate-800">{s.chief_concern.text}</p>
          <p className="text-xs text-slate-600">Source quote: “{s.chief_concern.evidence_quote}”</p></div>
      )}
      {s.key_diagnoses.length > 0 && (
        <div><h3 className="text-sm font-semibold text-slate-900">Diagnoses mentioned in the records</h3>
          <ul className="space-y-1 text-sm text-slate-800">
            {s.key_diagnoses.map((d, i) => (
              <li key={i}>{d.text} <span className="text-slate-600">({d.certainty}, {d.temporal})</span>
                <div className="text-xs text-slate-600">Source quote: “{d.evidence_quote}”</div></li>
            ))}
          </ul></div>
      )}
      {s.missing_information.length > 0 && (
        <div><h3 className="text-sm font-semibold text-slate-900">Missing information</h3>
          <ul className="list-inside list-disc text-sm text-slate-800">{s.missing_information.map((m) => <li key={m}>{m}</li>)}</ul></div>
      )}
      <div>
        <h3 className="text-sm font-semibold text-slate-900">Source records</h3>
        <ul className="list-inside list-disc text-sm text-slate-800">
          {s.source_record_ids.map((id) => <li key={id}>DocumentReference/{id}</li>)}
        </ul>
      </div>
      <p className="text-xs text-slate-600">
        Generated {s.generated_at.slice(0, 10)} by {s.model_identifier}. {s.confidence_or_quality_indicator.explanation}
      </p>
      <p role="note" className="border-t border-slate-300 pt-3 text-xs text-slate-700">{s.disclaimer}</p>
    </section>
  );
}
