"""Made-up sample articles for local development and tests. Never run against production.

Publishers and headlines are invented. They cover the cases the River page must render:
Arabic, English and Turkish titles, Latin names inside Arabic text, very long titles,
syndicated copies, articles without a publish time, and a publish time later than fetch time.
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.config import get_settings
from collector.db import init_db, make_engine, session_factory
from collector.models import Article, Publisher, Source
from collector.urls import url_hash

PUBLISHERS = [
    # slug, name, name_ar, country, language
    ("sample-gulf-daily", "Gulf Daily (sample)", "الخليج اليومية (عينة)", "SA", "ar"),
    ("sample-nile-post", "Nile Post (sample)", "بريد النيل (عينة)", "EG", "ar"),
    ("sample-emirates-wire", "Emirates Wire (sample)", "أخبار الإمارات (عينة)", "AE", "ar"),
    ("sample-levant-times", "Levant Times (sample)", "أخبار الشام (عينة)", "LB", "ar"),
    ("sample-bosphorus", "Bosphorus Haber (sample)", None, "TR", "tr"),
    ("sample-mideast-monitor", "Mideast Monitor (sample)", None, "AE", "en"),
]

TITLES = {
    "ar": [
        "وزير الطاقة السعودي: أسواق النفط تحتاج إلى استقرار في الإمدادات خلال الربع المقبل",
        "البنك المركزي المصري يبقي أسعار الفائدة دون تغيير للمرة الثالثة",
        "قمة خليجية في الرياض لبحث التكامل الاقتصادي وربط شبكات الكهرباء",
        "شركة Aramco توقع اتفاقية مع Microsoft لتطوير مراكز بيانات في المنطقة",
        "ارتفاع مؤشر تداول 1.2% بدعم من أسهم البنوك",
        "مطار دبي الدولي يستقبل 45 مليون مسافر في تسعة أشهر",
        "الأمم المتحدة تدعو إلى وقف إطلاق النار وإدخال المساعدات الإنسانية دون عوائق",
        "افتتاح المرحلة الأولى من مشروع NEOM للطاقة المتجددة بقدرة 2.2 غيغاواط",
        "وزارة الصحة: حملة تطعيم وطنية تستهدف خمسة ملايين طفل قبل نهاية العام",
        "تقرير: الاستثمار الأجنبي المباشر في الإمارات يسجل مستوى قياسياً في 2026 بدعم من قطاعي التقنية والخدمات المالية والعقارات، وسط توقعات باستمرار النمو",
    ],
    "en": [
        "Saudi wealth fund raises $5bn in second green bond sale this year",
        "Egypt and IMF reach staff-level agreement on fourth review",
        "UAE central bank keeps rates steady after Fed decision",
        "Qatar LNG expansion on track for first cargo in 2027, minister says",
    ],
    "tr": [
        "Merkez Bankası politika faizini sabit tuttu",
        "İstanbul'da Körfez yatırımcılarıyla enerji zirvesi düzenlendi",
        "Türkiye ile BAE arasında yeni ticaret anlaşması imzalandı",
    ],
}


def seed(session: Session, count: int = 120, *, now: datetime | None = None, rng_seed: int = 7) -> None:
    """Insert ``count`` sample articles spread over the last ``count / 6`` hours."""
    rng = random.Random(rng_seed)
    now = now or datetime.now(timezone.utc)
    publishers, sources = [], []
    for slug, name, name_ar, country, language in PUBLISHERS:
        pub = session.scalar(select(Publisher).where(Publisher.slug == slug))
        if pub is None:
            pub = Publisher(slug=slug, name=name, name_ar=name_ar, website=f"https://{slug}.example", country=country)
            session.add(pub)
            session.flush()
        src = session.scalar(select(Source).where(Source.slug == slug))
        if src is None:
            src = Source(publisher_id=pub.id, slug=slug, feed_url=f"https://{slug}.example/feed", language=language)
            session.add(src)
            session.flush()
        publishers.append((pub, language))
        sources.append(src)

    start = session.scalar(select(Article.id).order_by(Article.id.desc()).limit(1)) or 0
    originals: list[Article] = []
    for n in range(count):
        index = rng.randrange(len(publishers))
        pub, language = publishers[index]
        fetched = now - timedelta(minutes=10 * (count - 1 - n) + rng.randrange(10))  # oldest first
        published: datetime | None = fetched - timedelta(minutes=rng.randrange(1, 30))
        if n % 17 == 5:
            published = None  # feed gave no time: River uses fetch time
        elif n % 23 == 7:
            published = fetched + timedelta(hours=3)  # future time: River ignores it
        title = rng.choice(TITLES[language])
        url = f"https://{pub.slug}.example/news/{start + n + 1}"
        syndicated_of = None
        if originals and n % 9 == 4:
            original = rng.choice(originals[-5:])
            if original.publisher_id != pub.id:
                syndicated_of, title = original.id, original.title
        article = Article(
            source_id=sources[index].id, publisher_id=pub.id, feed_url=url, feed_url_hash=url_hash(url),
            canonical_url=url, canonical_url_hash=url_hash(url), title=title, language=language,
            published_at=published, published_at_origin="feed" if published else None, fetched_at=fetched,
            page_check="skipped", fingerprint=rng.getrandbits(63), syndicated_of_id=syndicated_of,
        )
        session.add(article)
        session.flush()
        if syndicated_of is None:
            originals.append(article)
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Insert made-up sample articles (development only).")
    parser.add_argument("--count", type=int, default=120)
    args = parser.parse_args()
    engine = make_engine(get_settings().database_url)
    init_db(engine)
    with session_factory(engine)() as session:
        seed(session, args.count)
    print(f"inserted {args.count} sample articles")


if __name__ == "__main__":
    main()
