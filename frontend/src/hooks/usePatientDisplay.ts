import { useEffect, useState } from "react";
import { getPatientDisplay } from "../api/client";
import type { PatientDisplay } from "../api/types";

export type DisplayState = { kind: "loading" } | { kind: "ok"; data: PatientDisplay } | { kind: "unavailable" };

// Session cache: one lookup per patient id, shared by every card. Stores the in-flight promise so concurrent
// cards for the same patient trigger a single request. Failures are NOT cached (a later render may retry).
// Not persisted anywhere (no localStorage): identifiers live only in memory for the page session.
const cache = new Map<string, Promise<PatientDisplay>>();

export function clearPatientDisplayCache(): void {
  cache.clear();
}

export function lookupPatientDisplay(patientId: string): Promise<PatientDisplay> {
  let p = cache.get(patientId);
  if (!p) {
    p = getPatientDisplay(patientId);
    cache.set(patientId, p);
    p.catch(() => cache.delete(patientId));
  }
  return p;
}

export function usePatientDisplay(patientId: string): DisplayState {
  const [state, setState] = useState<DisplayState>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    lookupPatientDisplay(patientId).then(
      (data) => live && setState({ kind: "ok", data }),
      () => live && setState({ kind: "unavailable" }),
    );
    return () => {
      live = false;
    };
  }, [patientId]);
  return state;
}
