import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  // A self-contained server in .next/standalone, so the Docker image needs no node_modules.
  output: "standalone",
  async rewrites() {
    return [
      { source: "/river", destination: "/api/river" },
      // The homepage runs at /preview while story grouping is below its targets (spec section 15).
      { source: "/preview", destination: "/api/home" },
      { source: "/story/:id", destination: "/api/story?id=:id" },
    ];
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
