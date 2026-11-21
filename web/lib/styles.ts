// Inlined into the page head: one request for the document, no render-blocking stylesheet.
// Logical properties (inline-start/end, block) only, so the same rules serve a left-to-right site.
export const STYLES = `
:root{
  --bg:#ffffff;--text:#16181b;--muted:#5f6368;--faint:#6b7077;--rule:#ececec;--link:#0b3a8c;--visited:#4b4f55;
  --focus:#0b57d0;
  color-scheme:light dark;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#111214;--text:#e8e9eb;--muted:#a4a8ae;--faint:#8b9096;--rule:#25272b;--link:#9ec1ff;--visited:#b9bdc3;
  --focus:#8ab4f8;
}}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:"IBM Plex Sans Arabic",system-ui,"Segoe UI",Tahoma,sans-serif;font-size:.9375rem;line-height:1.6}
a{color:var(--link);text-underline-offset:.2em}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:2px}
.skip{position:absolute;inset-inline-start:1rem;inset-block-start:-3rem;background:var(--bg);padding:.5rem 1rem;z-index:1}
.skip:focus{inset-block-start:.5rem}
.wrap{max-inline-size:50rem;margin-inline:auto;padding-inline:1rem}
header.site{border-block-end:1px solid var(--rule)}
header.site .wrap{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;padding-block:.9rem}
.brand{font-weight:600;font-size:1.05rem;color:var(--text);text-decoration:none}
.tagline{color:var(--faint);font-size:.8rem;margin:0}
.bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem 1rem;margin-block:1.5rem .5rem}
h1{font-size:1rem;font-weight:600;margin:0}
details.filter-toggle summary{font-size:.8rem;color:var(--muted);cursor:pointer;list-style:none}
details.filter-toggle summary::-webkit-details-marker{display:none}
details.filter-toggle summary::after{content:" ▾";font-size:.7em}
details.filter-toggle[open]{flex-basis:100%}
details.filter-toggle[open] summary{text-align:end}
details.filter-toggle[open] summary::after{content:" ▴"}
form.filters{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;margin-block-start:.6rem}
form.filters select{font:inherit;font-size:.8rem;color:var(--muted);background:var(--bg);border:1px solid var(--rule);border-radius:999px;
  padding:.2rem .7rem;max-inline-size:11rem}
form.filters button{font:inherit;font-size:.8rem;color:var(--text);background:none;border:1px solid var(--muted);border-radius:999px;padding:.2rem .8rem;cursor:pointer}
.reset{font-size:.8rem;color:var(--muted)}
section.hour h2{font-size:.8rem;font-weight:600;color:var(--text);margin:1.75rem 0 .4rem;padding-block-end:.35rem;border-block-end:1px solid var(--rule)}
ol.items{list-style:none;margin:0;padding:0}
ol.items>li{display:grid;grid-template-columns:2.8rem minmax(0,1fr);gap:0 .75rem;align-items:baseline;padding-block:.3rem}
.row{display:grid;grid-template-columns:9.5rem minmax(0,1fr);gap:0 .75rem;align-items:baseline}
.time{color:var(--faint);font-size:.75rem;font-variant-numeric:tabular-nums}
.publisher{font-size:.8rem;color:var(--muted);overflow-wrap:anywhere}
.headline{font-weight:500;color:var(--text);overflow-wrap:anywhere;text-decoration:none}
.headline:visited{color:var(--visited)}
.headline:hover,.headline:focus-visible{color:var(--link);text-decoration:underline}
.lang{font-size:.68rem;font-weight:500;letter-spacing:.06em;color:var(--faint);margin-inline-start:.4rem;text-decoration:none}
details.copies{display:inline;margin-inline-start:.5rem;font-size:.75rem}
details.copies[open]{display:block;margin-inline-start:0}
details.copies summary{display:inline;white-space:nowrap;cursor:pointer;color:var(--faint);list-style:none}
details.copies summary::-webkit-details-marker{display:none}
details.copies ul{margin:.2rem 0 .3rem;padding-inline-start:1rem;list-style:none}
details.copies a{color:var(--muted);text-decoration:none}
details.copies a:hover{color:var(--link);text-decoration:underline}
nav.pager{display:flex;justify-content:space-between;gap:1rem;padding-block:2rem;font-size:.85rem}
nav.pager a{text-decoration:none}
.empty,.error{padding-block:2rem;color:var(--muted)}
footer.site{color:var(--faint);font-size:.75rem;padding-block:1.5rem 2.5rem;border-block-start:1px solid var(--rule)}
.sr-only{position:absolute;inline-size:1px;block-size:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media (max-width:40rem){
  /* Too narrow for three columns: headlines align under the publisher name instead */
  .row{display:block}
  .publisher{display:block;font-size:.75rem;line-height:1.5}
  ol.items>li{padding-block:.45rem}
  .tagline{display:none}
}
`;
