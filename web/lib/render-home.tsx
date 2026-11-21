// Renders the homepage and story pages to complete HTML documents, like render-river.tsx.
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { HomePage, NotFoundPage, StoryPage } from "../components/Home";
import { ApiError, fetchHome, fetchStory } from "./api";
import type { Rendered } from "./render-river";

const CACHE = "public, max-age=30, s-maxage=60, stale-while-revalidate=60";

function page(element: ReactElement, status = 200, cacheControl = CACHE): Rendered {
  return { status, cacheControl, body: "<!DOCTYPE html>" + renderToStaticMarkup(element) };
}

export async function renderHome(): Promise<Rendered> {
  try {
    const home = await fetchHome();
    return page(<HomePage stories={home.stories} updated={home.updated} />);
  } catch (error) {
    console.error("home: API request failed", error);
    return page(<NotFoundPage message="تعذّر تحميل الأخبار الآن. حاول مرة أخرى بعد قليل." />, 503, "no-store");
  }
}

export type StoryRendered = Rendered & { redirect?: string };

export async function renderStory(rawId: string | null): Promise<StoryRendered> {
  const id = rawId && /^[1-9][0-9]{0,9}$/.test(rawId) ? Number(rawId) : NaN;
  if (!Number.isSafeInteger(id) || id > 2 ** 31 - 1) {
    return page(<NotFoundPage message="لا توجد قصة بهذا الرقم." />, 404, "no-store");
  }
  try {
    const story = await fetchStory(id);
    if (story.redirect_to) return { status: 301, cacheControl: CACHE, body: "", redirect: `/story/${story.redirect_to}` };
    return page(<StoryPage story={story} />);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return page(<NotFoundPage message="لا توجد قصة بهذا الرقم." />, 404, "public, max-age=60");
    }
    console.error("story: API request failed", error);
    return page(<NotFoundPage message="تعذّر تحميل القصة الآن. حاول مرة أخرى بعد قليل." />, 503, "no-store");
  }
}
