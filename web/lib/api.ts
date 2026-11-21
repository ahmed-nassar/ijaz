// Server-side client for the Python read API. Never runs in the browser.

export type Copy = { publisher: string; publisher_ar: string | null; url: string };
export type Item = {
  id: number;
  title: string;
  url: string;
  language: string;
  time: string;
  publisher_slug: string;
  publisher: string;
  publisher_ar: string | null;
  copies: Copy[];
};
export type RiverResponse = { items: Item[]; next_cursor: string | null };
export type FilterOptions = {
  languages: string[];
  countries: string[];
  publishers: { slug: string; name: string; name_ar: string | null }[];
};
export type Filters = { lang?: string; country?: string; publisher?: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const TIMEOUT_MS = 5000;

// Same patterns the API enforces; anything else is dropped rather than sent.
const PATTERNS: Record<keyof Filters, RegExp> = {
  lang: /^[a-z]{2,3}$/,
  country: /^[A-Za-z]{2}$/,
  publisher: /^[a-z0-9-]{1,100}$/,
};

export function readFilters(params: URLSearchParams): Filters {
  const filters: Filters = {};
  for (const key of Object.keys(PATTERNS) as (keyof Filters)[]) {
    const value = params.get(key);
    if (value && PATTERNS[key].test(value)) filters[key] = key === "country" ? value.toUpperCase() : value;
  }
  return filters;
}

async function get<T>(path: string, params: Record<string, string | undefined>): Promise<T> {
  const url = new URL(path, API_URL);
  for (const [key, value] of Object.entries(params)) if (value) url.searchParams.set(key, value);
  const response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS), cache: "no-store" });
  if (!response.ok) throw new ApiError(response.status, `${path} returned ${response.status}`);
  return (await response.json()) as T;
}

export function fetchRiver(filters: Filters, cursor?: string): Promise<RiverResponse> {
  return get<RiverResponse>("/api/river", { ...filters, cursor: cursor || undefined, limit: "50" });
}

export function fetchFilters(): Promise<FilterOptions> {
  return get<FilterOptions>("/api/river/filters", {});
}
