/**
 * Asset Management System v2 — Assets List Page JavaScript
 * Handles: Bulk select, bulk action submit, status filter tabs, CSV export
 */
(function () {
  'use strict';

  /* ────────────────────────────────────────────────────────────
   * Helpers
   * ──────────────────────────────────────────────────────────── */
  function escHtml(str) {
    if (window.escHtml) return window.escHtml(str);
    const d = document.createElement('div');
    d.textContent = String(str == null ? '' : str);
    return d.innerHTML;
  }

  function showFlash(msg, type) {
    if (window.showFlash) return window.showFlash(msg, type);
    alert(msg);
  }

  function apiFetch(url, opts) {
    if (window.apiFetch) return window.apiFetch(url, opts);
    return fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}));
  }

  /* ────────────────────────────────────────────────────────────
   * 1. Bulk Select System
   * ──────────────────────────────────────────────────────────── */
  function getCheckboxes() {
    return Array.from(document.querySelectorAll('.asset-cb'));
  }

  function getChecked() {
    return getCheckboxes().filter(cb => cb.checked);
  }

  function updateBulkUI() {
    const all     = getCheckboxes();
    const checked = getChecked();
    const count   = checked.length;

    // Select-all state
    const selectAll = document.getElementById('selectAll');
    if (selectAll) {
      selectAll.checked       = count > 0 && count === all.length;
      selectAll.indeterminate = count > 0 && count < all.length;
    }

    // Count label
    const countLabel = document.getElementById('bulkCount');
    if (countLabel) countLabel.textContent = `${count} selected`;

    // Toolbar visibility
    const toolbar = document.getElementById('bulkToolbar');
    if (toolbar) toolbar.classList.toggle('hidden', count === 0);

    // Row highlight
    all.forEach(function (cb) {
      const row = cb.closest('tr');
      if (row) row.classList.toggle('row-selected', cb.checked);
    });
  }

  function initBulkSelect() {
    const selectAll = document.getElementById('selectAll');

    if (selectAll) {
      selectAll.addEventListener('change', function () {
        getCheckboxes().forEach(cb => { cb.checked = selectAll.checked; });
        updateBulkUI();
      });
    }

    // Individual checkbox changes (delegation)
    const tableBody = document.querySelector('.assets-table-body, .data-table tbody');
    if (tableBody) {
      tableBody.addEventListener('change', function (e) {
        if (e.target.classList.contains('asset-cb')) {
          updateBulkUI();
        }
      });
    } else {
      // Fallback: direct listener on each
      document.addEventListener('change', function (e) {
        if (e.target.classList.contains('asset-cb')) {
          updateBulkUI();
        }
      });
    }

    // Clear selection
    const clearBtn = document.getElementById('bulkClearBtn');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        getCheckboxes().forEach(cb => { cb.checked = false; });
        if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
        updateBulkUI();
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 2. Bulk Action Submit
   * ──────────────────────────────────────────────────────────── */
  function initBulkSubmit() {
    const submitBtn    = document.getElementById('bulkActionSubmit');
    const actionSelect = document.getElementById('bulkActionSelect');
    if (!submitBtn) return;

    submitBtn.addEventListener('click', function () {
      const checked = getChecked();
      if (!checked.length) {
        showFlash('Please select at least one asset.', 'warning');
        return;
      }

      const action = actionSelect ? actionSelect.value : '';
      if (!action) {
        showFlash('Please choose an action to apply.', 'warning');
        return;
      }

      const assetIds = checked.map(cb => cb.value);

      // Some actions need confirmation
      const dangerActions = ['delete', 'retire'];
      if (dangerActions.includes(action)) {
        const ok = confirm(`Apply "${action}" to ${assetIds.length} asset(s)? This cannot be undone.`);
        if (!ok) return;
      }

      const payload = { action, asset_ids: assetIds };

      // Include new status for status-change bulk action
      const statusSelect = document.getElementById('bulkStatusSelect');
      if (statusSelect && statusSelect.value) {
        payload.new_status = statusSelect.value;
      }

      submitBtn.disabled    = true;
      submitBtn.textContent = 'Processing…';

      apiFetch('/assets/bulk-action', { method: 'POST', body: payload })
        .then(function (r) {
          if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
          return r.json();
        })
        .then(function (data) {
          if (data.redirect) {
            window.location.href = data.redirect;
            return;
          }
          showFlash(data.message || `Action "${action}" applied to ${assetIds.length} asset(s).`, 'success');
          setTimeout(function () { window.location.reload(); }, 900);
        })
        .catch(function (err) {
          console.error('[assets] bulk action error', err);
          showFlash(err.message || 'Bulk action failed. Please try again.', 'error');
        })
        .finally(function () {
          submitBtn.disabled    = false;
          submitBtn.textContent = 'Apply';
        });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 3. Status Filter Tabs / Chips
   * ──────────────────────────────────────────────────────────── */
  function initStatusFilter() {
    // Status tab chips: .status-filter-chip[data-status]
    document.querySelectorAll('.status-filter-chip[data-status]').forEach(function (chip) {
      chip.addEventListener('click', function (e) {
        e.preventDefault();
        const status = chip.getAttribute('data-status');
        const url    = new URL(window.location.href);

        if (!status || status === 'all') {
          url.searchParams.delete('status');
        } else {
          url.searchParams.set('status', status);
        }

        // Reset to page 1 when changing filter
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    });

    // Mark active chip based on current URL
    const currentStatus = new URLSearchParams(window.location.search).get('status') || 'all';
    document.querySelectorAll('.status-filter-chip[data-status]').forEach(function (chip) {
      const s = chip.getAttribute('data-status') || 'all';
      chip.classList.toggle('active', s === currentStatus);
    });

    // Status dropdown filter (if present — filter-bar variant)
    const filterStatusSelect = document.getElementById('filterStatus');
    if (filterStatusSelect) {
      // Set current value from URL
      const urlStatus = new URLSearchParams(window.location.search).get('status') || '';
      if (urlStatus) filterStatusSelect.value = urlStatus;

      filterStatusSelect.addEventListener('change', function () {
        const url = new URL(window.location.href);
        const val = filterStatusSelect.value;
        if (val && val !== 'all') {
          url.searchParams.set('status', val);
        } else {
          url.searchParams.delete('status');
        }
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    }

    // Category filter
    const filterCategory = document.getElementById('filterCategory');
    if (filterCategory) {
      const urlCat = new URLSearchParams(window.location.search).get('category') || '';
      if (urlCat) filterCategory.value = urlCat;

      filterCategory.addEventListener('change', function () {
        const url = new URL(window.location.href);
        const val = filterCategory.value;
        if (val && val !== 'all') {
          url.searchParams.set('category', val);
        } else {
          url.searchParams.delete('category');
        }
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    }

    // Location filter
    const filterLocation = document.getElementById('filterLocation');
    if (filterLocation) {
      const urlLoc = new URLSearchParams(window.location.search).get('location') || '';
      if (urlLoc) filterLocation.value = urlLoc;

      filterLocation.addEventListener('change', function () {
        const url = new URL(window.location.href);
        const val = filterLocation.value;
        if (val && val !== 'all') {
          url.searchParams.set('location', val);
        } else {
          url.searchParams.delete('location');
        }
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * Search input (with debounce + form submit)
   * ──────────────────────────────────────────────────────────── */
  function initSearchInput() {
    const searchInput = document.getElementById('assetsSearch');
    const searchForm  = document.getElementById('assetsSearchForm');
    if (!searchInput) return;

    // Restore search value from URL
    const urlQ = new URLSearchParams(window.location.search).get('q') || '';
    if (urlQ) searchInput.value = urlQ;

    let debounceTimer;
    searchInput.addEventListener('input', function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        if (searchForm) {
          searchForm.submit();
        } else {
          const url = new URL(window.location.href);
          const q   = searchInput.value.trim();
          if (q) {
            url.searchParams.set('q', q);
          } else {
            url.searchParams.delete('q');
          }
          url.searchParams.delete('page');
          window.location.href = url.toString();
        }
      }, 400);
    });

    // Clear search button
    const clearSearch = document.getElementById('clearSearch');
    if (clearSearch) {
      clearSearch.addEventListener('click', function () {
        searchInput.value = '';
        const url = new URL(window.location.href);
        url.searchParams.delete('q');
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 4. CSV / XLSX Export
   * ──────────────────────────────────────────────────────────── */
  function initExportButtons() {
    const csvBtn  = document.getElementById('exportCsvBtn');
    const xlsxBtn = document.getElementById('exportXlsxBtn');

    function buildExportUrl(format) {
      const url        = new URL('/reports/download', window.location.origin);
      const current    = new URLSearchParams(window.location.search);
      const status     = current.get('status') || '';
      const q          = current.get('q')      || '';
      const category   = current.get('category') || '';

      url.searchParams.set('format', format);
      if (status)   url.searchParams.set('status',   status);
      if (q)        url.searchParams.set('q',        q);
      if (category) url.searchParams.set('category', category);

      return url.toString();
    }

    if (csvBtn) {
      csvBtn.addEventListener('click', function () {
        window.location.href = buildExportUrl('csv');
      });
    }

    if (xlsxBtn) {
      xlsxBtn.addEventListener('click', function () {
        window.location.href = buildExportUrl('xlsx');
      });
    }

    // Generic export buttons with data-format attribute
    document.querySelectorAll('[data-export]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        const format = btn.getAttribute('data-export') || 'csv';
        window.location.href = buildExportUrl(format);
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Row click — navigate to asset detail
   * ──────────────────────────────────────────────────────────── */
  function initRowNavigation() {
    document.querySelectorAll('.assets-table-body tr[data-asset-id], .data-table tbody tr[data-asset-id]').forEach(function (row) {
      row.style.cursor = 'pointer';
      row.addEventListener('click', function (e) {
        // Don't navigate if user clicked a button, link, or checkbox
        if (e.target.closest('a, button, input, .table-actions, .popover-wrapper')) return;
        const id = row.getAttribute('data-asset-id');
        if (id) window.location.href = `/assets/${id}`;
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Column sort (links with ?sort=X&dir=asc/desc)
   * ──────────────────────────────────────────────────────────── */
  function initSortHeaders() {
    document.querySelectorAll('.data-table th.sortable[data-sort]').forEach(function (th) {
      th.style.cursor = 'pointer';
      const current = new URLSearchParams(window.location.search);
      const curSort  = current.get('sort') || '';
      const curDir   = current.get('dir')  || 'asc';
      const col      = th.getAttribute('data-sort');

      // Show active sort indicator
      if (curSort === col) {
        th.classList.add('sort-active');
        th.setAttribute('aria-sort', curDir === 'asc' ? 'ascending' : 'descending');
        th.insertAdjacentHTML('beforeend', curDir === 'asc' ? ' ↑' : ' ↓');
      }

      th.addEventListener('click', function () {
        const url    = new URL(window.location.href);
        const newDir = (curSort === col && curDir === 'asc') ? 'desc' : 'asc';
        url.searchParams.set('sort', col);
        url.searchParams.set('dir',  newDir);
        url.searchParams.delete('page');
        window.location.href = url.toString();
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Boot
   * ──────────────────────────────────────────────────────────── */
  function boot() {
    initBulkSelect();
    initBulkSubmit();
    initStatusFilter();
    initSearchInput();
    initExportButtons();
    initRowNavigation();
    initSortHeaders();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
