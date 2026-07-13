/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "camera=(), geolocation=(), payment=()" },
        { key: "Content-Security-Policy", value: "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" }
      ]
    }];
  },
  async rewrites() {
    const api = process.env.API_INTERNAL_URL || "http://api:8000";
    return [
      { source: "/api/docs", destination: `${api}/docs` },
      { source: "/api/health", destination: `${api}/health` },
      { source: "/api/ready", destination: `${api}/ready` },
      { source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }
    ];
  }
};
export default nextConfig;
