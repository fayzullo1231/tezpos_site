(() => {
  const data = window.TEZPOS_CHARTS || {};
  const fmt = (n) => Number(n || 0).toLocaleString("uz-UZ");
  const palette = [
    "#2c86e0", "#12b3a1", "#3fd07a", "#f59e0b", "#6366f1",
    "#0ea5e9", "#14b8a6", "#84cc16", "#f97316", "#8b5cf6",
    "#0284c7", "#0d9488", "#65a30d", "#ea580c", "#7c3aed",
  ];

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
    a.addEventListener("click", () => setNavOpen(false));
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
  const salesRangeToggle = document.getElementById("sales-range-toggle");
  const salesRangeSummary = document.getElementById("sales-range-summary");
  const rangeLabels = {
    d1: "1 kunlik",
    d7: "7 kunlik",
    d15: "15 kunlik",
    d30: "30 kunlik",
    m1: "1 oylik",
    m3: "3 oylik",
    m6: "6 oylik",
    y1: "1 yillik",
  };

  const paintSalesSummary = (key, pack) => {
    if (!salesRangeSummary || !pack) return;
    const total = (pack.totals || []).reduce((a, b) => a + b, 0);
    const count = (pack.counts || []).reduce((a, b) => a + b, 0);
    salesRangeSummary.innerHTML = `
      <li>Davr: <strong>${rangeLabels[key] || key}</strong></li>
      <li>Cheklar: <strong>${count}</strong></li>
      <li>Tushum: <strong>${Math.round(total).toLocaleString("uz-UZ")} so'm</strong></li>
    `;
  };

  if (hasChart && lineEl) {
    const stats = data.salesStats || {
      d7: { labels: data.labels || [], totals: data.totals || [], counts: data.counts || [] },
    };
    const initialKey = "d7";
    const initial = stats[initialKey] || { labels: [], totals: [], counts: [] };
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
    paintSalesSummary(initialKey, initial);

    salesRangeToggle?.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.range;
        const pack = stats[key];
        if (!pack) return;
        salesRangeToggle.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        salesChart.data.labels = pack.labels;
        salesChart.data.datasets[0].data = pack.totals;
        salesChart.update();
        paintSalesSummary(key, pack);
      });
    });
  }

  const shareSelect = document.getElementById("share-top-select");
  const shareGrid = document.getElementById("share-product-grid");

  const productTileHtml = (item, color) => {
    const initial = (item.name || "?").trim().charAt(0).toUpperCase();
    const media = item.image
      ? `<img src="${item.image}" alt="${item.name}" loading="lazy" width="72" height="72">`
      : `<span class="product-tile-fallback" style="--tile-accent:${color}">${initial}</span>`;
    return `<article class="product-tile">
      <div class="product-tile-media">${media}</div>
      <div class="product-tile-body">
        <h4>${item.name}</h4>
        <p class="product-tile-stock">Qoldiq: <strong>${fmt(item.stock)}</strong></p>
        <div class="product-tile-prices">
          <span><em>Ulgurji</em>${fmt(item.wholesale)}</span>
          <span><em>Sotuv</em>${fmt(item.selling)}</span>
        </div>
      </div>
    </article>`;
  };

  if (shareGrid) {
    const items = Array.isArray(data.shareItems) ? data.shareItems : [];
    const paintShare = (n) => {
      const rows = items.slice(0, n);
      shareGrid.innerHTML = rows.map((row, i) => productTileHtml(row, palette[i % palette.length])).join("");
    };
    paintShare(Number(shareSelect?.value || 50));
    shareSelect?.addEventListener("change", () => {
      paintShare(Number(shareSelect.value));
    });
  }

  const reportEl = document.getElementById("reportLineChart");
  const periodToggle = document.getElementById("period-toggle");
  if (hasChart && reportEl && data.reports) {
    const reportChart = new Chart(reportEl, {
      type: "line",
      data: {
        labels: data.reports.daily.labels,
        datasets: [
          {
            label: "Tushum",
            data: data.reports.daily.totals,
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

    periodToggle?.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.period;
        const pack = data.reports[key];
        if (!pack) return;
        periodToggle.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        reportChart.data.labels = pack.labels;
        reportChart.data.datasets[0].data = pack.totals;
        reportChart.update();

        const summary = document.getElementById("report-summary");
        if (summary) {
          const total = pack.totals.reduce((a, b) => a + b, 0);
          const count = (pack.counts || []).reduce((a, b) => a + b, 0);
          const periodLabel =
            key === "weekly" ? "Haftalik" : key === "monthly" ? "Oylik" : "Kunlik";
          summary.innerHTML = `
              <li>Davr: <strong>${periodLabel}</strong></li>
              <li>Cheklar (davr): <strong>${count}</strong></li>
              <li>Tushum (davr): <strong>${Math.round(total).toLocaleString("uz-UZ")} so'm</strong></li>
            `;
        }
      });
    });
  }

  const signalsList = document.getElementById("signals-list");
  const signalsSelect = document.getElementById("signals-top-select");
  const signalsEmpty = document.getElementById("signals-empty");
  const renderSignals = (limit) => {
    if (!signalsList) return;
    const rows = (data.nearMin || []).slice(0, limit);
    signalsList.innerHTML = rows
      .map((s) => {
        const minNote =
          s.min_stock != null
            ? ` <span class="signal-min">(min ${fmt(s.min_stock)})</span>`
            : "";
        return `<article class="signal-card signal-${s.level}">
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
  }

  const productsBody = document.getElementById("tops-products-body");
  const productsSelect = document.getElementById("tops-products-select");
  const renderProducts = (limit) => {
    if (!productsBody) return;
    const rows = (data.topProducts || []).slice(0, limit);
    productsBody.innerHTML = rows.length
      ? rows
          .map(
            (row, i) => `<tr>
              <td>${i + 1}</td>
              <td>${row.name}</td>
              <td>${fmt(row.qty)}</td>
              <td>${fmt(row.revenue)}</td>
              <td>${fmt(row.stock)}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="5">Ma'lumot yo‘q</td></tr>`;
  };
  if (productsBody) {
    renderProducts(Number(productsSelect?.value || 10));
    productsSelect?.addEventListener("change", () => {
      renderProducts(Number(productsSelect.value));
    });
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

  const printBtn = document.getElementById("print-labels-btn");
  if (printBtn) {
    printBtn.addEventListener("click", () => {
      const checks = [...document.querySelectorAll(".label-check:checked")];
      if (!checks.length) {
        alert("Kamida bitta tovarni belgilang.");
        return;
      }
      const labels = checks
        .map((el) => {
          const name = el.dataset.name || "";
          const price = el.dataset.price || "";
          const barcode = el.dataset.barcode || "";
          return `<div class="label">
            <div class="brand">TezPOS</div>
            <div class="name">${name}</div>
            <div class="price">${Number(price).toLocaleString("uz-UZ")} so'm</div>
            <div class="code">${barcode}</div>
          </div>`;
        })
        .join("");

      const win = window.open("", "_blank", "width=900,height=700");
      if (!win) return;
      win.document.open();
      win.document.write("<!doctype html><html><head><title>Narx yorliqlari</title>");
      win.document.write(`<style>
          @page { margin: 12mm; }
          body { font-family: Arial, sans-serif; margin: 0; padding: 12px; background: #fff; color: #000; }
          .sheet { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
          .label {
            border: 1px solid #222; border-radius: 6px; padding: 10px 12px;
            min-height: 90px; break-inside: avoid;
          }
          .brand { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
          .name { font-size: 14px; font-weight: 700; margin: 8px 0 6px; }
          .price { font-size: 18px; font-weight: 800; }
          .code { font-size: 11px; margin-top: 8px; letter-spacing: .04em; }
          @media print {
            body { padding: 0; }
            .no-print { display: none !important; }
          }
        </style></head><body>
          <p class="no-print" style="margin:0 0 12px;font-size:13px;">Chop etish oynasi — faqat yorliqlar</p>
          <div class="sheet">${labels}</div>`);
      win.document.write("<script>window.onload=function(){window.print();}</" + "script>");
      win.document.write("</body></html>");
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
  const priceAttr = { selling: "data-selling", wholesale: "data-wholesale", cost: "data-cost" };
  const priceLabels = { selling: "Narxi", wholesale: "Ulgurji", cost: "Tannarx" };

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

  const formatProductRowNumbers = () => {
    if (!table) return;
    table.querySelectorAll("tbody tr[data-edit-product]").forEach((row) => {
      const costEl = row.querySelector("[data-cost-display]");
      if (costEl) costEl.textContent = fmtMoney(row.getAttribute("data-cost"));

      const marginEl = row.querySelector("[data-margin-display]");
      if (marginEl) {
        const hasCost = parseMoney(row.getAttribute("data-cost")) > 0;
        marginEl.textContent = hasCost ? fmtMargin(row.getAttribute("data-margin")) : "—";
      }
    });
  };

  const applyPriceType = () => {
    if (!table || !priceType) return;
    const key = priceType.value || "selling";
    const attr = priceAttr[key] || priceAttr.selling;
    table.querySelectorAll("tbody tr").forEach((row) => {
      const el = row.querySelector("[data-price-display]");
      if (!el) return;
      el.textContent = fmtMoney(row.getAttribute(attr));
    });
    if (priceHeader) priceHeader.textContent = priceLabels[key] || "Narxi";
  };

  search?.addEventListener("input", applyProductFilters);
  categoryFilter?.addEventListener("change", applyProductFilters);
  priceType?.addEventListener("change", applyPriceType);
  formatProductRowNumbers();
  applyPriceType();
  applyProductFilters();

  if (document.querySelector(".form-error-box")) {
    openModal(null);
  }
})();

(() => {
  const data = window.TEZPOS_CHARTS || {};
  const daySales = Array.isArray(data.daySales) ? data.daySales : [];
  const salesMap = Object.fromEntries(daySales.map((s) => [String(s.id), s]));

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
