"""CSV and HTML report generation."""

import csv
import os
from dataclasses import asdict
from datetime import datetime
from urllib.parse import quote_plus

from jinja2 import Environment

NYAA = "https://nyaa.si/?f=0&c=0_0&q={q}&s=seeders&o=desc"


def nyaa_url(title):
    return NYAA.format(q=quote_plus(title))


# ==========================
# CSV
# ==========================

def _write_csv(path, rows):
    if not rows:
        # Still write the file so downstream tooling sees an empty result
        # rather than a missing one.
        open(path, "w", encoding="utf-8").close()
        return

    fieldnames = list(asdict(rows[0]).keys())

    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def export_csvs(results, sonarr_results, output_dir):
    owned = sorted([r for r in results if r.owned], key=lambda r: r.rank)
    missing = sorted([r for r in results if not r.owned],
                     key=lambda r: r.recommendation_score, reverse=True)

    _write_csv(os.path.join(output_dir, "owned.csv"), owned)
    _write_csv(os.path.join(output_dir, "missing.csv"), missing)

    migrated = sorted([r for r in sonarr_results if r.migrated],
                      key=lambda r: r.title)
    remaining = sorted([r for r in sonarr_results if not r.migrated],
                       key=lambda r: r.size_gb, reverse=True)

    _write_csv(os.path.join(output_dir, "sonarr_migrated.csv"), migrated)
    _write_csv(os.path.join(output_dir, "sonarr_remaining.csv"), remaining)

    print("CSV files exported")


# ==========================
# HTML
# ==========================

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Anime Collection Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
--ink:#0D1117; --panel:#161B22; --panel-alt:#1C232D; --paper:#E6E8EB;
--muted:#7D8590; --signal:#FF4F64; --owned:#3DDC84; --border:#262D38;
}
* { box-sizing:border-box; }
body {
margin:0; background:var(--ink); color:var(--paper);
font-family:"IBM Plex Sans","Segoe UI",Arial,sans-serif;
padding:48px 32px 80px; line-height:1.5;
}
.wrap { max-width:1080px; margin:0 auto; }
.eyebrow {
font-family:"IBM Plex Mono","Consolas",monospace; font-size:12px;
letter-spacing:0.14em; color:var(--signal); text-transform:uppercase;
margin:0 0 8px;
}
h2 {
font-family:"Bebas Neue",Impact,sans-serif; font-weight:400; font-size:32px;
letter-spacing:0.01em; margin:0 0 20px;
}
.hero { border-bottom:1px solid var(--border); padding-bottom:36px; margin-bottom:40px; }
.hero-figure { display:flex; align-items:baseline; gap:16px; flex-wrap:wrap; }
.hero-number {
font-family:"Bebas Neue",Impact,sans-serif; font-size:clamp(64px,12vw,128px);
line-height:0.9; color:var(--paper);
}
.hero-label { font-family:"IBM Plex Mono","Consolas",monospace; font-size:14px; color:var(--muted); }
.hero-meta {
margin-top:16px; font-family:"IBM Plex Mono","Consolas",monospace;
font-size:13px; color:var(--muted); display:flex; gap:10px; flex-wrap:wrap;
}
.hero-meta .dot { color:var(--border); }
.diff-section { margin-bottom:56px; }
.diff-grid { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
.diff-label { font-family:"IBM Plex Mono","Consolas",monospace; font-size:12px; color:var(--paper); margin:0 0 8px; }
.diff-list { list-style:none; margin:0; padding:0; font-size:13px; }
.diff-list li { padding:6px 0; border-bottom:1px solid var(--border); }
.diff-list li:last-child { border-bottom:none; }
.diff-list a { color:var(--paper); text-decoration:none; }
.diff-list a:hover { color:var(--signal); }
.stat-grid {
display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
gap:12px; margin-bottom:28px;
}
.stat-chip { background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:14px 16px; }
.stat-chip .value { display:block; font-family:"IBM Plex Mono","Consolas",monospace; font-size:20px; color:var(--paper); }
.stat-chip .label { display:block; font-size:12px; color:var(--muted); margin-top:4px; }
.subheading { font-family:"IBM Plex Mono","Consolas",monospace; font-size:13px; color:var(--paper); margin:0 0 12px; }
.tiers { margin-bottom:56px; }
.tier-row { display:grid; grid-template-columns:90px 1fr 90px; align-items:center; gap:16px; padding:10px 0; }
.tier-label { font-family:"IBM Plex Mono","Consolas",monospace; font-size:12px; color:var(--muted); }
.tier-track { height:6px; background:var(--panel); border-radius:3px; overflow:hidden; }
.tier-fill { height:100%; background:var(--signal); border-radius:3px; transition:width 0.6s ease; }
.tier-value { font-family:"IBM Plex Mono","Consolas",monospace; font-size:12px; color:var(--paper); text-align:right; }
.shelf-section { margin-bottom:56px; }
.shelf { display:flex; gap:18px; overflow-x:auto; padding-bottom:12px; }
.shelf-item { position:relative; flex:0 0 140px; text-decoration:none; color:inherit; transition:transform 0.2s ease; }
.shelf-item:hover { transform:translateY(-4px); }
.shelf-item img { width:140px; height:198px; object-fit:cover; border-radius:4px; border:1px solid var(--border); display:block; }
.shelf-rank {
position:absolute; top:8px; left:8px; background:var(--signal); color:var(--ink);
font-family:"IBM Plex Mono","Consolas",monospace; font-size:11px; font-weight:600;
padding:2px 6px; border-radius:2px;
}
.shelf-title { display:block; margin-top:8px; font-size:13px; color:var(--paper); line-height:1.3; }
.empty-state {
font-family:"IBM Plex Mono","Consolas",monospace; color:var(--muted);
font-size:14px; padding:24px; border:1px dashed var(--border); border-radius:6px;
}
.index-section { margin-bottom:56px; }
.search-bar { margin-bottom:16px; }
.search-bar input {
width:100%; max-width:320px; padding:10px 12px; background:var(--panel);
border:1px solid var(--border); border-radius:4px; color:var(--paper);
font-family:"IBM Plex Sans","Segoe UI",Arial,sans-serif; font-size:14px;
}
.table-scroll { overflow-x:auto; border:1px solid var(--border); border-radius:6px; }
table { width:100%; border-collapse:collapse; font-size:14px; min-width:640px; }
thead th {
text-align:left; font-family:"IBM Plex Mono","Consolas",monospace; font-size:11px;
letter-spacing:0.08em; text-transform:uppercase; color:var(--muted);
padding:12px 14px; background:var(--panel); border-bottom:1px solid var(--border); white-space:nowrap;
}
th.sortable { cursor:pointer; user-select:none; }
th.sortable:hover { color:var(--signal); }
th.sortable::after { content:" \\21C5"; opacity:0.4; font-size:10px; }
th.sortable[data-dir="asc"]::after { content:" \\25B2"; opacity:1; color:var(--signal); }
th.sortable[data-dir="desc"]::after { content:" \\25BC"; opacity:1; color:var(--signal); }
tbody tr { border-bottom:1px solid var(--border); }
tbody tr:last-child { border-bottom:none; }
tbody tr:hover { background:var(--panel-alt); }
td { padding:10px 14px; vertical-align:middle; }
td.num { font-family:"IBM Plex Mono","Consolas",monospace; color:var(--muted); }
td.title a { color:var(--paper); text-decoration:none; font-weight:500; }
td.title a:hover { color:var(--signal); }
.sub-link { font-family:"IBM Plex Mono","Consolas",monospace; font-size:11px; color:var(--muted); text-decoration:none; }
.sub-link:hover { color:var(--signal); }
.cover-cell img { width:40px; height:56px; object-fit:cover; border-radius:2px; display:block; }
.owned-yes { color:var(--owned); font-weight:600; }
.owned-no { color:var(--border); }
details.matched-details { border:1px solid var(--border); border-radius:6px; margin-top:28px; }
details.matched-details summary {
cursor:pointer; padding:14px 16px; font-family:"IBM Plex Mono","Consolas",monospace;
font-size:13px; color:var(--muted); list-style:none;
}
details.matched-details summary::-webkit-details-marker { display:none; }
details.matched-details summary::before { content:"\\25B8  "; color:var(--signal); }
details.matched-details[open] summary::before { content:"\\25BE  "; }
details.matched-details summary:hover { color:var(--paper); }
details.matched-details .table-scroll { border:none; border-top:1px solid var(--border); border-radius:0; }
a:focus-visible, th.sortable:focus-visible, input:focus-visible { outline:2px solid var(--signal); outline-offset:2px; }
footer { margin-top:48px; font-family:"IBM Plex Mono","Consolas",monospace; font-size:12px; color:var(--border); }
@media (prefers-reduced-motion: reduce) { .shelf-item, .tier-fill { transition:none; } }
@media (max-width: 640px) {
body { padding:32px 16px 56px; }
.tier-row { grid-template-columns:70px 1fr 70px; }
.diff-grid { grid-template-columns:1fr; }
}
</style>
<script>
function searchTable(id) {
  const input = document.getElementById("search").value.toLowerCase();
  const rows = document.getElementById(id).tBodies[0].rows;
  for (let i = 0; i < rows.length; i++) {
    rows[i].style.display =
      rows[i].innerText.toLowerCase().includes(input) ? "" : "none";
  }
}
function sortTable(tableId, colIndex, type, header) {
  const table = document.getElementById(tableId);
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const asc = header.getAttribute("data-dir") !== "asc";
  rows.sort(function (a, b) {
    let x = a.cells[colIndex].innerText.trim();
    let y = b.cells[colIndex].innerText.trim();
    if (type === "num") {
      x = parseFloat(x.replace(/[^0-9.\\-]/g, "")) || 0;
      y = parseFloat(y.replace(/[^0-9.\\-]/g, "")) || 0;
      return asc ? x - y : y - x;
    }
    return asc ? x.localeCompare(y) : y.localeCompare(x);
  });
  rows.forEach(function (row) { tbody.appendChild(row); });
  Array.from(table.tHead.rows[0].cells).forEach(function (c) {
    c.removeAttribute("data-dir");
  });
  header.setAttribute("data-dir", asc ? "asc" : "desc");
}
</script>
</head>
<body>
<div class="wrap">

<header class="hero">
<p class="eyebrow">Collection status</p>
<div class="hero-figure">
<span class="hero-number">{{ stats.completion }}%</span>
<span class="hero-label">of {{ stats.total }} tracked TV series owned</span>
</div>
<div class="hero-meta">
<span>{{ stats.owned }} owned</span>
<span class="dot">&middot;</span>
<span>{{ stats.missing }} missing</span>
<span class="dot">&middot;</span>
<span>avg score owned {{ stats.avg_owned_score }}</span>
<span class="dot">&middot;</span>
<span>avg score missing {{ stats.avg_missing_score }}</span>
</div>
</header>

<section class="diff-section">
<p class="eyebrow">Since last run</p>
{% if not diff.has_previous %}
<p class="empty-state">First run - nothing to compare yet.</p>
{% elif not diff.newly_owned and not diff.newly_tracked %}
<p class="empty-state">No changes since last run.</p>
{% else %}
<div class="diff-grid">
<div>
<p class="diff-label">Newly owned ({{ diff.newly_owned|length }})</p>
{% if diff.newly_owned %}
<ul class="diff-list">
{% for d in diff.newly_owned[:10] %}
<li><a href="https://anilist.co/anime/{{ d.anilist_id }}" target="_blank" rel="noopener">{{ d.title }}</a></li>
{% endfor %}
</ul>
{% else %}<p class="empty-state">None</p>{% endif %}
</div>
<div>
<p class="diff-label">Newly tracked ({{ diff.newly_tracked|length }})</p>
{% if diff.newly_tracked %}
<ul class="diff-list">
{% for d in diff.newly_tracked[:10] %}
<li><a href="https://anilist.co/anime/{{ d.anilist_id }}" target="_blank" rel="noopener">{{ d.title }}</a></li>
{% endfor %}
</ul>
{% else %}<p class="empty-state">None</p>{% endif %}
</div>
</div>
{% endif %}
</section>

<section class="tiers">
<p class="eyebrow">Progress by depth</p>
{% for tier in tiers %}
<div class="tier-row">
<span class="tier-label">TOP {{ tier.tier }}</span>
<div class="tier-track"><div class="tier-fill" style="width:{{ tier.completion }}%"></div></div>
<span class="tier-value">{{ tier.owned }}/{{ tier.total }}</span>
</div>
{% endfor %}
</section>

<section class="shelf-section">
<p class="eyebrow">Next up</p>
<h2>Top picks to add</h2>
{% if missing_roots %}
<div class="shelf">
{% for a in missing_roots[:5] %}
<a class="shelf-item" href="{{ nyaa(a.title) }}" target="_blank" rel="noopener">
<span class="shelf-rank">#{{ a.rank }}</span>
<img src="{{ a.image }}" alt="{{ a.title }}" width="140" height="198" loading="lazy">
<span class="shelf-title">{{ a.title }}</span>
</a>
{% endfor %}
</div>
{% else %}
<p class="empty-state">Nothing to add - every franchise root in your target range is already in the library.</p>
{% endif %}
</section>

<section class="index-section">
<h2>Priority queue (franchise roots)</h2>
{% if missing_roots %}
<div class="table-scroll">
<table id="missing">
<thead><tr>
<th scope="col">Cover</th>
<th class="sortable" scope="col" onclick="sortTable('missing',1,'num',this)">Rank</th>
<th class="sortable" scope="col" onclick="sortTable('missing',2,'text',this)">Title</th>
<th class="sortable" scope="col" onclick="sortTable('missing',3,'num',this)">Score</th>
<th class="sortable" scope="col" onclick="sortTable('missing',4,'num',this)">Popularity</th>
<th class="sortable" scope="col" onclick="sortTable('missing',5,'num',this)">Recommendation</th>
</tr></thead>
<tbody>
{% for a in missing_roots[:25] %}
<tr>
<td class="cover-cell"><img src="{{ a.image }}" alt="{{ a.title }}" width="40" height="56" loading="lazy"></td>
<td class="num">#{{ a.rank }}</td>
<td class="title">
<a href="{{ nyaa(a.title) }}" target="_blank" rel="noopener">{{ a.title }}</a><br>
<a href="https://anilist.co/anime/{{ a.anilist_id }}" target="_blank" rel="noopener" class="sub-link">AniList</a>
<span class="sub-link"> &middot; </span>
<a href="https://myanimelist.net/anime/{{ a.mal_id }}" target="_blank" rel="noopener" class="sub-link">MAL</a>
</td>
<td class="num">{{ a.score }}</td>
<td class="num">{{ a.popularity }}</td>
<td class="num">{{ a.recommendation_score }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
{% else %}
<p class="empty-state">No missing franchise roots to queue up.</p>
{% endif %}
</section>

<section class="index-section">
<h2>Full index</h2>
<div class="search-bar">
<input id="search" onkeyup="searchTable('anime')" placeholder="Search anime...">
</div>
<div class="table-scroll">
<table id="anime">
<thead><tr>
<th scope="col">Cover</th>
<th class="sortable" scope="col" onclick="sortTable('anime',1,'num',this)">Rank</th>
<th class="sortable" scope="col" onclick="sortTable('anime',2,'text',this)">Title</th>
<th class="sortable" scope="col" onclick="sortTable('anime',3,'num',this)">Score</th>
<th class="sortable" scope="col" onclick="sortTable('anime',4,'text',this)">Owned</th>
</tr></thead>
<tbody>
{% for a in results %}
<tr>
<td class="cover-cell"><img src="{{ a.image }}" alt="{{ a.title }}" width="40" height="56" loading="lazy"></td>
<td class="num">#{{ a.rank }}</td>
<td class="title">
<a href="{{ nyaa(a.title) }}" target="_blank" rel="noopener">{{ a.title }}</a><br>
<a href="https://anilist.co/anime/{{ a.anilist_id }}" target="_blank" rel="noopener" class="sub-link">AniList</a>
</td>
<td class="num">{{ a.score }}</td>
<td>{% if a.owned %}<span class="owned-yes">&#10003;</span>{% else %}<span class="owned-no">&#8212;</span>{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</section>

{% if sonarr_enabled %}
<section class="index-section">
<p class="eyebrow">Migration status</p>
<h2>Sonarr &rarr; Shoko</h2>

<div class="stat-grid">
<div class="stat-chip"><span class="value">{{ totals.shoko_shows }}</span><span class="label">Shoko shows</span></div>
<div class="stat-chip"><span class="value">{{ totals.shoko_episodes }}</span><span class="label">Shoko episodes</span></div>
<div class="stat-chip"><span class="value">{{ totals.sonarr_shows }}</span><span class="label">Sonarr shows</span></div>
<div class="stat-chip"><span class="value">{{ totals.sonarr_episodes }}</span><span class="label">Sonarr episodes</span></div>
<div class="stat-chip"><span class="value">{{ migration.migrated }}/{{ migration.total }}</span><span class="label">In both ({{ migration.completion }}%)</span></div>
<div class="stat-chip"><span class="value">{{ migration.remaining_size_gb }} GB</span><span class="label">Left to move</span></div>
</div>

<p class="subheading">Missing from Shoko ({{ sonarr_missing|length }})</p>
{% if sonarr_missing %}
<div class="table-scroll">
<table id="sonarr-missing">
<thead><tr>
<th class="sortable" scope="col" onclick="sortTable('sonarr-missing',0,'text',this)">Title</th>
<th class="sortable" scope="col" onclick="sortTable('sonarr-missing',1,'text',this)">Status</th>
<th class="sortable" scope="col" onclick="sortTable('sonarr-missing',2,'num',this)">Episodes</th>
<th class="sortable" scope="col" onclick="sortTable('sonarr-missing',3,'num',this)">Size (GB)</th>
</tr></thead>
<tbody>
{% for r in sonarr_missing %}
<tr>
<td class="title"><a href="https://www.thetvdb.com/?tab=series&amp;id={{ r.tvdb_id }}" target="_blank" rel="noopener">{{ r.title }}</a></td>
<td>{{ r.status }}</td>
<td class="num">{{ r.episode_file_count }}/{{ r.episode_count }}</td>
<td class="num">{{ r.size_gb }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
{% else %}
<p class="empty-state">Migration complete - nothing left in Sonarr that isn't already in Shoko.</p>
{% endif %}

<details class="matched-details">
<summary>In both libraries ({{ sonarr_matched|length }})</summary>
{% if sonarr_matched %}
<div class="table-scroll">
<table id="sonarr-matched">
<thead><tr>
<th class="sortable" scope="col" onclick="sortTable('sonarr-matched',0,'text',this)">Title</th>
<th class="sortable" scope="col" onclick="sortTable('sonarr-matched',1,'text',this)">Status</th>
<th class="sortable" scope="col" onclick="sortTable('sonarr-matched',2,'num',this)">Episodes</th>
<th class="sortable" scope="col" onclick="sortTable('sonarr-matched',3,'num',this)">Size (GB)</th>
</tr></thead>
<tbody>
{% for r in sonarr_matched %}
<tr>
<td class="title"><a href="https://www.thetvdb.com/?tab=series&amp;id={{ r.tvdb_id }}" target="_blank" rel="noopener">{{ r.title }}</a></td>
<td>{{ r.status }}</td>
<td class="num">{{ r.episode_file_count }}/{{ r.episode_count }}</td>
<td class="num">{{ r.size_gb }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
{% else %}
<p class="empty-state">Nothing matched yet.</p>
{% endif %}
</details>
</section>
{% endif %}

<footer>Generated {{ generated_at }}</footer>

</div>
</body>
</html>
"""


def export_html(results, stats, tiers, diff, sonarr_results,
                migration, totals, output_dir, sonarr_enabled=True):
    env = Environment(autoescape=True)
    template = env.from_string(TEMPLATE)

    missing = sorted([r for r in results if not r.owned],
                     key=lambda r: r.recommendation_score, reverse=True)
    missing_roots = [r for r in missing if r.is_franchise_root]

    sonarr_missing = sorted([r for r in sonarr_results if not r.migrated],
                            key=lambda r: r.size_gb, reverse=True)
    sonarr_matched = sorted([r for r in sonarr_results if r.migrated],
                            key=lambda r: r.title)

    html = template.render(
        results=sorted(results, key=lambda r: r.rank),
        stats=stats,
        tiers=tiers,
        diff=diff,
        missing_roots=missing_roots,
        sonarr_missing=sonarr_missing,
        sonarr_matched=sonarr_matched,
        migration=migration,
        totals=totals,
        sonarr_enabled=sonarr_enabled,
        nyaa=nyaa_url,
        generated_at=datetime.now().strftime("%d %b %Y, %H:%M"),
    )

    path = os.path.join(output_dir, "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)

    # Land on the dashboard rather than a directory listing.
    with open(os.path.join(output_dir, "index.html"), "w",
              encoding="utf-8") as fh:
        fh.write('<!DOCTYPE html><html><head><meta http-equiv="refresh" '
                 'content="0; url=report.html"></head><body>'
                 '<a href="report.html">report.html</a></body></html>')

    print("HTML dashboard created")
    return path
