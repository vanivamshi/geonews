"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { api } from "@/lib/api";

type Article = {
  id: string;
  title: string;
  source: string;
  url: string;
  published_at: string;
  story_id: number | null;
  summary: string;
};

function ArticleView() {
  const { id } = useParams<{ id: string }>();
  const [a, setA] = useState<Article | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Article>(`/articles/${id}`).then(setA).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <main><p className="error">{error}</p></main>;
  if (!a) return <main><p className="muted">Loading…</p></main>;
  return (
    <main>
      <div className="card">
        <div className="muted">{a.id} · {a.source} · {new Date(a.published_at).toLocaleString()}</div>
        <h2>{a.title}</h2>
        <p>{a.summary}</p>
        <div className="row">
          <a href={a.url} target="_blank" rel="noreferrer">Read at source ↗</a>
          {a.story_id && <Link href={`/stories/${a.story_id}`}>All coverage of this story</Link>}
        </div>
      </div>
    </main>
  );
}

export default function Page() {
  return (
    <RequireAuth>
      <ArticleView />
    </RequireAuth>
  );
}
