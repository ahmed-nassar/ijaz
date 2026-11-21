// Inlined into the page head: one request for the document, no render-blocking stylesheet.
// Logical properties (inline-start/end, block) only, so the same rules serve a left-to-right site.
export const STYLES = `
:root{
  --bg:#ffffff;--text:#1b1b1b;--muted:#545454;--rule:#dedede;--link:#0b3a8c;--visited:#5a2a82;
  --accent:#8a1c1c;--chip-bg:#eef1f6;--chip-text:#2a3444;--focus:#0b57d0;
  color-scheme:light dark;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#121212;--text:#ececec;--muted:#b0b0b0;--rule:#333;--link:#9ec1ff;--visited:#d0b0f0;
  --accent:#ff9c8f;--chip-bg:#23272f;--chip-text:#d6dbe4;--focus:#8ab4f8;
}}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:"IBM Plex Sans Arabic",system-ui,"Segoe UI",Tahoma,sans-serif;font-size:1.0625rem;line-height:1.75}
a{color:var(--link);text-underline-offset:.2em}
a:visited{color:var(--visited)}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:2px}
.skip{position:absolute;inset-inline-start:1rem;inset-block-start:-3rem;background:var(--bg);padding:.5rem 1rem;z-index:1}
.skip:focus{inset-block-start:.5rem}
.wrap{max-inline-size:52rem;margin-inline:auto;padding-inline:1rem}
header.site{border-block-end:3px solid var(--accent)}
header.site .wrap{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;padding-block:.75rem}
.brand{font-weight:600;font-size:1.35rem;color:var(--text);text-decoration:none}
.brand:visited{color:var(--text)}
.tagline{color:var(--muted);font-size:.9rem;margin:0}
h1{font-size:1.5rem;font-weight:600;margin-block:1.25rem .5rem}
form.filters{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:end;padding-block:.75rem;border-block-end:1px solid var(--rule)}
form.filters label{display:flex;flex-direction:column;font-size:.85rem;color:var(--muted);min-inline-size:8.5rem;flex:1 1 8.5rem}
form.filters select{font:inherit;font-size:1rem;color:var(--text);background:var(--bg);border:1px solid var(--muted);border-radius:4px;padding:.3rem .4rem;max-inline-size:100%}
form.filters button{font:inherit;font-weight:600;color:#fff;background:var(--link);border:0;border-radius:4px;padding:.4rem 1.1rem;cursor:pointer}
@media (prefers-color-scheme:dark){form.filters button{color:#0b1a33}}
.reset{font-size:.9rem;align-self:center}
section.hour h2{font-size:.95rem;font-weight:600;color:var(--muted);margin:1.5rem 0 .25rem;padding-block-end:.25rem;border-block-end:1px solid var(--rule)}
ol.items{list-style:none;margin:0;padding:0}
ol.items>li{padding-block:.4rem;border-block-end:1px solid var(--rule);line-height:1.65}
ol.items>li:last-child{border-block-end:0}
.time{color:var(--muted);font-size:.9rem;font-variant-numeric:tabular-nums;margin-inline-end:.4rem}
.publisher{font-size:.92rem;color:var(--muted);margin-inline-end:.15rem}
.headline{font-weight:600;overflow-wrap:anywhere}
/* A left-to-right headline inside a right-to-left row stays one block, so a wrapped English line still reads in order */
.headline[dir=ltr]{display:inline-block;max-inline-size:100%}
.lang{display:inline-block;font-size:.72rem;font-weight:600;letter-spacing:.04em;line-height:1.4;color:var(--chip-text);background:var(--chip-bg);
  border-radius:3px;padding-inline:.35rem;margin-inline-start:.4rem;vertical-align:.1em;text-decoration:none}
details.copies{display:inline;margin-inline-start:.5rem;font-size:.85rem}
details.copies[open]{display:block;margin-inline-start:0}
details.copies summary{display:inline;white-space:nowrap;cursor:pointer;color:var(--muted);list-style:none}
details.copies summary::-webkit-details-marker{display:none}
details.copies summary::before{content:"▸ "}
[dir=rtl] details.copies summary::before{content:"◂ "}
details.copies[open] summary::before{content:"▾ "}
details.copies ul{margin:.25rem 0 0;padding-inline-start:1.25rem}
nav.pager{display:flex;justify-content:space-between;gap:1rem;padding-block:1.5rem;font-weight:600}
.empty,.error{padding-block:2rem;color:var(--muted)}
footer.site{border-block-start:1px solid var(--rule);color:var(--muted);font-size:.85rem;padding-block:1rem 2rem}
.sr-only{position:absolute;inline-size:1px;block-size:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media (max-width:30rem){
  body{font-size:1rem}
}
`;
