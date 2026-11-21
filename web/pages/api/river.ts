// Serves the River HTML (reached at /river through a rewrite). See lib/render-river.tsx.
import type { NextApiRequest, NextApiResponse } from "next";
import { renderRiver, SECURITY_HEADERS } from "../../lib/render-river";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("Allow", "GET, HEAD");
    res.status(405).end();
    return;
  }
  const params = new URL(req.url ?? "/", "http://localhost").searchParams;
  const page = await renderRiver(params);
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(key, value);
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", page.cacheControl);
  res.status(page.status).send(page.body);
}
