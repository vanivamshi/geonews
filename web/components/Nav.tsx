"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "Feed"],
  ["/chat", "Ask"],
  ["/settings", "Settings"],
  ["/admin", "Admin"],
] as const;

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="top">
      <Link href="/" className="brand">GeoNews</Link>
      {LINKS.map(([href, label]) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} className={active ? "active" : undefined}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
