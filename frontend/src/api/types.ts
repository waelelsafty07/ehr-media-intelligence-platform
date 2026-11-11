// Wire types. Search results and patient display info are deliberately SEPARATE models:
// /search never returns names or MRNs; those come only from GET /patients/{id}/display.

export type ResourceType = "DocumentReference" | "ClinicalSummary";
export const RESOURCE_TYPES: ResourceType[] = ["DocumentReference", "ClinicalSummary"];

export interface SearchResult {
  rank: number;
  resource_type: ResourceType;
  resource_id: string;
  fhir_reference: string | null;
  link: string;
  patient_id: string; // opaque internal id
  record_date: string | null;
  score: number; // embedding similarity, NOT clinical relevance
  snippet: string;
  source_record_id: string | null;
  ai_generated: boolean;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  returned: number;
  candidates_considered: number;
  stale_results_dropped: number;
  took_ms: number;
  embedding: { model_identifier: string; kind: "semantic" | "lexical" | string };
  note: string;
}

export interface SearchFilters {
  patient_id?: string;
  resource_types?: ResourceType[];
  date_from?: string;
  date_to?: string;
}

export interface SearchStatus {
  embedding: { model_identifier: string; kind: string; available: boolean };
}

export interface PatientDisplay {
  patient_id: string;
  patient_name: string | null;
  mrn: string;
}

export interface EvidencedStatement {
  text: string;
  evidence_quote: string;
  source_record_ids: string[];
}
export interface DiagnosisStatement extends EvidencedStatement {
  name: string;
  certainty: string;
  temporal: string;
}
export interface ClinicalSummary {
  patient_id: string;
  chief_concern: EvidencedStatement | null;
  key_diagnoses: DiagnosisStatement[];
  anomalies: EvidencedStatement[];
  missing_information: string[];
  summary_text: string;
  source_record_ids: string[];
  confidence_or_quality_indicator: { level: string; explanation: string };
  generated_at: string;
  model_identifier: string;
  disclaimer: string;
}
export interface SummaryResponse {
  summary: ClinicalSummary;
  summary_id: string;
  is_stale: boolean;
  stale_reasons: string[];
}

// The only parts of a FHIR DocumentReference the UI reads.
export interface DocumentDetail {
  reference: string; // "DocumentReference/<id>"
  text: string | null;
  contentType: string | null;
  date: string | null; // clinical document date (context.period.start), not the ingestion time
  related: string[]; // e.g. "DiagnosticReport/<id>" found in the same bundle
}
