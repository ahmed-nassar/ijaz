import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/river", destination: "/api/river" }];
  },
  async redirects() {
    // The homepage is a later increment; until then the River is the front page.
    return [{ source: "/", destination: "/river", permanent: false }];
  },
  async headers() {
    return [{ source: "/fonts/:file*", headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }] }];
  },
};

export default config;
