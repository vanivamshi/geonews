"use client";

import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { auth } from "@/lib/api";

const subscribe = () => () => {};

export default function AdminGate({ children }: { children: React.ReactNode }) {
  const stored = useSyncExternalStore(subscribe, () => auth.adminToken(), () => null);
  const [token, setToken] = useState<string | null>(null);
  const [value, setValue] = useState("");

  if (!(token ?? stored)) {
    return (
      <main>
        <div className="card">
          <h2>Admin</h2>
          <label htmlFor="admin">Admin token</label>
          <div className="row">
            <input id="admin" type="password" value={value} onChange={(e) => setValue(e.target.value)} style={{ flex: 1 }} />
            <button className="primary" onClick={() => { auth.setAdminToken(value); setToken(value); }}>Enter</button>
          </div>
        </div>
      </main>
    );
  }
  return (
    <main className="wide">
      <div className="row" style={{ marginBottom: 16 }}>
        <Link href="/admin">Metrics</Link>
        <Link href="/admin/runs">Traces</Link>
        <Link href="/admin/evals">Evals</Link>
        <Link href="/admin/label">Labeling</Link>
      </div>
      {children}
    </main>
  );
}
