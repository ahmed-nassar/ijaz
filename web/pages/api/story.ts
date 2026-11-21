// Serves a story page (reached at /story/<number> through a rewrite). See lib/render-home.tsx.
import type { NextApiRequest, NextApiResponse } from "next";
import { renderStory } from "../../lib/render-home";
import { SECURITY_HEADERS } from "../../lib/render-river";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("Allow", "GET, HEAD");
    res.status(405).end();
    return;
  }
  const id = typeof req.query.id === "string" ? req.query.id : null;
  const page = await renderStory(id);
  res.setHeader("Cache-Control", page.cacheControl);
  if (page.redirect) {
    res.redirect(page.status, page.redirect);
    return;
  }
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(key, value);
  res.setHeader("X-Robots-Tag", "noindex");
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.status(page.status).send(page.body);
}
