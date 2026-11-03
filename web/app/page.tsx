"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Cited from "@/components/Cited";
import RequireAuth from "@/components/RequireAuth";
import { api, logEvent } from "@/lib/api";

type FeedItem = {
  story_id: number;
  title: string;
  topic: string | null;
  article_count: number;
  distance_km: number;
  top_article: { id: string; source: string; url: string; summary: string; published_at: string };
};
type Briefing = { id?: number; body: string | null; created_at?: string };

function ago(iso: string) {
  const h = Math.round((Date.now() - new Date(iso).getTime()) / 3.6e6);
  return h < 1 ? "just now" : h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function Feed() {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [feed, brief] = await Promise.all([
        api<{ items: FeedItem[] }>("/feed"),
        api<Briefing>("/briefing/latest"),
      ]);
      setItems(feed.items);
      setBriefing(brief);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch
    load();
  }, [load]);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      const res = await api<{ body: string; briefing_id: number }>("/briefing/generate", { method: "POST" });
      setBriefing({ id: res.briefing_id, body: res.body });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <div className="card">
        <div className="row">
          <h2 style={{ margin: 0, marginRight: "auto" }}>Your briefing</h2>
          <button onClick={generate} disabled={busy}>{busy ? "Writing…" : "Refresh briefing"}</button>
        </div>
        {briefing?.body ? (
          <>
            {briefing.created_at && <p className="muted">{ago(briefing.created_at)}</p>}
            <Cited text={briefing.body} />
          </>
        ) : (
          <p className="muted">
            No briefing yet. Set your location in <Link href="/settings">Settings</Link>, then refresh.
          </p>
        )}
      </div>

      {error && <p className="error">{error}</p>}
      <h3>Near you</h3>
      {items === null && <p className="muted">Loading…</p>}
      {items?.length === 0 && <p className="muted">Nothing yet. Ingestion runs every 15 minutes.</p>}
      {items?.map((it) => (
        <div className="card" key={it.story_id}>
          <Link href={`/stories/${it.story_id}`} onClick={() => logEvent({ kind: "click", story_id: it.story_id, meta: { surface: "feed" } })}>
            <strong>{it.title}</strong>
          </Link>
          <p style={{ margin: "6px 0" }}>{it.top_article.summary}</p>
          <div className="row muted">
            <span>{it.top_article.source}</span>·<span>{ago(it.top_article.published_at)}</span>
            {it.topic && <span className="pill">{it.topic}</span>}
            {it.article_count > 1 && <span>{it.article_count} sources</span>}
            {it.distance_km < 5000 && <span>{it.distance_km} km</span>}
            <button style={{ marginLeft: "auto", padding: "2px 8px" }}
              onClick={() => {
                logEvent({ kind: "hide", story_id: it.story_id });
                setItems((xs) => xs?.filter((x) => x.story_id !== it.story_id) ?? null);
              }}>
              Not relevant
            </button>
          </div>
        </div>
      ))}
    </main>
  );
}

export default function Page() {
  return (
    <RequireAuth>
      <Feed />
    </RequireAuth>
  );
}
