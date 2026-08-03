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
          `<div class="tip-value">${point.value.toFixed(1)}% owned</div>` +
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

      rows.sort((a, b) => {
        const x = a.cells[index] ? a.cells[index].innerText.trim() : "";
        const y = b.cells[index] ? b.cells[index].innerText.trim() : "";
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

    async init() {
      const host = $("#library-body");
      if (!host) return;

      try {
        const data = await api("/api/results");
        this.entries = data.entries || [];
      } catch (err) {
        host.innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(err.message)}</td></tr>`;
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

    apply() {
      this.filtered = this.entries.filter(entry => {
        if (this.filter === "owned" && !entry.owned) return false;
        if (this.filter === "missing" && entry.owned) return false;
        if (this.filter === "roots" && (entry.owned || !entry.is_franchise_root)) return false;
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
        body.innerHTML = '<tr><td colspan="6"><p class="empty-state">Nothing matches those filters.</p></td></tr>';
      } else {
        body.innerHTML = slice.map(entry => `
          <tr>
            <td><img class="cover" src="${escapeHtml(entry.image)}" alt="" loading="lazy"></td>
            <td class="num">#${entry.rank}</td>
            <td class="title">
              <a href="https://anilist.co/anime/${entry.anilist_id}" target="_blank" rel="noopener">${escapeHtml(entry.title)}</a>
              <div>
                <a class="sub-link" href="https://myanimelist.net/anime/${escapeHtml(entry.mal_id)}" target="_blank" rel="noopener">MAL</a>
                <span class="sub-link">&middot;</span>
                <a class="sub-link" href="https://nyaa.si/?f=0&c=0_0&q=${encodeURIComponent(entry.title)}&s=seeders&o=desc" target="_blank" rel="noopener">Search</a>
                ${entry.is_franchise_root ? '<span class="sub-link">&middot; root</span>' : ""}
              </div>
            </td>
            <td class="num">${entry.score || "-"}</td>
            <td class="num">${formatNumber(entry.popularity)}</td>
            <td>${entry.owned
              ? '<span class="pill pill-good">&#10003; Owned</span>'
              : '<span class="pill pill-muted">Missing</span>'}</td>
          </tr>
        `).join("");
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

    $$("[data-table]").forEach(el => DataTable.init(el));

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
