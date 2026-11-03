"use client";

import { useState, useSyncExternalStore } from "react";
import { api, auth } from "@/lib/api";

const subscribe = () => () => {};

/** Gate a page behind a user token; offers signup or pasting an existing token. */
export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const stored = useSyncExternalStore(subscribe, () => auth.token(), () => null);
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [paste, setPaste] = useState("");
  const [error, setError] = useState("");
  const [issued, setIssued] = useState("");

  if (token ?? stored) return <>{children}</>;

  async function signup(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api<{ token: string }>("/auth/signup", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      auth.setToken(res.token);
      setIssued(res.token);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (issued) {
    return (
      <main>
        <div className="card">
          <h2>You&apos;re in</h2>
          <p>Save this token to sign in on other devices. It won&apos;t be shown again.</p>
          <pre>{issued}</pre>
          <p><button className="primary" onClick={() => setToken(issued)}>Continue</button></p>
        </div>
      </main>
    );
  }

  return (
    <main>
      <div className="card">
        <h2>Get your local briefing</h2>
        <form onSubmit={signup}>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <p><button className="primary" type="submit">Sign up</button></p>
        </form>
        <label htmlFor="token">Already have a token?</label>
        <div className="row">
          <input id="token" value={paste} onChange={(e) => setPaste(e.target.value)} style={{ flex: 1 }} />
          <button
            onClick={() => {
              auth.setToken(paste.trim());
              setToken(paste.trim());
            }}
          >
            Sign in
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </div>
    </main>
  );
}
