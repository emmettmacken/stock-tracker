

// Proxy all backend traffic through this Next app so the browser only ever talks
// to the Vercel origin. Cookies are then first-party to the Vercel domain
// (SameSite=Lax works; third-party cookie blocking is irrelevant).
//
// Override the upstream for local full-stack dev by setting BACKEND_ORIGIN
// (e.g. http://localhost:8000) in .env.local — otherwise `next dev` proxies to
// the deployed Railway backend below.
const BACKEND_ORIGIN =
  process.env.BACKEND_ORIGIN ||
  "https://stock-tracker-production-238e.up.railway.app";

const nextConfig = {
  async rewrites() {
    return [
      // Preserve the /api and /auth prefixes — the backend serves routes under
      // both (e.g. GET /api/quote/AAPL, POST /auth/login).
      { source: "/api/:path*", destination: `${BACKEND_ORIGIN}/api/:path*` },
      { source: "/auth/:path*", destination: `${BACKEND_ORIGIN}/auth/:path*` },
    ];
  },
  async redirects() {
    return [
      { source: "/briefing", destination: "/automation", permanent: false },
    ];
  },
};

export default nextConfig;
