import type { SearchResult } from "../api/types";
import { usePatientDisplay } from "../hooks/usePatientDisplay";

const TYPE_LABEL: Record<string, string> = { DocumentReference: "Clinical document", ClinicalSummary: "AI summary" };

export default function ResultCard({ result, onOpen, onFilterPatient }: {
  result: SearchResult;
  onOpen: (r: SearchResult) => void;
  onFilterPatient: (patientId: string) => void;
}) {
  const display = usePatientDisplay(result.patient_id);
  const label = TYPE_LABEL[result.resource_type] ?? result.resource_type;
  return (
    <li className="rounded-lg border border-slate-300 bg-white p-4 shadow-sm">
      <article aria-labelledby={`r-${result.rank}`}>
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 id={`r-${result.rank}`} className="text-base font-semibold text-slate-900">
              {display.kind === "ok" && (display.data.patient_name ?? "Name not recorded")}
              {display.kind === "loading" && <span className="text-slate-600">Loading patient…</span>}
              {display.kind === "unavailable" && <span className="text-slate-700">Patient details unavailable</span>}
            </h3>
            <p className="text-sm text-slate-700">
              MRN: {display.kind === "ok" ? display.data.mrn : "—"}
            </p>
          </div>
          <p className="text-sm text-slate-700" title="Embedding similarity: not clinical relevance or confidence">
            <span className="font-medium">Similarity</span> {result.score.toFixed(2)}
          </p>
        </header>
        <p className="mt-2 flex flex-wrap gap-2 text-xs">
          <span className="rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-800">#{result.rank}</span>
          <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-800">{label}</span>
          {result.ai_generated && (
            <span className="rounded bg-amber-100 px-2 py-0.5 font-medium text-amber-900">AI-generated</span>
          )}
          <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-800">
            {result.record_date ? <time dateTime={result.record_date}>{result.record_date}</time> : "Date not recorded"}
          </span>
        </p>
        <blockquote className="mt-3 border-l-4 border-slate-300 pl-3 text-sm text-slate-800">{result.snippet}</blockquote>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => onOpen(result)}
            className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700">
            View details<span className="sr-only"> for result {result.rank}</span>
          </button>
          <button type="button" onClick={() => onFilterPatient(result.patient_id)}
            className="rounded border border-slate-400 px-3 py-1.5 text-sm text-slate-800 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700">
            Only this patient<span className="sr-only"> (result {result.rank})</span>
          </button>
        </div>
      </article>
    </li>
  );
}
