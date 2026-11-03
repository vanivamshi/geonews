"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import AdminGate from "@/components/AdminGate";
import Cited from "@/components/Cited";
import { api } from "@/lib/api";

type Detail = {
  id: number;
  question: string;
  answer: string;
  status: string;
  prompt_version: string;
  trace_id: string | null;
  cost_usd: number;
  latency_ms: number;
  tokens: { input: number; output: number; cache_read: number };
  verdict: { all_supported?: boolean; citation_validity?: number; unsupported?: { text: string; reason: string }[] };
  tool_calls: { agent: string; tool: string; arguments: unknown; result_preview: string; is_error: boolean; latency_ms: number; at: string }[];
  llm_calls: { task: string; model: string; input_tokens: number; output_tokens: number; cache_read_tokens: number; cost_usd: number; latency_ms: number; stop_reason: string | null }[];
};

function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<Detail | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    api<Detail>(`/admin/runs/${id}`, { admin: true }).then(setD);
  }, [id]);

  if (!d) return <p className="muted">Loading…</p>;
  const cacheRate = d.tokens.cache_read / Math.max(1, d.tokens.input + d.tokens.cache_read);

  return (
    <>
      <h2>Run {d.id}: {d.question}</h2>
      <div className="grid">
        <div className="card"><div className="muted">Status</div><div className="stat">{d.status}</div></div>
        <div className="card"><div className="muted">Latency</div><div className="stat">{(d.latency_ms / 1000).toFixed(1)}s</div></div>
        <div className="card"><div className="muted">Cost</div><div className="stat">${d.cost_usd.toFixed(4)}</div></div>
        <div className="card"><div className="muted">Tokens in / out</div><div className="stat">{d.tokens.input.toLocaleString()} / {d.tokens.output.toLocaleString()}</div></div>
        <div className="card"><div className="muted">Cache hit rate</div><div className="stat">{(cacheRate * 100).toFixed(0)}%</div></div>
      </div>
      <p className="muted">prompt {d.prompt_version} · trace {d.trace_id ?? "–"}</p>

      <h3>Answer</h3>
      <div className="card"><Cited text={d.answer} /></div>

      <h3>Verifier</h3>
      <div className="card">
        <p>All supported: <strong>{String(d.verdict.all_supported ?? "–")}</strong> · citation validity {d.verdict.citation_validity ?? "–"}</p>
        {d.verdict.unsupported?.map((u, i) => (
          <p key={i}><span className="pill bad">unsupported</span> {u.text} <span className="muted">({u.reason})</span></p>
        ))}
      </div>

      <h3>Tool calls ({d.tool_calls.length})</h3>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Agent</th><th>Tool</th><th>Arguments</th><th className="num">ms</th></tr></thead>
          <tbody>
            {d.tool_calls.map((t, i) => (
              <tr key={i} onClick={() => setOpen(open === i ? null : i)} style={{ cursor: "pointer" }}>
                <td>{t.agent}</td>
                <td>{t.is_error ? <span className="pill bad">{t.tool}</span> : t.tool}</td>
                <td>
                  <pre>{JSON.stringify(t.arguments)}</pre>
                  {open === i && <pre className="muted" style={{ marginTop: 8 }}>{t.result_preview}</pre>}
                </td>
                <td className="num">{t.latency_ms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>Model calls ({d.llm_calls.length})</h3>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Task</th><th>Model</th><th className="num">In</th><th className="num">Cache read</th><th className="num">Out</th><th className="num">ms</th><th className="num">Cost</th><th>Stop</th></tr></thead>
          <tbody>
            {d.llm_calls.map((c, i) => (
              <tr key={i}>
                <td>{c.task}</td><td>{c.model}</td>
                <td className="num">{c.input_tokens}</td><td className="num">{c.cache_read_tokens}</td>
                <td className="num">{c.output_tokens}</td><td className="num">{c.latency_ms}</td>
                <td className="num">${c.cost_usd.toFixed(4)}</td><td>{c.stop_reason}</td>
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
      <RunDetail />
    </AdminGate>
  );
}
