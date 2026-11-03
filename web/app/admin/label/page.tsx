"use client";

import { useCallback, useEffect, useState } from "react";
import AdminGate from "@/components/AdminGate";
import Cited from "@/components/Cited";
import { api } from "@/lib/api";

type RunItem = { target_type: "agent_run"; target_id: number; question: string; answer: string };
type ArticleItem = {
  target_type: "article"; target_id: number; title: string; summary: string;
  topic: string; locality: string; newsworthiness: number;
};
type Pair = { article_id: number; prompt: string; a: string; b: string };
type Mode = "faithfulness" | "relevance" | "pairs";

function Labeling() {
  const [mode, setMode] = useState<Mode>("faithfulness");
  const [queue, setQueue] = useState<(RunItem | ArticleItem)[]>([]);
  const [pair, setPair] = useState<Pair | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState(0);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      if (mode === "pairs") setPair(await api<Pair>("/admin/pairs/next", { admin: true }));
      else setQueue(await api(`/admin/label/queue?kind=${mode}`, { admin: true }));
    } finally {
      setBusy(false);
    }
  }, [mode]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch queue when mode changes
    load();
  }, [load]);

  async function label(item: RunItem | ArticleItem, value: number) {
    await api("/admin/labels", {
      method: "POST", admin: true,
      body: JSON.stringify({ kind: mode, target_type: item.target_type, target_id: item.target_id, value, note }),
    });
    setNote("");
    setCount((c) => c + 1);
    const rest = queue.slice(1);
    setQueue(rest);
    if (rest.length === 0) load();
  }

  async function choose(chosen: string, rejected: string) {
    if (!pair) return;
    await api("/admin/preferences", {
      method: "POST", admin: true, body: JSON.stringify({ prompt: pair.prompt, chosen, rejected }),
    });
    setCount((c) => c + 1);
    load();
  }

  const item = queue[0];
  return (
    <>
      <div className="row">
        <h2 style={{ marginRight: "auto" }}>Labeling</h2>
        <span className="muted">{count} labeled this session</span>
        <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
          <option value="faithfulness">Answer faithfulness (1-5)</option>
          <option value="relevance">Article newsworthiness (0-1)</option>
          <option value="pairs">Summary preference (DPO)</option>
        </select>
      </div>
      {busy && <p className="muted">Loading…</p>}

      {mode === "faithfulness" && item?.target_type === "agent_run" && (
        <div className="card">
          <p><strong>{item.question}</strong></p>
          <Cited text={item.answer} />
          <p className="muted">Open the cited articles. 5 = every claim supported; 1 = largely unsupported.</p>
          <input placeholder="Optional note (why)" value={note} onChange={(e) => setNote(e.target.value)} />
          <p className="row">{[1, 2, 3, 4, 5].map((v) => <button key={v} onClick={() => label(item, v)}>{v}</button>)}</p>
        </div>
      )}

      {mode === "relevance" && item?.target_type === "article" && (
        <div className="card">
          <p><strong>{item.title}</strong></p>
          <p>{item.summary}</p>
          <p className="muted">Model said: {item.topic} · {item.locality} · newsworthiness {item.newsworthiness.toFixed(2)}</p>
          <p className="row">
            {[0, 0.25, 0.5, 0.75, 1].map((v) => <button key={v} onClick={() => label(item, v)}>{v}</button>)}
          </p>
        </div>
      )}

      {mode !== "pairs" && !busy && !item && <p className="muted">Queue empty.</p>}

      {mode === "pairs" && pair && (
        <>
          <details className="card"><summary>Source article</summary><pre>{pair.prompt}</pre></details>
          <div className="grid">
            {[[pair.a, pair.b], [pair.b, pair.a]].map(([mine, other], i) => (
              <div className="card" key={i}>
                <p>{mine}</p>
                <button className="primary" onClick={() => choose(mine, other)}>This one is better</button>
              </div>
            ))}
          </div>
          <button onClick={load}>Skip</button>
        </>
      )}
    </>
  );
}

export default function Page() {
  return (
    <AdminGate>
      <Labeling />
    </AdminGate>
  );
}
