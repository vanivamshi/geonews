"use client";

import { useEffect, useState } from "react";
import AdminGate from "@/components/AdminGate";
import { api } from "@/lib/api";

type EvalRun = {
  id: number;
  suite: string;
  git_sha: string | null;
  prompt_version: string;
  model: string;
  summary: Record<string, number | string>;
  created_at: string;
};
type Score = { case_id: string; metric: string; score: number; detail: Record<string, unknown> };

function Evals() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [scores, setScores] = useState<Score[]>([]);

  useEffect(() => {
    api<EvalRun[]>("/admin/evals", { admin: true }).then(setRuns);
  }, []);

  useEffect(() => {
    if (selected !== null) api<Score[]>(`/admin/evals/${selected}`, { admin: true }).then(setScores);
  }, [selected]);

  const suites = [...new Set(runs?.map((r) => r.suite) ?? [])];

  return (
    <>
      <h2>Eval runs</h2>
      {suites.map((suite) => {
        const rows = runs!.filter((r) => r.suite === suite);
        const metrics = [...new Set(rows.flatMap((r) => Object.keys(r.summary)))]
          .filter((k) => typeof rows[0].summary[k] === "number").sort();
        return (
          <div key={suite} className="card table-wrap">
            <h3 style={{ marginTop: 0 }}>{suite}</h3>
            <table>
              <thead>
                <tr><th>Run</th><th>Commit</th><th>Prompt</th>{metrics.map((m) => <th key={m} className="num">{m}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const prev = rows[i + 1];
                  return (
                    <tr key={r.id} onClick={() => setSelected(r.id)} style={{ cursor: "pointer" }}>
                      <td>{r.id}</td><td>{r.git_sha ?? "–"}</td><td>{r.prompt_version}</td>
                      {metrics.map((m) => {
                        const v = r.summary[m] as number;
                        const p = prev?.summary[m] as number | undefined;
                        const delta = p === undefined ? 0 : v - p;
                        return (
                          <td key={m} className="num">
                            {v?.toFixed?.(3) ?? "–"}
                            {delta !== 0 && <span className="muted"> ({delta > 0 ? "+" : ""}{delta.toFixed(3)})</span>}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
      {runs?.length === 0 && <p className="muted">No eval runs yet. Run <code>make eval</code>.</p>}

      {selected !== null && (
        <>
          <h3>Run {selected}: per-case scores (lowest first)</h3>
          <div className="card table-wrap">
            <table>
              <thead><tr><th>Case</th><th>Metric</th><th className="num">Score</th><th>Detail</th></tr></thead>
              <tbody>
                {[...scores].sort((a, b) => a.score - b.score).map((s, i) => (
                  <tr key={i}>
                    <td>{s.case_id}</td><td>{s.metric}</td><td className="num">{s.score.toFixed(3)}</td>
                    <td><pre>{typeof s.detail?.reason === "string" ? s.detail.reason : JSON.stringify(s.detail).slice(0, 300)}</pre></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

export default function Page() {
  return (
    <AdminGate>
      <Evals />
    </AdminGate>
  );
}
