// The homepage and story pages (spec section 15), plain server-rendered markup like the River.
import type { HomeStory, Item, Outlet, StoryResponse } from "../lib/api";
import { clockTime, isRtl, reportsLabel, storyTime } from "../lib/format";
import { ArabicText, Copies, Document, Headline, LANGUAGE_LABEL, languageNames, SITE_NAME } from "./River";

// While grouping is below its targets the homepage lives at a preview address, hidden from search.
export const HOME_PATH = "/preview";
const HOME_FOOTER =
  "عناوين المصادر وروابطها ملك ناشريها، ونعرض العنوان ورابط المقال الأصلي فقط. العنوان فوق كل قصة تكتبه إيجاز آلياً من تقارير المصادر بعد مراجعة آلية، ويظهر عنوان المصدر تحته. الأوقات بتوقيت مكة المكرمة (UTC+3).";
const HOME_DESCRIPTION = "أهم أخبار الشرق الأوسط الآن، مجمّعة من مصادرها، مع روابط إلى المقالات الأصلية.";

function Title({ text, language }: { text: string; language: string }) {
  return language === "ar" ? <ArabicText text={text} /> : <>{text}</>;
}

function LanguageLabel({ language }: { language: string }) {
  const label = LANGUAGE_LABEL[language] ?? (isRtl(language) ? null : language.toUpperCase());
  if (!label) return null;
  return (
    <abbr className="lang" title={`بلغة: ${languageNames.of(language) ?? language}`} lang="en">{label}</abbr>
  );
}

function More({ outlets }: { outlets: Outlet[] }) {
  if (outlets.length === 0) return null;
  return (
    <>
      <span className="more-label">المزيد:</span>{" "}
      {outlets.map((o, i) => (
        <span key={o.url}>
          {i > 0 && "، "}
          <a href={o.url} target="_blank" rel="noopener"><bdi>{o.publisher_ar ?? o.publisher}</bdi></a>
        </span>
      ))}
    </>
  );
}

function StoryEntry({ story, now }: { story: HomeStory; now: Date }) {
  const { lead } = story;
  const rtl = isRtl(lead.language);
  const ours = story.headline_by === "ijaz";
  return (
    <li className="story-entry">
      <p className="meta">
        <bdi className="publisher">{lead.publisher_ar ?? lead.publisher}</bdi>
        {" · "}
        <time dateTime={lead.time}>{storyTime(lead.time, now)}</time>
      </p>
      <h2 className="story-title">
        {ours ? (
          <a className="headline" href={lead.url} target="_blank" rel="noopener">
            <ArabicText text={story.headline} />
            <span className="sr-only"> (يفتح مقال {lead.publisher_ar ?? lead.publisher} في نافذة جديدة)</span>
          </a>
        ) : (
          <Headline item={lead} />
        )}
      </h2>
      {ours && (
        // The headline above is written by إيجاز from all the reports; the publisher's own headline is kept visible.
        <p className="original">
          <span className="original-label">عنوان المصدر:</span>{" "}
          <span lang={lead.language} dir={rtl ? "rtl" : "ltr"}><Title text={lead.title} language={lead.language} /></span>
          <LanguageLabel language={lead.language} />
        </p>
      )}
      {!ours && story.lead_title_ar && <p className="original">{story.lead_title_ar}</p>}
      <p className="more">
        <More outlets={story.more} />
        {story.more.length > 0 && " · "}
        <a className="all" href={`/story/${story.id}`}>كل التغطية ({reportsLabel(story.article_count)})</a>
        <Copies copies={lead.copies} />
      </p>
    </li>
  );
}

export function HomePage({ stories, updated }: { stories: HomeStory[]; updated: string | null }) {
  const now = new Date();
  return (
    <Document title={`${SITE_NAME} | أهم الأخبار`} description={HOME_DESCRIPTION} noindex home={HOME_PATH} footer={HOME_FOOTER}>
      <div className="bar">
        <h1>أهم الأخبار</h1>
        <p className="updated">
          {updated && <>آخر تحديث <time dateTime={updated}>{clockTime(updated)}</time> · </>}
          نسخة تجريبية · <a href="/river">كل الأخبار</a>
        </p>
      </div>
      {stories.length === 0 && <p className="empty">لم تُجمَّع القصص بعد. <a href="/river">تصفّح كل الأخبار</a>.</p>}
      <ol className="stories">
        {stories.map((story) => <StoryEntry key={story.id} story={story} now={now} />)}
      </ol>
    </Document>
  );
}

function StoryArticle({ item, titleAr, now }: { item: Item; titleAr: string | null; now: Date }) {
  return (
    <li>
      <time className="time" dateTime={item.time}>{storyTime(item.time, now)}</time>
      <div className="row">
        <span className="publisher"><bdi>{item.publisher_ar ?? item.publisher}</bdi></span>{" "}
        <span className="story">
          <Headline item={item} />
          <Copies copies={item.copies} />
          {titleAr && <span className="translation">{titleAr}</span>}
        </span>
      </div>
    </li>
  );
}

export function StoryPage({ story }: { story: StoryResponse }) {
  const now = new Date();
  return (
    <Document title={`${story.headline} | ${SITE_NAME}`} description={story.headline} noindex home={HOME_PATH} footer={HOME_FOOTER}>
      <p className="back"><a href={HOME_PATH}>أهم الأخبار</a></p>
      <h1 className="story-page-title"><ArabicText text={story.headline} /></h1>
      <p className="updated">
        {reportsLabel(story.articles.length)} · أول تقرير <time dateTime={story.first_time}>{storyTime(story.first_time, now)}</time>
        {" · "}آخر تقرير <time dateTime={story.last_time}>{storyTime(story.last_time, now)}</time>
        {story.headline_by === "ijaz" && " · العنوان من إيجاز"}
      </p>
      <ol className="items story-articles">
        {story.articles.map((a) => <StoryArticle key={a.item.id} item={a.item} titleAr={a.title_ar} now={now} />)}
      </ol>
    </Document>
  );
}

export function NotFoundPage({ message }: { message: string }) {
  return (
    <Document title={`${SITE_NAME}`} noindex home={HOME_PATH} footer={HOME_FOOTER}>
      <p className="error" role="alert">{message} <a href={HOME_PATH}>أهم الأخبار</a></p>
    </Document>
  );
}
