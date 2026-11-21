import assert from "node:assert/strict";
import { test } from "node:test";
import { clockTime, copiesLabel, groupByHour, hourHeading, splitRuns } from "./format.ts";

test("times are shown in UTC+3 with Western digits and the Gregorian calendar", () => {
  assert.equal(clockTime("2026-11-21T11:05:00+00:00"), "14:05");
  assert.equal(clockTime("2026-11-21T22:30:00+00:00"), "01:30");
  const heading = hourHeading("2026-11-21T11:05:00+00:00");
  assert.match(heading, /21/);
  assert.match(heading, /نوفمبر/);
  assert.match(heading, /14:00$/);
  assert.doesNotMatch(heading, /[٠-٩]/);
});

test("items are grouped by Gulf-time hour", () => {
  const groups = groupByHour([
    { time: "2026-11-21T11:59:00+00:00" },
    { time: "2026-11-21T11:01:00+00:00" },
    { time: "2026-11-21T10:59:00+00:00" },
    { time: "2026-11-20T21:30:00+00:00" }, // 00:30 on the 21st in UTC+3
    { time: "2026-11-20T20:30:00+00:00" }, // 23:30 on the 20th
  ]);
  assert.deepEqual(groups.map((g) => g.items.length), [2, 1, 1, 1]);
});

test("copies use Arabic number agreement", () => {
  assert.deepEqual([1, 2, 3, 10, 11, 100].map(copiesLabel), ["+1 نسخة", "+2 نسختان", "+3 نسخ", "+10 نسخ", "+11 نسخة", "+100 نسخة"]);
});

test("Latin names, figures and symbols are split out of Arabic headlines", () => {
  const latin = (t: string) => splitRuns(t).filter((r) => r.latin).map((r) => r.text);
  assert.deepEqual(latin("شركة Aramco توقع اتفاقية مع Microsoft لتطوير مراكز بيانات"), ["Aramco", "Microsoft"]);
  assert.deepEqual(latin("ارتفاع مؤشر تداول 1.2% بدعم من أسهم البنوك"), ["1.2%"]);
  assert.deepEqual(latin("صندوق يجمع $5bn في 2026."), ["$5bn", "2026"]);
  assert.deepEqual(latin("مشروع NEOM للطاقة بقدرة 2.2 غيغاواط"), ["NEOM", "2.2"]);
  assert.deepEqual(latin("خبر بلا أسماء لاتينية"), []);
  const text = "قمة Gulf Summit 2026: نتائج";
  assert.equal(splitRuns(text).map((r) => r.text).join(""), text);
});

test("story times show the weekday when not today, and report counts agree in number", async () => {
  const { storyTime, reportsLabel } = await import("./format.ts");
  const now = new Date("2026-11-21T12:00:00Z");
  assert.equal(storyTime("2026-11-21T11:05:00Z", now), "14:05");
  assert.match(storyTime("2026-11-20T11:05:00Z", now), /^الجمعة 14:05$/);
  assert.deepEqual([1, 2, 3, 10, 11].map(reportsLabel), ["تقرير واحد", "تقريران", "3 تقارير", "10 تقارير", "11 تقريرًا"]);
});
