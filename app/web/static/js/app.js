/* Anime Collection Tracker - client runtime.
   Small hand-rolled modules; no build step and no external libraries so the
   dashboard works on a LAN with no internet access. */

(function () {
  "use strict";

  // ==========================
  // Utilities
  // ==========================

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function toast(message, kind) {
    const host = $("#toasts");
    if (!host) return;

    const el = document.createElement("div");
    el.className = "toast" + (kind ? " toast-" + kind : "");
    el.textContent = message;
    host.appendChild(el);

    setTimeout(() => {
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 250);
    }, 4200);
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.body !== undefined && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
    }
    // Mutating endpoints require a JSON content type by design.
    if (opts.method && opts.method !== "GET") {
      opts.headers["Content-Type"] = "application/json";
    }

    const response = await fetch(path, opts);
    let payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      payload = null;
    }

    if (!response.ok) {
      const message = (payload && (payload.error || payload.message)) ||
        `Request failed (${response.status})`;
      throw new Error(message);
    }
    return payload;
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function formatNumber(value) {
    if (value == null || isNaN(value)) return "-";
    return Number(value).toLocaleString();
  }

  // Mirrors the SONARR_* constants in core/models.py.
  const SONARR_PILLS = {
    owned:    '<span class="pill pill-good">&#10003; In Sonarr</span>',
    wanted:   '<span class="pill pill-warn" title="Sonarr has the series but no episode files for it">Monitored</span>',
    missing:  '<span class="pill pill-muted">Not in Sonarr</span>',
    unmapped: '<span class="pill pill-muted" title="No TVDB ID in the mapping file - Sonarr can\'t be checked">No TVDB ID</span>',
    unknown:  '<span class="pill pill-muted">&mdash;</span>'
  };

  /* The autobrr cell only appears on the seasons page. A show already in
     Shoko has nothing to grab, so it gets a dash rather than a dead button. */
  function autobrrCell(entry) {
    if (entry.owned) {
      return '<td><span class="pill pill-muted">&mdash;</span></td>';
    }

    const tracked = !!entry.autobrr_tracked;
    return `<td>
      <button class="button button-tiny track-toggle${tracked ? " is-tracked" : ""}"
              data-anilist-id="${entry.anilist_id}"
              data-title="${escapeHtml(entry.title)}"
              data-title-alt="${escapeHtml(entry.title_alt || "")}"
              data-mal-id="${escapeHtml(entry.mal_id || "")}"
              data-anidb-id="${escapeHtml(entry.anidb_id || "")}">
        ${tracked ? "&#10003; Tracked" : "Track"}
      </button>
    </td>`;
  }

  /* Only the states worth interrupting for. FINISHED is the overwhelming
     majority of any list, so a pill for it would be noise on every row. */
  const STATUS_PILLS = {
    RELEASING:        '<span class="sub-link" title="Currently airing">&middot; airing</span>',
    NOT_YET_RELEASED: '<span class="sub-link" title="Announced, not airing yet">&middot; upcoming</span>'
  };

  /* One row shape shared by the library and seasons tables - both are the same
     "an AniList entry, and where it lives" list. */
  function entryRow(entry, withSonarr, withAutobrr) {
    const mal = entry.mal_id
      ? `<a class="sub-link" href="https://myanimelist.net/anime/${escapeHtml(entry.mal_id)}" target="_blank" rel="noopener">MAL</a>
         <span class="sub-link">&middot;</span>`
      : "";

    const sonarr = withSonarr
      ? `<td>${SONARR_PILLS[entry.sonarr_status] || SONARR_PILLS.unknown}</td>`
      : "";

    return `
      <tr>
        <td><img class="cover" src="${escapeHtml(entry.image)}" alt="" loading="lazy"></td>
        <td class="num">#${entry.rank}</td>
        <td class="title">
          <a href="https://anilist.co/anime/${entry.anilist_id}" target="_blank" rel="noopener">${escapeHtml(entry.title)}</a>
          <div>
            ${mal}
            <a class="sub-link" href="https://nyaa.si/?f=0&c=0_0&q=${encodeURIComponent(entry.title)}&s=seeders&o=desc" target="_blank" rel="noopener">Search</a>
            ${entry.is_franchise_root ? '<span class="sub-link">&middot; root</span>' : ""}
            ${STATUS_PILLS[entry.status || ""] || ""}
            ${entry.sequel_of_owned
              ? '<span class="pill pill-good" title="You already have an earlier season of this in Shoko">New season</span>'
              : ""}
          </div>
        </td>
        <td class="num">${entry.score || "-"}</td>
        <td class="num">${formatNumber(entry.popularity)}</td>
        <td>${entry.owned
          ? '<span class="pill pill-good">&#10003; In Shoko</span>'
          : '<span class="pill pill-muted">Missing</span>'}</td>
        ${sonarr}
        ${withAutobrr ? autobrrCell(entry) : ""}
      </tr>
    `;
  }

  function tableMessage(table, text) {
    const columns = table ? table.tHead.rows[0].cells.length : 7;
    return `<tr><td colspan="${columns}"><p class="empty-state">${escapeHtml(text)}</p></td></tr>`;
  }

  function formatDateTime(iso) {
    if (!iso) return "-";
    const date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    return date.toLocaleString(undefined, {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
    });
  }

  // ==========================
  // Theme
  // ==========================

  const Theme = {
    init() {
      const toggle = $("#theme-toggle");
      if (!toggle) return;
      toggle.addEventListener("click", () => {
        const root = document.documentElement;
        const current = root.getAttribute("data-theme");
        const isDark = current
          ? current === "dark"
          : window.matchMedia("(prefers-color-scheme: dark)").matches;
        const next = isDark ? "light" : "dark";
        root.setAttribute("data-theme", next);
        document.cookie = `theme=${next};path=/;max-age=31536000;SameSite=Lax`;
        window.dispatchEvent(new CustomEvent("themechange"));
      });
    }
  };

  // ==========================
  // Run status polling
  // ==========================

  const Status = {
    timer: null,
    wasRunning: false,

    init() {
      const button = $("#run-now");
      if (button) {
        button.addEventListener("click", () => this.trigger(button));
      }
      this.poll();
    },

    async trigger(button) {
      button.disabled = true;
      try {
        await api("/api/run", { method: "POST", body: {} });
        toast("Run started", "success");
        this.poll();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        setTimeout(() => { button.disabled = false; }, 1200);
      }
    },

    async poll() {
      clearTimeout(this.timer);

      let running = false;
      try {
        const data = await api("/api/status");
        running = data.run && data.run.running;
        this.render(data);

        // A run that just finished means the page data is stale.
        if (this.wasRunning && !running) {
          toast("Run finished - refreshing", "success");
          setTimeout(() => window.location.reload(), 900);
        }
        this.wasRunning = running;
      } catch (err) {
        /* transient - just try again on the next tick */
      }

      this.timer = setTimeout(() => this.poll(), running ? 1500 : 20000);
    },

    render(data) {
      const box = $("#run-status");
      const text = $("#run-status-text");
      if (!box || !text) return;

      const run = data.run || {};
      if (run.running) {
        box.hidden = false;
        const phase = run.phase ? run.phase : "starting";
        text.textContent = run.message || phase;
      } else {
        box.hidden = true;
      }

      const phaseTrack = $("#phase-track");
      if (phaseTrack && run.phases) {
        const index = run.phases.indexOf(run.phase);
        phaseTrack.innerHTML = run.phases.map((name, i) => {
          let cls = "phase-step";
          if (run.running && index >= 0) {
            if (i < index) cls += " is-done";
            else if (i === index) cls += " is-active";
          }
          return `<span class="${cls}" title="${escapeHtml(name)}"></span>`;
        }).join("");
      }
    }
  };

  // ==========================
  // Charts
  // ==========================

  const Chart = {
    /* Single-series completion trend: 2px line, 10% area wash, hover
       crosshair. One series, so no legend - the card title names it. */
    trend(host, points) {
      if (!host) return;
      if (!points || points.length < 2) {
        host.innerHTML =
          '<p class="empty-state">Not enough run history yet - the trend appears after a second run.</p>';
        return;
      }

      const width = host.clientWidth || 640;
      const height = 200;
      const pad = { top: 12, right: 14, bottom: 24, left: 38 };
      const plotW = Math.max(width - pad.left - pad.right, 10);
      const plotH = height - pad.top - pad.bottom;

      const values = points.map(p => p.value);
      let min = Math.min.apply(null, values);
      let max = Math.max.apply(null, values);

      // Give a flat series some breathing room instead of a zero-height band.
      if (max - min < 1) {
        const mid = (max + min) / 2;
        min = Math.max(0, mid - 2);
        max = Math.min(100, mid + 2);
      }
      const span = max - min || 1;

      const x = i => pad.left + (plotW * i) / (points.length - 1);
      const y = v => pad.top + plotH - ((v - min) / span) * plotH;

      const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
      const area = `${line}L${x(points.length - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)}L${x(0).toFixed(1)},${(pad.top + plotH).toFixed(1)}Z`;

      const ticks = 4;
      let gridlines = "";
      let axisText = "";
      for (let i = 0; i <= ticks; i++) {
        const value = min + (span * i) / ticks;
        const yy = y(value).toFixed(1);
        gridlines += `<line class="grid-line" x1="${pad.left}" y1="${yy}" x2="${pad.left + plotW}" y2="${yy}"></line>`;
        axisText += `<text class="axis-text" x="${pad.left - 8}" y="${yy}" text-anchor="end" dominant-baseline="middle">${value.toFixed(0)}%</text>`;
      }

      const firstLabel = points[0].label;
      const lastLabel = points[points.length - 1].label;

      host.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" role="img"
             aria-label="Collection completion over time">
          ${gridlines}
          <path class="series-area" d="${area}"></path>
          <path class="series-line" d="${line}"></path>
          <circle class="end-dot" cx="${x(points.length - 1).toFixed(1)}"
                  cy="${y(points[points.length - 1].value).toFixed(1)}" r="4"></circle>
          <line class="axis-line" x1="${pad.left}" y1="${pad.top + plotH}"
                x2="${pad.left + plotW}" y2="${pad.top + plotH}"></line>
          ${axisText}
          <text class="axis-text" x="${pad.left}" y="${height - 6}">${escapeHtml(firstLabel)}</text>
          <text class="axis-text" x="${pad.left + plotW}" y="${height - 6}" text-anchor="end">${escapeHtml(lastLabel)}</text>
          <g class="hover-layer" hidden>
            <line class="hover-line" y1="${pad.top}" y2="${pad.top + plotH}"></line>
            <circle class="hover-dot" r="4"></circle>
          </g>
          <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}"
                fill="transparent" class="hit-area"></rect>
        </svg>
        <div class="chart-tooltip" hidden></div>
      `;

      this.attachHover(host, points, x, y, pad, plotW);
    },

    attachHover(host, points, x, y, pad, plotW) {
      const svg = $("svg", host);
      const hit = $(".hit-area", host);
      const layer = $(".hover-layer", host);
      const hLine = $(".hover-line", host);
      const hDot = $(".hover-dot", host);
      const tip = $(".chart-tooltip", host);
      if (!hit) return;

      const show = event => {
        const box = svg.getBoundingClientRect();
        const scale = box.width / svg.viewBox.baseVal.width;
        const localX = (event.clientX - box.left) / scale;

        const ratio = (localX - pad.left) / plotW;
        let index = Math.round(ratio * (points.length - 1));
        index = Math.max(0, Math.min(points.length - 1, index));

        const point = points[index];
        const px = x(index);
        const py = y(point.value);

        layer.removeAttribute("hidden");
        hLine.setAttribute("x1", px);
        hLine.setAttribute("x2", px);
        hDot.setAttribute("cx", px);
        hDot.setAttribute("cy", py);

        tip.removeAttribute("hidden");
        tip.innerHTML =
          `<div class="tip-label">${escapeHtml(point.label)}</div>` +
          `<div class="tip-value">${point.value.toFixed(1)}${host.dataset.unit || "% owned"}</div>` +
          (point.detail ? `<div class="tip-label">${escapeHtml(point.detail)}</div>` : "");
        tip.style.left = (px * scale) + "px";
        tip.style.top = ((py * scale) - 10) + "px";
      };

      hit.addEventListener("mousemove", show);
      hit.addEventListener("mouseleave", () => {
        layer.setAttribute("hidden", "");
        tip.setAttribute("hidden", "");
      });
    },

    /* Horizontal bars for ownership by decade - magnitude, one hue. */
    bars(host, rows) {
      if (!host) return;
      if (!rows || !rows.length) {
        host.innerHTML = '<p class="empty-state">No data.</p>';
        return;
      }

      host.innerHTML = rows.map(row => `
        <div class="meter-row">
          <span class="meter-label">${escapeHtml(row.label)}</span>
          <div class="meter-track">
            <div class="meter-fill" style="width:${row.completion}%"></div>
          </div>
          <span class="meter-value">${row.owned}/${row.total}</span>
        </div>
      `).join("");
    }
  };

  // ==========================
  // Sortable / filterable tables
  // ==========================

  const DataTable = {
    init(root) {
      const table = $("table", root);
      if (!table || !table.tHead) return;

      $$("th.sortable", table).forEach((th, index) => {
        th.setAttribute("tabindex", "0");
        const sort = () => this.sort(table, th, index);
        th.addEventListener("click", sort);
        th.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
        });
      });
    },

    sort(table, header, index) {
      const tbody = table.tBodies[0];
      const rows = Array.from(tbody.rows);
      const type = header.dataset.type || "text";
      const asc = header.getAttribute("data-dir") !== "asc";

      // textContent, not innerText: innerText reflects what is laid out, so a
      // table inside a collapsed <details> reads as empty and sorts into
      // nonsense. textContent doesn't care whether it's on screen.
      rows.sort((a, b) => {
        const x = a.cells[index] ? a.cells[index].textContent.trim() : "";
        const y = b.cells[index] ? b.cells[index].textContent.trim() : "";
        if (type === "num") {
          const nx = parseFloat(x.replace(/[^0-9.\-]/g, "")) || 0;
          const ny = parseFloat(y.replace(/[^0-9.\-]/g, "")) || 0;
          return asc ? nx - ny : ny - nx;
        }
        return asc ? x.localeCompare(y) : y.localeCompare(x);
      });

      rows.forEach(row => tbody.appendChild(row));
      $$("th", table.tHead).forEach(th => th.removeAttribute("data-dir"));
      header.setAttribute("data-dir", asc ? "asc" : "desc");
    }
  };

  /* Remembers which <details> sections were left open.
   *
   * The markup carries a sensible default, so a browser with storage blocked
   * still gets a usable page - this only restores a choice already made. */
  const Collapse = {
    init(el) {
      const key = "collapse:" + (el.dataset.collapse || "");

      let saved = null;
      try { saved = localStorage.getItem(key); } catch (e) { /* private mode */ }
      if (saved !== null) el.open = saved === "1";

      el.addEventListener("toggle", () => {
        try { localStorage.setItem(key, el.open ? "1" : "0"); } catch (e) { /* ignore */ }
      });
    }
  };

  /* Search over rows that are already in the DOM.
   *
   * Separate from Library's engine on purpose: that one renders rows from
   * JSON it fetches and owns paging and filter buttons with it, none of which
   * applies to a server-rendered table. Hiding <tr>s composes with
   * DataTable.sort for free, since sorting only reorders them. */
  const TableFilter = {
    init(root) {
      const input = $("[data-filter-input]", root);
      const table = $("table", root);
      if (!input || !table || !table.tBodies.length) return;

      const count = $("[data-filter-count]", root);
      const rows = Array.from(table.tBodies[0].rows);

      // Searched once here rather than per keystroke. textContent for the same
      // reason DataTable uses it - a row in a collapsed section has no
      // innerText and would never match. These rows are server-rendered and
      // never change, so the text can't go stale.
      const haystack = rows.map(row => row.textContent.toLowerCase());

      const apply = () => {
        const term = input.value.trim().toLowerCase();
        let shown = 0;

        rows.forEach((row, i) => {
          const hit = !term || haystack[i].includes(term);
          row.hidden = !hit;
          if (hit) shown++;
        });

        if (count) {
          count.textContent = term ? `${shown} of ${rows.length}` : "";
        }
      };

      // Same debounce as the library search, so the two behave alike.
      let timer;
      input.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(apply, 140);
      });
      apply();
    }
  };

  // ==========================
  // Library page
  // ==========================

  const Library = {
    entries: [],
    filtered: [],
    page: 0,
    pageSize: 50,
    filter: "all",
    query: "",
    sonarr: false,

    async init() {
      const host = $("#library-body");
      if (!host) return;

      const table = $("#library-table");
      this.sonarr = !!(table && table.dataset.sonarr);

      try {
        const data = await api("/api/results");
        this.entries = data.entries || [];
      } catch (err) {
        host.innerHTML = tableMessage(table, err.message);
        return;
      }

      const search = $("#library-search");
      if (search) {
        let debounce;
        search.addEventListener("input", () => {
          clearTimeout(debounce);
          debounce = setTimeout(() => {
            this.query = search.value.toLowerCase().trim();
            this.page = 0;
            this.apply();
          }, 140);
        });
      }

      $$("#library-filters button").forEach(button => {
        button.addEventListener("click", () => {
          $$("#library-filters button").forEach(b => b.classList.remove("is-active"));
          button.classList.add("is-active");
          this.filter = button.dataset.filter;
          this.page = 0;
          this.apply();
        });
      });

      const prev = $("#page-prev");
      const next = $("#page-next");
      if (prev) prev.addEventListener("click", () => { if (this.page > 0) { this.page--; this.render(); } });
      if (next) next.addEventListener("click", () => {
        if ((this.page + 1) * this.pageSize < this.filtered.length) { this.page++; this.render(); }
      });

      this.apply();
    },

    /* "In neither" deliberately excludes entries whose Sonarr state is
       unknown or unmapped - we can't claim a show is in neither library when
       one of the two answers is missing. */
    matches(entry) {
      const inShoko = entry.owned;
      const inSonarr = entry.sonarr_status === "owned";
      const sonarrKnown = entry.sonarr_status !== "unknown" &&
                          entry.sonarr_status !== "unmapped";

      switch (this.filter) {
        case "owned":       return inShoko;
        case "missing":     return !inShoko;
        case "roots":       return !inShoko && entry.is_franchise_root;
        case "shoko-only":  return inShoko && !inSonarr && sonarrKnown;
        case "sonarr-only": return !inShoko && inSonarr;
        case "neither":     return !inShoko && !inSonarr && sonarrKnown;
        default:            return true;
      }
    },

    apply() {
      this.filtered = this.entries.filter(entry => {
        if (!this.matches(entry)) return false;
        if (this.query && entry.title.toLowerCase().indexOf(this.query) === -1) return false;
        return true;
      });
      this.render();
    },

    render() {
      const body = $("#library-body");
      if (!body) return;

      const start = this.page * this.pageSize;
      const slice = this.filtered.slice(start, start + this.pageSize);

      if (!slice.length) {
        body.innerHTML = tableMessage($("#library-table"), "Nothing matches those filters.");
      } else {
        body.innerHTML = slice.map(entry => entryRow(entry, this.sonarr)).join("");
      }

      const info = $("#page-info");
      if (info) {
        const end = Math.min(start + this.pageSize, this.filtered.length);
        info.textContent = this.filtered.length
          ? `${start + 1}-${end} of ${formatNumber(this.filtered.length)}`
          : "0 results";
      }

      const prev = $("#page-prev");
      const next = $("#page-next");
      if (prev) prev.disabled = this.page === 0;
      if (next) next.disabled = (this.page + 1) * this.pageSize >= this.filtered.length;
    }
  };

  // ==========================
  // Seasons page
  // ==========================

  /* Every season and every ranking is already in the page as JSON - the run
     fetched all of it - so switching tabs is a re-render, not a request. */
  const Seasons = {
    blocks: [],
    index: 0,
    sort: "popularity",
    sonarr: false,

    init() {
      const body = $("#season-body");
      const source = $("#season-data");
      if (!body || !source) return;

      const table = $("#season-table");
      this.sonarr = !!(table && table.dataset.sonarr);

      try {
        this.blocks = JSON.parse(source.textContent) || [];
      } catch (err) {
        body.innerHTML = tableMessage(table, "The seasonal data could not be read.");
        return;
      }

      const active = $("#season-tabs button.is-active");
      this.index = active ? Number(active.dataset.season) : 0;

      this.bind("#season-tabs", button => {
        this.index = Number(button.dataset.season);
      });
      this.bind("#season-sorts", button => {
        this.sort = button.dataset.sort;
      });

      // Delegated: the body is re-rendered on every toggle, so per-button
      // listeners would be rebound constantly.
      body.addEventListener("click", event => {
        const button = event.target.closest(".track-toggle");
        if (button) this.toggle(button);
      });

      this.render();
    },

    /* The same entry object appears in several rankings and seasons, so the
       tracked flag is flipped everywhere it occurs, not just on the row that
       was clicked. */
    setTracked(anilistId, tracked) {
      this.blocks.forEach(block => {
        Object.keys(block.sorts || {}).forEach(key => {
          block.sorts[key].forEach(entry => {
            if (entry.anilist_id === anilistId) entry.autobrr_tracked = tracked;
          });
        });
      });
    },

    async toggle(button) {
      const anilistId = Number(button.dataset.anilistId);
      const tracked = button.classList.contains("is-tracked");
      const next = !tracked;

      button.disabled = true;
      this.setTracked(anilistId, next);
      this.render();

      try {
        if (next) {
          await api("/api/autobrr/track", {
            method: "POST",
            body: {
              anilist_id: anilistId,
              title: button.dataset.title,
              title_alt: button.dataset.titleAlt,
              mal_id: button.dataset.malId,
              anidb_id: button.dataset.anidbId
            }
          });
          toast(`Tracking "${button.dataset.title}" for autobrr`, "success");
        } else {
          const data = await api(`/api/autobrr/track/${anilistId}`, { method: "DELETE" });
          toast(data.excluded
            ? "Untracked - it won't be auto-added again"
            : "Untracked", "success");
        }
      } catch (err) {
        this.setTracked(anilistId, tracked);
        this.render();
        toast(err.message, "error");
      }
    },

    bind(selector, apply) {
      $$(`${selector} button`).forEach(button => {
        button.addEventListener("click", () => {
          $$(`${selector} button`).forEach(b => b.classList.remove("is-active"));
          button.classList.add("is-active");
          apply(button);
          this.render();
        });
      });
    },

    render() {
      const body = $("#season-body");
      if (!body) return;

      const block = this.blocks[this.index] || {};
      const entries = (block.sorts && block.sorts[this.sort]) || [];

      body.innerHTML = entries.length
        ? entries.map(entry => entryRow(entry, this.sonarr, true)).join("")
        : tableMessage($("#season-table"), "AniList has nothing listed for this season yet.");

      const summary = $("#season-summary");
      if (summary) {
        const owned = entries.filter(e => e.owned).length;
        const tracked = entries.filter(e => !e.owned && e.autobrr_tracked).length;
        summary.textContent = entries.length
          ? `${owned}/${entries.length} in Shoko · ${tracked} tracked`
          : "";
      }
    }
  };

  // ==========================
  // Settings page
  // ==========================

  const Settings = {
    init() {
      const form = $("#settings-form");
      if (!form) return;

      form.addEventListener("submit", e => {
        e.preventDefault();
        this.save(form);
      });

      $$("[data-test]").forEach(button => {
        button.addEventListener("click", () => this.test(button));
      });

      const refresh = $("#refresh-mappings");
      if (refresh) refresh.addEventListener("click", () => this.refreshMappings(refresh));

      const clearCache = $("#clear-cache");
      if (clearCache) clearCache.addEventListener("click", () => this.clearCache(clearCache));

      const diagnose = $("#run-diagnostics");
      if (diagnose) diagnose.addEventListener("click", () => this.diagnose(diagnose));
    },

    collect(form) {
      const payload = {};

      $$("[data-path]", form).forEach(input => {
        if (input.disabled) return;

        const [section, key] = input.dataset.path.split(".");
        payload[section] = payload[section] || {};

        if (input.type === "checkbox" && !input.dataset.multi) {
          payload[section][key] = input.checked;
        } else if (input.dataset.multi) {
          payload[section][key] = payload[section][key] || [];
          if (input.checked) payload[section][key].push(input.value);
        } else if (input.type === "number") {
          payload[section][key] = parseInt(input.value, 10) || 0;
        } else if (input.dataset.list) {
          payload[section][key] = input.value
            .split(",")
            .map(v => parseInt(v.trim(), 10))
            .filter(v => !isNaN(v) && v > 0);
        } else {
          payload[section][key] = input.value.trim();
        }
      });

      return payload;
    },

    async save(form) {
      const button = $("#save-settings");
      button.disabled = true;

      try {
        await api("/api/settings", { method: "POST", body: this.collect(form) });
        toast("Settings saved", "success");
        const banner = $("#setup-banner");
        if (banner) setTimeout(() => window.location.reload(), 700);
      } catch (err) {
        toast(err.message, "error");
      } finally {
        button.disabled = false;
      }
    },

    async test(button) {
      const service = button.dataset.test;
      const target = $(`#test-result-${service}`);
      button.disabled = true;
      if (target) { target.className = "test-result"; target.textContent = "Testing..."; }

      const body = {};
      const urlInput = $(`[data-path="${service}.url"]`);
      const keyInput = $(`[data-path="${service}.api_key"]`);
      if (urlInput) body.url = urlInput.value.trim();
      if (keyInput) body.api_key = keyInput.value;

      try {
        const data = await api(`/api/test/${service}`, { method: "POST", body: body });
        if (target) {
          target.className = "test-result " + (data.ok ? "ok" : "fail");
          target.textContent = data.ok ? "✓ " + data.message : "✗ " + data.error;
        }
      } catch (err) {
        if (target) { target.className = "test-result fail"; target.textContent = "✗ " + err.message; }
      } finally {
        button.disabled = false;
      }
    },

    async refreshMappings(button) {
      button.disabled = true;
      const target = $("#test-result-mappings");
      if (target) { target.className = "test-result"; target.textContent = "Downloading..."; }
      try {
        const data = await api("/api/mappings/refresh", { method: "POST", body: {} });
        if (target) {
          target.className = "test-result " + (data.ok ? "ok" : "fail");
          target.textContent = data.ok ? "✓ " + data.message : "✗ " + data.error;
        }
      } catch (err) {
        if (target) { target.className = "test-result fail"; target.textContent = "✗ " + err.message; }
      } finally {
        button.disabled = false;
      }
    },

    async clearCache(button) {
      button.disabled = true;
      try {
        const data = await api("/api/cache", { method: "DELETE" });
        toast(data.message, "success");
      } catch (err) {
        toast(err.message, "error");
      } finally {
        button.disabled = false;
      }
    },

    async diagnose(button) {
      const host = $("#diagnostics-output");
      button.disabled = true;
      if (host) host.innerHTML = '<p class="hint">Querying Shoko...</p>';

      try {
        const data = await api("/api/diagnostics/shoko");
        if (!data.ok) {
          host.innerHTML = `<p class="test-result fail">✗ ${escapeHtml(data.error)}</p>`;
          return;
        }

        const found = data.found || {};
        const line = (label, values) => {
          const ok = values && values.length;
          return `<div class="test-result ${ok ? "ok" : "fail"}">${ok ? "✓" : "✗"} ${label}: ${ok ? escapeHtml(values.join(", ")) : "none found"}</div>`;
        };

        host.innerHTML = `
          <p class="hint">Sample series: <strong>${escapeHtml(data.series_name)}</strong></p>
          ${line("AniDB ID", found.anidb)}
          ${line("MAL ID", found.mal)}
          ${line("TVDB ID", found.tvdb)}
          <div class="test-result ${data.episodes_suspect ? "fail" : "ok"}">
            ${data.episodes_suspect ? "✗" : "✓"} Episode count: ${data.episodes}
          </div>
          <p class="hint">IDs object keys: <code>${escapeHtml((data.id_keys || []).join(", ") || "none")}</code></p>
        `;
      } catch (err) {
        if (host) host.innerHTML = `<p class="test-result fail">✗ ${escapeHtml(err.message)}</p>`;
      } finally {
        button.disabled = false;
      }
    }
  };

  // ==========================
  // Logs page
  // ==========================

  const Logs = {
    timer: null,

    init() {
      const view = $("#log-view");
      if (!view) return;
      this.load(view);

      const auto = $("#log-autorefresh");
      const tick = () => {
        clearTimeout(this.timer);
        if (!auto || auto.checked) this.load(view);
        this.timer = setTimeout(tick, 5000);
      };
      this.timer = setTimeout(tick, 5000);
    },

    async load(view) {
      try {
        const data = await api("/api/logs");
        const atBottom = view.scrollTop + view.clientHeight >= view.scrollHeight - 30;

        view.innerHTML = (data.logs || []).map(entry => `
          <div class="log-line">
            <span class="log-time">${escapeHtml(entry.time)}</span>
            <span class="log-level ${escapeHtml(entry.level)}">${escapeHtml(entry.level)}</span>
            <span class="log-msg">${escapeHtml(entry.message)}</span>
          </div>
        `).join("");

        if (atBottom) view.scrollTop = view.scrollHeight;
      } catch (err) {
        /* keep the last good view */
      }
    }
  };

  // ==========================
  // Boot
  // ==========================

  function boot() {
    Theme.init();
    Status.init();
    Settings.init();
    Logs.init();
    Library.init();
    Seasons.init();

    $$("[data-collapse]").forEach(el => Collapse.init(el));
    $$("[data-table]").forEach(el => DataTable.init(el));
    $$("[data-filterable]").forEach(el => TableFilter.init(el));

    const menu = $("#menu-toggle");
    if (menu) {
      menu.addEventListener("click", () => {
        const sidebar = $("#sidebar");
        const open = sidebar.classList.toggle("is-open");
        menu.setAttribute("aria-expanded", String(open));
      });
    }

    // Charts read their data from a JSON script block on the page.
    const trendData = $("#trend-data");
    if (trendData) {
      const points = JSON.parse(trendData.textContent);
      const host = $("#trend-chart");
      const draw = () => Chart.trend(host, points);
      draw();

      let resizeTimer;
      window.addEventListener("resize", () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(draw, 180);
      });
      window.addEventListener("themechange", draw);

      // A chart drawn inside a closed <details> measures zero width and falls
      // back to a guess, so it has to be redrawn once the section is actually
      // on screen and can be measured properly.
      const panel = host && host.closest("details");
      if (panel) {
        panel.addEventListener("toggle", () => { if (panel.open) draw(); });
      }
    }

    const decadeData = $("#decade-data");
    if (decadeData) {
      Chart.bars($("#decade-chart"), JSON.parse(decadeData.textContent));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
