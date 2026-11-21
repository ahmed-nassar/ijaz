// The River page as plain server-rendered markup. No hooks, no event handlers, no client code.
import type { ReactNode } from "react";
import type { Filters, FilterOptions, Item } from "../lib/api";
import { FONT_FACE } from "../lib/font-face.generated";
import { clockTime, copiesWord, groupByHour, isRtl, splitRuns } from "../lib/format";
import { STYLES } from "../lib/styles";

// Placeholder until branding is decided (spec section 14).
export const SITE_NAME = process.env.SITE_NAME ?? "اسم الموقع";

const LANGUAGE_LABEL: Record<string, string> = { en: "EN", tr: "TR", fr: "FR" };
const languageNames = new Intl.DisplayNames(["ar"], { type: "language" });
const regionNames = new Intl.DisplayNames(["ar"], { type: "region" });

export function riverHref(filters: Filters, cursor?: string): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  if (cursor) params.set("cursor", cursor);
  const query = params.toString();
  return query ? `/river?${query}` : "/river";
}

function Document({ title, children }: { title: string; children: ReactNode }) {
  return (
    <html lang="ar" dir="rtl">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{title}</title>
        <meta name="description" content="كل الأخبار التي جمعناها من الناشرين، الأحدث أولاً، مع روابط إلى المصادر الأصلية." />
        <link rel="preload" href="/fonts/ibm-plex-sans-arabic-arabic-400-normal.woff2" as="font" type="font/woff2" crossOrigin="" />
        <style dangerouslySetInnerHTML={{ __html: FONT_FACE + STYLES }} />
      </head>
      <body>
        <a className="skip" href="#main">انتقل إلى المحتوى</a>
        <header className="site">
          <div className="wrap">
            <a className="brand" href="/river">{SITE_NAME}</a>
            <p className="tagline">أخبار الشرق الأوسط من مصادرها</p>
          </div>
        </header>
        <main id="main" className="wrap">{children}</main>
        <footer className="site">
          <div className="wrap">
            العناوين والروابط ملك ناشريها. نعرض العنوان ورابط المقال الأصلي فقط، والأوقات بتوقيت مكة المكرمة (UTC+3).
          </div>
        </footer>
      </body>
    </html>
  );
}

/** Arabic text with each Latin name or figure isolated, so it cannot reorder its neighbours. */
function ArabicText({ text }: { text: string }) {
  return (
    <>
      {splitRuns(text).map((run, i) =>
        run.latin ? <bdi key={i} dir="ltr">{run.text}</bdi> : run.text,
      )}
    </>
  );
}

function Headline({ item }: { item: Item }) {
  const rtl = isRtl(item.language);
  const label = LANGUAGE_LABEL[item.language] ?? (rtl ? null : item.language.toUpperCase());
  return (
    <>
      <a className="headline" href={item.url} target="_blank" rel="noopener" lang={item.language} dir={rtl ? "rtl" : "ltr"}>
        {item.language === "ar" ? <ArabicText text={item.title} /> : item.title}
        <span className="sr-only"> (يفتح في نافذة جديدة)</span>
      </a>
      {label && (
        <abbr className="lang" title={`بلغة: ${languageNames.of(item.language) ?? item.language}`} lang="en">
          {label}
        </abbr>
      )}
    </>
  );
}

function Copies({ copies }: { copies: Item["copies"] }) {
  if (copies.length === 0) return null;
  return (
    <details className="copies">
      {/* "+3" is isolated left-to-right, or the bidi algorithm shows it as "3+" */}
      <summary><bdi dir="ltr">+{copies.length}</bdi> {copiesWord(copies.length)}</summary>
      <ul>
        {copies.map((copy) => (
          <li key={copy.url}>
            <a href={copy.url} target="_blank" rel="noopener">
              <bdi>{copy.publisher_ar ?? copy.publisher}</bdi>
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}

function FilterForm({ options, filters }: { options: FilterOptions | null; filters: Filters }) {
  if (!options) return null;
  const active = Boolean(filters.lang || filters.country || filters.publisher);
  return (
    <form className="filters" method="get" action="/river" aria-label="تصفية الأخبار">
      <label>
        اللغة
        <select name="lang" defaultValue={filters.lang ?? ""}>
          <option value="">كل اللغات</option>
          {options.languages.map((code) => (
            <option key={code} value={code}>{languageNames.of(code) ?? code}</option>
          ))}
        </select>
      </label>
      <label>
        البلد
        <select name="country" defaultValue={filters.country ?? ""}>
          <option value="">كل البلدان</option>
          {options.countries
            .map((code) => ({ code, name: regionNames.of(code) ?? code }))
            .sort((a, b) => a.name.localeCompare(b.name, "ar"))
            .map(({ code, name }) => (
              <option key={code} value={code}>{name}</option>
            ))}
        </select>
      </label>
      <label>
        الناشر
        <select name="publisher" defaultValue={filters.publisher ?? ""}>
          <option value="">كل الناشرين</option>
          {options.publishers.map((p) => (
            <option key={p.slug} value={p.slug}>{p.name_ar ?? p.name}</option>
          ))}
        </select>
      </label>
      <button type="submit">عرض</button>
      {active && <a className="reset" href="/river">إلغاء التصفية</a>}
    </form>
  );
}

export function RiverPage(props: {
  items: Item[];
  nextCursor: string | null;
  filters: Filters;
  options: FilterOptions | null;
  isFirstPage: boolean;
}) {
  const { items, nextCursor, filters, options, isFirstPage } = props;
  const groups = groupByHour(items);
  return (
    <Document title={`كل الأخبار | ${SITE_NAME}`}>
      <h1>كل الأخبار</h1>
      <FilterForm options={options} filters={filters} />
      {groups.length === 0 && <p className="empty">لا توجد أخبار تطابق هذا الاختيار.</p>}
      {groups.map((group) => (
        <section className="hour" key={group.key} aria-labelledby={`h-${group.key}`}>
          <h2 id={`h-${group.key}`}>{group.heading}</h2>
          <ol className="items">
            {group.items.map((item) => (
              <li key={item.id}>
                {/* One line per story: time, publisher, headline (wraps only when it does not fit) */}
                <time className="time" dateTime={item.time}>{clockTime(item.time)}</time>{" "}
                <span className="publisher"><bdi>{item.publisher_ar ?? item.publisher}</bdi>:</span>{" "}
                <Headline item={item} />
                <Copies copies={item.copies} />
              </li>
            ))}
          </ol>
        </section>
      ))}
      {(nextCursor || !isFirstPage) && (
        <nav className="pager" aria-label="صفحات الأخبار">
          {!isFirstPage ? <a href={riverHref(filters)}>الأحدث</a> : <span />}
          {nextCursor && <a href={riverHref(filters, nextCursor)} rel="next">أقدم</a>}
        </nav>
      )}
    </Document>
  );
}

export function ErrorPage({ message, filters }: { message: string; filters: Filters }) {
  return (
    <Document title={`كل الأخبار | ${SITE_NAME}`}>
      <h1>كل الأخبار</h1>
      <p className="error" role="alert">{message} <a href={riverHref(filters)}>العودة إلى أحدث الأخبار</a></p>
    </Document>
  );
}
