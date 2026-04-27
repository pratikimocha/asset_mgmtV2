/**
 * Asset Management System v2 — Dashboard JavaScript
 * Handles: Status donut chart, KPI tile clicks, inline asset list,
 *          debounced search/filter, auto-load on DOMContentLoaded
 */
(function () {
  'use strict';

  /* ────────────────────────────────────────────────────────────
   * Utilities
   * ──────────────────────────────────────────────────────────── */
  function escHtml(str) {
    if (window.escHtml) return window.escHtml(str);
    const d = document.createElement('div');
    d.textContent = String(str == null ? '' : str);
    return d.innerHTML;
  }

  function debounce(fn, delay) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  /* ────────────────────────────────────────────────────────────
   * Safe JSON parse from <script type="application/json"> block
   * ──────────────────────────────────────────────────────────── */
  function safeParse(elId) {
    try {
      const el = document.getElementById(elId);
      return el ? JSON.parse(el.textContent) : null;
    } catch (e) {
      console.error('[dashboard] data parse error for', elId, e);
      return null;
    }
  }

  const stats = safeParse('dashboard-data') || {};

  /* ────────────────────────────────────────────────────────────
   * Inline asset list state
   * ──────────────────────────────────────────────────────────── */
  let currentParams    = {};
  let currentPage      = 0;
  const PAGE_LIMIT     = 25;
  let moreAvailable    = false;
  let activeTile       = null;

  /* ────────────────────────────────────────────────────────────
   * Status color map (matches CSS status pills)
   * ──────────────────────────────────────────────────────────── */
  const STATUS_COLORS = {
    deployed : '#22c55e',
    instock  : '#3b82f6',
    repair   : '#f97316',
    sold     : '#8b5cf6',
    retired  : '#94a3b8',
    ordered  : '#06b6d4',
    received : '#14b8a6',
  };

  /* ────────────────────────────────────────────────────────────
   * 1. Status Breakdown Donut Chart (Chart.js)
   * ──────────────────────────────────────────────────────────── */
  function initStatusChart() {
    if (typeof Chart === 'undefined') return;

    const ctx = document.getElementById('statusChart');
    if (!ctx) return;

    const breakdown = stats.breakdown || [];
    if (!breakdown.length) return;

    const labels  = breakdown.map(i => i.label || i.key);
    const data    = breakdown.map(i => i.count || 0);
    const colors  = breakdown.map(i => i.color || STATUS_COLORS[i.key] || '#94a3b8');

    const chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: data,
          backgroundColor: colors,
          borderWidth: 3,
          borderColor: '#ffffff',
          hoverOffset: 6,
        }],
      },
      options: {
        cutout: '62%',
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (context) {
                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                const pct   = total > 0 ? Math.round((context.parsed / total) * 100) : 0;
                return ` ${context.label}: ${context.parsed} (${pct}%)`;
              },
            },
          },
        },
        onClick: function (evt, elements) {
          if (!elements.length) return;
          const idx = elements[0].index;
          const key = breakdown[idx].key;
          if (key) fetchAssets({ filter: key });
        },
      },
    });

    // Store reference for potential updates
    ctx.__chartInstance = chart;
  }

  /* ────────────────────────────────────────────────────────────
   * Model Distribution Pie Chart
   * ──────────────────────────────────────────────────────────── */
  function initModelChart() {
    if (typeof Chart === 'undefined') return;
    const ctx    = document.getElementById('modelChart');
    if (!ctx) return;
    const models = stats.models || [];
    if (!models.length) return;

    const palette = ['#60A5FA','#34D399','#F97316','#F472B6','#FDE68A','#C084FC','#F87171','#93C5FD','#6EE7B7','#FCA5A5'];

    new Chart(ctx, {
      type: 'pie',
      data: {
        labels: models.map(m => m.label || m.key),
        datasets: [{
          data: models.map(m => m.count || 0),
          backgroundColor: models.map((_, i) => palette[i % palette.length]),
          borderColor: '#ffffff',
          borderWidth: 2,
        }],
      },
      options: {
        plugins: {
          legend: { display: false },
        },
        onClick: function (evt, elts) {
          if (!elts.length) return;
          const m = models[elts[0].index];
          if (m && m.key) fetchAssets({ model: m.key });
        },
      },
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Warranty Donut Chart
   * ──────────────────────────────────────────────────────────── */
  function initWarrantyChart() {
    if (typeof Chart === 'undefined') return;
    const ctx     = document.getElementById('warrantyChart');
    if (!ctx) return;
    const warranty = stats.warranty || {};

    const values = [
      warranty.active   || 0,
      warranty.expiring || 0,
      warranty.expired  || 0,
      warranty.unknown  || 0,
    ];

    new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['Active', 'Expiring Soon', 'Expired', 'Unknown'],
        datasets: [{
          data: values,
          backgroundColor: ['#34D399', '#FDE68A', '#F87171', '#cbd5e1'],
          borderColor: '#ffffff',
          borderWidth: 2,
        }],
      },
      options: {
        cutout: '60%',
        plugins: { legend: { display: false } },
        onClick: function (evt, elts) {
          if (!elts.length) return;
          const keys = ['active', 'expiring', 'expired', 'unknown'];
          const key  = keys[elts[0].index];
          fetchAssets({ warranty: key });
        },
      },
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Legend chip click bindings
   * ──────────────────────────────────────────────────────────── */
  function bindLegendChips() {
    document.querySelectorAll('#chartLegend .legend-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        const key = chip.dataset.filter;
        if (!key || key === 'all') return fetchAssets({});
        fetchAssets({ filter: key });
      });
    });

    const modelLegend = document.getElementById('modelLegend');
    if (modelLegend) {
      const models = stats.models || [];
      modelLegend.querySelectorAll('.legend-chip').forEach(function (chip, idx) {
        chip.addEventListener('click', function () {
          const key = chip.dataset.filter;
          if (!key || key === 'all') return fetchAssets({});
          const m = models[idx];
          fetchAssets({ model: m ? m.key : key });
        });
      });
    }

    document.querySelectorAll('#warrantyLegend .legend-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        const key = chip.dataset.filter;
        if (!key || key === 'all') return fetchAssets({});
        fetchAssets({ warranty: key });
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 2. KPI Tile Click Handlers
   * ──────────────────────────────────────────────────────────── */
  function bindKpiTiles() {
    const tileMappings = [
      { id: 'kpiAll',              params: {} },
      { id: 'kpiDeployed',         params: { filter: 'deployed' } },
      { id: 'kpiInstock',          params: { filter: 'instock' } },
      { id: 'kpiRepair',           params: { filter: 'repair' } },
      { id: 'kpiIssues',           params: { working: '0' } },
      { id: 'kpiWarrantyExpiring', params: { warranty: 'expiring' } },
      { id: 'kpiUnassigned',       params: { filter: 'instock', assigned: '0' } },
      { id: 'kpiSold',             params: { filter: 'sold' } },
      { id: 'kpiRetired',          params: { filter: 'retired' } },
    ];

    tileMappings.forEach(function (t) {
      const el = document.getElementById(t.id);
      if (!el) return;
      el.addEventListener('click', function () {
        // Highlight clicked tile
        if (activeTile) activeTile.classList.remove('active');
        el.classList.add('active');
        activeTile = el;
        fetchAssets(t.params);
      });
    });

    // Also support generic .kpi-tile[data-status] and .kpi-tile[data-filter]
    document.querySelectorAll('.kpi-tile[data-status], .kpi-tile[data-filter]').forEach(function (tile) {
      tile.addEventListener('click', function () {
        if (activeTile) activeTile.classList.remove('active');
        tile.classList.add('active');
        activeTile = tile;

        const status = tile.getAttribute('data-status');
        const filter = tile.getAttribute('data-filter');
        if (status) fetchAssets({ filter: status });
        else if (filter) fetchAssets({ filter });
        else fetchAssets({});
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 3. Render Inline Asset Table
   * ──────────────────────────────────────────────────────────── */
  function renderInlineAssets(assets, params, append) {
    const container = document.getElementById('inlineAssets');
    if (!container) return;

    if (!assets || !assets.length) {
      container.innerHTML = `
        <div class="card">
          <div class="empty-state">
            <div class="empty-state-icon">
              <svg viewBox="0 0 24 24"><path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 0 1 0 10h-2"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
            </div>
            <p class="empty-state-title">No assets found</p>
            <p class="empty-state-msg">No assets match the selected filter.</p>
          </div>
        </div>`;
      return;
    }

    const rows = assets.map(function (a) {
      const statusClass  = escHtml(a.status || 'unknown');
      const warrantyHtml = a.warranty_label
        ? `<span class="warranty-pill state-${escHtml(a.warranty_state || 'unknown')}">${escHtml(a.warranty_label)}</span>`
        : '<span class="text-muted">—</span>';

      return `<tr>
        <td><a href="/assets/${escHtml(a.id)}" class="text-mono font-bold" style="color:#3b82f6">${escHtml(a.serial_number)}</a></td>
        <td>${escHtml(a.model || '—')}</td>
        <td>${escHtml(a.manufacturer || '—')}</td>
        <td><span class="status-pill status-${statusClass}">${escHtml((a.status || '—').toUpperCase())}</span></td>
        <td>${escHtml(a.location || '—')}</td>
        <td>${escHtml(a.current_user || '—')}</td>
        <td>${warrantyHtml}</td>
        <td>
          <div class="table-actions">
            <a class="btn btn-primary btn-sm" href="/assets/${escHtml(a.id)}">View</a>
          </div>
        </td>
      </tr>`;
    }).join('');

    const filterDesc = Object.keys(params || {}).length
      ? Object.entries(params).map(([k, v]) => `${k.toUpperCase()}: ${v}`).join(' • ')
      : 'All assets';

    const loadMoreBtn = moreAvailable
      ? `<div class="text-center mt-12">
           <button id="loadMoreBtn" class="btn btn-light btn-sm">Load more</button>
         </div>`
      : '';

    if (append) {
      const tbody = container.querySelector('tbody');
      if (tbody) { tbody.insertAdjacentHTML('beforeend', rows); }
      if (moreAvailable) {
        const existingBtn = container.querySelector('#loadMoreBtn');
        if (!existingBtn) container.insertAdjacentHTML('beforeend', loadMoreBtn);
      } else {
        const existingBtn = container.querySelector('#loadMoreBtn');
        if (existingBtn) existingBtn.closest('div').remove();
      }
    } else {
      const assetListUrl = '/assets' + (Object.keys(params || {}).length ? '?' + new URLSearchParams(params).toString() : '');
      container.innerHTML = `
        <div class="card" style="padding:0">
          <div class="card-header" style="padding:14px 18px; border-bottom:1px solid #f1f5f9; margin-bottom:0">
            <h3 style="margin:0; font-size:13.5px; font-weight:700; color:#0f172a">Assets — ${escHtml(filterDesc)}</h3>
            <a class="btn btn-light btn-sm" href="${escHtml(assetListUrl)}">Open full list →</a>
          </div>
          <div class="table-container" style="border:none; border-radius:0 0 10px 10px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Serial</th>
                  <th>Model</th>
                  <th>Manufacturer</th>
                  <th>Status</th>
                  <th>Location</th>
                  <th>Assigned To</th>
                  <th>Warranty</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        </div>
        ${loadMoreBtn}`;
    }

    // Bind load-more button
    const btn = container.querySelector('#loadMoreBtn');
    if (btn) {
      btn.addEventListener('click', function () {
        fetchAssets(currentParams, currentPage + 1, true);
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 3a. Fetch Assets from API
   * ──────────────────────────────────────────────────────────── */
  function fetchAssets(params, page, append) {
    params = params || {};
    page   = Number(page) || 0;
    append = !!append;

    currentParams = params;
    currentPage   = page;

    const container = document.getElementById('inlineAssets');
    const loader    = document.getElementById('inlineAssetsLoading');

    if (loader) loader.classList.remove('hidden');
    if (!append && container) {
      container.innerHTML = buildLoadingSkeleton();
    }

    const q = new URLSearchParams(params);
    q.set('limit', String(PAGE_LIMIT));
    q.set('offset', String(page * PAGE_LIMIT));

    const apiFetchFn = window.apiFetch || fetch;

    apiFetchFn(`/api/assets/list?${q.toString()}`)
      .then(function (r) {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(function (data) {
        moreAvailable = !!data.has_more;
        renderInlineAssets(data.assets || [], params, append);
      })
      .catch(function (err) {
        console.error('[dashboard] asset fetch failed', err);
        if (container) {
          container.innerHTML = `
            <div class="card">
              <div class="alert alert-error" style="margin:0">
                Failed to load assets. <button class="btn-link" onclick="fetchAssets({})">Retry</button>
              </div>
            </div>`;
        }
      })
      .finally(function () {
        if (loader) loader.classList.add('hidden');
      });
  }

  // Expose for global use (e.g. onclick handlers in HTML)
  window.fetchAssets = fetchAssets;

  /* ────────────────────────────────────────────────────────────
   * Loading skeleton
   * ──────────────────────────────────────────────────────────── */
  function buildLoadingSkeleton() {
    const skeletonRow = `<tr>
      ${Array(8).fill('<td><div style="height:14px; background:linear-gradient(90deg,#f1f5f9 25%,#e2e8f0 50%,#f1f5f9 75%); background-size:200% 100%; animation:shimmer 1.5s ease-in-out infinite; border-radius:4px;"></div></td>').join('')}
    </tr>`;

    return `
      <div class="card" style="padding:0">
        <div style="padding:14px 18px; border-bottom:1px solid #f1f5f9; display:flex; align-items:center; gap:10px;">
          <div class="spinner spinner-sm"></div>
          <span style="font-size:13px; color:#64748b">Loading assets…</span>
        </div>
        <div class="table-container" style="border:none">
          <table class="data-table">
            <thead>
              <tr>
                <th>Serial</th><th>Model</th><th>Manufacturer</th>
                <th>Status</th><th>Location</th><th>Assigned To</th>
                <th>Warranty</th><th></th>
              </tr>
            </thead>
            <tbody>
              ${Array(5).fill(skeletonRow).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  }

  /* ────────────────────────────────────────────────────────────
   * 4. Search + Filter Debounce
   * ──────────────────────────────────────────────────────────── */
  function initSearchAndFilter() {
    const searchInput  = document.getElementById('dashSearch');
    const filterSelect = document.getElementById('dashFilter');

    const doSearch = debounce(function () {
      const params = {};
      const q      = searchInput  ? searchInput.value.trim()  : '';
      const filter = filterSelect ? filterSelect.value.trim() : '';

      if (q)      params.q      = q;
      if (filter && filter !== 'all') params.filter = filter;

      // De-highlight tiles on manual search
      if (activeTile) { activeTile.classList.remove('active'); activeTile = null; }
      fetchAssets(params);
    }, 300);

    if (searchInput)  searchInput.addEventListener('input', doSearch);
    if (filterSelect) filterSelect.addEventListener('change', doSearch);
  }

  /* ────────────────────────────────────────────────────────────
   * 5. Notification bell count (dashboard-specific helper)
   * ──────────────────────────────────────────────────────────── */
  function loadNotifications() {
    if (window.loadNotifications && window.loadNotifications !== loadNotifications) {
      return window.loadNotifications();
    }
    const badge = document.getElementById('notifBadge');
    if (!badge) return;
    const apiFetchFn = window.apiFetch || fetch;
    apiFetchFn('/api/notifications', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        const count = data.count || 0;
        if (count > 0) {
          badge.textContent = count > 99 ? '99+' : String(count);
          badge.style.display = '';
        } else {
          badge.style.display = 'none';
        }
      })
      .catch(function () { /* silent */ });
  }

  /* ────────────────────────────────────────────────────────────
   * In-stock model breakdown panel
   * ──────────────────────────────────────────────────────────── */
  function showInstockModelBreakdown() {
    const container = document.getElementById('inlineAssets');
    if (!container) return;
    container.innerHTML = '<div class="card"><div class="loading-spinner"><div class="spinner"></div><span class="loading-text">Loading model breakdown…</span></div></div>';

    const apiFetchFn = window.apiFetch || fetch;
    apiFetchFn('/api/assets/model-breakdown?status=instock')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        const models = data.models || [];
        if (!models.length) {
          container.innerHTML = '<div class="card"><p class="text-muted text-center" style="padding:24px">No in-stock assets found.</p></div>';
          return;
        }

        const modelRows = models.map(function (m) {
          return `<tr>
            <td style="font-weight:600">${escHtml(m.model || 'Unknown')}</td>
            <td style="text-align:center">${escHtml(m.total)}</td>
            <td style="text-align:center; cursor:pointer; color:#22c55e; font-weight:700"
                data-model="${escHtml(m.model || '')}" data-working="1" class="model-working-cell">${escHtml(m.working)}</td>
            <td style="text-align:center; cursor:pointer; color:#ef4444; font-weight:700"
                data-model="${escHtml(m.model || '')}" data-working="0" class="model-issues-cell">${escHtml(m.not_working)}</td>
          </tr>`;
        }).join('');

        container.innerHTML = `
          <div class="card" style="padding:0">
            <div class="card-header" style="padding:14px 18px; border-bottom:1px solid #f1f5f9; margin-bottom:0">
              <h3 style="margin:0; font-size:13.5px; font-weight:700">In-Stock Assets by Model</h3>
              <a class="btn btn-light btn-sm" href="/assets?filter=instock">View All</a>
            </div>
            <div class="table-container" style="border:none; border-radius:0 0 10px 10px">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th style="text-align:center">Total</th>
                    <th style="text-align:center; color:#22c55e">Working</th>
                    <th style="text-align:center; color:#ef4444">Issues</th>
                  </tr>
                </thead>
                <tbody>${modelRows}</tbody>
              </table>
            </div>
            <p style="font-size:11.5px; color:#94a3b8; margin:8px 18px 14px">
              Click Working or Issues count to see the asset list.
            </p>
          </div>`;

        container.querySelectorAll('.model-working-cell').forEach(function (cell) {
          cell.addEventListener('click', function () {
            fetchAssets({ filter: 'instock', model: cell.dataset.model, working: '1' });
          });
        });

        container.querySelectorAll('.model-issues-cell').forEach(function (cell) {
          cell.addEventListener('click', function () {
            fetchAssets({ filter: 'instock', model: cell.dataset.model, working: '0' });
          });
        });
      })
      .catch(function (err) {
        console.error('[dashboard] model breakdown failed', err);
        container.innerHTML = '<div class="card"><div class="alert alert-error" style="margin:0">Failed to load model breakdown.</div></div>';
      });
  }

  /* ────────────────────────────────────────────────────────────
   * Boot
   * ──────────────────────────────────────────────────────────── */
  function boot() {
    // Apply any data-color attributes (chip dots)
    if (window.__site && typeof window.__site.applyDataColors === 'function') {
      window.__site.applyDataColors();
    }

    initStatusChart();
    initModelChart();
    initWarrantyChart();
    bindLegendChips();
    bindKpiTiles();
    initSearchAndFilter();

    // Auto-load all assets on page load
    fetchAssets({});
    loadNotifications();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
