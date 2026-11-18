import type { NextConfig } from "next";

// API traffic stays on Ahead's origin. Only the Next.js server talks to FastAPI.
// In local development, the backend binds to IPv4 loopback deliberately.
// For hosted deployments, configure AHEAD_API_INTERNAL_URL to the backend HTTPS URL
// or a trusted private service address; never expose this URL to client JS.
const backend = (process.env.AHEAD_API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  devIndicators: false,
  // Defense-in-depth browser headers. A stricter script CSP needs explicit
  // verification against Plaid Link and Next.js in a real browser before launch.
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "Permissions-Policy", value: "geolocation=(),camera=()" },
    ] }];
  },
  async rewrites() {
    return [{ source: "/api/ahead/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
