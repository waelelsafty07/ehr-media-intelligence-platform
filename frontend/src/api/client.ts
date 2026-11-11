// The ONLY module that talks to the backend. Components never call fetch directly.
import type {
  DocumentDetail, PatientDisplay, SearchFilters, SearchResponse, SearchStatus, SummaryResponse,
} from "./types";

const BASE = "/api";

/** Safe error: carries a status and a stable code only. Backend/internal text is never shown to users. */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const FRIENDLY: Record<string, string> = {
  embedding_provider_unavailable: "Semantic search is unavailable on the server. See the README to enable it.",
  search_index_unavailable: "The search index is unavailable.",
  patient_not_found: "That patient was not found.",
  summary_not_found: "No AI summary has been generated for this patient.",
  fhir_not_available: "FHIR resources have not been generated for this patient.",
  fhir_resource_not_found: "This FHIR resource was not found for this patient.",
  validation_error: "The request was not valid. Check the query and filters.",
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Could not reach the server.");
  }
  if (!res.ok) {
    let code = "http_error";
    try {
      const body = await res.json();
      if (typeof body?.error?.code === "string") code = body.error.code;
    } catch {
      /* non-JSON error body: keep generic code */
    }
    throw new ApiError(res.status, code, FRIENDLY[code] ?? `The server returned an error (${res.status}).`);
  }
  return (await res.json()) as T;
}

export function searchRecords(query: string, filters: SearchFilters, signal?: AbortSignal): Promise<SearchResponse> {
  const body: Record<string, unknown> = { query };
  if (filters.patient_id) body.patient_id = filters.patient_id;
  if (filters.resource_types?.length) body.resource_types = filters.resource_types;
  if (filters.date_from) body.date_from = filters.date_from;
  if (filters.date_to) body.date_to = filters.date_to;
  return request<SearchResponse>("/search", { method: "POST", body: JSON.stringify(body), signal });
}

export const getSearchStatus = (signal?: AbortSignal) => request<SearchStatus>("/search/status", { signal });

export const getPatientDisplay = (patientId: string, signal?: AbortSignal) =>
  request<PatientDisplay>(`/patients/${encodeURIComponent(patientId)}/display`, { signal });

export const getSummary = (patientId: string, signal?: AbortSignal) =>
  request<SummaryResponse>(`/patients/${encodeURIComponent(patientId)}/summary`, { signal });

function decodeBase64Utf8(b64: string): string | null {
  try {
    const bin = atob(b64);
    return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
  } catch {
    return null;
  }
}

/** Related resources named by the DocumentReference itself (e.g. its DiagnosticReport), as "Type/id". */
function relatedRefs(res: Record<string, any>): string[] {
  const out: string[] = [];
  for (const rel of res.context?.related ?? []) {
    const id = typeof rel?.reference === "string" ? rel.reference.replace(/^urn:uuid:/, "") : null;
    if (id && typeof rel?.type === "string") out.push(`${rel.type}/${id}`);
  }
  return out;
}

/** Fetches ONE DocumentReference through the focused endpoint (never the Bundle, which also holds the full Patient).
 *  Returns null when the backend reports it missing / not this patient's (404). */
export async function getDocumentReference(
  patientId: string, documentId: string, signal?: AbortSignal,
): Promise<DocumentDetail | null> {
  let res: Record<string, any>;
  try {
    res = await request<Record<string, any>>(fhirResourcePath(patientId, "DocumentReference", documentId), { signal });
  } catch (e) {
    if (e instanceof ApiError && e.code === "fhir_resource_not_found") return null;
    throw e;
  }
  const att = res.content?.[0]?.attachment;
  return {
    reference: `DocumentReference/${documentId}`,
    text: typeof att?.data === "string" ? decodeBase64Utf8(att.data) : null,
    contentType: att?.contentType ?? null,
    date: typeof res.context?.period?.start === "string" ? res.context.period.start.slice(0, 10) : null,
    related: relatedRefs(res),
  };
}

export const fhirResourcePath = (patientId: string, type: string, id: string) =>
  `/patients/${encodeURIComponent(patientId)}/fhir/resources/${encodeURIComponent(type)}/${encodeURIComponent(id)}`;
