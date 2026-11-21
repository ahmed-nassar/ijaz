// Renders the River to a complete HTML document. Called from pages/api/river.ts.
//
// Why not an App Router page: an App Router page always ships the React runtime to the browser,
// and this page must send no JavaScript at all (spec section 14). App Router route handlers may
// not import react-dom/server, so the page is rendered here with renderToStaticMarkup and served
// at /river through a rewrite in next.config.ts. The Content-Security-Policy forbids scripts.
import { renderToStaticMarkup } from "react-dom/server";
import { ErrorPage, RiverPage } from "../components/River";
import { ApiError, fetchFilters, fetchRiver, readFilters } from "./api";

export const SECURITY_HEADERS: Record<string, string> = {
  "Content-Security-Policy":
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src 'self'; " +
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
};

export type Rendered = { status: number; cacheControl: string; body: string };

export async function renderRiver(params: URLSearchParams): Promise<Rendered> {
  const filters = readFilters(params);
  const cursor = params.get("cursor") || undefined;
  const [river, options] = await Promise.allSettled([fetchRiver(filters, cursor), fetchFilters()]);

  if (river.status === "rejected") {
    const badCursor = river.reason instanceof ApiError && river.reason.status === 400;
    if (!badCursor) console.error("river: API request failed", river.reason);
    const message = badCursor ? "رابط الصفحة غير صالح." : "تعذّر تحميل الأخبار الآن. حاول مرة أخرى بعد قليل.";
    return {
      status: badCursor ? 400 : 503,
      cacheControl: "no-store",
      body: "<!DOCTYPE html>" + renderToStaticMarkup(<ErrorPage filters={filters} message={message} />),
    };
  }
  if (options.status === "rejected") console.error("river: filter options failed", options.reason);

  const markup = renderToStaticMarkup(
    <RiverPage
      items={river.value.items}
      nextCursor={river.value.next_cursor}
      filters={filters}
      options={options.status === "fulfilled" ? options.value : null}
      isFirstPage={!cursor}
    />,
  );
  // Browsers keep it 30 s; the CDN keeps it 60 s and may serve it stale while refreshing.
  return { status: 200, cacheControl: "public, max-age=30, s-maxage=60, stale-while-revalidate=60", body: "<!DOCTYPE html>" + markup };
}
