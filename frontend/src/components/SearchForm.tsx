import { useState } from "react";
import { RESOURCE_TYPES, type ResourceType, type SearchFilters } from "../api/types";

export interface FormValues {
  query: string;
  patientId: string;
  types: ResourceType[];
  dateFrom: string;
  dateTo: string;
}
export const EMPTY_FORM: FormValues = { query: "", patientId: "", types: [], dateFrom: "", dateTo: "" };

export function toFilters(v: FormValues): SearchFilters {
  return {
    patient_id: v.patientId.trim() || undefined,
    resource_types: v.types.length ? v.types : undefined,
    date_from: v.dateFrom || undefined,
    date_to: v.dateTo || undefined,
  };
}

const field = "mt-1 w-full rounded border border-slate-400 bg-white px-3 py-2 text-slate-900 focus-visible:outline-2 focus-visible:outline-blue-700";

export default function SearchForm({ values, onChange, onSubmit, busy }: {
  values: FormValues; onChange: (v: FormValues) => void; onSubmit: () => void; busy: boolean;
}) {
  const [touched, setTouched] = useState(false);
  const dateError = values.dateFrom && values.dateTo && values.dateFrom > values.dateTo ? "“From” must not be after “To”." : null;
  const queryError = touched && !values.query.trim() ? "Enter a search query." : null;
  const set = (p: Partial<FormValues>) => onChange({ ...values, ...p });
  const toggle = (t: ResourceType) =>
    set({ types: values.types.includes(t) ? values.types.filter((x) => x !== t) : [...values.types, t] });

  return (
    <form role="search" aria-label="Search clinical records" noValidate
      onSubmit={(e) => { e.preventDefault(); setTouched(true); if (values.query.trim() && !dateError && !busy) onSubmit(); }}
      className="space-y-4 rounded-lg border border-slate-300 bg-white p-4">
      <div>
        <label htmlFor="q" className="block text-sm font-medium text-slate-900">Search query</label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input id="q" type="search" value={values.query} onChange={(e) => set({ query: e.target.value })}
            aria-invalid={!!queryError} aria-describedby={queryError ? "q-err" : undefined}
            placeholder="e.g. chest pain on exertion" className={field} />
          <button type="submit" disabled={busy}
            className="mt-1 rounded bg-blue-700 px-5 py-2 font-medium text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700">
            {busy ? "Searching…" : "Search"}
          </button>
        </div>
        {queryError && <p id="q-err" role="alert" className="mt-1 text-sm text-red-700">{queryError}</p>}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor="pid" className="block text-sm font-medium text-slate-900">Patient ID</label>
          <input id="pid" value={values.patientId} onChange={(e) => set({ patientId: e.target.value })}
            placeholder="all patients" autoComplete="off" className={field} />
        </div>
        <fieldset>
          <legend className="text-sm font-medium text-slate-900">Resource type</legend>
          <div className="mt-2 space-y-1">
            {RESOURCE_TYPES.map((t) => (
              <label key={t} className="flex items-center gap-2 text-sm text-slate-800">
                <input type="checkbox" checked={values.types.includes(t)} onChange={() => toggle(t)}
                  className="h-4 w-4 focus-visible:outline-2 focus-visible:outline-blue-700" />
                {t === "DocumentReference" ? "Clinical documents" : "AI summaries"}
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <label htmlFor="from" className="block text-sm font-medium text-slate-900">Date from</label>
          <input id="from" type="date" value={values.dateFrom} onChange={(e) => set({ dateFrom: e.target.value })} className={field} />
        </div>
        <div>
          <label htmlFor="to" className="block text-sm font-medium text-slate-900">Date to</label>
          <input id="to" type="date" value={values.dateTo} onChange={(e) => set({ dateTo: e.target.value })}
            aria-invalid={!!dateError} aria-describedby={dateError ? "d-err" : undefined} className={field} />
        </div>
      </div>
      {dateError && <p id="d-err" role="alert" className="text-sm text-red-700">{dateError}</p>}
      <p className="text-xs text-slate-600">
        Leave the patient empty to search everyone. Date filters exclude records that have no date.
        {" "}
        <button type="button" onClick={() => onChange({ ...EMPTY_FORM, query: values.query })}
          className="underline focus-visible:outline-2 focus-visible:outline-blue-700">Clear filters</button>
      </p>
    </form>
  );
}
