"use client";

import { useEffect, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { api, auth } from "@/lib/api";

type Profile = {
  home_label: string | null;
  radius_km: number;
  interests: string[];
  follows: string[];
  timezone: string;
  channels: string[];
  memory: string;
};

const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

function Settings() {
  const [p, setP] = useState<Profile | null>(null);
  const [interests, setInterests] = useState("");
  const [follows, setFollows] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api<Profile>("/me/profile").then((pr) => {
      setP(pr);
      setInterests(pr.interests.join(", "));
      setFollows(pr.follows.join(", "));
    });
  }, []);

  if (!p) return <main><p className="muted">Loading…</p></main>;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setMsg("Saving…");
    try {
      const saved = await api<Profile>("/me/profile", {
        method: "PUT",
        body: JSON.stringify({ ...p, interests: split(interests), follows: split(follows) }),
      });
      setP(saved);
      setMsg("Saved.");
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  const toggle = (ch: string) =>
    setP({ ...p, channels: p.channels.includes(ch) ? p.channels.filter((c) => c !== ch) : [...p.channels, ch] });

  return (
    <main>
      <h2>Settings</h2>
      <form className="card" onSubmit={save}>
        <label htmlFor="home">Home area</label>
        <input id="home" value={p.home_label ?? ""} placeholder="e.g. Oakland, CA"
          onChange={(e) => setP({ ...p, home_label: e.target.value })} />
        <label htmlFor="radius">Radius (km)</label>
        <input id="radius" type="number" min={1} max={1000} value={p.radius_km}
          onChange={(e) => setP({ ...p, radius_km: Number(e.target.value) })} />
        <label htmlFor="interests">Interests (comma-separated)</label>
        <input id="interests" value={interests} placeholder="housing, transit, schools"
          onChange={(e) => setInterests(e.target.value)} />
        <label htmlFor="follows">Follow (people, organizations, places)</label>
        <input id="follows" value={follows} placeholder="BART, City Council"
          onChange={(e) => setFollows(e.target.value)} />
        <label htmlFor="tz">Timezone</label>
        <input id="tz" value={p.timezone} onChange={(e) => setP({ ...p, timezone: e.target.value })} />
        <label>Daily briefing delivery</label>
        <div className="row">
          {["web", "email", "telegram"].map((ch) => (
            <label key={ch} style={{ fontWeight: 400, margin: 0 }}>
              <input type="checkbox" style={{ width: "auto" }} checked={p.channels.includes(ch)}
                onChange={() => toggle(ch)} /> {ch}
            </label>
          ))}
        </div>
        <p className="row">
          <button className="primary" type="submit">Save</button>
          <span className="muted">{msg}</span>
        </p>
      </form>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>What GeoNews remembers</h3>
        <pre>{p.memory || "Nothing yet. Tell GeoNews about your commute or interests in chat."}</pre>
        <p className="row">
          <button onClick={async () => { await api("/me/memory", { method: "DELETE" }); setP({ ...p, memory: "" }); }}>
            Clear memory
          </button>
          <button onClick={() => { auth.setToken(null); window.location.assign(window.location.origin); }}>Sign out</button>
        </p>
      </div>
    </main>
  );
}

export default function Page() {
  return (
    <RequireAuth>
      <Settings />
    </RequireAuth>
  );
}
