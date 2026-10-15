/** @type {import('next').NextConfig} */

// In local dev the browser talks to the API through this same-origin proxy
// (/api/* on the frontend -> the FastAPI server), so requests are never
// cross-origin. That sidesteps CORS and browser privacy blockers that refuse
// cross-port requests to localhost. Set NEXT_PUBLIC_API_URL to bypass it.
const API_PROXY_TARGET = process.env.API_PROXY_TARGET || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_PROXY_TARGET}/:path*` }];
  },
};

module.exports = nextConfig;
