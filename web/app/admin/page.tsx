"use client";

import { useEffect, useState } from "react";
import AdminGate from "@/components/AdminGate";
import { api } from "@/lib/api";

type Metrics = {
  cost_by_day_task: { day: string; task: string; cost_usd: number; calls: number }[];
  chat: { runs: number; p50_ms: number | null; p95_ms: number | null; avg_cost_usd: number; active_users: number };
  run_status: Record<string, number>;
  ingestion: { articles_24h: number; lag_minutes: number | null; queue_depth: number; dead_letters: number };
};

const usd = (n: number) => `$${n.toFixed(n < 1 ? 4 : 2)}`;
const secs = (ms: number | null) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)}s`);

function MetricsView() {
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Metrics>("/admin/metrics", { admin: true }).then(setM).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!m) return <p className="muted">Loading…</p>;

  const days = [...new Set(m.cost_by_day_task.map((r) => r.day))];
  const tasks = [...new Set(m.cost_by_day_task.map((r) => r.task))].sort();
  const cell = (day: string, task: string) => m.cost_by_day_task.find((r) => r.day === day && r.task === task);
  const totalCost = m.cost_by_day_task.reduce((s, r) => s + r.cost_usd, 0);

  return (
    <>
      <h2>Last 7 days</h2>
      <div className="grid">
        <div className="card"><div className="muted">Active users</div><div className="stat">{m.chat.active_users}</div></div>
        <div className="card"><div className="muted">Chat runs</div><div className="stat">{m.chat.runs}</div></div>
        <div className="card"><div className="muted">Chat latency p50 / p95</div><div className="stat">{secs(m.chat.p50_ms)} / {secs(m.chat.p95_ms)}</div></div>
        <div className="card"><div className="muted">Avg cost per answer</div><div className="stat">{usd(m.chat.avg_cost_usd)}</div></div>
        <div className="card"><div className="muted">Total LLM spend</div><div className="stat">{usd(totalCost)}</div></div>
        <div className="card"><div className="muted">Articles ingested (24h)</div><div className="stat">{m.ingestion.articles_24h}</div></div>
        <div className="card"><div className="muted">Ingestion lag</div><div className="stat">{m.ingestion.lag_minutes ?? "–"} min</div></div>
        <div className="card"><div className="muted">Queue / dead letters</div><div className="stat">{m.ingestion.queue_depth} / {m.ingestion.dead_letters}</div></div>
      </div>

      <h3>Run outcomes</h3>
      <div className="row">
        {Object.entries(m.run_status).map(([k, v]) => (
          <span key={k} className={`pill ${k === "done" ? "good" : k === "degraded" ? "warn" : k === "running" ? "" : "bad"}`}>{k}: {v}</span>
        ))}
      </div>

      <h3>Cost by day and task</h3>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Day</th>{tasks.map((t) => <th key={t} className="num">{t}</th>)}</tr></thead>
          <tbody>
            {days.map((d) => (
              <tr key={d}>
                <td>{d}</td>
                {tasks.map((t) => {
                  const c = cell(d, t);
                  return <td key={t} className="num" title={c ? `${c.calls} calls` : ""}>{c ? usd(c.cost_usd) : "–"}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <AdminGate>
      <MetricsView />
    </AdminGate>
  );
}
