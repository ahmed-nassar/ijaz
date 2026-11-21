// Formatting for the River page: Gulf time (UTC+3), Western digits, Gregorian calendar.

// "ar-SA" would switch to the Hijri calendar; the -u- extension pins digits and calendar.
const LOCALE = "ar-u-nu-latn-ca-gregory";
export const TIME_ZONE = "Asia/Riyadh"; // UTC+3 all year, same clock as Istanbul

const clock = new Intl.DateTimeFormat(LOCALE, { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const day = new Intl.DateTimeFormat(LOCALE, { timeZone: TIME_ZONE, weekday: "long", day: "numeric", month: "long" });
const hourKey = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
});

export function clockTime(iso: string): string {
  return clock.format(new Date(iso));
}

/** Heading for the hour an item falls in, e.g. "السبت 21 نوفمبر، 14:00". */
export function hourHeading(iso: string): string {
  const date = new Date(iso);
  const hour = clock.formatToParts(date).find((p) => p.type === "hour")?.value ?? "00";
  return `${day.format(date)}، ${hour}:00`;
}

export function groupByHour<T extends { time: string }>(items: T[]): { key: string; heading: string; items: T[] }[] {
  const groups: { key: string; heading: string; items: T[] }[] = [];
  for (const item of items) {
    const key = hourKey.format(new Date(item.time));
    const last = groups.at(-1);
    if (last && last.key === key) last.items.push(item);
    else groups.push({ key, heading: hourHeading(item.time), items: [item] });
  }
  return groups;
}

/** "+1 نسخة", "+2 نسختان", "+3 نسخ" … "+10 نسخ", "+11 نسخة" (Arabic number agreement). */
export function copiesLabel(n: number): string {
  return `+${n} ${copiesWord(n)}`;
}

export function copiesWord(n: number): string {
  return n === 2 ? "نسختان" : n >= 3 && n <= 10 ? "نسخ" : "نسخة";
}

export type Run = { text: string; latin: boolean };

// A run of Latin words, figures and the symbols that travel with them: "Aramco", "1.2%", "$5bn", "2.2".
const LATIN_RUN = /[A-Za-z0-9$€£%][A-Za-z0-9$€£%.,:'’&/+-]*(?:\s+[A-Za-z0-9$€£%][A-Za-z0-9$€£%.,:'’&/+-]*)*/g;

/** Split an Arabic headline into Arabic text and Latin runs, so each Latin run can sit in its own <bdi>. */
export function splitRuns(text: string): Run[] {
  const runs: Run[] = [];
  let at = 0;
  for (const match of text.matchAll(LATIN_RUN)) {
    let value = match[0];
    const trailing = value.match(/[.,:'’/+-]+$/)?.[0] ?? ""; // sentence punctuation stays with the Arabic text
    value = value.slice(0, value.length - trailing.length);
    if (!value) continue;
    const start = match.index!;
    if (start > at) runs.push({ text: text.slice(at, start), latin: false });
    runs.push({ text: value, latin: true });
    at = start + value.length;
  }
  if (at < text.length) runs.push({ text: text.slice(at), latin: false });
  return runs;
}

const RTL_LANGUAGES = new Set(["ar", "fa", "ur", "he", "ckb"]);
export function isRtl(language: string): boolean {
  return RTL_LANGUAGES.has(language);
}
