"use client";

import ReactMarkdown from "react-markdown";

/** Render markdown with [A123] citations turned into links to the article's story page. */
export default function Cited({ text }: { text: string }) {
  const linked = text.replace(/\[(A\d+(?:\s*,\s*A\d+)*)\]/g, (_, group: string) =>
    group
      .split(/\s*,\s*/)
      .map((id) => `[[${id}]](/articles/${id})`)
      .join(" "),
  );
  return (
    <div className="markdown">
      <ReactMarkdown>{linked}</ReactMarkdown>
    </div>
  );
}
