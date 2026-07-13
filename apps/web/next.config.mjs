/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    const api = process.env.API_INTERNAL_URL || "http://api:8000";
    return [
      { source: "/api/docs", destination: `${api}/docs` },
      { source: "/api/health", destination: `${api}/health` },
      { source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }
    ];
  }
};
export default nextConfig;

