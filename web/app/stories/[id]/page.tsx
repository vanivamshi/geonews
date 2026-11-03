"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { api, logEvent } from "@/lib/api";

type Story = {
  story_id: number;
  title: string;
  topic: string | null;
  articles: { id: string; title: string; source: string; url: string; published_at: string; summary: string }[];
};

function StoryView() {
  const { id } = useParams<{ id: string }>();
  const [story, setStory] = useState<Story | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const opened = Date.now();
    api<Story>(`/stories/${id}`).then(setStory).catch((e) => setError(e.message));
    return () => {
      // Dwell time is a stronger relevance signal than the click itself.
      logEvent({ kind: "dwell", story_id: Number(id), value: (Date.now() - opened) / 1000 });
    };
  }, [id]);

  if (error) return <main><p className="error">{error}</p></main>;
  if (!story) return <main><p className="muted">Loading…</p></main>;
  return (
    <main>
      <h2>{story.title}</h2>
      {story.topic && <span className="pill">{story.topic}</span>}
      <h3>Coverage timeline</h3>
      {story.articles.map((a) => (
        <div className="card" key={a.id}>
          <div className="muted">
            {new Date(a.published_at).toLocaleString()} · {a.source} · {a.id}
          </div>
          <a href={a.url} target="_blank" rel="noreferrer"><strong>{a.title}</strong></a>
          <p style={{ marginBottom: 0 }}>{a.summary}</p>
        </div>
      ))}
    </main>
  );
}

export default function Page() {
  return (
    <RequireAuth>
      <StoryView />
    </RequireAuth>
  );
}
