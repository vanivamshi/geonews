const API = process.env.GEONEWS_API_URL || "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Same-origin proxy to the FastAPI backend: no CORS in production, and briefing links
  // (/r/<briefing>/<article>) hit the click-tracking redirect.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/:path*` },
      { source: "/r/:path*", destination: `${API}/r/:path*` },
    ];
  },
};

export default nextConfig;
