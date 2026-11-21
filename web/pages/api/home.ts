// Serves the homepage HTML (reached at /preview through a rewrite). See lib/render-home.tsx.
import type { NextApiRequest, NextApiResponse } from "next";
import { renderHome } from "../../lib/render-home";
import { SECURITY_HEADERS } from "../../lib/render-river";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("Allow", "GET, HEAD");
    res.status(405).end();
    return;
  }
  const page = await renderHome();
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(key, value);
  res.setHeader("X-Robots-Tag", "noindex"); // preview: hidden from search until it moves to /
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", page.cacheControl);
  res.status(page.status).send(page.body);
}
