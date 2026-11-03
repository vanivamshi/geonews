"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import AdminGate from "@/components/AdminGate";
import { api } from "@/lib/api";

type Run = {
  id: number;
  kind: string;
  question: string;
  status: string;
  steps: number;
  cost_usd: number;
  latency_ms: number;
  all_supported: boolean | null;
  created_at: string;
};

function Runs() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    api<Run[]>(`/admin/runs?limit=100${status ? `&status=${status}` : ""}`, { admin: true }).then(setRuns);
  }, [status]);

  return (
    <>
      <div className="row">
        <h2 style={{ marginRight: "auto" }}>Agent traces</h2>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all</option>
          {["done", "degraded", "refused", "budget", "steps", "max_tokens", "running"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      <div className="card table-wrap">
        <table>
          <thead>
            <tr><th>ID</th><th>Question</th><th>Kind</th><th>Status</th><th className="num">Steps</th><th className="num">Latency</th><th className="num">Cost</th><th>When</th></tr>
          </thead>
          <tbody>
            {runs?.map((r) => (
              <tr key={r.id}>
                <td><Link href={`/admin/runs/${r.id}`}>{r.id}</Link></td>
                <td>{r.question}</td>
                <td>{r.kind}</td>
                <td><span className={`pill ${r.status === "done" ? "good" : r.status === "degraded" ? "warn" : "bad"}`}>{r.status}</span></td>
                <td className="num">{r.steps}</td>
                <td className="num">{(r.latency_ms / 1000).toFixed(1)}s</td>
                <td className="num">${r.cost_usd.toFixed(4)}</td>
                <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs?.length === 0 && <p className="muted">No runs yet.</p>}
      </div>
    </>
  );
}

export default function Page() {
  return (
    <AdminGate>
      <Runs />
    </AdminGate>
  );
}
