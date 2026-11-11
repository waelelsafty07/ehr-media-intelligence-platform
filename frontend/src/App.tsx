import { useEffect, useState } from "react";

type HealthState = { kind: "loading" } | { kind: "ok"; version: string } | { kind: "error" };

// Phase 1 shell: only proves the frontend reaches the real backend. The search UI is Phase 6.
export default function App() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("bad status"))))
      .then((d) => setHealth({ kind: "ok", version: d.version }))
      .catch(() => setHealth({ kind: "error" }));
  }, []);

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold text-slate-900">EHR Media Intelligence</h1>
      <p role="status" className="mt-4 text-slate-700">
        {health.kind === "loading" && "Checking backend…"}
        {health.kind === "ok" && `Backend connected (v${health.version}).`}
        {health.kind === "error" && "Backend unreachable. Start the API on port 8000."}
      </p>
    </main>
  );
}
