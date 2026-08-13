(() => {
  const data = window.TEZPOS_CHARTS || {};
  const fmt = (n) => Number(n || 0).toLocaleString("uz-UZ");
  const palette = [
    "#2c86e0", "#12b3a1", "#3fd07a", "#f59e0b", "#6366f1",
    "#0ea5e9", "#14b8a6", "#84cc16", "#f97316", "#8b5cf6",
    "#0284c7", "#0d9488", "#65a30d", "#ea580c", "#7c3aed",
  ];

  // Brauzer kesh — sahifadan sahifaga loading ko‘rinmasin
  const CACHE_P = "tezpos_v2_";
  const cacheGet = (key) => {
    try {
      const raw = sessionStorage.getItem(CACHE_P + key);
      return raw ? JSON.parse(raw) : null;
    } catch (_e) {
      return null;
    }
  };
  const cacheSet = (key, val) => {
    try {
      sessionStorage.setItem(CACHE_P + key, JSON.stringify(val));
    } catch (_e) {
      /* quota */
    }
  };
  window.tezposCacheGet = cacheGet;
  window.tezposCacheSet = cacheSet;

  const showApiBanner = (msg, ok = false) => {
    const el = document.getElementById("cabinet-api-banner");
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.classList.toggle("is-ok", Boolean(ok));
    el.textContent = msg;
  };
  window.tezposShowApiBanner = showApiBanner;

  // API ulanishini tekshirish — 0 lar o‘rniga aniq xabar
  if (data.apiStatusUrl) {
    fetch(data.apiStatusUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (r) => {
        const json = await r.json().catch(() => ({}));
        if (r.status === 401 || json.error === "auth") {
          showApiBanner("Sessiya tugagan. Qayta kiring.");
          return;
        }
        if (!json.ok) {
          showApiBanner(
            `TezPOS API ulanmadi (${json.api || "noma’lum"}). ` +
              (json.error || "Backend (port 8000) o‘chiq yoki sekin.") +
              " Contabo da backendni yoqing."
          );
          return;
        }
        showApiBanner("", true);
      })
      .catch(() => {
        showApiBanner("TezPOS API tekshiruvi muvaffaqiyatsiz. Backend ishlayotganini tekshiring.");
      });
  }

  let catalogInflight = null;
  let warmStarted = false;

  const applyCatalogPayload = (json, { emit = true } = {}) => {
    if (!json || json.error) return data;
    data.products = json.products || [];
    data.priceLists = json.priceLists || data.priceLists || [];
    data.nearMin = json.nearMin || data.nearMin || [];
    window.TEZPOS_CHARTS = data;
    cacheSet("catalog", {
      products: data.products,
      priceLists: data.priceLists,
      nearMin: data.nearMin,
      ts: Date.now(),
    });
    if (emit) {
      document.dispatchEvent(new CustomEvent("tezpos:catalog", { detail: data }));
    }
    if (typeof window.tezposPaintShellKpis === "function") {
      window.tezposPaintShellKpis();
    }
    return data;
  };

  const fetchCatalog = () => {
    if (!data.catalogUrl) return Promise.resolve(data);
    if (catalogInflight) return catalogInflight;
    catalogInflight = fetch(data.catalogUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (r) => {
        const json = await r.json().catch(() => ({}));
        if (!r.ok || (json.error && !(json.products || []).length)) {
          if (typeof window.tezposShowApiBanner === "function") {
            window.tezposShowApiBanner(
              `Katalog yuklanmadi: ${json.error || r.status}. TezPOS API (13.140.146.78:8000) ni tekshiring.`
            );
          }
          throw new Error(json.error || "catalog fail");
        }
        return applyCatalogPayload(json);
      })
      .catch(() => data)
      .finally(() => {
        catalogInflight = null;
      });
    return catalogInflight;
  };

  // Katalog AJAX — kesh bo‘lsa darhol, yangilash fonida
  const ensureCatalog = ({ force = false } = {}) => {
    const need =
      document.getElementById("products-mgmt-tbody") ||
      document.getElementById("stock-value-panel") ||
      document.getElementById("signals-list") ||
      document.getElementById("label-designer") ||
      document.getElementById("label-print-view") ||
      document.getElementById("labels-table") ||
      document.querySelector(".cabinet-products");

    if (!data.products?.length) {
      const cached = cacheGet("catalog");
      if (cached?.products?.length) {
        applyCatalogPayload(cached, { emit: false });
      }
    }

    if (data.products?.length && need) {
      document.dispatchEvent(new CustomEvent("tezpos:catalog", { detail: data }));
    }

    if (!data.catalogUrl) return Promise.resolve(data);
    if (data.products?.length && !force) {
      // Foniy yangilash — UI kutmaydi
      fetchCatalog();
      return Promise.resolve(data);
    }

    // Birinchi yuklash — skeleton faqat kesh bo‘lmasa
    if (need && !data.products?.length) {
      const skelTargets = [
        document.getElementById("products-mgmt-tbody"),
        document.getElementById("stock-value-tbody"),
        document.getElementById("signals-list"),
        document.querySelector("#labels-table tbody"),
      ].filter(Boolean);
      skelTargets.forEach((el) => {
        if (el && !el.dataset.loaded) {
          el.innerHTML =
            el.tagName === "TBODY"
              ? '<tr><td colspan="8" class="cabinet-hint">Mahsulotlar yuklanmoqda…</td></tr>'
              : "";
        }
      });
    }
    return fetchCatalog();
  };
  window.tezposEnsureCatalog = ensureCatalog;

  const warmCabinet = () => {
    if (warmStarted) {
      ensureCatalog();
      return;
    }
    warmStarted = true;
    if (data.warmUrl) {
      fetch(data.warmUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }).catch(() => {});
    }
    ensureCatalog();
    // Bugungi range-stats + day-sales oldindan
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(
      today.getDate()
    ).padStart(2, "0")}`;
    if (data.rangeStatsUrl) {
      const key = `${iso}_${iso}`;
      const ss = cacheGet("salesStats") || {};
      if (!ss[key]) {
        const qs = new URLSearchParams({ from: iso, to: iso, fast: "1" });
        fetch(`${data.rangeStatsUrl}?${qs}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
          .then((r) => r.json())
          .then((payload) => {
            if (!payload || payload.error) return;
            const chart = payload.chart || {};
            const next = cacheGet("salesStats") || {};
            next[key] = {
              labels: chart.labels || [],
              totals: chart.totals || [],
              counts: chart.counts || [],
              summary: payload.summary || null,
              partial: Boolean(payload.partial),
            };
            cacheSet("salesStats", next);
            const pl = cacheGet("priceListStats") || {};
            if (Array.isArray(payload.priceLists)) {
              pl[key] = payload.priceLists;
              cacheSet("priceListStats", pl);
            }
            data.salesStats = Object.assign({}, data.salesStats || {}, next);
            data.priceListStats = Object.assign({}, data.priceListStats || {}, pl);
            window.TEZPOS_CHARTS = data;
            if (typeof window.tezposPaintShellKpis === "function") {
              window.tezposPaintShellKpis();
            }
          })
          .catch(() => {});
      }
    }
    if (data.daySalesUrl) {
      const ds = cacheGet("daySales") || {};
      if (!ds[iso]) {
        fetch(`${data.daySalesUrl}?sale_date=${iso}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
          .then((r) => r.json())
          .then((json) => {
            if (!json || json.error) return;
            const next = cacheGet("daySales") || {};
            next[iso] = json;
            cacheSet("daySales", next);
            data._daySalesPack = json;
            if (typeof window.tezposPaintShellKpis === "function") {
              window.tezposPaintShellKpis();
            }
            document.dispatchEvent(new CustomEvent("tezpos:daySales", { detail: json }));
          })
          .catch(() => {});
      }
    }
    if (data.shiftsUrl && !cacheGet("shifts")) {
      fetch(data.shiftsUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((r) => r.json())
        .then((json) => {
          if (!json || json.error) return;
          cacheSet("shifts", {
            shifts: json.shifts || [],
            source: json.source || "none",
            ts: Date.now(),
          });
        })
        .catch(() => {});
    }
    if (data.reportsUrl && !cacheGet("reports")) {
      fetch(data.reportsUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((r) => r.json())
        .then((json) => {
          if (!json || json.error) return;
          cacheSet("reports", json);
        })
        .catch(() => {});
    }
    if (data.abcUrl && !cacheGet("abc")) {
      fetch(data.abcUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((r) => r.json())
        .then((json) => {
          if (!json || json.error) return;
          cacheSet("abc", json);
        })
        .catch(() => {});
    }
  };
  window.tezposWarmCabinet = warmCabinet;

  ensureCatalog();
  warmCabinet();

  // Menyudan bosilishi bilan keshni isitish (sahifa ochilishidan oldin)
  document.querySelectorAll(".cabinet-nav a").forEach((a) => {
    const prep = () => {
      try {
        sessionStorage.setItem("tezpos_cab_seen", "1");
      } catch (e) {}
      warmCabinet();
    };
    a.addEventListener("pointerdown", prep, { passive: true });
    a.addEventListener("mouseenter", prep, { passive: true });
    a.addEventListener("touchstart", prep, { passive: true });
  });

  const skelHtml = (kind, count = 4) => {
    if (kind === "tiles") {
      return `<div class="cab-inline-skel cab-inline-skel--tiles" aria-hidden="true">${"<div class=\"sk\"></div>".repeat(count)}</div>`;
    }
    if (kind === "stats") {
      return `<div class="cab-inline-skel cab-inline-skel--stats" aria-hidden="true">${"<div class=\"sk\"></div>".repeat(count)}</div>`;
    }
    if (kind === "chips") {
      return `<div class="cab-inline-skel cab-inline-skel--chips cab-inline-skel--mix" aria-hidden="true">${"<span class=\"sk\" style=\"width:76px\"></span>".repeat(count)}</div>`;
    }
    if (kind === "lines") {
      return `<li class="cab-inline-skel cab-inline-skel--lines" aria-hidden="true"><span class="sk"></span><span class="sk"></span><span class="sk"></span></li>`;
    }
    return `<div class="cab-inline-skel" aria-hidden="true"><div class="sk" style="height:72px"></div></div>`;
  };

  const app = document.querySelector(".cabinet-app");
  const menuBtn = document.getElementById("cabinet-menu-btn");
  const menuClose = document.getElementById("cabinet-menu-close");
  const menuBackdrop = document.getElementById("cabinet-sidebar-backdrop");
  const setNavOpen = (open) => {
    if (!app) return;
    app.classList.toggle("is-nav-open", open);
    if (menuBackdrop) menuBackdrop.hidden = !open;
    if (menuBtn) menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.style.overflow = open ? "hidden" : "";
  };
  menuBtn?.addEventListener("click", () => setNavOpen(!app?.classList.contains("is-nav-open")));
  menuClose?.addEventListener("click", () => setNavOpen(false));
  menuBackdrop?.addEventListener("click", () => setNavOpen(false));
  document.querySelectorAll(".cabinet-nav a").forEach((a) => {
    a.addEventListener("click", () => {
      try {
        sessionStorage.setItem("tezpos_cab_seen", "1");
      } catch (e) {}
      setNavOpen(false);
    });
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 1024) setNavOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && app?.classList.contains("is-nav-open")) setNavOpen(false);
  });

  const gridColor = "rgba(6,48,94,0.08)";
  const tickColor = "#8b97ab";
  const lineColor = "#2c86e0";
  const fillColor = "rgba(44,134,224,0.14)";
  const hasChart = typeof Chart !== "undefined";

  const lineOptions = {
    responsive: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: tickColor }, grid: { color: gridColor } },
      y: { ticks: { color: tickColor }, grid: { color: gridColor } },
    },
  };

  const lineEl = document.getElementById("salesLineChart");
  const salesRangeSummary = document.getElementById("sales-range-summary");
  const datePickerRoot = document.getElementById("sales-date-picker");
  const dateTrigger = document.getElementById("sales-date-trigger");
  const dateLabelEl = document.getElementById("sales-date-label");
  const calendarEl = document.getElementById("sales-calendar");
  const calBanner = document.getElementById("sales-calendar-banner");
  const calGrid = document.getElementById("sales-calendar-grid");
  const calMonthSel = document.getElementById("sales-cal-month");
  const calYearSel = document.getElementById("sales-cal-year");

  const MONTHS_UZ = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
  ];
  const MONTHS_SHORT = [
    "yan", "fev", "mar", "apr", "may", "iyn",
    "iyl", "avg", "sen", "okt", "noy", "dek",
  ];

  const startOfDay = (d) => {
    if (!d) return null;
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  };
  const toIso = (d) => {
    const x = startOfDay(d);
    if (!x) return "";
    const y = x.getFullYear();
    const m = String(x.getMonth() + 1).padStart(2, "0");
    const day = String(x.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  };
  const parseIso = (s) => {
    const m = String(s || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  };
  const sameDay = (a, b) => Boolean(a && b && toIso(a) === toIso(b));
  const dayTime = (d) => startOfDay(d)?.getTime() ?? 0;
  const fmtDayUz = (d) =>
    `${String(d.getDate()).padStart(2, "0")} ${MONTHS_SHORT[d.getMonth()]}. ${d.getFullYear()} y.`;
  const formatRangeLabel = (fromIso, toIsoVal) => {
    const a = parseIso(fromIso);
    const b = parseIso(toIsoVal);
    if (!a || !b) return "Sana tanlang";
    if (sameDay(a, b)) return fmtDayUz(a);
    return `${fmtDayUz(a)} – ${fmtDayUz(b)}`;
  };
  const rangeCacheKey = (fromIso, toIsoVal) => `${fromIso}_${toIsoVal}`;
  const clampRange = (a, b) => {
    const start = startOfDay(a);
    const end = startOfDay(b || a);
    if (!start) return null;
    if (!end || dayTime(end) >= dayTime(start)) return { start, end: end || start };
    return { start: end, end: start };
  };

  const todayDate = startOfDay(new Date());
  const defaultTo = new Date(todayDate);
  const defaultFrom = new Date(todayDate);
  // Birinchi ochilish: faqat bugun (tez). Oraliqni kalendardan kengaytirish mumkin.
  // defaultFrom.setDate(defaultFrom.getDate() - 6);

  let appliedFrom = toIso(defaultFrom);
  let appliedTo = toIso(defaultTo);
  let draftStart = parseIso(appliedFrom);
  let draftEnd = parseIso(appliedTo);
  let viewYear = draftEnd.getFullYear();
  let viewMonth = draftEnd.getMonth();
  /** 'start' | 'end' — keyingi bosish qaysi chegara */
  let pickMode = "start";

  const paintSalesSummary = (key, pack) => {
    if (!salesRangeSummary || !pack) return;
    const total = (pack.totals || []).reduce((a, b) => a + b, 0);
    const count = (pack.counts || []).reduce((a, b) => a + b, 0);
    const summary = pack.summary || {};
    const checks = summary.checks != null ? summary.checks : count;
    const gross = summary.gross != null ? summary.gross : total;
    const profit = summary.profit != null ? summary.profit : 0;
    const margin = summary.margin != null ? summary.margin : 0;
    const label = formatRangeLabel(appliedFrom, appliedTo);
    salesRangeSummary.innerHTML = `
      <li>Davr: <strong>${label}</strong></li>
      <li>Cheklar: <strong>${fmt(checks)}</strong></li>
      <li>Tushum: <strong>${Math.round(gross).toLocaleString("uz-UZ")} so'm</strong></li>
      <li>Sof foyda: <strong class="is-profit">${Math.round(profit).toLocaleString("uz-UZ")} so'm</strong></li>
      <li>Marja: <strong>${Number(margin).toFixed(1)}%</strong></li>
    `;
  };

  const fmtMoneyKpi = (n) =>
    Math.round(Number(n || 0)).toLocaleString("uz-UZ");

  const priceListEl = document.getElementById("price-list-stats");
  const priceListFilter = document.getElementById("overview-price-list-filter");
  const priceListCache = { ...(data.priceListStats || {}) };
  const catalogLists = Array.isArray(data.priceLists) ? data.priceLists : [];
  let currentRangeKey = rangeCacheKey(appliedFrom, appliedTo);
  let currentPriceLists = priceListCache[currentRangeKey] || priceListCache.d7 || [];

  const fillOverviewPriceListFilter = () => {
    if (!priceListFilter) return;
    const selected = priceListFilter.value || "all";
    const opts = [
      { value: "all", label: "Barcha narxlar" },
      { value: "__selling__", label: "Sotuv" },
      ...catalogLists
        .filter((pl) => pl && pl.id)
        .map((pl) => ({
          value: String(pl.id),
          label: (pl.name || "").trim() || "Narxlar",
        })),
    ];
    priceListFilter.innerHTML = opts
      .map((o) => `<option value="${o.value}">${o.label}</option>`)
      .join("");
    if (opts.some((o) => o.value === selected)) priceListFilter.value = selected;
  };

  const paintShellKpis = () => {
    const sigEl = document.getElementById("kpi-signals");
    const todayCountEl = document.getElementById("kpi-today-count");
    const todayGrossEl = document.getElementById("kpi-today-gross");
    if (!sigEl && !todayCountEl && !todayGrossEl) return;

    const near = Array.isArray(data.nearMin) ? data.nearMin : [];
    if (sigEl) sigEl.textContent = fmt(near.length);

    const todayIso = toIso(todayDate);
    let todayCount = null;
    let todayGross = null;

    // daySales kesh
    const dsPack = (window.tezposCacheGet && window.tezposCacheGet("daySales")) || {};
    const dayPack = data._daySalesPack || dsPack[todayIso];
    if (dayPack) {
      if (dayPack.count != null) todayCount = Number(dayPack.count);
      else if (Array.isArray(dayPack.sales)) todayCount = dayPack.sales.length;
      if (dayPack.gross != null) todayGross = Number(dayPack.gross);
    }

    // range-stats bugungi kun
    const key = `${todayIso}_${todayIso}`;
    const pack = (data.salesStats || {})[key] || {};
    const sum = pack.summary || {};
    if (sum.checks != null && todayCount == null) todayCount = Number(sum.checks);
    if (sum.gross != null && todayGross == null) todayGross = Number(sum.gross);

    // Tanlangan oralik bugun bo‘lsa
    if (
      todayCount == null &&
      appliedFrom === todayIso &&
      appliedTo === todayIso &&
      pack.summary
    ) {
      todayCount = Number(pack.summary.checks || 0);
      todayGross = Number(pack.summary.gross || 0);
    }

    if (todayCountEl && todayCount != null) todayCountEl.textContent = fmt(todayCount);
    if (todayGrossEl && todayGross != null) {
      todayGrossEl.textContent = fmtMoneyKpi(todayGross);
      todayGrossEl.setAttribute("data-fmt-money", String(todayGross || 0));
    }
  };
  window.tezposPaintShellKpis = paintShellKpis;

  const paintOverviewKpis = (key, pack, priceRows) => {
    const summary = (pack && pack.summary) || {};
    const selected = priceListFilter?.value || "all";
    const rows = Array.isArray(priceRows) ? priceRows : [];
    const parts = rows.filter((r) => !r.is_total);
    const jami = rows.find((r) => r.is_total) || null;
    const one =
      selected !== "all"
        ? parts.find((r) => String(r.id) === String(selected))
        : null;

    const checksEl = document.getElementById("kpi-checks");
    const grossEl = document.getElementById("kpi-gross");
    const profitEl = document.getElementById("kpi-profit");
    const marginEl = document.getElementById("kpi-margin");
    const periodEl = document.getElementById("kpi-period-label");
    const hintEl = document.getElementById("price-list-period-hint");
    const plNameEl = document.getElementById("kpi-price-list-name");
    const mixEl = document.getElementById("price-list-mix");
    const label = formatRangeLabel(appliedFrom, appliedTo);

    if (periodEl) periodEl.textContent = label;
    if (hintEl) hintEl.textContent = label;
    if (dateLabelEl) dateLabelEl.textContent = label;

    if (mixEl) {
      const mixParts = parts.filter((r) => Number(r.revenue) > 0);
      mixEl.innerHTML = mixParts.length
        ? mixParts
            .map(
              (r) =>
                `<span><strong>${r.name}</strong>: tushum ${Number(r.share || 0).toFixed(1)}% · chek ${fmt(r.checks)} · marja ${Number(r.markup != null ? r.markup : r.margin).toFixed(1)}%</span>`
            )
            .join("")
        : "";
    }

    const applyKpi = (checks, gross, profit, margin, tag) => {
      if (plNameEl) plNameEl.textContent = tag || "";
      if (checksEl) checksEl.textContent = fmt(checks);
      if (grossEl) {
        grossEl.textContent = fmtMoneyKpi(gross);
        grossEl.setAttribute("data-fmt-money", String(gross || 0));
      }
      if (profitEl) {
        profitEl.textContent = fmtMoneyKpi(profit);
        profitEl.setAttribute("data-fmt-money", String(profit || 0));
      }
      if (marginEl) marginEl.textContent = Number(margin || 0).toFixed(1);
    };

    if (one) {
      applyKpi(
        one.checks,
        one.revenue,
        one.profit,
        one.markup != null ? one.markup : one.margin,
        `· ${one.name || ""}`
      );
      return;
    }

    if (jami && Number(jami.revenue) > 0) {
      applyKpi(
        summary.checks != null ? summary.checks : jami.checks,
        summary.gross != null ? summary.gross : jami.revenue,
        summary.profit != null ? summary.profit : jami.profit,
        summary.margin != null ? summary.margin : jami.margin,
        ""
      );
      return;
    }

    applyKpi(
      summary.checks,
      summary.gross,
      summary.profit,
      summary.margin,
      ""
    );
  };

  const paintPriceLists = (rows) => {
    if (!priceListEl) return;
    const selected = priceListFilter?.value || "all";
    let list = Array.isArray(rows) ? rows.slice() : [];
    if (selected !== "all") {
      list = list.filter((row) => String(row.id) === String(selected));
    } else {
      // Tartib: Sotuv → Optom → Jami
      const jami = list.find((r) => r.is_total);
      const parts = list.filter((r) => !r.is_total && Number(r.revenue) > 0);
      const selling = parts.filter((r) => String(r.id) === "__selling__");
      const rest = parts.filter((r) => String(r.id) !== "__selling__");
      list = [...selling, ...rest];
      if (jami) list.push(jami);
    }
    if (!list.length) {
      priceListEl.innerHTML = `<p class="cabinet-hint">Bu davr / narxlar ro‘yxati uchun ma’lumot yo‘q.</p>`;
      return;
    }
    priceListEl.innerHTML = list
      .map((row, i) => {
        const isTotal = Boolean(row.is_total);
        const marja = Number(row.markup != null ? row.markup : row.margin || 0);
        return `<article class="price-list-stat cab-reveal-item${isTotal ? " is-total" : ""}" data-pl-id="${row.id}" style="animation-delay:${Math.min(i, 6) * 45}ms">
        <h4>${row.name || "Ro‘yxat"}${isTotal ? "" : ` <em>${Number(row.share || 0).toFixed(1)}%</em>`}</h4>
        <dl>
          <div><dt>Chek chiqdi</dt><dd>${fmt(row.checks)}</dd></div>
          <div><dt>Tushum</dt><dd>${fmtMoneyKpi(row.revenue)}</dd></div>
          <div><dt>Tannarx</dt><dd>${fmtMoneyKpi(row.cost)}</dd></div>
          <div><dt>Foyda</dt><dd class="is-profit">${fmtMoneyKpi(row.profit)}</dd></div>
          <div><dt>Marja</dt><dd>${marja.toFixed(1)}%</dd></div>
        </dl>
      </article>`;
      })
      .join("");
  };

  const refreshOverviewPriceUi = () => {
    const pack = (data.salesStats || {})[currentRangeKey] || {};
    paintPriceLists(currentPriceLists);
    paintOverviewKpis(currentRangeKey, pack, currentPriceLists);
  };

  let priceListReq = 0;
  let salesChartRef = null;

  const persistRangeCaches = () => {
    try {
      if (window.tezposCacheSet) {
        window.tezposCacheSet("salesStats", data.salesStats || {});
        window.tezposCacheSet("priceListStats", priceListCache);
      }
    } catch (_e) {}
  };

  const applyRangePayload = (key, payload) => {
    if (!data.salesStats) data.salesStats = {};
    if (!data.salesStats[key]) data.salesStats[key] = { labels: [], totals: [], counts: [] };
    if (payload.chart) {
      data.salesStats[key].labels = payload.chart.labels || [];
      data.salesStats[key].totals = payload.chart.totals || [];
      data.salesStats[key].counts = payload.chart.counts || [];
    }
    if (payload.summary) {
      data.salesStats[key].summary = {
        ...(data.salesStats[key].summary || {}),
        ...payload.summary,
      };
    }
    data.salesStats[key].partial = Boolean(payload.partial);
    if (payload.priceLists && payload.priceLists.length) {
      priceListCache[key] = payload.priceLists;
      currentPriceLists = priceListCache[key];
    } else if (!payload.fast) {
      priceListCache[key] = [];
      currentPriceLists = [];
    }
    if (salesChartRef && currentRangeKey === key) {
      salesChartRef.data.labels = data.salesStats[key].labels || [];
      salesChartRef.data.datasets[0].data = data.salesStats[key].totals || [];
      salesChartRef.update();
    }
    paintSalesSummary(key, data.salesStats[key]);
    refreshOverviewPriceUi();
    persistRangeCaches();
    if (typeof paintShellKpis === "function") paintShellKpis();
  };

  const paintCachedRange = (key) => {
    const pack = (data.salesStats || {})[key] || {};
    currentRangeKey = key;
    currentPriceLists = priceListCache[key] || [];
    if (salesChartRef) {
      salesChartRef.data.labels = pack.labels || [];
      salesChartRef.data.datasets[0].data = pack.totals || [];
      salesChartRef.update();
    }
    paintSalesSummary(key, pack);
    refreshOverviewPriceUi();
  };

  const seedRangeFromDaySales = () => {
    const todayIso = toIso(todayDate);
    if (appliedFrom !== todayIso || appliedTo !== todayIso) return false;
    const dsAll = (window.tezposCacheGet && window.tezposCacheGet("daySales")) || {};
    const pack = data._daySalesPack || dsAll[todayIso];
    if (!pack) return false;
    const count = Number(
      pack.count != null
        ? pack.count
        : Array.isArray(pack.sales)
          ? pack.sales.length
          : 0
    );
    const gross = Number(pack.gross || 0);
    if (!count && !gross) return false;
    const profit = Number(pack.profit != null ? pack.profit : gross * 0.25);
    const margin = gross > 0 ? (profit / gross) * 100 : 0;
    const key = rangeCacheKey(todayIso, todayIso);
    // Bo‘sh/eskirgan keshni yozib yubormasin
    const existing = (data.salesStats || {})[key];
    if (existing?.summary && Number(existing.summary.checks || 0) > 0) return false;
    applyRangePayload(key, {
      summary: { checks: count, gross, profit, margin },
      chart: {
        labels: ["Bugun"],
        totals: [gross],
        counts: [count],
      },
      priceLists: [
        {
          id: "__selling__",
          name: "Sotuv",
          checks: count,
          revenue: gross,
          cost: Math.max(0, gross - profit),
          profit,
          margin,
          markup: margin,
          share: 100,
          is_total: false,
        },
        {
          id: "__all__",
          name: "Jami",
          checks: count,
          revenue: gross,
          cost: Math.max(0, gross - profit),
          profit,
          margin,
          markup: margin,
          share: 100,
          is_total: true,
        },
      ],
      partial: true,
      fast: true,
    });
    return true;
  };

  const loadRangeStats = async (fromIso, toIsoVal, { force = false, fast = false } = {}) => {
    const key = rangeCacheKey(fromIso, toIsoVal);
    const pack = (data.salesStats || {})[key] || {};
    const hasRealSummary =
      pack.summary &&
      (Number(pack.summary.checks || 0) > 0 || Number(pack.summary.gross || 0) > 0);
    const hasUiCache =
      (priceListCache[key] &&
        priceListCache[key].some((r) => Number(r.revenue || 0) > 0)) ||
      (pack.labels && pack.labels.length && (pack.totals || []).some((n) => Number(n) > 0)) ||
      hasRealSummary;

    if (hasUiCache) {
      paintCachedRange(key);
      if (!force && !fast && pack.partial !== true && priceListCache[key] && pack.labels) {
        return;
      }
    } else {
      seedRangeFromDaySales();
    }

    const url = data.rangeStatsUrl;
    if (!url) {
      currentPriceLists = priceListCache[key] || [];
      refreshOverviewPriceUi();
      return;
    }
    const reqId = ++priceListReq;
    if (!hasUiCache && !((data.salesStats || {})[key] || {}).summary) {
      if (priceListEl && !(priceListCache[key] || []).length) {
        priceListEl.innerHTML = skelHtml("stats", 3);
      }
      if (document.getElementById("price-list-mix")) {
        const mix = document.getElementById("price-list-mix");
        if (mix && !(priceListCache[key] || []).length) {
          mix.innerHTML = skelHtml("chips", 5);
        }
      }
      if (salesRangeSummary && !pack.summary) {
        salesRangeSummary.innerHTML = skelHtml("lines");
      }
    }
    try {
      const qs = new URLSearchParams({ from: fromIso, to: toIsoVal });
      if (fast) qs.set("fast", "1");
      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      // Server hard deadline ~14–20s; brauzer biroz ko‘proq kutadi
      const timer = ctrl ? setTimeout(() => ctrl.abort(), fast ? 28000 : 45000) : null;
      const res = await fetch(`${url}?${qs}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (timer) clearTimeout(timer);
      if (!res.ok) throw new Error("fail");
      const payload = await res.json();
      if (reqId !== priceListReq && !fast) return;
      if (!fast && priceListReq !== reqId) return;
      // Bo‘sh javobni eski yaxshi ma’lumot ustiga yozmaslik
      const inChecks = Number(payload?.summary?.checks || 0);
      const inGross = Number(payload?.summary?.gross || 0);
      if (!inChecks && !inGross && hasRealSummary) return;
      currentRangeKey = key;
      applyRangePayload(key, payload);
      if (payload.error && salesRangeSummary && !hasUiCache && !inChecks && !inGross) {
        const tip = document.createElement("li");
        tip.innerHTML =
          "<span style=\"color:#c2410c\">API sekin yoki ulanmadi. Qayta urinib ko‘ring.</span>";
        salesRangeSummary.appendChild(tip);
      }
      if (
        fast &&
        Array.isArray(payload.priceLists) &&
        !payload.priceLists.some((r) => r && !r.is_total && Number(r.revenue) > 0) &&
        (inChecks > 0 || inGross > 0)
      ) {
        loadRangeStats(fromIso, toIsoVal, { force: true, fast: false });
      }
    } catch (_err) {
      if (reqId !== priceListReq) return;
      // Bir marta tez rejimda qayta urinish
      if (!fast && !force) {
        loadRangeStats(fromIso, toIsoVal, { force: true, fast: true });
        return;
      }
      seedRangeFromDaySales();
      currentPriceLists = priceListCache[key] || [];
      refreshOverviewPriceUi();
      const cachedSummary = ((data.salesStats || {})[key] || {}).summary;
      if (cachedSummary && (Number(cachedSummary.checks) > 0 || Number(cachedSummary.gross) > 0)) {
        paintCachedRange(key);
        return;
      }
      if (!hasUiCache && !cachedSummary) {
        if (salesRangeSummary) {
          salesRangeSummary.innerHTML = `<li>Davr: <strong>${formatRangeLabel(fromIso, toIsoVal)}</strong></li><li>Ma’lumot yuklanmadi. <button type="button" id="range-retry-btn" style="margin-left:6px;border:0;background:none;color:#0369a1;text-decoration:underline;cursor:pointer;font:inherit;padding:0">Qayta urinish</button></li>`;
          const btn = document.getElementById("range-retry-btn");
          if (btn) {
            btn.addEventListener("click", () => {
              loadRangeStats(fromIso, toIsoVal, { force: true, fast: true });
            });
          }
        }
      }
    }
  };

  const syncCalSelects = () => {
    if (!calMonthSel || !calYearSel) return;
    if (!calMonthSel.options.length) {
      MONTHS_UZ.forEach((name, i) => {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = name;
        calMonthSel.appendChild(opt);
      });
    }
    const yNow = todayDate.getFullYear();
    const minY = yNow - 10;
    const maxY = yNow + 1;
    const years = [];
    for (let y = minY; y <= maxY; y += 1) years.push(y);
    if (
      calYearSel.options.length !== years.length ||
      Number(calYearSel.options[0]?.value) !== minY
    ) {
      calYearSel.innerHTML = years
        .map((y) => `<option value="${y}">${y}</option>`)
        .join("");
    }
    if (viewYear < minY) viewYear = minY;
    if (viewYear > maxY) viewYear = maxY;
    calMonthSel.value = String(viewMonth);
    calYearSel.value = String(viewYear);
  };

  const updateCalBanner = () => {
    if (!calBanner) return;
    if (draftStart && draftEnd) {
      calBanner.textContent = formatRangeLabel(toIso(draftStart), toIso(draftEnd));
    } else if (draftStart) {
      calBanner.textContent = `${fmtDayUz(draftStart)} – tugash sanasini tanlang`;
    } else {
      calBanner.textContent = "Boshlanish sanasini tanlang";
    }
  };

  const inDraftRange = (d) => {
    if (!draftStart) return false;
    const end = draftEnd || draftStart;
    const a = Math.min(dayTime(draftStart), dayTime(end));
    const b = Math.max(dayTime(draftStart), dayTime(end));
    const t = dayTime(d);
    return t >= a && t <= b;
  };

  const paintCalendar = () => {
    if (!calGrid) return;
    syncCalSelects();
    updateCalBanner();
    const first = new Date(viewYear, viewMonth, 1);
    const startPad = first.getDay(); // 0 = Yakshanba
    const gridStart = new Date(viewYear, viewMonth, 1 - startPad);
    const cells = [];
    for (let i = 0; i < 42; i += 1) {
      const d = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
      const iso = toIso(d);
      const inMonth = d.getMonth() === viewMonth;
      const isStart = Boolean(draftStart && sameDay(d, draftStart));
      const isEnd = Boolean(draftEnd && sameDay(d, draftEnd));
      const inRange = inDraftRange(d);
      const isSingle = isStart && (isEnd || (!draftEnd && pickMode === "end"));
      const classes = ["sales-cal-day"];
      if (!inMonth) classes.push("is-outside");
      if (inRange) classes.push("is-in-range");
      if (isStart) classes.push("is-start");
      if (isEnd || (isStart && !draftEnd)) classes.push("is-end");
      if (isSingle || (isStart && isEnd && sameDay(draftStart, draftEnd))) classes.push("is-single");
      if (sameDay(d, todayDate)) classes.push("is-today");
      cells.push(
        `<button type="button" class="${classes.join(" ")}" data-date="${iso}" aria-label="${iso}">${d.getDate()}</button>`
      );
    }
    calGrid.innerHTML = cells.join("");
  };

  const positionCalendar = () => {
    if (!calendarEl || !dateTrigger || calendarEl.hidden) return;
    const rect = dateTrigger.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 16);
    let left = rect.right - width;
    if (left < 8) left = 8;
    if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
    let top = rect.bottom + 8;
    const estHeight = 420;
    if (top + estHeight > window.innerHeight - 8 && rect.top > estHeight) {
      top = rect.top - estHeight - 8;
    }
    calendarEl.style.position = "fixed";
    calendarEl.style.left = `${Math.round(left)}px`;
    calendarEl.style.top = `${Math.round(top)}px`;
    calendarEl.style.right = "auto";
    calendarEl.style.width = `${width}px`;
    calendarEl.style.zIndex = "4000";
  };

  const openCalendar = () => {
    if (!calendarEl || !dateTrigger) return;
    draftStart = parseIso(appliedFrom);
    draftEnd = parseIso(appliedTo);
    pickMode = "start";
    viewYear = (draftEnd || draftStart || todayDate).getFullYear();
    viewMonth = (draftEnd || draftStart || todayDate).getMonth();
    calendarEl.hidden = false;
    dateTrigger.setAttribute("aria-expanded", "true");
    datePickerRoot?.classList.add("is-open");
    paintCalendar();
    positionCalendar();
  };

  const closeCalendar = () => {
    if (!calendarEl || !dateTrigger) return;
    calendarEl.hidden = true;
    dateTrigger.setAttribute("aria-expanded", "false");
    datePickerRoot?.classList.remove("is-open");
  };

  const applyDraftRange = () => {
    if (!draftStart) {
      updateCalBanner();
      return;
    }
    const ranged = clampRange(draftStart, draftEnd || draftStart);
    if (!ranged) return;
    appliedFrom = toIso(ranged.start);
    appliedTo = toIso(ranged.end);
    draftStart = ranged.start;
    draftEnd = ranged.end;
    currentRangeKey = rangeCacheKey(appliedFrom, appliedTo);
    if (dateLabelEl) dateLabelEl.textContent = formatRangeLabel(appliedFrom, appliedTo);
    closeCalendar();
    loadRangeStats(appliedFrom, appliedTo, { force: true, fast: true });
  };

  const selectDay = (d) => {
    const day = startOfDay(d);
    if (!day) return;
    if (pickMode === "start" || !draftStart) {
      draftStart = day;
      draftEnd = null;
      pickMode = "end";
      viewYear = day.getFullYear();
      viewMonth = day.getMonth();
    } else {
      // Ikkinchi bosish — tugash (yoki bitta kun)
      const ranged = clampRange(draftStart, day);
      draftStart = ranged.start;
      draftEnd = ranged.end;
      pickMode = "start";
    }
    paintCalendar();
  };

  if (dateTrigger && calendarEl) {
    dateTrigger.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (calendarEl.hidden) openCalendar();
      else closeCalendar();
    });
    calendarEl.addEventListener("click", (e) => e.stopPropagation());
    calendarEl.addEventListener("mousedown", (e) => e.stopPropagation());

    document.getElementById("sales-cal-prev")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      viewMonth -= 1;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear -= 1;
      }
      paintCalendar();
    });
    document.getElementById("sales-cal-next")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      viewMonth += 1;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear += 1;
      }
      paintCalendar();
    });
    calMonthSel?.addEventListener("change", () => {
      viewMonth = Number(calMonthSel.value);
      paintCalendar();
    });
    calYearSel?.addEventListener("change", () => {
      viewYear = Number(calYearSel.value);
      paintCalendar();
    });
    calGrid?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const btn = e.target.closest(".sales-cal-day");
      if (!btn) return;
      const d = parseIso(btn.dataset.date);
      if (!d) return;
      selectDay(d);
    });
    // Bir kunni tez tanlash
    calGrid?.addEventListener("dblclick", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const btn = e.target.closest(".sales-cal-day");
      if (!btn) return;
      const d = parseIso(btn.dataset.date);
      if (!d) return;
      draftStart = startOfDay(d);
      draftEnd = startOfDay(d);
      pickMode = "start";
      paintCalendar();
      applyDraftRange();
    });
    document.getElementById("sales-cal-cancel")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeCalendar();
    });
    document.getElementById("sales-cal-apply")?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      applyDraftRange();
    });
    document.addEventListener(
      "mousedown",
      (e) => {
        if (!datePickerRoot || calendarEl.hidden) return;
        if (datePickerRoot.contains(e.target) || calendarEl.contains(e.target)) return;
        closeCalendar();
      },
      true
    );
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && calendarEl && !calendarEl.hidden) closeCalendar();
    });
    window.addEventListener("resize", () => {
      if (!calendarEl.hidden) positionCalendar();
    });
    window.addEventListener(
      "scroll",
      () => {
        if (!calendarEl.hidden) positionCalendar();
      },
      true
    );
    if (dateLabelEl) dateLabelEl.textContent = formatRangeLabel(appliedFrom, appliedTo);
  }

  fillOverviewPriceListFilter();
  priceListFilter?.addEventListener("change", refreshOverviewPriceUi);

  if (hasChart && lineEl) {
    const stats = data.salesStats || {
      d7: { labels: data.labels || [], totals: data.totals || [], counts: data.counts || [] },
    };
    data.salesStats = stats;
    // Dastlabki 7 kunlik paketni yangi kalitga ko'chirish
    if (stats.d7 && !stats[currentRangeKey]) {
      stats[currentRangeKey] = { ...stats.d7 };
      if (priceListCache.d7 && !priceListCache[currentRangeKey]) {
        priceListCache[currentRangeKey] = priceListCache.d7;
        currentPriceLists = priceListCache[currentRangeKey];
      }
    }
    const initial = stats[currentRangeKey] || stats.d7 || { labels: [], totals: [], counts: [] };
    const salesChart = new Chart(lineEl, {
      type: "line",
      data: {
        labels: initial.labels,
        datasets: [
          {
            label: "Tushum",
            data: initial.totals,
            borderColor: lineColor,
            backgroundColor: fillColor,
            fill: true,
            tension: 0.35,
            pointRadius: 3,
            pointBackgroundColor: lineColor,
          },
        ],
      },
      options: lineOptions,
    });
    salesChartRef = salesChart;
    paintSalesSummary(currentRangeKey, initial);
    refreshOverviewPriceUi();
    loadRangeStats(appliedFrom, appliedTo, { force: false, fast: true });
  } else if (priceListEl) {
    refreshOverviewPriceUi();
    loadRangeStats(appliedFrom, appliedTo, { force: false, fast: true });
  }
  if (typeof paintShellKpis === "function") paintShellKpis();
  seedRangeFromDaySales();
  document.addEventListener("tezpos:daySales", () => {
    seedRangeFromDaySales();
  });

  const productTileHtml = (item, color, rank, opts = {}) => {
    const topsMode = Boolean(opts.tops);
    const initial = (item.name || "?").trim().charAt(0).toUpperCase();
    const delay = rank != null ? Math.min(rank - 1, 10) * 45 : 0;
    if (topsMode) {
      const media = item.image
        ? `<img src="${item.image}" alt="" loading="lazy" width="44" height="44">`
        : `<span class="tops-row-fallback">${initial}</span>`;
      return `<article class="tops-row cab-reveal-item" style="animation-delay:${delay}ms">
        <div class="tops-row-rank">${rank != null ? rank : "—"}</div>
        <div class="tops-row-media">${media}</div>
        <div class="tops-row-name">${item.name || "—"}</div>
        <div class="tops-row-metrics">
          <div class="tops-row-metric">
            <em>Sotildi</em>
            <strong>${item.qty != null ? fmt(item.qty) : "—"}</strong>
          </div>
          <div class="tops-row-metric tops-row-metric--rev">
            <em>Tushum</em>
            <strong>${item.revenue != null ? fmt(item.revenue) : "—"}</strong>
          </div>
        </div>
      </article>`;
    }
    const media = item.image
      ? `<img src="${item.image}" alt="${item.name}" loading="lazy" width="72" height="72">`
      : `<span class="product-tile-fallback" style="--tile-accent:${color}">${initial}</span>`;
    const rankHtml = rank != null ? `<span class="product-tile-rank">#${rank}</span>` : "";
    const revHtml =
      item.revenue != null
        ? `<p class="product-tile-rev">Tushum: <strong>${fmt(item.revenue)}</strong></p>`
        : "";
    const qtyHtml =
      item.qty != null ? `<p class="product-tile-stock">Sotildi: <strong>${fmt(item.qty)}</strong></p>` : "";
    return `<article class="product-tile cab-reveal-item" style="animation-delay:${delay}ms">
      <div class="product-tile-media">${rankHtml}${media}</div>
      <div class="product-tile-body">
        <h4>${item.name}</h4>
        ${qtyHtml}
        ${revHtml}
        <p class="product-tile-stock">Qoldiq: <strong>${fmt(item.stock)}</strong></p>
        <div class="product-tile-prices">
          <span><em>Ulgurji</em>${fmt(item.wholesale)}</span>
          <span><em>Sotuv</em>${fmt(item.selling)}</span>
        </div>
      </div>
    </article>`;
  };

  // Bosh sahifadagi «Mahsulot bo‘yicha ulush» olib tashlandi

  const reportEl = document.getElementById("reportLineChart");
  const periodToggle = document.getElementById("period-toggle");
  let reportChartRef = null;

  const paintReportSummary = (summary, pack, periodKey) => {
    const fmtM = (n) => Math.round(Number(n || 0)).toLocaleString("uz-UZ");
    const checksEl = document.getElementById("report-checks");
    const grossEl = document.getElementById("report-gross");
    const costEl = document.getElementById("report-cost");
    const profitEl = document.getElementById("report-profit");
    const marginEl = document.getElementById("report-margin");
    const todayProfitEl = document.getElementById("report-today-profit");
    if (summary) {
      if (checksEl) checksEl.textContent = fmt(summary.checks);
      if (grossEl) {
        grossEl.textContent = fmtM(summary.gross);
        grossEl.setAttribute("data-fmt-money", String(summary.gross || 0));
      }
      if (costEl) {
        costEl.textContent = fmtM(summary.cost);
        costEl.setAttribute("data-fmt-money", String(summary.cost || 0));
      }
      if (profitEl) {
        profitEl.textContent = fmtM(summary.profit);
        profitEl.setAttribute("data-fmt-money", String(summary.profit || 0));
      }
      if (marginEl) marginEl.textContent = Number(summary.margin || 0).toFixed(1);
      if (todayProfitEl) {
        todayProfitEl.textContent = fmtM(summary.today_profit);
        todayProfitEl.setAttribute("data-fmt-money", String(summary.today_profit || 0));
      }
    }
    const list = document.getElementById("report-summary");
    if (list && pack) {
      // period hint — optional, summary list already updated
    }
  };

  const applyReportsPayload = (payload) => {
    if (!payload || !payload.reports) return;
    data.reports = payload.reports;
    window.TEZPOS_CHARTS = data;
    if (window.tezposCacheSet) window.tezposCacheSet("reports", payload);
    const active =
      periodToggle?.querySelector("button.active")?.dataset.period || "daily";
    const pack = data.reports[active] || data.reports.daily;
    if (hasChart && reportEl) {
      if (!reportChartRef) {
        reportChartRef = new Chart(reportEl, {
          type: "line",
          data: {
            labels: pack.labels || [],
            datasets: [
              {
                label: "Tushum",
                data: pack.totals || [],
                borderColor: "#12b3a1",
                backgroundColor: "rgba(18,179,161,0.14)",
                fill: true,
                tension: 0.35,
                pointRadius: 4,
                pointBackgroundColor: "#12b3a1",
              },
            ],
          },
          options: lineOptions,
        });
      } else {
        reportChartRef.data.labels = pack.labels || [];
        reportChartRef.data.datasets[0].data = pack.totals || [];
        reportChartRef.update();
      }
    }
    paintReportSummary(payload.summary, pack, active);
    if (payload.summary) {
      data._daySalesPack = {
        count: payload.summary.today_count,
        gross: payload.summary.today_gross,
        profit: payload.summary.today_profit,
      };
      if (typeof paintShellKpis === "function") paintShellKpis();
    }
  };

  if (reportEl || document.getElementById("report-summary")) {
    const cached =
      window.tezposCacheGet && window.tezposCacheGet("reports");
    if (cached && cached.reports) applyReportsPayload(cached);
    else if (data.reports && (data.reports.daily?.labels || []).length) {
      applyReportsPayload({ reports: data.reports, summary: null });
    }
    if (data.reportsUrl) {
      fetch(data.reportsUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((r) => r.json())
        .then((json) => {
          if (json && !json.error) applyReportsPayload(json);
        })
        .catch(() => {});
    }
    periodToggle?.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.period;
        const pack = (data.reports || {})[key];
        if (!pack) return;
        periodToggle.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        if (reportChartRef) {
          reportChartRef.data.labels = pack.labels || [];
          reportChartRef.data.datasets[0].data = pack.totals || [];
          reportChartRef.update();
        }
      });
    });
  }

  // Eski report chart bloki olib tashlandi (AJAX yuqorida)

  const signalsList = document.getElementById("signals-list");
  const signalsSelect = document.getElementById("signals-top-select");
  const signalsEmpty = document.getElementById("signals-empty");
  const renderSignals = (limit) => {
    if (!signalsList) return;
    const rows = (data.nearMin || []).slice(0, limit);
    signalsList.innerHTML = rows
      .map((s, i) => {
        const minNote =
          s.min_stock != null
            ? ` <span class="signal-min">(min ${fmt(s.min_stock)})</span>`
            : "";
        return `<article class="signal-card signal-${s.level} cab-reveal-item" style="animation-delay:${Math.min(i, 8) * 40}ms">
          <div>
            <h4>${s.title}</h4>
            <p>${s.text}${minNote}</p>
          </div>
          <div class="signal-channels">
            ${(s.channels || [])
              .map((ch) => `<span class="ch-badge ch-${String(ch).toLowerCase()}">${ch}</span>`)
              .join("")}
          </div>
        </article>`;
      })
      .join("");
    if (signalsEmpty) signalsEmpty.hidden = rows.length > 0;
  };
  if (signalsList) {
    renderSignals(Number(signalsSelect?.value || 10));
    signalsSelect?.addEventListener("change", () => {
      renderSignals(Number(signalsSelect.value));
    });
    document.addEventListener("tezpos:catalog", () => {
      renderSignals(Number(signalsSelect?.value || 10));
      if (typeof paintShellKpis === "function") paintShellKpis();
    });
  }

  const productsSelect = document.getElementById("tops-products-select");
  const productsGrid = document.getElementById("tops-products-grid");
  const topsDatePicker = document.getElementById("tops-date-picker");
  const topsDateTrigger = document.getElementById("tops-date-trigger");
  const topsDateLabel = document.getElementById("tops-date-label");
  const topsCalendar = document.getElementById("tops-calendar");
  const topsCalBanner = document.getElementById("tops-calendar-banner");
  const topsCalGrid = document.getElementById("tops-calendar-grid");
  const topsCalMonth = document.getElementById("tops-cal-month");
  const topsCalYear = document.getElementById("tops-cal-year");
  const topsPeriodHint = document.getElementById("tops-period-hint");

  let topsFrom = appliedFrom;
  let topsTo = appliedTo;
  let topsDraftStart = parseIso(topsFrom);
  let topsDraftEnd = parseIso(topsTo);
  let topsViewYear = (topsDraftEnd || todayDate).getFullYear();
  let topsViewMonth = (topsDraftEnd || todayDate).getMonth();
  let topsPickMode = "start";
  let topsReq = 0;
  const topsCache = {};

  const renderProducts = (limit) => {
    const rows = (data.topProducts || []).slice(0, limit);
    if (!productsGrid) return;
    productsGrid.className = "tops-list";
    productsGrid.innerHTML = rows.length
      ? rows
          .map((row, i) => productTileHtml(row, palette[i % palette.length], i + 1, { tops: true }))
          .join("")
      : `<p class="cabinet-hint">Bu sana oralig‘ida top tovarlar yo‘q.</p>`;
  };

  const syncTopsHint = () => {
    const label = formatRangeLabel(topsFrom, topsTo);
    if (topsDateLabel) topsDateLabel.textContent = label;
    if (topsPeriodHint) {
      topsPeriodHint.textContent = `${label} — eng yaxshi sotilgan mahsulotlar`;
    }
  };

  const updateTopsBanner = () => {
    if (!topsCalBanner) return;
    if (!topsDraftStart) {
      topsCalBanner.textContent = "Boshlanish sanasini tanlang";
      return;
    }
    if (!topsDraftEnd) {
      topsCalBanner.textContent = `${fmtDayUz(topsDraftStart)} → tugash sanasini tanlang`;
      return;
    }
    topsCalBanner.textContent = formatRangeLabel(toIso(topsDraftStart), toIso(topsDraftEnd));
  };

  const syncTopsCalSelects = () => {
    if (!topsCalMonth || !topsCalYear) return;
    if (!topsCalMonth.options.length) {
      MONTHS_UZ.forEach((name, i) => {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = name;
        topsCalMonth.appendChild(opt);
      });
    }
    if (!topsCalYear.options.length) {
      const y0 = todayDate.getFullYear();
      for (let y = y0 - 5; y <= y0 + 1; y += 1) {
        const opt = document.createElement("option");
        opt.value = String(y);
        opt.textContent = String(y);
        topsCalYear.appendChild(opt);
      }
    }
    topsCalMonth.value = String(topsViewMonth);
    topsCalYear.value = String(topsViewYear);
  };

  const paintTopsCalendar = () => {
    if (!topsCalGrid) return;
    syncTopsCalSelects();
    updateTopsBanner();
    const first = new Date(topsViewYear, topsViewMonth, 1);
    const startPad = first.getDay();
    const daysInMonth = new Date(topsViewYear, topsViewMonth + 1, 0).getDate();
    const cells = [];
    for (let i = 0; i < startPad; i += 1) cells.push(`<span class="sales-cal-pad"></span>`);
    for (let day = 1; day <= daysInMonth; day += 1) {
      const d = new Date(topsViewYear, topsViewMonth, day);
      const iso = toIso(d);
      const classes = ["sales-cal-day"];
      const isStart = topsDraftStart && sameDay(d, topsDraftStart);
      const isEnd = topsDraftEnd && sameDay(d, topsDraftEnd);
      const inRange =
        topsDraftStart &&
        topsDraftEnd &&
        dayTime(d) >= dayTime(topsDraftStart) &&
        dayTime(d) <= dayTime(topsDraftEnd);
      if (inRange) classes.push("is-in-range");
      if (isStart) classes.push("is-start");
      if (isEnd || (isStart && !topsDraftEnd)) classes.push("is-end");
      if (isStart && isEnd) classes.push("is-single");
      if (sameDay(d, todayDate)) classes.push("is-today");
      cells.push(
        `<button type="button" class="${classes.join(" ")}" data-date="${iso}" aria-label="${iso}">${day}</button>`
      );
    }
    topsCalGrid.innerHTML = cells.join("");
  };

  const positionTopsCalendar = () => {
    if (!topsCalendar || !topsDateTrigger || topsCalendar.hidden) return;
    const rect = topsDateTrigger.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 16);
    let left = rect.right - width;
    if (left < 8) left = 8;
    if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
    let top = rect.bottom + 8;
    const estHeight = 420;
    if (top + estHeight > window.innerHeight - 8 && rect.top > estHeight) {
      top = rect.top - estHeight - 8;
    }
    topsCalendar.style.position = "fixed";
    topsCalendar.style.left = `${Math.round(left)}px`;
    topsCalendar.style.top = `${Math.round(top)}px`;
    topsCalendar.style.right = "auto";
    topsCalendar.style.width = `${width}px`;
    topsCalendar.style.zIndex = "4000";
  };

  const openTopsCalendar = () => {
    if (!topsCalendar || !topsDateTrigger) return;
    topsDraftStart = parseIso(topsFrom);
    topsDraftEnd = parseIso(topsTo);
    topsPickMode = "start";
    topsViewYear = (topsDraftEnd || topsDraftStart || todayDate).getFullYear();
    topsViewMonth = (topsDraftEnd || topsDraftStart || todayDate).getMonth();
    topsCalendar.hidden = false;
    topsDateTrigger.setAttribute("aria-expanded", "true");
    topsDatePicker?.classList.add("is-open");
    paintTopsCalendar();
    positionTopsCalendar();
  };

  const closeTopsCalendar = () => {
    if (!topsCalendar || !topsDateTrigger) return;
    topsCalendar.hidden = true;
    topsDateTrigger.setAttribute("aria-expanded", "false");
    topsDatePicker?.classList.remove("is-open");
  };

  const loadTopProducts = async (fromIso, toIsoVal, { force = false } = {}) => {
    const key = `${fromIso}_${toIsoVal}`;
    if (!Object.keys(topsCache).length && data._topsCache) {
      Object.assign(topsCache, data._topsCache);
    }
    if (!force && topsCache[key]) {
      data.topProducts = topsCache[key];
      renderProducts(Number(productsSelect?.value || 10));
      return;
    }
    // Boshqa kalitdan kesh — darhol ko‘rsatish
    if (!force && !topsCache[key] && (data.topProducts || []).length) {
      renderProducts(Number(productsSelect?.value || 10));
    }
    const url = data.topStatsUrl;
    if (!url || !productsGrid) return;
    const reqId = ++topsReq;
    const hasCache = Boolean(topsCache[key] || (data.topProducts || []).length);
    if (!hasCache) {
      productsGrid.className = "tops-list";
      productsGrid.innerHTML = skelHtml("lines", 8);
    }
    try {
      const qs = new URLSearchParams({
        from: fromIso,
        to: toIsoVal,
        limit: "100",
      });
      const res = await fetch(`${url}?${qs}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("fail");
      const payload = await res.json();
      if (reqId !== topsReq) return;
      const rows = Array.isArray(payload.topProducts) ? payload.topProducts : [];
      topsCache[key] = rows;
      data.topProducts = rows;
      data._topsCache = topsCache;
      if (window.tezposCacheSet) window.tezposCacheSet("tops", topsCache);
      renderProducts(Number(productsSelect?.value || 10));
    } catch (_err) {
      if (reqId !== topsReq) return;
      if (!hasCache) {
        productsGrid.innerHTML = `<p class="cabinet-hint">Top tovarlar yuklanmadi. Qayta urinib ko‘ring.</p>`;
      }
    }
  };

  const applyTopsDraftRange = () => {
    if (!topsDraftStart) {
      updateTopsBanner();
      return;
    }
    const ranged = clampRange(topsDraftStart, topsDraftEnd || topsDraftStart);
    if (!ranged) return;
    topsFrom = toIso(ranged.start);
    topsTo = toIso(ranged.end);
    topsDraftStart = ranged.start;
    topsDraftEnd = ranged.end;
    syncTopsHint();
    closeTopsCalendar();
    loadTopProducts(topsFrom, topsTo, { force: true });
  };

  const selectTopsDay = (d) => {
    const day = startOfDay(d);
    if (!day) return;
    if (topsPickMode === "start" || !topsDraftStart) {
      topsDraftStart = day;
      topsDraftEnd = null;
      topsPickMode = "end";
      topsViewYear = day.getFullYear();
      topsViewMonth = day.getMonth();
    } else {
      const ranged = clampRange(topsDraftStart, day);
      topsDraftStart = ranged.start;
      topsDraftEnd = ranged.end;
      topsPickMode = "start";
    }
    paintTopsCalendar();
  };

  if (productsGrid) {
    syncTopsHint();
    if (topsDateTrigger && topsCalendar) {
      topsDateTrigger.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (topsCalendar.hidden) openTopsCalendar();
        else closeTopsCalendar();
      });
      topsCalendar.addEventListener("click", (e) => e.stopPropagation());
      topsCalendar.addEventListener("mousedown", (e) => e.stopPropagation());
      document.getElementById("tops-cal-prev")?.addEventListener("click", (e) => {
        e.preventDefault();
        topsViewMonth -= 1;
        if (topsViewMonth < 0) {
          topsViewMonth = 11;
          topsViewYear -= 1;
        }
        paintTopsCalendar();
      });
      document.getElementById("tops-cal-next")?.addEventListener("click", (e) => {
        e.preventDefault();
        topsViewMonth += 1;
        if (topsViewMonth > 11) {
          topsViewMonth = 0;
          topsViewYear += 1;
        }
        paintTopsCalendar();
      });
      topsCalMonth?.addEventListener("change", () => {
        topsViewMonth = Number(topsCalMonth.value);
        paintTopsCalendar();
      });
      topsCalYear?.addEventListener("change", () => {
        topsViewYear = Number(topsCalYear.value);
        paintTopsCalendar();
      });
      topsCalGrid?.addEventListener("click", (e) => {
        e.preventDefault();
        const btn = e.target.closest(".sales-cal-day");
        if (!btn) return;
        const d = parseIso(btn.dataset.date);
        if (!d) return;
        selectTopsDay(d);
      });
      topsCalGrid?.addEventListener("dblclick", (e) => {
        e.preventDefault();
        const btn = e.target.closest(".sales-cal-day");
        if (!btn) return;
        const d = parseIso(btn.dataset.date);
        if (!d) return;
        topsDraftStart = startOfDay(d);
        topsDraftEnd = startOfDay(d);
        topsPickMode = "start";
        paintTopsCalendar();
        applyTopsDraftRange();
      });
      document.getElementById("tops-cal-cancel")?.addEventListener("click", (e) => {
        e.preventDefault();
        closeTopsCalendar();
      });
      document.getElementById("tops-cal-apply")?.addEventListener("click", (e) => {
        e.preventDefault();
        applyTopsDraftRange();
      });
      document.addEventListener(
        "mousedown",
        (e) => {
          if (!topsDatePicker || !topsCalendar || topsCalendar.hidden) return;
          if (topsDatePicker.contains(e.target) || topsCalendar.contains(e.target)) return;
          closeTopsCalendar();
        },
        true
      );
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && topsCalendar && !topsCalendar.hidden) closeTopsCalendar();
      });
      window.addEventListener("resize", () => {
        if (topsCalendar && !topsCalendar.hidden) positionTopsCalendar();
      });
    }
    productsSelect?.addEventListener("change", () => {
      renderProducts(Number(productsSelect.value));
    });
    loadTopProducts(topsFrom, topsTo, { force: true });
  }

  const customersBody = document.getElementById("tops-customers-body");
  const customersSelect = document.getElementById("tops-customers-select");
  const renderCustomers = (limit) => {
    if (!customersBody) return;
    const rows = (data.topCustomers || []).slice(0, limit);
    customersBody.innerHTML = rows.length
      ? rows
          .map(
            (row, i) => `<tr>
              <td>${i + 1}</td>
              <td>${row.customer_name}</td>
              <td>${row.orders}</td>
              <td>${fmt(row.total)}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4">Ma'lumot yo‘q</td></tr>`;
  };
  if (customersBody) {
    renderCustomers(Number(customersSelect?.value || 10));
    customersSelect?.addEventListener("change", () => {
      renderCustomers(Number(customersSelect.value));
    });
  }

  const labelHub = document.getElementById("label-hub");
  const labelDesigner = document.getElementById("label-designer");
  const labelPrintView = document.getElementById("label-print-view");
  if (labelHub && labelDesigner) {
    const STORAGE_KEY = "tezpos_label_template_v2";
    const EL_KEYS = [
      "name", "price", "old_price", "wholesale", "sku", "created",
      "custom1", "custom2", "custom3", "old_label", "print_date", "barcode", "logo",
    ];
    const box = (x, y, w, h, extra = {}) => ({
      size: 14, weight: 700, align: "center", text: "", rotate: 0, x, y, w, h, ...extra,
    });
    const DEFAULT_STYLES = {
      name: box(6, 6, 88, 38, { size: 14, weight: 700 }),
      price: box(6, 50, 88, 36, { size: 22, weight: 800 }),
      old_price: box(6, 40, 88, 12, { size: 12, weight: 500 }),
      wholesale: box(6, 40, 88, 12, { size: 12, weight: 600 }),
      sku: box(6, 88, 88, 10, { size: 11, weight: 500 }),
      created: box(6, 88, 88, 10, { size: 10, weight: 400 }),
      custom1: box(6, 78, 88, 10, { size: 12, weight: 600 }),
      custom2: box(6, 82, 88, 10, { size: 12, weight: 600 }),
      custom3: box(6, 86, 88, 10, { size: 12, weight: 600 }),
      old_label: box(6, 36, 88, 8, { size: 10, weight: 500, text: "Eski narx" }),
      print_date: box(6, 90, 88, 8, { size: 10, weight: 400 }),
      barcode: box(6, 90, 88, 8, { size: 11, weight: 600 }),
      logo: box(6, 2, 88, 8, { size: 11, weight: 700, text: "TezPOS" }),
    };
    const DEFAULT_ENABLED = {
      name: true, price: true, old_price: false, wholesale: false, sku: false,
      created: false, custom1: false, custom2: false, custom3: false,
      old_label: false, print_date: false, barcode: false, logo: false,
    };

    const state = {
      name: "Cennik 38x58",
      widthMm: 38,
      heightMm: 58,
      formatPrice: true,
      priceSuffix: "",
      enabled: { ...DEFAULT_ENABLED },
      styles: JSON.parse(JSON.stringify(DEFAULT_STYLES)),
      activeEl: "name",
      sample: {
        name: "Kungaboqar yog'i Laska, tozalangan 1 litr",
        price: 30000,
        wholesale: 27000,
        cost: 25000,
        barcode: "4780000000001",
        sku: "YG-001",
      },
    };

    const elSize = document.getElementById("ld-font-size");
    const elWeight = document.getElementById("ld-font-weight");
    const elAlign = document.getElementById("ld-font-align");
    const elRotate = document.getElementById("ld-font-rotate");
    const elText = document.getElementById("ld-font-text");
    const preview = document.getElementById("ld-preview");
    const previewChrome = document.getElementById("ld-preview-chrome");
    const previewCard = document.getElementById("ld-preview-card");
    const miniPreview = document.getElementById("ld-mini-preview");
    let drag = null;

    if (elSize) {
      for (let s = 8; s <= 48; s += 2) {
        const opt = document.createElement("option");
        opt.value = String(s);
        opt.textContent = `${s}px`;
        elSize.appendChild(opt);
      }
    }

    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    const escHtml = (s) =>
      String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
    const parseMoney = (v) => {
      if (typeof v === "number") return Number.isFinite(v) ? v : 0;
      const s = String(v ?? "")
        .trim()
        .replace(/\u00a0/g, "")
        .replace(/\s/g, "")
        .replace(/,/g, ".");
      const n = Number(s);
      return Number.isFinite(n) ? n : 0;
    };

    const normalizeStyle = (k, st) => {
      const base = DEFAULT_STYLES[k] || box(5, 5, 90, 20);
      return {
        ...base,
        ...st,
        x: Number(st?.x ?? base.x),
        y: Number(st?.y ?? base.y),
        w: Number(st?.w ?? base.w),
        h: Number(st?.h ?? base.h),
        rotate: Number(st?.rotate ?? 0),
        size: Number(st?.size ?? base.size),
        weight: Number(st?.weight ?? base.weight),
        align: st?.align || base.align,
        text: st?.text ?? base.text,
      };
    };

    const loadTpl = () => {
      try {
        const raw = localStorage.getItem(STORAGE_KEY) || localStorage.getItem("tezpos_label_template_v1");
        if (!raw) return;
        const data = JSON.parse(raw);
        if (data.name) state.name = data.name;
        if (data.widthMm) state.widthMm = Number(data.widthMm);
        if (data.heightMm) state.heightMm = Number(data.heightMm);
        if (typeof data.formatPrice === "boolean") state.formatPrice = data.formatPrice;
        if (typeof data.priceSuffix === "string") state.priceSuffix = data.priceSuffix;
        if (data.enabled) state.enabled = { ...DEFAULT_ENABLED, ...data.enabled };
        if (data.styles) {
          EL_KEYS.forEach((k) => {
            state.styles[k] = normalizeStyle(k, { ...DEFAULT_STYLES[k], ...(data.styles[k] || {}) });
          });
        }
      } catch (_e) {
        /* ignore */
      }
    };

    const saveTpl = () => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          name: state.name,
          widthMm: state.widthMm,
          heightMm: state.heightMm,
          formatPrice: state.formatPrice,
          priceSuffix: state.priceSuffix,
          enabled: state.enabled,
          styles: state.styles,
        })
      );
    };

    const fmtPrice = (n) => {
      const v = Number(n || 0);
      const body = state.formatPrice
        ? Math.round(v).toLocaleString("uz-UZ")
        : String(Math.round(v));
      const suf = (state.priceSuffix || "").trim();
      return suf ? `${body} ${suf}` : body;
    };

    const todayStr = () => {
      const d = new Date();
      return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
    };

    const valueFor = (key, product, priceType) => {
      const st = state.styles[key] || {};
      const custom = (st.text || "").trim();
      const sell = Number(product.price || 0);
      const whole = Number(product.wholesale || 0);
      const priceVal = priceType === "wholesale" && whole > 0 ? whole : sell;
      switch (key) {
        case "name":
          return custom || product.name || "";
        case "price":
          return fmtPrice(priceVal);
        case "old_price": {
          const old = Number(product.cost) > 0 ? product.cost * 1.15 : sell * 1.1;
          return fmtPrice(old);
        }
        case "wholesale":
          return fmtPrice(whole || sell);
        case "sku":
          return custom || product.sku || product.barcode || "";
        case "created":
        case "print_date":
          return custom || todayStr();
        case "custom1":
        case "custom2":
        case "custom3":
        case "old_label":
          return custom || (key === "old_label" ? "Eski narx" : "");
        case "barcode":
          return custom || product.barcode || product.sku || "";
        case "logo":
          return custom || "TezPOS";
        default:
          return custom;
      }
    };

    const elWrapStyle = (st) =>
      `left:${st.x}%;top:${st.y}%;width:${st.w}%;height:${st.h}%;` +
      `transform:rotate(${Number(st.rotate) || 0}deg);transform-origin:center center;`;
    const elBodyStyle = (st) =>
      `font-size:${st.size}px;font-weight:${st.weight};text-align:${st.align};`;

    const handlesHtml = () =>
      ["nw", "n", "ne", "e", "se", "s", "sw", "w"]
        .map((hnd) => `<span class="ld-handle ld-handle--${hnd}" data-handle="${hnd}"></span>`)
        .join("") +
      `<span class="ld-rotate-stem" data-handle="rotate"></span>` +
      `<span class="ld-handle ld-handle--rotate" data-handle="rotate" title="Aylantirish"></span>`;

    const normAngle = (deg) => {
      let a = Number(deg) || 0;
      a = ((a % 360) + 360) % 360;
      return Math.round(a);
    };

    const renderPreview = () => {
      if (!preview || !previewCard) return;
      const mm = 3.6;
      const w = Math.max(90, state.widthMm * mm);
      const h = Math.max(90, state.heightMm * mm);
      previewCard.style.width = `${w}px`;
      previewCard.style.height = `${h}px`;
      const parts = [];
      let chromeHtml = "";
      EL_KEYS.forEach((key) => {
        if (!state.enabled[key]) return;
        const st = normalizeStyle(key, state.styles[key]);
        st.rotate = normAngle(st.rotate);
        state.styles[key] = st;
        const text = valueFor(key, state.sample);
        if (!text && String(key).startsWith("custom")) return;
        const strike = key === "old_price" ? " ld-strike" : "";
        const justify =
          st.align === "left" ? "flex-start" : st.align === "right" ? "flex-end" : "center";
        parts.push(
          `<div class="ld-el-wrap" data-el="${key}" style="${elWrapStyle(st)}">` +
            `<div class="ld-el-body ld-el--${key}${strike}" style="${elBodyStyle(st)};justify-content:${justify}">` +
            `<div class="ld-el-text">${escHtml(text)}</div></div></div>`
        );
        if (state.activeEl === key) {
          chromeHtml =
            `<div class="ld-el-chrome" data-el="${key}" style="${elWrapStyle(st)}">${handlesHtml()}</div>`;
        }
      });
      preview.innerHTML = parts.join("") || `<span class="ld-empty">Element yoqing</span>`;
      if (previewChrome) previewChrome.innerHTML = chromeHtml;
      if (miniPreview) {
        const miniParts = EL_KEYS.filter((k) => state.enabled[k])
          .map((key) => {
            const st = state.styles[key];
            const text = valueFor(key, state.sample);
            if (!text && String(key).startsWith("custom")) return "";
            const justify =
              st.align === "left" ? "flex-start" : st.align === "right" ? "flex-end" : "center";
            return (
              `<div class="ld-el-wrap" style="${elWrapStyle(st)}">` +
              `<div class="ld-el-body" style="${elBodyStyle(st)};justify-content:${justify}">` +
              `<div class="ld-el-text">${escHtml(text)}</div></div></div>`
            );
          })
          .join("");
        miniPreview.innerHTML = `<div class="ld-mini-card" style="aspect-ratio:${state.widthMm}/${state.heightMm}">${miniParts}</div>`;
      }
      const title = document.getElementById("ld-tpl-title");
      if (title) title.textContent = state.name || "Shablon";
    };

    const syncForm = () => {
      const nameEl = document.getElementById("ld-tpl-name");
      const wEl = document.getElementById("ld-tpl-w");
      const hEl = document.getElementById("ld-tpl-h");
      const fmtEl = document.getElementById("ld-fmt-price");
      const sufEl = document.getElementById("ld-price-suffix");
      if (nameEl) nameEl.value = state.name;
      if (wEl) wEl.value = String(state.widthMm);
      if (hEl) hEl.value = String(state.heightMm);
      if (fmtEl) fmtEl.checked = state.formatPrice;
      if (sufEl) sufEl.value = state.priceSuffix;
      document.querySelectorAll("#ld-elements .ld-check").forEach((lab) => {
        const key = lab.dataset.el;
        const cb = lab.querySelector("input");
        if (cb && key) cb.checked = Boolean(state.enabled[key]);
        lab.classList.toggle("is-active-el", state.activeEl === key);
      });
      const st = normalizeStyle(state.activeEl, state.styles[state.activeEl]);
      state.styles[state.activeEl] = st;
      if (elSize) elSize.value = String(st.size);
      if (elWeight) elWeight.value = String(st.weight);
      if (elAlign) elAlign.value = st.align || "center";
      if (elRotate) elRotate.value = String(st.rotate || 0);
      if (elText) elText.value = st.text || "";
      const textWrap = document.getElementById("ld-text-wrap");
      if (textWrap) {
        textWrap.hidden = ![
          "name", "custom1", "custom2", "custom3", "old_label", "logo", "sku", "barcode", "created", "print_date",
        ].includes(state.activeEl);
      }
      renderPreview();
    };

    const paintActiveOnly = (key) => {
      if (!preview) return;
      if (previewChrome) {
        const stChrome = normalizeStyle(key, state.styles[key]);
        stChrome.rotate = normAngle(stChrome.rotate);
        state.styles[key] = stChrome;
        previewChrome.innerHTML =
          `<div class="ld-el-chrome" data-el="${key}" style="${elWrapStyle(stChrome)}">${handlesHtml()}</div>`;
      }
      document.querySelectorAll("#ld-elements .ld-check").forEach((lab) => {
        lab.classList.toggle("is-active-el", lab.dataset.el === key);
      });
      const st = normalizeStyle(key, state.styles[key]);
      st.rotate = normAngle(st.rotate);
      state.styles[key] = st;
      if (elSize) elSize.value = String(st.size);
      if (elWeight) elWeight.value = String(st.weight);
      if (elAlign) elAlign.value = st.align || "center";
      if (elRotate) elRotate.value = String(st.rotate || 0);
      if (elText) elText.value = st.text || "";
    };

    const setActive = (key, { fullSync = true } = {}) => {
      if (!EL_KEYS.includes(key)) return;
      state.activeEl = key;
      if (!state.enabled[key]) state.enabled[key] = true;
      if (fullSync) syncForm();
      else paintActiveOnly(key);
    };

    const showMode = (mode) => {
      labelHub.hidden = mode !== "hub";
      labelDesigner.hidden = mode !== "design";
      if (labelPrintView) labelPrintView.hidden = mode !== "print";
      if (mode === "design" || mode === "print") renderPreview();
      if (mode === "print") {
        if ((data.products || []).length) renderLabelsTable();
        else if (typeof window.tezposEnsureCatalog === "function") {
          window.tezposEnsureCatalog({ force: true });
        }
      }
    };

    loadTpl();
    EL_KEYS.forEach((k) => {
      state.styles[k] = normalizeStyle(k, state.styles[k]);
    });
    syncForm();
    showMode("hub");

    document.getElementById("ld-open-design")?.addEventListener("click", () => showMode("design"));
    document.getElementById("ld-open-print")?.addEventListener("click", () => showMode("print"));
    document.getElementById("ld-back-hub")?.addEventListener("click", () => showMode("hub"));
    document.getElementById("ld-print-back")?.addEventListener("click", () => showMode("hub"));

    document.getElementById("ld-tpl-name")?.addEventListener("input", (e) => {
      state.name = e.target.value;
      const title = document.getElementById("ld-tpl-title");
      if (title) title.textContent = state.name;
    });
    document.getElementById("ld-tpl-w")?.addEventListener("input", (e) => {
      state.widthMm = Math.max(20, Number(e.target.value) || 38);
      renderPreview();
    });
    document.getElementById("ld-tpl-h")?.addEventListener("input", (e) => {
      state.heightMm = Math.max(20, Number(e.target.value) || 58);
      renderPreview();
    });
    document.getElementById("ld-fmt-price")?.addEventListener("change", (e) => {
      state.formatPrice = e.target.checked;
      renderPreview();
    });
    document.getElementById("ld-price-suffix")?.addEventListener("input", (e) => {
      state.priceSuffix = e.target.value;
      renderPreview();
    });
    document.getElementById("ld-elements")?.addEventListener("change", (e) => {
      const lab = e.target.closest(".ld-check");
      if (!lab) return;
      const key = lab.dataset.el;
      state.enabled[key] = e.target.checked;
      if (e.target.checked) state.activeEl = key;
      syncForm();
    });
    document.getElementById("ld-elements")?.addEventListener("click", (e) => {
      const lab = e.target.closest(".ld-check");
      if (!lab || e.target.matches("input")) return;
      setActive(lab.dataset.el);
    });

    const applyBoxDom = (key) => {
      const st = state.styles[key];
      const wrap = preview?.querySelector(`.ld-el-wrap[data-el="${key}"]`);
      if (wrap) {
        wrap.style.cssText = elWrapStyle(st);
        const body = wrap.querySelector(".ld-el-body");
        if (body) {
          const justify =
            st.align === "left" ? "flex-start" : st.align === "right" ? "flex-end" : "center";
          body.style.cssText = `${elBodyStyle(st)};justify-content:${justify}`;
        }
      }
      const chrome = previewChrome?.querySelector(`.ld-el-chrome[data-el="${key}"]`);
      if (chrome) chrome.style.cssText = elWrapStyle(st);
    };

    previewCard?.addEventListener("pointerdown", (e) => {
      const handle = e.target.closest(".ld-handle, .ld-rotate-stem");
      const chromeEl = e.target.closest(".ld-el-chrome");
      const wrapEl = e.target.closest(".ld-el-wrap");
      const key = (chromeEl || wrapEl)?.dataset?.el;
      if (!key || !preview) return;
      setActive(key, { fullSync: false });
      const liveEl = preview.querySelector(`.ld-el-wrap[data-el="${key}"]`);
      if (!liveEl) return;
      const st = normalizeStyle(key, state.styles[key]);
      state.styles[key] = st;
      const rect = preview.getBoundingClientRect();
      const pref = preview.getBoundingClientRect();
      const stPct = state.styles[key];
      const cx = pref.left + ((stPct.x + stPct.w / 2) / 100) * pref.width;
      const cy = pref.top + ((stPct.y + stPct.h / 2) / 100) * pref.height;
      const mode = handle ? handle.dataset.handle : "move";
      drag = {
        key,
        mode,
        startX: e.clientX,
        startY: e.clientY,
        orig: { x: st.x, y: st.y, w: st.w, h: st.h, rotate: normAngle(st.rotate) },
        rectW: Math.max(rect.width, 1),
        rectH: Math.max(rect.height, 1),
        cx,
        cy,
        startAngle: (Math.atan2(e.clientY - cy, e.clientX - cx) * 180) / Math.PI,
        shiftAxis: null,
      };
      try {
        (handle || liveEl).setPointerCapture(e.pointerId);
      } catch (_err) {
        /* ignore */
      }
      e.preventDefault();
      e.stopPropagation();
    });

    const onPointerMove = (e) => {
      if (!drag) return;
      const st = state.styles[drag.key];
      if (!st) return;
      let dx = ((e.clientX - drag.startX) / drag.rectW) * 100;
      let dy = ((e.clientY - drag.startY) / drag.rectH) * 100;
      const o = drag.orig;

      if (drag.mode === "rotate") {
        const ang = (Math.atan2(e.clientY - drag.cy, e.clientX - drag.cx) * 180) / Math.PI;
        let next = o.rotate + (ang - drag.startAngle);
        if (e.shiftKey) next = Math.round(next / 15) * 15;
        st.rotate = normAngle(next);
        if (elRotate) elRotate.value = String(st.rotate);
        applyBoxDom(drag.key);
        e.preventDefault();
        return;
      }

      if (drag.mode === "move") {
        if (e.shiftKey) {
          if (!drag.shiftAxis) {
            drag.shiftAxis = Math.abs(dx) >= Math.abs(dy) ? "x" : "y";
          }
          if (drag.shiftAxis === "x") dy = 0;
          else dx = 0;
        } else {
          drag.shiftAxis = null;
        }
        st.x = o.x + dx;
        st.y = o.y + dy;
      } else {
        // Aylanishni hisobga olib — ekran surishini lokal X/Y ga aylantirish
        // (yon o‘rtadagi nuqtalar chap/o‘ngdan siqadi)
        const rad = ((Number(o.rotate) || 0) * Math.PI) / 180;
        const cos = Math.cos(rad);
        const sin = Math.sin(rad);
        const localDx = dx * cos + dy * sin;
        const localDy = -dx * sin + dy * cos;
        let x = o.x;
        let y = o.y;
        let w = o.w;
        let h = o.h;
        const m = drag.mode || "";
        if (m.includes("e")) w = Math.max(6, o.w + localDx);
        if (m.includes("s")) h = Math.max(6, o.h + localDy);
        if (m.includes("w")) {
          w = Math.max(6, o.w - localDx);
          x = o.x + o.w - w;
        }
        if (m.includes("n")) {
          h = Math.max(6, o.h - localDy);
          y = o.y + o.h - h;
        }
        st.x = x;
        st.y = y;
        st.w = w;
        st.h = h;
      }
      applyBoxDom(drag.key);
      e.preventDefault();
    };
    const onPointerUp = () => {
      if (!drag) return;
      drag = null;
      // Mini-preview ni yangilash
      renderPreview();
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);

    // Strelkalar bilan nozik surish (Shift = 5%)
    document.addEventListener("keydown", (e) => {
      if (labelDesigner.hidden || drag) return;
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
      const tag = (e.target && e.target.tagName) || "";
      if (["INPUT", "SELECT", "TEXTAREA"].includes(tag)) return;
      const st = state.styles[state.activeEl];
      if (!st || !state.enabled[state.activeEl]) return;
      const step = e.shiftKey ? 5 : 1;
      if (e.key === "ArrowLeft") st.x -= step;
      if (e.key === "ArrowRight") st.x += step;
      if (e.key === "ArrowUp") st.y -= step;
      if (e.key === "ArrowDown") st.y += step;
      applyBoxDom(state.activeEl);
      e.preventDefault();
    });

    elSize?.addEventListener("change", () => {
      state.styles[state.activeEl].size = Number(elSize.value);
      renderPreview();
    });
    elWeight?.addEventListener("change", () => {
      state.styles[state.activeEl].weight = Number(elWeight.value);
      renderPreview();
    });
    elAlign?.addEventListener("change", () => {
      state.styles[state.activeEl].align = elAlign.value;
      renderPreview();
    });
    elRotate?.addEventListener("change", () => {
      state.styles[state.activeEl].rotate = normAngle(elRotate.value);
      elRotate.value = String(state.styles[state.activeEl].rotate);
      renderPreview();
    });
    elRotate?.addEventListener("input", () => {
      state.styles[state.activeEl].rotate = normAngle(elRotate.value);
      applyBoxDom(state.activeEl);
    });
    const bumpRotate = (delta) => {
      const st = state.styles[state.activeEl];
      if (!st) return;
      st.rotate = normAngle((st.rotate || 0) + delta);
      if (elRotate) elRotate.value = String(st.rotate);
      applyBoxDom(state.activeEl);
    };
    document.getElementById("ld-rot-left")?.addEventListener("click", () => bumpRotate(-15));
    document.getElementById("ld-rot-right")?.addEventListener("click", () => bumpRotate(15));
    elText?.addEventListener("input", () => {
      state.styles[state.activeEl].text = elText.value;
      renderPreview();
    });
    document.getElementById("ld-save-btn")?.addEventListener("click", () => {
      saveTpl();
      alert("Shablon saqlandi. Chop etishda shu ko‘rinishda chiqadi.");
    });
    document.getElementById("ld-reset-btn")?.addEventListener("click", () => {
      state.name = "Cennik 38x58";
      state.widthMm = 38;
      state.heightMm = 58;
      state.formatPrice = true;
      state.priceSuffix = "";
      state.enabled = { ...DEFAULT_ENABLED };
      state.styles = JSON.parse(JSON.stringify(DEFAULT_STYLES));
      state.activeEl = "name";
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem("tezpos_label_template_v1");
      syncForm();
    });

    const productFromEl = (el) => {
      const row = el?.closest?.("tr") || el;
      const src = el?.dataset?.price != null && el.dataset.price !== "" ? el : row;
      return {
        name: src.dataset.name || row.dataset?.name || "",
        price: parseMoney(src.dataset.price ?? row.dataset?.price),
        wholesale: parseMoney(src.dataset.wholesale ?? row.dataset?.wholesale),
        cost: parseMoney(src.dataset.cost ?? row.dataset?.cost),
        barcode: src.dataset.barcode || row.dataset?.barcode || "",
        sku: src.dataset.sku || row.dataset?.sku || "",
      };
    };

    const moneyCell = (n) => {
      const v = Number(n || 0);
      if (!v) return "—";
      return Math.round(v).toLocaleString("uz-UZ");
    };

    const renderLabelsTable = () => {
      const table = document.getElementById("labels-table");
      const tbody = table?.querySelector("tbody");
      if (!tbody) return;
      const list = data.products || [];
      tbody.dataset.loaded = "1";
      if (!list.length) {
        tbody.innerHTML =
          '<tr><td colspan="5" class="cabinet-hint">Mahsulot yo‘q. Katalog yuklanmagan bo‘lsa — sahifani yangilang.</td></tr>';
        return;
      }
      tbody.innerHTML = list
        .map((p) => {
          const name = escHtml(p.name || "");
          const barcode = escHtml(p.barcode || p.sku || "");
          const sku = escHtml(p.sku || p.barcode || "");
          const selling = Number(p.selling_price || 0);
          const wholesale = Number(p.wholesale_price || 0);
          const cost = Number(p.cost_price || 0);
          return `<tr class="ld-product-row" tabindex="0"
              data-name="${name}"
              data-price="${selling}"
              data-wholesale="${wholesale}"
              data-cost="${cost}"
              data-barcode="${barcode}"
              data-sku="${sku}">
            <td><input type="checkbox" class="label-check"
              data-name="${name}"
              data-price="${selling}"
              data-wholesale="${wholesale}"
              data-cost="${cost}"
              data-barcode="${barcode}"
              data-sku="${sku}"></td>
            <td>${name}</td>
            <td>${barcode || "—"}</td>
            <td>${moneyCell(selling)}</td>
            <td>${wholesale > 0 ? moneyCell(wholesale) : "—"}</td>
          </tr>`;
        })
        .join("");
      // Qidiruv filtrini qayta qo‘llash
      const q = String(document.getElementById("ld-product-search")?.value || "")
        .trim()
        .toLowerCase();
      if (q) {
        tbody.querySelectorAll(".ld-product-row").forEach((row) => {
          const hay = `${row.dataset.name || ""} ${row.dataset.barcode || ""} ${row.dataset.sku || ""}`.toLowerCase();
          row.hidden = !hay.includes(q);
        });
      }
    };

    // Event delegation — AJAX qayta renderdan keyin ham ishlaydi
    document.getElementById("labels-table")?.addEventListener("click", (e) => {
      const row = e.target.closest(".ld-product-row");
      if (!row) return;
      if (e.target.closest("input")) return;
      state.sample = productFromEl(row);
      document.querySelectorAll(".ld-product-row").forEach((r) => r.classList.remove("is-preview"));
      row.classList.add("is-preview");
      renderPreview();
    });

    document.addEventListener("tezpos:catalog", () => {
      renderLabelsTable();
      // Birinchi mahsulotni mini-preview uchun
      const first = (data.products || [])[0];
      if (first) {
        state.sample = {
          name: first.name || "",
          price: Number(first.selling_price || 0),
          wholesale: Number(first.wholesale_price || 0),
          cost: Number(first.cost_price || 0),
          barcode: first.barcode || "",
          sku: first.sku || first.barcode || "",
        };
        renderPreview();
      }
    });
    // Kesh / allaqachon yuklangan katalog
    if ((data.products || []).length) {
      renderLabelsTable();
    } else if (typeof window.tezposEnsureCatalog === "function") {
      window.tezposEnsureCatalog({ force: true });
    }

    document.getElementById("ld-select-all")?.addEventListener("click", () => {
      document.querySelectorAll(".label-check").forEach((c) => {
        if (!c.closest("tr")?.hidden) c.checked = true;
      });
    });
    document.getElementById("ld-select-none")?.addEventListener("click", () => {
      document.querySelectorAll(".label-check").forEach((c) => {
        c.checked = false;
      });
    });
    document.getElementById("ld-product-search")?.addEventListener("input", (e) => {
      const q = String(e.target.value || "").trim().toLowerCase();
      document.querySelectorAll(".ld-product-row").forEach((row) => {
        const hay = `${row.dataset.name || ""} ${row.dataset.barcode || ""} ${row.dataset.sku || ""}`.toLowerCase();
        row.hidden = Boolean(q) && !hay.includes(q);
      });
    });

    const buildLabelHtml = (product, priceType) => {
      const blocks = EL_KEYS.filter((k) => state.enabled[k])
        .map((key) => {
          const st = normalizeStyle(key, state.styles[key]);
          st.rotate = normAngle(st.rotate);
          const text = valueFor(key, product, priceType);
          if (!text && String(key).startsWith("custom")) return "";
          const strike = key === "old_price" ? "text-decoration:line-through;opacity:.75;" : "";
          const justify =
            st.align === "left" ? "flex-start" : st.align === "right" ? "flex-end" : "center";
          return (
            `<div class="lbl-wrap" style="position:absolute;${elWrapStyle(st)}box-sizing:border-box;">` +
            `<div class="lbl-body" style="width:100%;height:100%;display:flex;align-items:center;justify-content:${justify};` +
            `${elBodyStyle(st)}${strike}overflow:hidden;line-height:1.15;word-break:break-word;box-sizing:border-box;padding:1px">` +
            `${escHtml(text)}</div></div>`
          );
        })
        .join("");
      return (
        `<div class="label" style="position:relative;width:${state.widthMm}mm;height:${state.heightMm}mm;` +
        `overflow:hidden;box-sizing:border-box;border:1px solid #ddd;background:#fff;">${blocks}</div>`
      );
    };

    document.getElementById("print-labels-btn")?.addEventListener("click", () => {
      // Joriy joylashuvni saqlab, shablon bo'yicha barcha belgilangan yorliqlarni ko'rsatish
      saveTpl();
      EL_KEYS.forEach((k) => {
        state.styles[k] = normalizeStyle(k, state.styles[k]);
      });
      const checks = [...document.querySelectorAll(".label-check:checked")].filter(
        (el) => !el.closest("tr")?.hidden
      );
      if (!checks.length) {
        alert("Kamida bitta tovarni belgilang.");
        return;
      }
      const cols = Math.max(1, Number(document.getElementById("ld-print-cols")?.value || 1));
      const priceType = document.getElementById("ld-print-price-type")?.value || "selling";
      const wMm = state.widthMm;
      const hMm = state.heightMm;
      const labels = checks.map((el) => buildLabelHtml(productFromEl(el), priceType)).join("");
      const win = window.open("", "_blank", "width=920,height=800");
      if (!win) {
        alert("Brauzer yangi oynani blokladi. Popup-ni ruxsat bering.");
        return;
      }
      win.document.open();
      win.document.write("<!doctype html><html><head><meta charset='utf-8'><title>Narx yorliqlari</title>");
      win.document.write(`<style>
          @page { size: ${wMm}mm ${hMm}mm; margin: 0; }
          * { box-sizing: border-box; }
          html, body { margin: 0; padding: 0; }
          body { font-family: Arial, Helvetica, sans-serif; background: #f3f5f9; color: #000; }
          .bar {
            position: sticky; top: 0; z-index: 10; display: flex; justify-content: flex-end; gap: 8px;
            padding: 10px 14px; background: #fff; border-bottom: 1px solid #e2e8f2;
          }
          .bar button {
            width: 40px; height: 40px; border: 0; border-radius: 10px; cursor: pointer; color: #fff; font-size: 18px;
          }
          .btn-print { background: #2c86e0; }
          .btn-close { background: #94a3b8; }
          .meta { padding: 10px 14px; color: #64748b; font-size: 13px; background: #fff; }
          .sheet {
            padding: 12px; display: grid;
            grid-template-columns: repeat(${cols}, ${wMm}mm);
            gap: 2mm; justify-content: start; align-content: start; align-items: start;
            background: #fff; margin: 12px; border-radius: 12px;
          }
          .label {
            width: ${wMm}mm; height: ${hMm}mm;
            margin: 0; overflow: hidden;
            break-inside: avoid; page-break-inside: avoid;
          }
          @media print {
            @page { size: ${wMm}mm ${hMm}mm; margin: 0; }
            html, body { width: ${wMm}mm; background: #fff !important; }
            .bar, .meta { display: none !important; }
            .sheet {
              display: block; margin: 0 !important; padding: 0 !important;
              border-radius: 0; background: #fff; gap: 0;
            }
            .label {
              width: ${wMm}mm !important;
              height: ${hMm}mm !important;
              margin: 0 !important;
              border: 0 !important;
              page-break-after: always;
              break-after: page;
              page-break-inside: avoid;
              break-inside: avoid;
            }
            .label:last-child {
              page-break-after: auto;
              break-after: auto;
            }
          }
        </style></head><body>
          <div class="bar">
            <button type="button" class="btn-print" onclick="window.print()" title="Chop etish">&#128424;</button>
            <button type="button" class="btn-close" onclick="window.close()" title="Yopish">&#10005;</button>
          </div>
          <div class="meta">${escHtml(state.name)} · ${checks.length} ta yorliq · ${wMm}×${hMm} mm</div>
          <div class="sheet">${labels}</div>
        </body></html>`);
      win.document.close();
    });
  }

  const modal = document.getElementById("product-modal");
  const modalForm = document.getElementById("product-modal-form");
  const modalTitle = document.getElementById("product-modal-title");
  const productsMap = Object.fromEntries(
    (data.products || []).map((p) => [String(p.id), p])
  );

  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");

  const renderProductsTable = () => {
    const tbody = document.getElementById("products-mgmt-tbody");
    if (!tbody || tbody.dataset.dynamic !== "1") return;
    const list = data.products || [];
    if (!list.length) {
      tbody.innerHTML =
        '<tr><td colspan="6" class="cabinet-empty">Mahsulot yo‘q. “Mahsulot qo‘shish” orqali qo‘shing.</td></tr>';
      return;
    }
    const editIcon =
      '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M4 17.2V20h2.8l8.2-8.2-2.8-2.8L4 17.2zM18.1 7.7c.3-.3.3-.8 0-1.1l-1.7-1.7a.8.8 0 0 0-1.1 0l-1.3 1.3 2.8 2.8 1.3-1.3z"/></svg>';
    const rows = list.map((p) => {
      const stock = Number(p.stock_qty || 0);
      const minStock = Number(p.min_stock || 0);
      const cost = Number(p.cost_price || 0);
      const selling = Number(p.selling_price || 0);
      const wholesale = Number(p.wholesale_price || 0);
      const margin = cost > 0 ? ((selling - cost) / cost) * 100 : 0;
      const low = stock <= minStock;
      let stockClass = "is-stock-zero";
      if (stock < 0) stockClass = "is-stock-neg";
      else if (low) stockClass = "is-stock-low";
      else if (stock > 0) stockClass = "is-stock-pos";
      const marginClass = margin > 0 ? "is-pos" : margin < 0 ? "is-neg" : "";
      const letter = esc((p.name || "?").slice(0, 1).toUpperCase());
      const thumb = p.image
        ? `<img src="${esc(p.image)}" alt="" loading="lazy" width="44" height="44">`
        : `<span>${letter}</span>`;
      const metaBits = [p.barcode || p.sku || "—", p.category, p.brand]
        .filter(Boolean)
        .join(' <span class="meta-sep">·</span> ');
      const fav = p.is_favorite
        ? '<span class="fav-mark" title="Sevimli" aria-hidden="true">★</span> '
        : "";
      return `<tr
        data-edit-product="${esc(p.id)}"
        data-name="${esc((p.name || "").toLowerCase())}"
        data-barcode="${esc((p.barcode || "").toLowerCase())}"
        data-category="${esc((p.category || "").toLowerCase())}"
        data-selling="${selling.toFixed(2)}"
        data-wholesale="${wholesale.toFixed(2)}"
        data-cost="${cost.toFixed(2)}"
        data-margin="${margin.toFixed(2)}"
        class="is-clickable${low ? " is-low-stock" : ""}${p.is_favorite ? " is-favorite" : ""}"
      >
        <td class="col-name">
          <div class="product-cell">
            <div class="product-thumb">${thumb}</div>
            <div class="product-meta">
              <strong>${fav}${esc(p.name)}</strong>
              <small>${metaBits}</small>
            </div>
          </div>
        </td>
        <td class="col-num col-cost"><span data-cost-display></span> <span class="price-unit">SUM</span></td>
        <td class="col-num col-margin ${marginClass}" data-margin-display>${cost ? "" : "—"}</td>
        <td class="col-num col-price"><span class="price-value" data-price-display></span> <span class="price-unit">SUM</span></td>
        <td class="col-num col-stock ${stockClass}">
          ${Math.round(stock)}
          <span class="stock-unit">${esc(p.unit || "dona")}</span>
          ${low ? '<span class="stock-warn" title="Minimal qoldiqdan past">!</span>' : ""}
        </td>
        <td class="col-actions">
          <button type="button" class="row-edit-btn" data-edit-product="${esc(p.id)}" title="Tahrirlash">${editIcon}</button>
        </td>
      </tr>`;
    });
    tbody.innerHTML = rows.join("");
  };
  renderProductsTable();
  document.addEventListener("tezpos:catalog", () => {
    Object.keys(productsMap).forEach((k) => delete productsMap[k]);
    (data.products || []).forEach((p) => {
      productsMap[String(p.id)] = p;
    });
    renderProductsTable();
  });

  const barcodeList = document.getElementById("barcode-list");
  const mediaGallery = document.getElementById("media-gallery");
  const imagesCount = document.getElementById("images-count");
  let keepImages = [];
  let pendingFiles = [];

  const genCode = () => `460${Math.floor(1000000 + Math.random() * 8999999)}`;

  const iconRefresh = `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M12 6V3L8 7l4 4V8c2.76 0 5 2.24 5 5a5 5 0 0 1-8.9 3.1l-1.45 1.45A7 7 0 0 0 19 13c0-3.87-3.13-7-7-7zm0 12v3l4-4-4-4v3a5 5 0 0 1-5-5c0-.9.24-1.74.66-2.47L6.2 8.08A7 7 0 0 0 5 13c0 3.87 3.13 7 7 7z"/></svg>`;
  const iconTrash = `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M9 3h6l1 2h4v2H4V5h4l1-2zm1 6h2v9h-2V9zm4 0h2v9h-2V9zM7 9h2v9H7V9z"/></svg>`;
  const iconPlus = `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="currentColor" d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6V5z"/></svg>`;
  const iconClose = `<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" d="M6.4 5l5.6 5.6L17.6 5 19 6.4 13.4 12 19 17.6 17.6 19 12 13.4 6.4 19 5 17.6 10.6 12 5 6.4z"/></svg>`;

  const renderBarcodes = (codes) => {
    if (!barcodeList) return;
    const list = codes.length ? codes : [""];
    barcodeList.innerHTML = list
      .map(
        (code, i) => `<div class="barcode-row">
          <span class="barcode-label">#${i + 1}</span>
          <input type="text" name="barcodes" value="${String(code).replace(/"/g, "&quot;")}" placeholder="860..." ${i === 0 ? "required" : ""}>
          <button type="button" class="btn-icon gen-one" title="Generatsiya">${iconRefresh}</button>
          <button type="button" class="btn-icon add-one" title="Qo‘shish">${iconPlus}</button>
          <button type="button" class="btn-icon danger rm-barcode" title="O‘chirish" ${list.length <= 1 ? "disabled" : ""}>${iconTrash}</button>
        </div>`
      )
      .join("");
  };

  const updateImagesCount = () => {
    if (imagesCount) imagesCount.textContent = `${keepImages.length + pendingFiles.length} ta`;
  };

  const scrollMedia = () => {
    const body = document.querySelector(".product-modal-body");
    if (body) body.scrollTop = body.scrollHeight;
  };

  const renderGallery = () => {
    if (!mediaGallery) return;
    const existing = keepImages
      .map(
        (img) => `<div class="media-thumb ${img.is_primary ? "is-primary" : ""}" data-keep="${img.id}">
          <img src="${img.url}" alt="">
          <button type="button" class="rm" data-rm-keep="${img.id}" aria-label="O‘chirish">${iconClose}</button>
          <input type="hidden" name="keep_images" value="${img.id}">
        </div>`
      )
      .join("");
    const pending = pendingFiles
      .map(
        (file, i) => `<div class="media-thumb" data-pending="${i}">
          <img src="${file.preview}" alt="">
          <button type="button" class="rm" data-rm-pending="${i}" aria-label="O‘chirish">${iconClose}</button>
        </div>`
      )
      .join("");
    mediaGallery.innerHTML = existing + pending;
    updateImagesCount();
    const drop = document.getElementById("media-drop");
    if (drop) drop.hidden = keepImages.length + pendingFiles.length > 0;
  };

  const syncPendingInput = () => {
    const input = document.getElementById("f_images");
    if (!input) return;
    const dt = new DataTransfer();
    pendingFiles.forEach((item) => dt.items.add(item.file));
    input.files = dt.files;
  };

  const fillProductForm = (product) => {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val ?? "";
    };
    set("product_id", product?.id || "");
    set("f_name", product?.name || "");
    set("f_unit", product?.unit || "dona");
    set("f_cost_price", product ? product.cost_price : "");
    set("f_stock_qty", product ? product.stock_qty : "");
    set("f_selling_price", product ? product.selling_price : "");
    set("f_wholesale_price", product ? product.wholesale_price : "");
    set("f_min_stock", product ? product.min_stock : "");
    set("f_image_url", product?.image_url || "");
    const fav = document.getElementById("f_is_favorite");
    if (fav) fav.checked = Boolean(product?.is_favorite);

    const fillSelect = (selectId, newId, value, insertBeforeValue = "__new__") => {
      const select = document.getElementById(selectId);
      const extra = document.getElementById(newId);
      if (!select) return;
      const val = value || "";
      const hasOption = [...select.options].some((o) => o.value === val);
      if (val && !hasOption) {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = val;
        const before = select.querySelector(`option[value="${insertBeforeValue}"]`);
        select.insertBefore(opt, before || null);
      }
      select.value = val;
      if (extra) {
        extra.hidden = true;
        extra.value = "";
      }
    };
    fillSelect("f_brand", "f_brand_new", product?.brand || "");
    fillSelect("f_category", "f_category_new", product?.category || "");

    renderBarcodes(product?.barcodes?.length ? product.barcodes : product?.barcode ? [product.barcode] : [""]);
    keepImages = (product?.images || []).map((img) => ({ ...img }));
    pendingFiles.forEach((item) => URL.revokeObjectURL(item.preview));
    pendingFiles = [];
    syncPendingInput();
    renderGallery();
  };

  const openModal = (product) => {
    if (!modal) return;
    fillProductForm(product || null);
    if (modalTitle) {
      modalTitle.textContent = product ? "Mahsulotni tahrirlash" : "Mahsulot qo‘shish";
    }
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    document.getElementById("f_name")?.focus();
  };

  const closeModal = () => {
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = "";
  };

  document.getElementById("btn-product-add")?.addEventListener("click", () => openModal(null));

  // ——— Mahsulotlarni CSV orqali yuklash ———
  (() => {
    const importModal = document.getElementById("product-import-modal");
    const fieldsEl = document.getElementById("pi-fields");
    const fileInput = document.getElementById("pi-file");
    const fileNameEl = document.getElementById("pi-file-name");
    const previewWrap = document.getElementById("pi-preview-wrap");
    const previewTable = document.getElementById("pi-preview-table");
    const previewMeta = document.getElementById("pi-preview-meta");
    const statusEl = document.getElementById("pi-status");
    const runBtn = document.getElementById("pi-run");
    if (!importModal || !fieldsEl) return;

    const priceLists = Array.isArray(data.priceLists) ? data.priceLists : [];
    const BASE_FIELDS = [
      { key: "name", label: "Mahsulot nomi", def: true },
      { key: "barcode", label: "Shtrixkod (barkod)", def: true },
      { key: "brand", label: "Brend", def: true },
      { key: "category", label: "Bo‘lim", def: true },
      { key: "selling_price", label: "Sotuv narxi", def: true },
      { key: "cost_price", label: "Sotib olish narxi", def: true },
      { key: "stock_qty", label: "Omborda qoldiq", def: true },
      { key: "unit", label: "O‘lchov birligi", def: true },
      { key: "min_stock", label: "Minimal qoldiq", def: false },
    ];
    const PL_FIELDS = priceLists
      .filter((pl) => pl && pl.id && !pl.is_selling)
      .map((pl) => ({
        key: `pl_${pl.id}`,
        label: `Narxlar: ${pl.name || pl.id}`,
        def: true,
        priceListId: String(pl.id),
      }));
    // Sotuv ro‘yxati bo‘lsa ham alohida ko‘rsatish (is_selling)
    const SELLING_PL = priceLists
      .filter((pl) => pl && pl.id && pl.is_selling)
      .map((pl) => ({
        key: `pl_${pl.id}`,
        label: `Narxlar: ${pl.name || "Sotuv"}`,
        def: true,
        priceListId: String(pl.id),
      }));
    const ALL_FIELDS = [...BASE_FIELDS, ...SELLING_PL, ...PL_FIELDS];

    let parsedRows = [];
    let rawCsvRows = [];
    // Dastlab hammasi tanlangan (nom, brend, narxlar ro‘yxati…)
    let selectedKeys = new Set(ALL_FIELDS.map((f) => f.key));

    const csrfToken = () => {
      const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
      if (m) return decodeURIComponent(m[1]);
      return document.querySelector("#product-modal-form [name=csrfmiddlewaretoken]")?.value || "";
    };

    const renderFields = () => {
      const parts = [];
      parts.push(`<div class="pi-field is-group-title">Asosiy maydonlar</div>`);
      BASE_FIELDS.forEach((f) => {
        parts.push(
          `<label class="pi-field"><input type="checkbox" data-pi-key="${esc(f.key)}" ${
            selectedKeys.has(f.key) ? "checked" : ""
          }><span>${esc(f.label)}</span></label>`
        );
      });
      if (SELLING_PL.length || PL_FIELDS.length) {
        parts.push(`<div class="pi-field is-group-title">Narxlar ro‘yxati</div>`);
        [...SELLING_PL, ...PL_FIELDS].forEach((f) => {
          parts.push(
            `<label class="pi-field"><input type="checkbox" data-pi-key="${esc(f.key)}" ${
              selectedKeys.has(f.key) ? "checked" : ""
            }><span>${esc(f.label)}</span></label>`
          );
        });
      }
      fieldsEl.innerHTML = parts.join("");
    };

    const activeFields = () => ALL_FIELDS.filter((f) => selectedKeys.has(f.key));

    const setStatus = (text, kind = "") => {
      if (!statusEl) return;
      if (!text) {
        statusEl.hidden = true;
        statusEl.textContent = "";
        statusEl.className = "pi-status";
        return;
      }
      statusEl.hidden = false;
      statusEl.textContent = text;
      statusEl.className = `pi-status${kind ? ` is-${kind}` : ""}`;
    };

    const openImport = () => {
      parsedRows = [];
      rawCsvRows = [];
      if (fileInput) fileInput.value = "";
      if (fileNameEl) fileNameEl.textContent = "Fayl tanlanmagan";
      if (previewWrap) previewWrap.hidden = true;
      // Har ochilganda barcha maydonlar tanlangan (foydalanuvchi o‘chirishi mumkin)
      selectedKeys = new Set(ALL_FIELDS.map((f) => f.key));
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.textContent = "Excelga yuklash";
      }
      setStatus("");
      renderFields();
      importModal.hidden = false;
      document.body.style.overflow = "hidden";
    };
    const closeImport = () => {
      importModal.hidden = true;
      document.body.style.overflow = "";
    };

    const productCellValue = (p, field) => {
      if (!p) return "";
      const listPrices = p.list_prices || {};
      switch (field.key) {
        case "name":
          return p.name || "";
        case "barcode":
          return p.barcode || (Array.isArray(p.barcodes) ? p.barcodes[0] : "") || "";
        case "selling_price":
          return Number(p.selling_price || 0);
        case "cost_price":
          return Number(p.cost_price || 0);
        case "stock_qty":
          return Number(p.stock_qty || 0);
        case "unit":
          return p.unit || "dona";
        case "category":
          return p.category || "";
        case "brand":
          return p.brand || "";
        case "min_stock":
          return Number(p.min_stock || 0);
        default:
          if (field.key.startsWith("pl_")) {
            const id = field.priceListId || field.key.slice(3);
            return Number(listPrices[id] ?? listPrices[String(id)] ?? 0);
          }
          return "";
      }
    };

    const buildExportSheet = () => {
      const fields = activeFields();
      if (!fields.length) {
        alert("Kamida bitta maydonni tanlang.");
        return null;
      }
      if (!selectedKeys.has("name")) {
        alert("«Mahsulot nomi» maydonini belgilang.");
        return null;
      }
      if (typeof XLSX === "undefined") {
        alert("Excel kutubxonasi yuklanmadi. Sahifani yangilang.");
        return null;
      }
      const products = Array.isArray(data.products) ? data.products : [];
      if (!products.length) {
        alert("Hozircha mahsulot yo‘q.");
        return null;
      }
      const header = fields.map((f) => f.label);
      const body = products.map((p) => fields.map((f) => productCellValue(p, f)));
      const aoa = [header].concat(body);
      const ws = XLSX.utils.aoa_to_sheet(aoa);
      ws["!cols"] = fields.map((f, colIdx) => {
        let maxLen = String(f.label || "").length;
        for (let r = 0; r < body.length; r += 1) {
          const len = String(body[r][colIdx] ?? "").length;
          if (len > maxLen) maxLen = len;
        }
        return { wch: Math.min(120, Math.max(14, maxLen + 4)) };
      });
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Mahsulotlar");
      return { wb, count: products.length };
    };

    const downloadAllProductsExcel = async () => {
      if (!selectedKeys.has("name")) {
        alert("«Mahsulot nomi» maydonini belgilang.");
        return;
      }
      const fields = activeFields();
      if (!fields.length) {
        alert("Kamida bitta maydonni tanlang.");
        return;
      }
      const exportUrl = data.productsExportUrl;
      if (!exportUrl) {
        try {
          setStatus("Excel tayyorlanmoqda…", "");
          const pack = buildExportSheet();
          if (!pack) return;
          XLSX.writeFile(pack.wb, "barcha_mahsulotlar.xlsx");
          setStatus(`${pack.count} ta mahsulot Excelga yozildi.`, "");
        } catch (err) {
          setStatus(`Excel yaratilmadi: ${err.message || err}`, "error");
          alert(`Excel yuklanmadi: ${err.message || err}`);
        }
        return;
      }
      setStatus("Excel tayyorlanmoqda… biroz kuting (katta katalog).", "");
      if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = "Yuklanmoqda…";
      }
      try {
        const qs = new URLSearchParams({
          fields: fields.map((f) => f.key).join(","),
        });
        const res = await fetch(`${exportUrl}?${qs}`, {
          credentials: "same-origin",
          headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
        });
        if (!res.ok) {
          const errJson = await res.json().catch(() => ({}));
          throw new Error(errJson.error || `Xato ${res.status}`);
        }
        const blob = await res.blob();
        const type = (blob.type || "").toLowerCase();
        if (type.includes("json") || type.includes("html")) {
          throw new Error("Server Excel o‘rniga xato qaytardi");
        }
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objUrl;
        a.download = "barcha_mahsulotlar.xlsx";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 2000);
        setStatus("Excel yuklab olindi.", "");
      } catch (err) {
        setStatus(`Excel yuklanmadi: ${err.message || err}`, "error");
        alert(`Excel yuklanmadi: ${err.message || err}`);
      } finally {
        if (runBtn) {
          runBtn.disabled = false;
          syncRunButton();
        }
      }
    };

    const normalizeHeader = (h) =>
      String(h || "")
        .trim()
        .toLowerCase()
        .replace(/\u00a0/g, " ")
        .replace(/\s+/g, " ");

    const HEADER_ALIASES = {
      name: ["name", "nomi", "mahsulot nomi", "product name", "товар", "наименование"],
      barcode: ["barcode", "shtrixkod", "shtrix-kod", "barkod", "штрихкод", "ean"],
      selling_price: ["selling_price", "price", "sotuv narxi", "sotuv", "narx", "цена", "sale price"],
      cost_price: ["cost_price", "cost", "sotib olish narxi", "tannarx", "закуп", "purchase"],
      stock_qty: ["stock_qty", "quantity", "stock", "qoldiq", "ombor", "остаток", "qty"],
      unit: ["unit", "o‘lchov", "olchov", "birlik", "ед"],
      category: ["category", "bo‘lim", "bolim", "категория"],
      brand: ["brand", "brend", "бренд"],
      min_stock: ["min_stock", "minimal qoldiq", "min qoldiq"],
    };

    const resolveKeyFromHeader = (header) => {
      const n = normalizeHeader(header);
      for (const f of ALL_FIELDS) {
        if (normalizeHeader(f.key) === n || normalizeHeader(f.label) === n) return f.key;
        if (f.priceListId && (n === `pl_${f.priceListId}` || n.includes(normalizeHeader(f.label)))) {
          return f.key;
        }
      }
      for (const [key, aliases] of Object.entries(HEADER_ALIASES)) {
        if (aliases.some((a) => a === n)) return key;
      }
      return null;
    };

    const parseCsv = (text) => {
      const raw = String(text || "").replace(/^\uFEFF/, "");
      if (!raw.trim()) return { headers: [], rows: [] };
      const firstLine = raw.split(/\r?\n/).find((l) => l.trim()) || "";
      const delim =
        (firstLine.match(/;/g) || []).length > (firstLine.match(/,/g) || []).length ? ";" : ",";

      const splitLine = (line) => {
        const cells = [];
        let c = "";
        let q = false;
        for (let i = 0; i < line.length; i += 1) {
          const ch = line[i];
          if (ch === '"') {
            if (q && line[i + 1] === '"') {
              c += '"';
              i += 1;
            } else q = !q;
            continue;
          }
          if (ch === delim && !q) {
            cells.push(c);
            c = "";
            continue;
          }
          c += ch;
        }
        cells.push(c);
        return cells;
      };

      const nonEmpty = raw
        .split(/\r?\n/)
        .map((l) => l.trimEnd())
        .filter((l) => l.trim());
      if (!nonEmpty.length) return { headers: [], rows: [] };
      const headers = splitLine(nonEmpty[0]).map((h) => h.trim());
      const rows = nonEmpty.slice(1).map((line) => {
        const cells = splitLine(line);
        const obj = {};
        headers.forEach((h, i) => {
          obj[h] = (cells[i] ?? "").trim();
        });
        return obj;
      });
      return { headers, rows };
    };

    const money = (v) => {
      if (v == null || v === "") return 0;
      const s = String(v).replace(/\u00a0/g, "").replace(/\s/g, "").replace(",", ".");
      const n = Number(s);
      return Number.isFinite(n) ? n : 0;
    };

    const mapRows = (csvRows) => {
      const fields = activeFields();
      if (!fields.length) return [];
      return csvRows
        .map((raw) => {
          const out = { list_prices: {} };
          let hasAny = false;
          Object.entries(raw).forEach(([header, value]) => {
            const key = resolveKeyFromHeader(header);
            if (!key || !selectedKeys.has(key)) return;
            hasAny = true;
            if (key.startsWith("pl_")) {
              out.list_prices[key.slice(3)] = money(value);
              out[key] = money(value);
            } else if (["selling_price", "cost_price", "stock_qty", "min_stock"].includes(key)) {
              out[key] = money(value);
            } else {
              out[key] = String(value || "").trim();
            }
          });
          if (!hasAny) return null;
          if (!out.name) return null;
          return out;
        })
        .filter(Boolean);
    };

    const renderPreview = () => {
      const fields = activeFields();
      if (!previewTable || !previewWrap) return;
      if (!parsedRows.length || !fields.length) {
        previewWrap.hidden = true;
        if (runBtn) runBtn.disabled = true;
        return;
      }
      const head = previewTable.querySelector("thead");
      const body = previewTable.querySelector("tbody");
      if (head) {
        head.innerHTML = `<tr>${fields.map((f) => `<th>${esc(f.label)}</th>`).join("")}</tr>`;
      }
      const sample = parsedRows.slice(0, 30);
      if (body) {
        body.innerHTML = sample
          .map((row) => {
            const tds = fields.map((f) => {
              let v = row[f.key];
              if (f.key.startsWith("pl_") && (v == null || v === "")) {
                v = row.list_prices?.[f.key.slice(3)];
              }
              return `<td>${esc(v ?? "")}</td>`;
            });
            return `<tr>${tds.join("")}</tr>`;
          })
          .join("");
      }
      if (previewMeta) {
        previewMeta.textContent =
          parsedRows.length > 30
            ? `${parsedRows.length} ta qator (birinchi 30 ko‘rsatilgan)`
            : `${parsedRows.length} ta qator`;
      }
      previewWrap.hidden = false;
      if (runBtn) runBtn.disabled = false;
      syncRunButton();
    };

    const rowsFromWorkbook = (wb) => {
      const name = wb.SheetNames[0];
      const sheet = wb.Sheets[name];
      if (!sheet) return [];
      return XLSX.utils.sheet_to_json(sheet, { defval: "", raw: false });
    };

    const syncRunButton = () => {
      if (!runBtn) return;
      runBtn.disabled = false;
      runBtn.textContent = parsedRows.length
        ? `TezPOS ga import (${parsedRows.length})`
        : "Excelga yuklash";
    };

    fieldsEl.addEventListener("change", (e) => {
      const cb = e.target.closest("input[data-pi-key]");
      if (!cb) return;
      if (cb.checked) selectedKeys.add(cb.dataset.piKey);
      else selectedKeys.delete(cb.dataset.piKey);
      if (rawCsvRows.length) {
        parsedRows = mapRows(rawCsvRows);
        renderPreview();
      }
      syncRunButton();
    });

    document.getElementById("pi-select-all")?.addEventListener("click", () => {
      selectedKeys = new Set(ALL_FIELDS.map((f) => f.key));
      renderFields();
      if (rawCsvRows.length) parsedRows = mapRows(rawCsvRows);
      renderPreview();
      syncRunButton();
    });
    document.getElementById("pi-select-none")?.addEventListener("click", () => {
      selectedKeys = new Set(["name"]);
      renderFields();
      if (rawCsvRows.length) parsedRows = mapRows(rawCsvRows);
      renderPreview();
      syncRunButton();
    });
    document.getElementById("pi-download-tpl")?.addEventListener("click", () => {
      downloadAllProductsExcel();
    });
    document.getElementById("btn-product-export")?.addEventListener("click", openImport);
    document.getElementById("btn-product-import")?.addEventListener("click", openImport);
    document.querySelectorAll("[data-close-import]").forEach((el) => {
      el.addEventListener("click", closeImport);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && importModal && !importModal.hidden) closeImport();
    });

    fileInput?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      if (fileNameEl) fileNameEl.textContent = file.name;
      setStatus("");
      try {
        let rows = [];
        const lower = (file.name || "").toLowerCase();
        if (lower.endsWith(".xlsx") || lower.endsWith(".xls") || lower.endsWith(".xlsm")) {
          if (typeof XLSX === "undefined") {
            setStatus("Excel kutubxonasi yuklanmadi. Sahifani yangilang.", "error");
            return;
          }
          const buf = await file.arrayBuffer();
          const wb = XLSX.read(buf, { type: "array" });
          rows = rowsFromWorkbook(wb);
        } else {
          const text = await file.text();
          rows = parseCsv(text).rows;
        }
        rawCsvRows = rows;
        if (!selectedKeys.has("name")) {
          selectedKeys.add("name");
          renderFields();
        }
        parsedRows = mapRows(rows);
        if (!parsedRows.length) {
          setStatus("Faylda mos qator topilmadi. Ustun nomlari va tanlangan maydonlarni tekshiring.", "error");
          if (previewWrap) previewWrap.hidden = true;
          syncRunButton();
          return;
        }
        renderPreview();
        setStatus(`${parsedRows.length} ta mahsulot tayyor.`, "");
        syncRunButton();
      } catch (err) {
        setStatus(`Faylni o‘qib bo‘lmadi: ${err.message || err}`, "error");
      }
    });

    runBtn?.addEventListener("click", async () => {
      if (!selectedKeys.has("name")) {
        alert("«Mahsulot nomi» maydonini belgilang.");
        return;
      }

      // Fayl tanlanmagan — barcha mahsulotlarni Excelga yozib yuklash
      if (!parsedRows.length) {
        downloadAllProductsExcel();
        return;
      }

      const url = data.productsImportUrl;
      if (!url) {
        alert("Import URL topilmadi.");
        return;
      }
      runBtn.disabled = true;
      setStatus(`${parsedRows.length} ta mahsulot yuklanmoqda…`, "");
      const payloadProducts = parsedRows.map((row) => {
        const item = {
          name: row.name,
          barcode: row.barcode || "",
          selling_price: row.selling_price || 0,
          cost_price: row.cost_price || 0,
          stock_qty: row.stock_qty || 0,
          unit: row.unit || "dona",
          category: row.category || "",
          brand: row.brand || "",
          min_stock: row.min_stock || 0,
          list_prices: row.list_prices || {},
        };
        activeFields().forEach((f) => {
          if (f.key.startsWith("pl_") && row[f.key] != null) {
            item.list_prices[f.key.slice(3)] = row[f.key];
            item[f.key] = row[f.key];
          }
        });
        return item;
      });

      // Katta fayllarni bo‘lib yuborish
      const chunkSize = 100;
      let created = 0;
      const failed = [];
      try {
        for (let i = 0; i < payloadProducts.length; i += chunkSize) {
          const chunk = payloadProducts.slice(i, i + chunkSize);
          setStatus(
            `Yuklanmoqda… ${Math.min(i + chunk.length, payloadProducts.length)} / ${payloadProducts.length}`,
            ""
          );
          const res = await fetch(url, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken(),
            },
            body: JSON.stringify({ products: chunk }),
            credentials: "same-origin",
          });
          const json = await res.json().catch(() => ({}));
          if (!res.ok) {
            setStatus(json.error || "Yuklashda xato", "error");
            runBtn.disabled = false;
            syncRunButton();
            return;
          }
          created += Number(json.created || 0);
          (json.failed || []).forEach((f) => {
            failed.push({ ...f, row: (f.row || 0) + i });
          });
        }
        const failN = failed.length;
        if (failN) {
          const sample = failed
            .slice(0, 5)
            .map((f) => `#${f.row} ${f.name || ""}: ${f.error}`)
            .join(" · ");
          setStatus(`${created} ta qo‘shildi, ${failN} ta xato. ${sample}`, "warn");
        } else {
          setStatus(`${created} ta mahsulot muvaffaqiyatli yuklandi. Sahifa yangilanadi…`, "");
        }
        if (created > 0) {
          setTimeout(() => {
            window.location.href = "?section=products";
          }, 1200);
        } else {
          runBtn.disabled = false;
          syncRunButton();
        }
      } catch (err) {
        setStatus(`Tarmoq xatosi: ${err.message || err}`, "error");
        runBtn.disabled = false;
        syncRunButton();
      }
    });
  })();

  const openProductById = (id) => {
    const product = productsMap[String(id)];
    openModal(product || null);
  };
  document.getElementById("products-mgmt-tbody")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-edit-product].row-edit-btn");
    if (btn) {
      e.stopPropagation();
      openProductById(btn.dataset.editProduct);
      return;
    }
    const row = e.target.closest("tr[data-edit-product]");
    if (!row) return;
      if (e.target.closest("button, a, input, select, label")) return;
      openProductById(row.dataset.editProduct);
  });
  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && !modal.hidden) closeModal();
  });

  document.getElementById("f_brand")?.addEventListener("change", (e) => {
    const brandNew = document.getElementById("f_brand_new");
    if (!brandNew) return;
    const isNew = e.target.value === "__new__";
    brandNew.hidden = !isNew;
    if (isNew) brandNew.focus();
  });
  document.getElementById("f_category")?.addEventListener("change", (e) => {
    const categoryNew = document.getElementById("f_category_new");
    if (!categoryNew) return;
    const isNew = e.target.value === "__new__";
    categoryNew.hidden = !isNew;
    if (isNew) categoryNew.focus();
  });

  barcodeList?.addEventListener("click", (e) => {
    const gen = e.target.closest(".gen-one");
    const add = e.target.closest(".add-one");
    const rm = e.target.closest(".rm-barcode");
    if (gen) {
      const input = gen.parentElement.querySelector('input[name="barcodes"]');
      if (input) input.value = genCode();
    }
    if (add) {
      const values = [...barcodeList.querySelectorAll('input[name="barcodes"]')].map((el) => el.value);
      const row = add.closest(".barcode-row");
      const idx = [...barcodeList.children].indexOf(row);
      values.splice(idx + 1, 0, "");
      renderBarcodes(values);
      barcodeList.querySelector(`.barcode-row:nth-child(${idx + 2}) input`)?.focus();
    }
    if (rm && !rm.disabled) {
      const values = [...barcodeList.querySelectorAll('input[name="barcodes"]')].map((el) => el.value);
      const row = rm.closest(".barcode-row");
      const idx = [...barcodeList.children].indexOf(row);
      values.splice(idx, 1);
      renderBarcodes(values.length ? values : [""]);
    }
  });

  document.getElementById("pick-image")?.addEventListener("click", () => {
    document.getElementById("f_images")?.click();
  });
  document.getElementById("media-drop")?.addEventListener("click", (e) => {
    if (e.target.closest(".rm")) return;
    document.getElementById("f_images")?.click();
  });
  document.getElementById("f_images")?.addEventListener("change", (e) => {
    const files = [...(e.target.files || [])];
    files.forEach((file) => {
      pendingFiles.push({ file, preview: URL.createObjectURL(file) });
    });
    syncPendingInput();
    renderGallery();
    scrollMedia();
  });
  mediaGallery?.addEventListener("click", (e) => {
    const rmKeep = e.target.closest("[data-rm-keep]");
    const rmPending = e.target.closest("[data-rm-pending]");
    if (rmKeep) {
      const id = Number(rmKeep.dataset.rmKeep);
      keepImages = keepImages.filter((img) => img.id !== id);
      renderGallery();
    }
    if (rmPending) {
      const idx = Number(rmPending.dataset.rmPending);
      const item = pendingFiles[idx];
      if (item) URL.revokeObjectURL(item.preview);
      pendingFiles.splice(idx, 1);
      syncPendingInput();
      renderGallery();
    }
  });

  const search = document.getElementById("products-search");
  const table = document.getElementById("products-mgmt-table");
  const categoryFilter = document.getElementById("products-category-filter");
  const priceType = document.getElementById("products-price-type");
  const productsTotal = document.getElementById("products-total");
  const priceHeader = table?.querySelector("thead th.col-price");
  const catalogPriceLists = Array.isArray(data.priceLists) ? data.priceLists : [];
  const productsById = Object.fromEntries(
    (Array.isArray(data.products) ? data.products : []).map((p) => [String(p.id), p])
  );

  const parseMoney = (v) => {
    if (v == null || v === "") return 0;
    const cleaned = String(v).replace(/\s/g, "").replace(",", ".");
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : 0;
  };
  const fmtMoney = (n) =>
    parseMoney(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const fmtMargin = (n) => {
    const v = parseMoney(n);
    const rounded = Math.round(v * 10) / 10;
    const text = Number.isInteger(rounded)
      ? String(rounded)
      : rounded.toFixed(1).replace(".", ",");
    return `${text}%`;
  };

  const fillPriceTypeOptions = () => {
    if (!priceType) return;
    const current = priceType.value || "selling";
    const options = [
      { value: "selling", label: "Sotuv narxi" },
      ...catalogPriceLists
        .filter((pl) => pl && pl.id)
        .map((pl) => ({
          value: String(pl.id),
          label: (pl.name || "").trim() || "Narxlar",
        })),
      { value: "cost", label: "Tannarx" },
    ];
    priceType.innerHTML = options
      .map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`)
      .join("");
    if (options.some((o) => o.value === current)) priceType.value = current;
  };

  const resolveRowPrice = (row, key) => {
    if (key === "selling") return parseMoney(row.getAttribute("data-selling"));
    if (key === "cost") return parseMoney(row.getAttribute("data-cost"));
    const pid = row.getAttribute("data-edit-product");
    const product = productsById[pid];
    const listPrices = (product && product.list_prices) || {};
    if (listPrices[key] != null && listPrices[key] !== "") {
      return parseMoney(listPrices[key]);
    }
    return 0;
  };

  const priceTypeLabel = (key) => {
    if (key === "selling") return "Narxi";
    if (key === "cost") return "Tannarx";
    const pl = catalogPriceLists.find((x) => String(x.id) === String(key));
    return pl?.name ? String(pl.name) : "Narxi";
  };

  const applyProductFilters = () => {
    if (!table) return;
    const q = (search?.value || "").trim().toLowerCase();
    const cat = (categoryFilter?.value || "").trim().toLowerCase();
    let visible = 0;
    table.querySelectorAll("tbody tr").forEach((row) => {
      if (row.querySelector(".cabinet-empty")) return;
      const name = row.dataset.name || "";
      const barcode = row.dataset.barcode || "";
      const category = row.dataset.category || "";
      const matchQ = !q || name.includes(q) || barcode.includes(q);
      const matchCat = !cat || category === cat;
      const show = matchQ && matchCat;
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (productsTotal) {
      productsTotal.innerHTML = `Jami: <strong>${visible}</strong> ta`;
    }
  };

  const applyPriceType = () => {
    if (!table || !priceType) return;
    const key = priceType.value || "selling";
    table.querySelectorAll("tbody tr[data-edit-product]").forEach((row) => {
      const cost = parseMoney(row.getAttribute("data-cost"));
      const price = resolveRowPrice(row, key);

      const priceEl = row.querySelector("[data-price-display]");
      if (priceEl) priceEl.textContent = fmtMoney(price);

      const costEl = row.querySelector("[data-cost-display]");
      if (costEl) costEl.textContent = fmtMoney(cost);

      const marginEl = row.querySelector("[data-margin-display]");
      if (marginEl) {
        if (cost <= 0 || key === "cost") {
          marginEl.textContent = "—";
          marginEl.classList.remove("is-pos", "is-neg");
        } else {
          const margin = ((price - cost) / cost) * 100;
          marginEl.textContent = fmtMargin(margin);
          marginEl.classList.toggle("is-pos", margin > 0);
          marginEl.classList.toggle("is-neg", margin < 0);
          row.setAttribute("data-margin", margin.toFixed(2));
        }
      }
    });
    if (priceHeader) priceHeader.textContent = priceTypeLabel(key);
  };

  fillPriceTypeOptions();
  search?.addEventListener("input", applyProductFilters);
  categoryFilter?.addEventListener("change", applyProductFilters);
  priceType?.addEventListener("change", applyPriceType);
  applyPriceType();
  applyProductFilters();

  if (document.querySelector(".form-error-box")) {
    openModal(null);
  }
})();

(() => {
  const data = window.TEZPOS_CHARTS || {};
  let daySales = Array.isArray(data.daySales) ? data.daySales : [];
  let salesMap = Object.fromEntries(daySales.map((s) => [String(s.id), s]));

  const parseMoney = (v) => {
    if (v == null || v === "") return 0;
    const n = Number(String(v).replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  };
  const fmtMoney = (n) =>
    parseMoney(n).toLocaleString("en-US", {
      maximumFractionDigits: 2,
      minimumFractionDigits: 0,
    });
  const fmtQty = (n) => {
    const v = parseMoney(n);
    return Number.isInteger(v) ? String(v) : String(v);
  };

  document.querySelectorAll("[data-fmt-money]").forEach((el) => {
    el.textContent = fmtMoney(el.getAttribute("data-fmt-money"));
  });

  const paintDaySales = (payload) => {
    const tbody = document.querySelector("#sales-mgmt-table tbody");
    if (!tbody) return;
    const rows = payload.sales || [];
    daySales = rows;
    salesMap = Object.fromEntries(rows.map((s) => [String(s.id), s]));
    data.daySales = rows;
    const kpis = document.querySelectorAll(".sales-kpis .sales-kpi strong");
    if (kpis[0]) kpis[0].textContent = fmtMoney(payload.gross);
    if (kpis[1]) kpis[1].innerHTML = fmtMoney((payload.gross || 0) - (payload.profit || 0)) + " <em>SUM</em>";
    if (kpis[2]) kpis[2].textContent = fmtMoney(payload.gross);
    if (kpis[3]) kpis[3].textContent = fmtMoney(payload.profit);
    if (kpis[4]) kpis[4].textContent = String(payload.count || 0);
    if (!rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="9" class="cabinet-empty">Bu kunda chek yo‘q.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map((s) => {
        const id = String(s.id || "");
        const short = id.length > 8 ? id.slice(0, 8) + "…" : id;
        return `<tr class="is-clickable" data-sale-id="${id}" data-search="${id} ${(s.customer || "").toLowerCase()}">
          <td>#${short}</td>
          <td>${s.time || "—"}</td>
          <td>—</td>
          <td>${s.customer || "—"}</td>
          <td>${fmtMoney(s.total)}</td>
          <td>Yopilgan</td>
          <td>${fmtMoney(s.cost)}</td>
          <td class="is-profit">${fmtMoney(s.profit)}</td>
          <td>${s.payment_label || s.payment || "—"}</td>
        </tr>`;
      })
      .join("");
  };

  const loadDaySales = () => {
    if (!data.daySalesUrl || !document.getElementById("sales-mgmt-table")) return;
    const dateKey = data.saleDate || "";
    if (!daySales.length && data._daySalesPack) {
      paintDaySales(data._daySalesPack);
    }
    if (!daySales.length && dateKey && window.tezposCacheGet) {
      const ds = window.tezposCacheGet("daySales") || {};
      if (ds[dateKey]) paintDaySales(ds[dateKey]);
    }
    if (daySales.length) {
      // Foniy yangilash — loading yo‘q
      const qs = new URLSearchParams();
      if (dateKey) qs.set("sale_date", dateKey);
      fetch(data.daySalesUrl + (qs.toString() ? "?" + qs : ""), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((r) => r.json())
        .then((json) => {
          if (json && !json.error) {
            paintDaySales(json);
            if (window.tezposCacheSet && dateKey) {
              const next = window.tezposCacheGet("daySales") || {};
              next[dateKey] = json;
              window.tezposCacheSet("daySales", next);
            }
          }
        })
        .catch(() => {});
      return;
    }
    const loading = document.getElementById("day-sales-loading");
    if (loading) {
      loading.hidden = false;
      loading.textContent = "…";
    }
    const qs = new URLSearchParams();
    if (dateKey) qs.set("sale_date", dateKey);
    fetch(data.daySalesUrl + (qs.toString() ? "?" + qs : ""), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((json) => {
        if (json && !json.error) {
          paintDaySales(json);
          if (window.tezposCacheSet && dateKey) {
            const next = window.tezposCacheGet("daySales") || {};
            next[dateKey] = json;
            window.tezposCacheSet("daySales", next);
          }
          if (loading) loading.hidden = true;
        } else if (loading) {
          loading.hidden = false;
          loading.textContent = json?.error || "Yuklanmadi";
        }
      })
      .catch(() => {
        if (loading) {
          loading.hidden = false;
          loading.textContent = "API sekin / ulanmadi";
        }
      });
  };
  loadDaySales();

  const dateInput = document.getElementById("sales-date");
  dateInput?.addEventListener("change", () => {
    const value = dateInput.value;
    if (!value) return;
    const url = new URL(window.location.href);
    url.searchParams.set("section", "sales");
    url.searchParams.set("sale_date", value);
    url.searchParams.delete("export");
    window.location.href = url.toString();
  });

  const salesSearch = document.getElementById("sales-search");
  const salesTable = document.getElementById("sales-mgmt-table");
  salesSearch?.addEventListener("input", () => {
    const q = salesSearch.value.trim().toLowerCase();
    salesTable?.querySelectorAll("tbody tr[data-sale-id]").forEach((row) => {
      const hay = row.dataset.search || "";
      row.hidden = Boolean(q) && !hay.includes(q) && !String(row.dataset.saleId).includes(q);
    });
  });

  const drawer = document.getElementById("receipt-drawer");
  const body = document.getElementById("receipt-body");

  const closeReceipt = () => {
    if (!drawer) return;
    drawer.hidden = true;
    document.body.style.overflow = "";
  };

  const openReceipt = (sale) => {
    if (!drawer || !body || !sale) return;
    const itemsHtml = (sale.items || [])
      .map(
        (it) => `<div class="receipt-item">
          <div class="receipt-item-name">${it.name}</div>
          <div class="receipt-item-line">
            <span>${fmtQty(it.qty)} x ${fmtMoney(it.unit_price)}</span>
            <strong>${fmtMoney(it.line_total)}</strong>
          </div>
        </div>`
      )
      .join("");

    body.innerHTML = `
      <dl class="receipt-meta">
        <div class="receipt-row"><dt>ID</dt><dd>#${sale.id}</dd></div>
        <div class="receipt-row"><dt>Kassir</dt><dd>${sale.cashier || "—"}</dd></div>
        <div class="receipt-row"><dt>Mijoz</dt><dd>${sale.customer || "—"}</dd></div>
        <div class="receipt-row"><dt>Turi</dt><dd>${sale.type || "Sotilgan"}</dd></div>
        <div class="receipt-row"><dt>Status</dt><dd><span class="badge-ok">${sale.status || "Yakunlangan"}</span></dd></div>
        <div class="receipt-row"><dt>Yaratilgan sana</dt><dd>${sale.created_display || ""}</dd></div>
      </dl>
      <h4 class="receipt-section-title">Tovarlar</h4>
      <div class="receipt-items">${itemsHtml || '<p class="muted">Tovar yo‘q</p>'}</div>
      <h4 class="receipt-section-title">Umumiy</h4>
      <dl class="receipt-summary">
        <div class="receipt-row"><dt>Jami :</dt><dd>${fmtMoney(sale.total_amount)}</dd></div>
        <div class="receipt-row"><dt>Chegirma :</dt><dd>${fmtMoney(sale.discount || 0)}</dd></div>
        <div class="receipt-row"><dt>To‘lovga :</dt><dd>${fmtMoney(sale.total_amount)}</dd></div>
        <div class="receipt-row"><dt>Umumiy tannarxi :</dt><dd>${fmtMoney(sale.total_cost)} SUM</dd></div>
        <div class="receipt-row"><dt>Foyda :</dt><dd class="is-profit">${fmtMoney(sale.profit)}</dd></div>
      </dl>
      <h4 class="receipt-section-title">To‘lovlar</h4>
      <dl class="receipt-pays">
        <div class="receipt-row"><dt>${sale.payment_label || "Naqt"} :</dt><dd>${fmtMoney(sale.total_amount)}</dd></div>
      </dl>
    `;
    drawer.hidden = false;
    document.body.style.overflow = "hidden";
  };

  salesTable?.querySelectorAll("tbody tr[data-sale-id]").forEach((row) => {
    row.addEventListener("click", () => {
      openReceipt(salesMap[String(row.dataset.saleId)]);
    });
  });
  document.querySelectorAll("[data-close-receipt]").forEach((el) => {
    el.addEventListener("click", closeReceipt);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawer && !drawer.hidden) closeReceipt();
  });
})();

(() => {
  const data = window.TEZPOS_CHARTS || {};
  const listEl = document.getElementById("shifts-list");
  if (!listEl) return;

  let shifts = Array.isArray(data.shifts) ? data.shifts.slice() : [];
  const summaryEl = document.getElementById("shifts-summary-kpis");
  const emptyEl = document.getElementById("shifts-empty");
  const countEl = document.getElementById("shifts-total-count");
  const hintEl = document.getElementById("shifts-source-hint");
  const detailUrl = data.shiftDetailUrl || "";
  const shiftsUrl = data.shiftsUrl || "";

  const fmtMoney = (n) =>
    Math.round(Number(n || 0)).toLocaleString("uz-UZ");
  const fmt = (n) => Number(n || 0).toLocaleString("uz-UZ");

  const paintSummary = () => {
    if (countEl) countEl.textContent = String(shifts.length);
    if (!summaryEl) return;
    const openCount = shifts.filter((s) => s.status === "open").length;
    const gross = shifts.reduce((a, s) => a + Number(s.gross || 0), 0);
    const profit = shifts.reduce((a, s) => a + Number(s.profit || 0), 0);
    const checks = shifts.reduce((a, s) => a + Number(s.checks || 0), 0);
    const margin = gross > 0 ? (profit / gross) * 100 : 0;
    summaryEl.innerHTML = `
      <div class="sales-kpi"><span>Ochiq smena</span><strong>${fmt(openCount)}</strong></div>
      <div class="sales-kpi"><span>Jami savdo</span><strong>${fmtMoney(gross)}</strong></div>
      <div class="sales-kpi"><span>Jami foyda</span><strong class="is-profit">${fmtMoney(profit)}</strong></div>
      <div class="sales-kpi"><span>Cheklar</span><strong>${fmt(checks)}</strong></div>
      <div class="sales-kpi"><span>O‘rtacha marja</span><strong>${margin.toFixed(1)}%</strong></div>
    `;
  };

  const priceListHtml = (rows) => {
    const list = Array.isArray(rows) ? rows.filter((r) => !r.is_total) : [];
    if (!list.length) return `<p class="cabinet-hint">Batafsil uchun «Yangilash» ni bosing.</p>`;
    return `<div class="price-list-stats shifts-pl-grid">${list
      .map(
        (row) => `<article class="price-list-stat">
        <h4>${row.name || "Ro‘yxat"} <em>${Number(row.share || 0).toFixed(1)}%</em></h4>
        <dl>
          <div><dt>Mahsulot</dt><dd>${fmt(row.qty)}</dd></div>
          <div><dt>Chek</dt><dd>${fmt(row.checks)}</dd></div>
          <div><dt>Tushum</dt><dd>${fmtMoney(row.revenue)}</dd></div>
          <div><dt>Foyda</dt><dd class="is-profit">${fmtMoney(row.profit)}</dd></div>
          <div><dt>Marja</dt><dd>${Number(row.markup != null ? row.markup : row.margin || 0).toFixed(1)}%</dd></div>
        </dl>
      </article>`
      )
      .join("")}</div>`;
  };

  const paint = () => {
    paintSummary();
    if (!shifts.length) {
      listEl.innerHTML = "";
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    listEl.innerHTML = shifts
      .map((sh, idx) => {
        const openClass = sh.status === "open" ? "is-open" : "is-closed";
        return `<article class="shift-card ${openClass}" data-shift-idx="${idx}">
          <header class="shift-card-head">
            <div>
              <h4>${sh.opened_display || "Smena"} → ${sh.closed_display || "—"}</h4>
              <p>${sh.cashier || "Kassir"} · <span class="shift-status">${sh.status_label || sh.status}</span></p>
            </div>
            <button type="button" class="btn-soft shift-refresh" data-shift-idx="${idx}">Yangilash</button>
          </header>
          <div class="shift-metrics">
            <div><span>Savdo</span><strong data-sh-gross>${fmtMoney(sh.gross)}</strong></div>
            <div><span>Foyda</span><strong class="is-profit" data-sh-profit>${fmtMoney(sh.profit)}</strong></div>
            <div><span>Cheklar</span><strong data-sh-checks>${fmt(sh.checks)}</strong></div>
            <div><span>Marja</span><strong data-sh-margin>${Number(sh.margin || 0).toFixed(1)}%</strong></div>
          </div>
          <div class="shift-price-lists" data-sh-pl>${priceListHtml(sh.price_lists)}</div>
        </article>`;
      })
      .join("");
  };

  const loadShiftDetail = async (idx) => {
    const sh = shifts[idx];
    if (!sh || !detailUrl) return;
    const card = listEl.querySelector(`[data-shift-idx="${idx}"]`);
    const plBox = card?.querySelector("[data-sh-pl]");
    if (plBox) plBox.innerHTML = `<p class="cabinet-hint">…</p>`;
    const isOpen = sh.status === "open" || !sh.closed_at;
    const params = new URLSearchParams({
      id: sh.id || "",
      opened_at: sh.opened_at || "",
      closed_at: isOpen ? "" : sh.closed_at || "",
      status: isOpen ? "open" : sh.status || "closed",
    });
    if (!isOpen && Array.isArray(sh.sale_ids) && sh.sale_ids.length) {
      params.set("sale_ids", sh.sale_ids.slice(0, 80).join(","));
    }
    try {
      const res = await fetch(`${detailUrl}?${params}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("fail");
      const payload = await res.json();
      const sum = payload.summary || {};
      sh.gross = sum.gross != null ? sum.gross : sh.gross;
      sh.profit = sum.profit != null ? sum.profit : sh.profit;
      sh.checks = sum.checks != null ? sum.checks : sh.checks;
      sh.margin = sum.margin != null ? sum.margin : sh.margin;
      sh.price_lists = payload.priceLists || [];
      if (card) {
        const g = card.querySelector("[data-sh-gross]");
        const p = card.querySelector("[data-sh-profit]");
        const c = card.querySelector("[data-sh-checks]");
        const m = card.querySelector("[data-sh-margin]");
        if (g) g.textContent = fmtMoney(sh.gross);
        if (p) p.textContent = fmtMoney(sh.profit);
        if (c) c.textContent = fmt(sh.checks);
        if (m) m.textContent = `${Number(sh.margin || 0).toFixed(1)}%`;
        if (plBox) plBox.innerHTML = priceListHtml(sh.price_lists);
      }
      paintSummary();
      if (window.tezposCacheSet) {
        window.tezposCacheSet("shifts", { shifts, source: data.shiftsSource || "api", ts: Date.now() });
      }
    } catch (_err) {
      if (plBox) plBox.innerHTML = priceListHtml(sh.price_lists);
    }
  };

  const applyShiftsPayload = (json) => {
    shifts = Array.isArray(json.shifts) ? json.shifts : [];
    data.shifts = shifts;
    data.shiftsSource = json.source || data.shiftsSource || "none";
    window.TEZPOS_CHARTS = data;
    if (hintEl) {
      if (json.source === "api") {
        hintEl.textContent = "TezPOS Shifts API dan yuklandi.";
      } else if (json.source === "sales") {
        hintEl.textContent =
          "TezPOS smena endpointi topilmadi — sotuvlar oralig‘idan smenalar yig‘ildi (4 soatlik tanaffus).";
      } else {
        hintEl.textContent = "Smenalar.";
      }
    }
    if (window.tezposCacheSet) {
      window.tezposCacheSet("shifts", {
        shifts,
        source: json.source || "none",
        ts: Date.now(),
      });
    }
    paint();
  };

  // Keshdan darhol
  if (!shifts.length && window.tezposCacheGet) {
    const cached = window.tezposCacheGet("shifts");
    if (cached && Array.isArray(cached.shifts) && cached.shifts.length) {
      applyShiftsPayload(cached);
    }
  } else {
    paint();
  }

  listEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".shift-refresh");
    if (!btn) return;
    loadShiftDetail(Number(btn.dataset.shiftIdx));
  });

  const loadShiftsList = () => {
    if (!shiftsUrl) return;
    if (!shifts.length) {
      listEl.innerHTML = `<p class="cabinet-hint">…</p>`;
    }
    fetch(shiftsUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((json) => {
        if (json && !json.error) applyShiftsPayload(json);
        else if (!shifts.length && emptyEl) {
          emptyEl.hidden = false;
          emptyEl.textContent = json?.error || "Smena yuklanmadi.";
          listEl.innerHTML = "";
        }
      })
      .catch(() => {
        if (!shifts.length && emptyEl) {
          emptyEl.hidden = false;
          emptyEl.textContent = "API sekin / ulanmadi";
          listEl.innerHTML = "";
        }
      });
  };
  loadShiftsList();
})();


(() => {
  const data = window.TEZPOS_CHARTS || {};
  const csrfToken = () => {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    return document.querySelector("#product-modal-form [name=csrfmiddlewaretoken]")?.value || "";
  };

  const syncUrl = data.telegramSyncUrl;
  const runTelegramSync = () => {
    if (!syncUrl) return;
    const bs = data.botSettings || {};
    if (!bs.enabled) return;
    // Faqat bot bo‘limida yoki fon timer orqali — boshqa sahifalarni sekinlatmasin
    if (data.section && data.section !== "bot" && data.section !== "overview") return;
    fetch(syncUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .catch(() => null);
  };
  if ((data.botSettings || {}).enabled) {
    setTimeout(runTelegramSync, 4000);
    setInterval(runTelegramSync, 90000);
  }

  const form = document.getElementById("bot-settings-form");
  if (!form) return;
  const statusEl = document.getElementById("bot-form-status");
  const pill = document.getElementById("bot-status-pill");
  const saveUrl = data.botSettingsUrl;
  const setStatus = (text, kind) => {
    if (!statusEl) return;
    if (!text) {
      statusEl.hidden = true;
      statusEl.textContent = "";
      return;
    }
    statusEl.hidden = false;
    statusEl.textContent = text;
    statusEl.className = "bot-form-status" + (kind ? " is-" + kind : "");
  };
  const payloadFromForm = (test) => ({
    token: document.getElementById("bot-token")?.value || "",
    recipients: document.getElementById("bot-recipients")?.value || "",
    enabled: Boolean(document.getElementById("bot-enabled")?.checked),
    notify_open: Boolean(document.getElementById("bot-notify-open")?.checked),
    notify_close: Boolean(document.getElementById("bot-notify-close")?.checked),
    test: Boolean(test),
  });
  const save = async (test) => {
    if (!saveUrl) {
      setStatus("URL topilmadi", "error");
      return;
    }
    setStatus(test ? "Test yuborilmoqda…" : "Saqlanmoqda…", "");
    try {
      const body = payloadFromForm(test);
      const res = await fetch(saveUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
          Accept: "application/json",
        },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus(json.error || "Xato", "error");
        return;
      }
      data.botSettings = {
        enabled: Boolean(json.enabled),
        token_set: true,
        notify_open: body.notify_open,
        notify_close: body.notify_close,
      };
      if (pill) {
        pill.textContent = json.enabled ? "Yoqilgan" : "O‘chirilgan";
        pill.classList.toggle("is-on", Boolean(json.enabled));
        pill.classList.toggle("is-off", !json.enabled);
      }
      let msg = "Saqlandi.";
      let kind = "ok";
      if (test) {
        const rows = json.test || [];
        const okN = rows.filter((t) => t.ok).length;
        const failN = rows.length - okN;
        msg =
          "Test: " +
          okN +
          " ta muvaffaqiyatli" +
          (failN ? ", " + failN + " ta xato" : "") +
          ".";
        const bad = rows.find((t) => !t.ok);
        if (bad) {
          msg += " " + (bad.error || ((bad.raw && bad.raw.description) || "xato"));
          kind = "error";
        }
      }
      if (json.bot && json.bot.username) msg += " Bot: @" + json.bot.username;
      setStatus(msg, kind);
      if (!test) runTelegramSync();
    } catch (err) {
      setStatus(err.message || "Tarmoq xatosi", "error");
    }
  };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    save(false);
  });
  document.getElementById("bot-test-btn")?.addEventListener("click", () => save(true));
  document.getElementById("bot-find-chats-btn")?.addEventListener("click", async () => {
    const chatsUrl = data.botChatsUrl;
    const box = document.getElementById("bot-chats-box");
    const list = document.getElementById("bot-chats-list");
    const tokenVal = document.getElementById("bot-token")?.value || "";
    if (!chatsUrl) return;
    setStatus("Chatlar qidirilmoqda… Avval botga /start yozgan bo‘lishingiz kerak.", "");
    try {
      const qs = new URLSearchParams();
      if (tokenVal) qs.set("token", tokenVal);
      const res = await fetch(chatsUrl + (qs.toString() ? "?" + qs : ""), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus(json.error || "Chatlar topilmadi", "error");
        return;
      }
      const chats = json.chats || [];
      if (!chats.length) {
        if (box) box.hidden = true;
        const botU = (json.bot && json.bot.username) || "bot";
        setStatus(
          "Hali chat yo‘q. Telegramda @" + botU + " ga kirib /start yozing, keyin qayta bosing.",
          "error"
        );
        return;
      }
      if (list) {
        list.innerHTML = chats
          .map((c) => {
            const label =
              (c.name || "Chat") +
              (c.username ? " " + c.username : "") +
              " · " +
              c.id +
              (c.type ? " (" + c.type + ")" : "");
            return (
              '<button type="button" class="bot-chat-item" data-chat-id="' +
              String(c.id).replace(/"/g, "") +
              '">' +
              label.replace(/</g, "&lt;") +
              "</button>"
            );
          })
          .join("");
      }
      if (box) box.hidden = false;
      setStatus(chats.length + " ta chat topildi. Keraklisini bosing — ID qo‘shiladi.", "ok");
    } catch (err) {
      setStatus(err.message || "Tarmoq xatosi", "error");
    }
  });
  document.getElementById("bot-chats-list")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".bot-chat-item");
    if (!btn) return;
    const id = btn.getAttribute("data-chat-id");
    const ta = document.getElementById("bot-recipients");
    if (!ta || !id) return;
    const lines = String(ta.value || "")
      .split(/\r?\n/)
      .map((x) => x.trim())
      .filter(Boolean);
    if (!lines.includes(id)) {
      lines.push(id);
      ta.value = lines.join("\n");
    }
    setStatus("Qo‘shildi: " + id + ". Endi «Test xabar yuborish»ni bosing.", "ok");
  });
  document.getElementById("bot-sync-btn")?.addEventListener("click", async () => {
    if (!syncUrl) return;
    setStatus("Smenalar tekshirilmoqda…", "");
    try {
      const res = await fetch(syncUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus(json.error || "Tekshiruv xatosi", "error");
        return;
      }
      if (json.skipped) {
        setStatus("Bot o‘chirilgan yoki sozlanmagan.", "");
        return;
      }
      const n = (json.sent || []).length;
      setStatus(
        n
          ? n + " ta yangi smena xabari yuborildi (tekshirildi: " + (json.checked || 0) + ")."
          : "Yangi smena yo‘q (tekshirildi: " + (json.checked || 0) + ").",
        "ok"
      );
    } catch (err) {
      setStatus(err.message || "Tarmoq xatosi", "error");
    }
  });
})();

(() => {
  const data = window.TEZPOS_CHARTS || {};
  const listEl = document.getElementById("debtors-list");
  if (!listEl) return;

  const fmt = (n) => Number(n || 0).toLocaleString("uz-UZ");
  const esc = (s) =>
    String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const csrfToken = () => {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    return "";
  };
  const initials = (name) => {
    const parts = String(name || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  };
  const parseAmount = (raw) => {
    const n = Number(String(raw || "").replace(/\s/g, "").replace(",", "."));
    return Number.isFinite(n) ? n : NaN;
  };

  const countEl = document.getElementById("debtors-count");
  const totalEl = document.getElementById("debtors-total");
  const emptyEl = document.getElementById("debtors-empty");
  const errorEl = document.getElementById("debtors-error");
  const searchEl = document.getElementById("debtors-search");
  const refreshBtn = document.getElementById("debtors-refresh");
  const listUrl = data.debtorsUrl;
  const payUrl = data.debtorPayUrl;

  let debtors = [];
  let query = "";

  const presetsFor = (debt) => {
    const d = Number(debt) || 0;
    const half = Math.floor(d / 2);
    const opts = [
      { label: "1/2", amount: half },
      { label: "To‘liq", amount: d },
    ];
    [10000, 50000, 100000, 200000, 500000].forEach((v) => {
      if (v < d) opts.push({ label: fmt(v), amount: v });
    });
    return opts.filter((o) => o.amount > 0);
  };

  const cardHtml = (row) => {
    const debt = Number(row.debt) || 0;
    const paidOff = debt <= 0;
    const presets = presetsFor(debt)
      .map(
        (p) =>
          `<button type="button" class="debtor-preset" data-amount="${p.amount}">${esc(p.label)}</button>`
      )
      .join("");
    return `<article class="debtor-card${paidOff ? " is-paid-off" : ""}" data-id="${esc(row.id)}" data-debt="${debt}">
      <div class="debtor-card-head">
        <div class="debtor-who">
          <div class="debtor-avatar">${esc(initials(row.name))}</div>
          <div>
            <h4>${esc(row.name)}</h4>
            <p>${esc(row.phone || "Telefon yo‘q")}</p>
          </div>
        </div>
        <div class="debtor-balance">${fmt(debt)} so‘m</div>
      </div>
      ${
        paidOff
          ? `<p class="debtor-msg is-ok">Qarz to‘liq yopildi.</p>`
          : `<p class="debtor-pay-label">Qisman to‘lov</p>
      <div class="debtor-pay-row">
        <input type="text" inputmode="decimal" placeholder="Qancha to‘lash (so‘m)" class="debtor-amount" aria-label="To‘lov summasi">
        <select class="debtor-pay-type" aria-label="To‘lov turi">
          <option value="cash">Naqd</option>
          <option value="card">Karta</option>
        </select>
        <button type="button" class="btn-save debtor-pay-btn">Yuborish</button>
      </div>
      <div class="debtor-presets">${presets}</div>
      <p class="debtor-hint">Masalan 50 000 yozib Yuborish — qarz kamayadi. To‘liq — butun qoldiq.</p>
      <p class="debtor-msg" hidden></p>`
      }
    </article>`;
  };

  const filtered = () => {
    const q = query.trim().toLowerCase();
    if (!q) return debtors;
    return debtors.filter((d) => {
      const name = String(d.name || "").toLowerCase();
      const phone = String(d.phone || "").toLowerCase();
      return name.includes(q) || phone.includes(q);
    });
  };

  const paint = () => {
    const rows = filtered();
    if (countEl) countEl.textContent = String(debtors.length);
    if (totalEl) {
      const sum = debtors.reduce((a, d) => a + (Number(d.debt) || 0), 0);
      totalEl.textContent = `${fmt(sum)} so‘m`;
    }
    if (!rows.length) {
      listEl.innerHTML = "";
      if (emptyEl) {
        emptyEl.hidden = false;
        emptyEl.textContent = query.trim()
          ? "Qidiruv bo‘yicha qarzdor topilmadi."
          : "Qarzdorlar yo‘q.";
      }
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    listEl.innerHTML = rows.map(cardHtml).join("");
  };

  const setError = (msg) => {
    if (!errorEl) return;
    if (!msg) {
      errorEl.hidden = true;
      errorEl.textContent = "";
      return;
    }
    errorEl.hidden = false;
    errorEl.textContent = msg;
  };

  const load = async () => {
    if (!listUrl) return;
    setError("");
    const cached =
      Array.isArray(data._debtorsCache) && data._debtorsCache.length
        ? data._debtorsCache
        : window.tezposCacheGet
          ? (window.tezposCacheGet("debtors") || {}).debtors
          : null;
    if (Array.isArray(cached) && cached.length) {
      debtors = cached;
      paint();
    } else {
      listEl.innerHTML =
        '<div class="cab-inline-skel cab-inline-skel--lines" aria-hidden="true"><span class="sk"></span><span class="sk"></span><span class="sk"></span></div>';
      if (emptyEl) emptyEl.hidden = true;
    }
    try {
      const res = await fetch(listUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (!cached?.length) {
          debtors = [];
          paint();
          setError(json.error || "Qarzdorlar yuklanmadi.");
        }
        return;
      }
      debtors = Array.isArray(json.debtors) ? json.debtors : [];
      data._debtorsCache = debtors;
      if (window.tezposCacheSet) {
        window.tezposCacheSet("debtors", { debtors, ts: Date.now() });
      }
      paint();
    } catch (err) {
      if (!cached?.length) {
        debtors = [];
        paint();
        setError(err.message || "Tarmoq xatosi");
      }
    }
  };

  const showMsg = (card, text, ok) => {
    const msg = card.querySelector(".debtor-msg");
    if (!msg) return;
    msg.hidden = !text;
    msg.textContent = text || "";
    msg.classList.toggle("is-ok", Boolean(ok));
    msg.classList.toggle("is-error", !ok);
  };

  listEl.addEventListener("click", async (e) => {
    const preset = e.target.closest(".debtor-preset");
    if (preset) {
      const card = preset.closest(".debtor-card");
      const input = card?.querySelector(".debtor-amount");
      if (input) input.value = String(preset.dataset.amount || "");
      return;
    }
    const btn = e.target.closest(".debtor-pay-btn");
    if (!btn || !payUrl) return;
    const card = btn.closest(".debtor-card");
    if (!card) return;
    const id = card.getAttribute("data-id");
    const debt = Number(card.getAttribute("data-debt") || 0);
    const input = card.querySelector(".debtor-amount");
    const typeEl = card.querySelector(".debtor-pay-type");
    const amount = parseAmount(input?.value);
    if (!Number.isFinite(amount) || amount <= 0) {
      showMsg(card, "Summani kiriting.", false);
      return;
    }
    if (amount > debt + 1e-6) {
      showMsg(card, `Maksimal to‘lov: ${fmt(debt)} so‘m`, false);
      return;
    }
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "…";
    showMsg(card, "", true);
    try {
      const res = await fetch(payUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
        },
        body: JSON.stringify({
          customer_id: id,
          amount,
          payment_type: typeEl?.value || "cash",
        }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) {
        showMsg(card, json.error || "To‘lov amalga oshmadi.", false);
        return;
      }
      const balance = Number(json.balance || 0);
      const idx = debtors.findIndex((d) => String(d.id) === String(id));
      if (idx >= 0) {
        if (balance <= 0) debtors.splice(idx, 1);
        else debtors[idx] = { ...debtors[idx], debt: balance };
      }
      let okText = `To‘lov: ${json.paid_display || fmt(json.paid)} so‘m. Qoldiq: ${
        json.balance_display || fmt(balance)
      } so‘m.`;
      if (json.sms_sent) {
        okText += " SMS yuborildi.";
      } else if (json.sms_error) {
        okText += " SMS: " + json.sms_error;
      } else if (!json.sms_phone) {
        okText += " SMS: mijozda telefon yo‘q.";
      } else {
        okText += " SMS yuborilmadi.";
      }
      paint();
      const fresh = listEl.querySelector(`.debtor-card[data-id="${CSS.escape(String(id))}"]`);
      if (fresh) {
        showMsg(fresh, okText, Boolean(json.sms_sent) || !json.sms_error);
        if (json.check_url) {
          const link = document.createElement("a");
          link.href = json.check_url;
          link.target = "_blank";
          link.rel = "noopener";
          link.className = "debtor-check-link";
          link.textContent = "Elektron chekni ochish →";
          fresh.appendChild(link);
        }
      } else if (emptyEl && !debtors.length) {
        emptyEl.hidden = false;
        emptyEl.textContent = "Barcha qarzlar yopildi.";
      }
    } catch (err) {
      showMsg(card, err.message || "Tarmoq xatosi", false);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  });

  searchEl?.addEventListener("input", () => {
    query = searchEl.value || "";
    paint();
  });
  refreshBtn?.addEventListener("click", () => load());
  load();
})();

(() => {
  const panel = document.getElementById("stock-value-panel");
  if (!panel) return;

  const data = window.TEZPOS_CHARTS || {};
  const fmt = (n) =>
    Number(n || 0).toLocaleString("uz-UZ", {
      maximumFractionDigits: 2,
    });
  const esc = (s) =>
    String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const thead = document.getElementById("stock-value-thead");
  const tbody = document.getElementById("stock-value-tbody");
  const tfoot = document.getElementById("stock-value-tfoot");
  const emptyEl = document.getElementById("sv-empty");
  const searchEl = document.getElementById("sv-search");
  const filterEl = document.getElementById("sv-stock-filter");
  const kpisEl = document.getElementById("stock-value-kpis");

  // Sotuv ustuni selling_price; qolgan (is_selling bo‘lmagan) ro‘yxatlar list_prices dan
  const getExtraLists = () =>
    (Array.isArray(data.priceLists) ? data.priceLists : []).filter(
      (pl) => pl && pl.id && !pl.is_selling
    );

  const listPrice = (p, plId) => {
    const lp = p.list_prices || {};
    const v = lp[plId] ?? lp[String(plId)];
    return Number(v || 0);
  };

  const matchesFilter = (qty, mode) => {
    if (mode === "all") return true;
    if (mode === "lt0") return qty < 0;
    if (mode === "eq0") return qty === 0;
    return qty > 0;
  };

  const paint = () => {
    const products = Array.isArray(data.products) ? data.products : [];
    const extraLists = getExtraLists();
    const q = String(searchEl?.value || "")
      .trim()
      .toLowerCase();
    const mode = filterEl?.value || "gt0";

    const rows = [];
    let sumQty = 0;
    let sumCost = 0;
    let sumSell = 0;
    const sumLists = {};
    extraLists.forEach((pl) => {
      sumLists[String(pl.id)] = 0;
    });

    products.forEach((p) => {
      const qty = Number(p.stock_qty || 0);
      if (!matchesFilter(qty, mode)) return;
      const name = String(p.name || "");
      const barcode = String(p.barcode || "");
      if (
        q &&
        !name.toLowerCase().includes(q) &&
        !barcode.toLowerCase().includes(q)
      ) {
        return;
      }
      const cost = Number(p.cost_price || 0);
      const sell = Number(p.selling_price || 0);
      const costVal = qty * cost;
      const sellVal = qty * sell;
      const listVals = {};
      extraLists.forEach((pl) => {
        const id = String(pl.id);
        const unit = listPrice(p, id);
        const val = qty * unit;
        listVals[id] = { unit, val };
        sumLists[id] += val;
      });
      sumQty += qty;
      sumCost += costVal;
      sumSell += sellVal;
      rows.push({
        name,
        barcode,
        qty,
        cost,
        sell,
        costVal,
        sellVal,
        listVals,
      });
    });

    rows.sort((a, b) => Math.abs(b.costVal) - Math.abs(a.costVal));

    const countEl = document.getElementById("sv-count");
    const qtyEl = document.getElementById("sv-qty");
    const costEl = document.getElementById("sv-cost-total");
    const sellEl = document.getElementById("sv-sell-total");
    const marginEl = document.getElementById("sv-margin-total");
    if (countEl) countEl.textContent = fmt(rows.length);
    if (qtyEl) qtyEl.textContent = fmt(sumQty);
    if (costEl) costEl.textContent = fmt(sumCost) + " so'm";
    if (sellEl) sellEl.textContent = fmt(sumSell) + " so'm";
    if (marginEl) marginEl.textContent = fmt(sumSell - sumCost) + " so'm";

    // Narx ro‘yxatlari KPI
    if (kpisEl) {
      kpisEl.querySelectorAll("[data-sv-pl]").forEach((el) => el.remove());
      extraLists.forEach((pl) => {
        const id = String(pl.id);
        const card = document.createElement("div");
        card.className = "debtors-kpi";
        card.setAttribute("data-sv-pl", id);
        card.innerHTML =
          "<span>" +
          esc(pl.name || "Narx") +
          " jami</span><strong>" +
          fmt(sumLists[id] || 0) +
          " so'm</strong>";
        kpisEl.appendChild(card);
      });
    }

    if (thead) {
      thead.innerHTML =
        "<tr>" +
        "<th class=\"col-name\">Mahsulot</th>" +
        "<th class=\"col-num\">Qoldiq</th>" +
        "<th class=\"col-num\">Tannarx</th>" +
        "<th class=\"col-num\">Tannarx jami</th>" +
        "<th class=\"col-num\">Sotuv</th>" +
        "<th class=\"col-num\">Sotuv jami</th>" +
        extraLists
          .map(
            (pl) =>
              '<th class="col-num">' +
              esc(pl.name || "Narx") +
              "</th><th class=\"col-num\">" +
              esc(pl.name || "Narx") +
              " jami</th>"
          )
          .join("") +
        "</tr>";
    }

    if (!rows.length) {
      if (tbody) tbody.innerHTML = "";
      if (tfoot) tfoot.innerHTML = "";
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;

    if (tbody) {
      tbody.innerHTML = rows
        .map((r) => {
          const stockCls =
            r.qty > 0
              ? "is-stock-pos"
              : r.qty < 0
                ? "is-stock-neg"
                : "is-stock-zero";
          const listCells = extraLists
            .map((pl) => {
              const id = String(pl.id);
              const lv = r.listVals[id] || { unit: 0, val: 0 };
              return (
                '<td class="col-num">' +
                fmt(lv.unit) +
                '</td><td class="col-num">' +
                fmt(lv.val) +
                "</td>"
              );
            })
            .join("");
          return (
            "<tr>" +
            '<td class="col-name"><strong>' +
            esc(r.name) +
            "</strong>" +
            (r.barcode
              ? '<small class="sv-barcode">' + esc(r.barcode) + "</small>"
              : "") +
            "</td>" +
            '<td class="col-num ' +
            stockCls +
            '">' +
            fmt(r.qty) +
            "</td>" +
            '<td class="col-num">' +
            fmt(r.cost) +
            "</td>" +
            '<td class="col-num">' +
            fmt(r.costVal) +
            "</td>" +
            '<td class="col-num">' +
            fmt(r.sell) +
            "</td>" +
            '<td class="col-num">' +
            fmt(r.sellVal) +
            "</td>" +
            listCells +
            "</tr>"
          );
        })
        .join("");
    }

    if (tfoot) {
      const listFoot = extraLists
        .map((pl) => {
          const id = String(pl.id);
          return (
            '<td class="col-num"></td><td class="col-num"><strong>' +
            fmt(sumLists[id] || 0) +
            "</strong></td>"
          );
        })
        .join("");
      tfoot.innerHTML =
        "<tr>" +
        "<th>Jami</th>" +
        '<th class="col-num">' +
        fmt(sumQty) +
        "</th>" +
        "<th></th>" +
        '<th class="col-num">' +
        fmt(sumCost) +
        "</th>" +
        "<th></th>" +
        '<th class="col-num">' +
        fmt(sumSell) +
        "</th>" +
        listFoot +
        "</tr>";
    }
  };

  searchEl?.addEventListener("input", paint);
  filterEl?.addEventListener("change", paint);
  document.addEventListener("tezpos:catalog", paint);
  paint();
})();

(() => {
  const tbody = document.getElementById("abc-tbody");
  if (!tbody) return;
  const data = window.TEZPOS_CHARTS || {};
  const fmt = (n) => Number(n || 0).toLocaleString("uz-UZ");
  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");

  const paintAbc = (json) => {
    if (!json) return;
    const matrix = json.matrix || {};
    document.querySelectorAll("[data-abc]").forEach((el) => {
      const k = el.getAttribute("data-abc");
      el.textContent = String(matrix[k] != null ? matrix[k] : 0);
    });
    const totalEl = document.getElementById("abc-total");
    if (totalEl) totalEl.textContent = fmt(json.total);
    const rows = Array.isArray(json.rows) ? json.rows : [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7">Ma\'lumot yo‘q</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(
        (row) => `<tr>
        <td>${esc(row.name)}</td>
        <td><span class="badge-abc badge-${esc(row.abc)}">${esc(row.abc)}</span></td>
        <td><span class="badge-xyz badge-${esc(row.xyz)}">${esc(row.xyz)}</span></td>
        <td>${esc(row.group)}</td>
        <td>${fmt(row.revenue)}</td>
        <td>${Number(row.share || 0).toFixed(1)}%</td>
        <td>${fmt(row.stock)}</td>
      </tr>`
      )
      .join("");
  };

  const cached = window.tezposCacheGet && window.tezposCacheGet("abc");
  if (cached && cached.rows) paintAbc(cached);

  if (data.abcUrl) {
    fetch(data.abcUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then((r) => r.json())
      .then((json) => {
        if (json && !json.error) {
          if (window.tezposCacheSet) window.tezposCacheSet("abc", json);
          paintAbc(json);
        }
      })
      .catch(() => {
        if (!cached) {
          tbody.innerHTML = '<tr><td colspan="7">Yuklanmadi</td></tr>';
        }
      });
  }
})();
