"use client";

import { useRef, useState } from "react";
import Cited from "@/components/Cited";
import RequireAuth from "@/components/RequireAuth";
import { logEvent, streamChat } from "@/lib/api";

type Turn = {
  question: string;
  answer: string;
  steps: string[];
  status?: string;
  runId?: number;
  done: boolean;
  rated?: number;
  error?: string;
};

function describe(event: string, d: Record<string, unknown>): string | null {
  switch (event) {
    case "tool_call":
      if (d.tool === "find_stories") return "Finding coverage…";
      if (d.tool === "research_articles") return `Reading ${(d.input as { article_ids?: string[] })?.article_ids?.length ?? ""} articles…`;
      if (d.tool === "load_skill") return `Using skill: ${(d.input as { name?: string })?.name}`;
      if (d.tool === "remember_preference") return "Saving your preference";
      return null;
    case "verifying":
      return "Checking every claim against its sources…";
    case "revising":
      return `Fixing ${d.unsupported} unsupported point(s)…`;
    default:
      return null;
  }
}

function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const update = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setQuestion("");
    setBusy(true);
    setTurns((ts) => [...ts, { question: q, answer: "", steps: [], done: false }]);
    abort.current = new AbortController();
    try {
      for await (const { event, data } of streamChat(q, abort.current.signal)) {
        if (event === "run") update((t) => ({ ...t, runId: data.run_id as number }));
        else if (event === "answer_delta") update((t) => ({ ...t, answer: t.answer + (data.text as string) }));
        else if (event === "revising") update((t) => ({ ...t, answer: "", steps: [...t.steps, describe(event, data)!] }));
        else if (event === "final")
          update((t) => ({ ...t, answer: data.answer as string, status: data.status as string, done: true }));
        else if (event === "error") update((t) => ({ ...t, error: data.message as string, done: true }));
        else {
          const step = describe(event, data);
          if (step) update((t) => (t.steps.at(-1) === step ? t : { ...t, steps: [...t.steps, step] }));
        }
      }
    } catch (err) {
      update((t) => ({ ...t, error: (err as Error).message, done: true }));
    } finally {
      setBusy(false);
    }
  }

  function rate(value: number) {
    const t = turns[turns.length - 1];
    if (!t?.runId) return;
    logEvent({ kind: "rating", agent_run_id: t.runId, value });
    update((x) => ({ ...x, rated: value }));
  }

  return (
    <main>
      <h2>Ask about what&apos;s happening</h2>
      {turns.map((t, i) => (
        <div key={i} className="card">
          <p><strong>{t.question}</strong></p>
          {t.steps.length > 0 && !t.done && (
            <ul className="steps">{t.steps.map((s, j) => <li key={j}>{s}</li>)}</ul>
          )}
          {t.answer && <Cited text={t.answer} />}
          {t.error && <p className="error">{t.error}</p>}
          {t.done && !t.error && (
            <div className="row muted">
              {t.status === "degraded" && <span className="pill warn">partially verified</span>}
              {t.status === "done" && <span className="pill good">verified against sources</span>}
              {i === turns.length - 1 && t.runId && (
                <>
                  <button disabled={t.rated !== undefined} onClick={() => rate(1)}>👍</button>
                  <button disabled={t.rated !== undefined} onClick={() => rate(-1)}>👎</button>
                </>
              )}
            </div>
          )}
        </div>
      ))}
      <form onSubmit={ask} className="row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Will anything affect my commute this week?"
          style={{ flex: 1 }}
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}>Stop</button>
        ) : (
          <button className="primary" type="submit">Ask</button>
        )}
      </form>
    </main>
  );
}

export default function Page() {
  return (
    <RequireAuth>
      <Chat />
    </RequireAuth>
  );
}
