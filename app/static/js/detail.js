/**
 * Asset Management System v2 — Asset Detail Page JavaScript
 * Handles: Tab navigation (with hash persistence), Status modal,
 *          Assign modal, Return modal, Repair start, Issue form,
 *          PO file upload validation
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
    const csrfMeta  = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta ? csrfMeta.content : '';
    const method    = (opts && opts.method || 'GET').toUpperCase();
    const headers   = Object.assign({}, opts && opts.headers || {});
    if (['POST','PUT','PATCH','DELETE'].includes(method) && csrfToken) {
      headers['X-CSRFToken'] = csrfToken;
    }
    let body = opts && opts.body;
    if (body && typeof body === 'object' && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    return fetch(url, Object.assign({}, opts || {}, { headers, body, credentials: 'same-origin' }));
  }

  function openModal(id) {
    if (window.openModal) return window.openModal(typeof id === 'string' ? document.getElementById(id) : id);
    const m = typeof id === 'string' ? document.getElementById(id) : id;
    if (m) { m.classList.add('open'); document.body.style.overflow = 'hidden'; }
  }

  function closeModal(id) {
    if (window.closeModal) return window.closeModal(typeof id === 'string' ? document.getElementById(id) : id);
    const m = typeof id === 'string' ? document.getElementById(id) : id;
    if (m) { m.classList.remove('open'); document.body.style.overflow = ''; }
  }

  /* ────────────────────────────────────────────────────────────
   * Derive asset ID from the page
   * ──────────────────────────────────────────────────────────── */
  function getAssetId() {
    const el = document.getElementById('assetId') || document.querySelector('[data-asset-id]');
    return el ? (el.value || el.getAttribute('data-asset-id') || '') : '';
  }

  /* ────────────────────────────────────────────────────────────
   * 1. Tab Navigation with URL Hash Persistence
   * ──────────────────────────────────────────────────────────── */
  const VALID_TABS = ['overview', 'assignments', 'issues', 'repairs', 'maintenance', 'lifecycle', 'po'];

  function getHashTab() {
    const hash = window.location.hash;
    if (hash && hash.startsWith('#tab=')) {
      const key = hash.replace('#tab=', '').toLowerCase();
      if (VALID_TABS.includes(key)) return key;
    }
    return null;
  }

  function activateTab(tabKey) {
    if (!VALID_TABS.includes(tabKey)) tabKey = 'overview';

    // Update tab buttons
    document.querySelectorAll('.tab-btn[data-tab]').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === tabKey);
    });

    // Show/hide tab panes
    document.querySelectorAll('.tab-pane[data-tab]').forEach(function (pane) {
      pane.classList.toggle('active', pane.getAttribute('data-tab') === tabKey);
    });

    // Update URL hash without scrolling
    const newHash = '#tab=' + tabKey;
    if (window.location.hash !== newHash) {
      history.replaceState(null, '', newHash);
    }

    // Close any open inline forms when switching tabs
    const addIssueForm = document.getElementById('addIssueForm');
    if (addIssueForm) addIssueForm.classList.add('hidden');
  }

  function initTabs() {
    document.querySelectorAll('.tab-btn[data-tab]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        activateTab(btn.getAttribute('data-tab'));
      });
    });

    // Restore tab from hash, or activate first
    const hashTab = getHashTab();
    if (hashTab) {
      activateTab(hashTab);
    } else {
      const firstBtn = document.querySelector('.tab-btn[data-tab]');
      if (firstBtn) activateTab(firstBtn.getAttribute('data-tab'));
    }

    // Listen for hash changes (back/forward navigation)
    window.addEventListener('hashchange', function () {
      const hashTab = getHashTab();
      if (hashTab) activateTab(hashTab);
    });

    // Allow external links to deep-link into tabs (e.g. href="/assets/5#tab=issues")
    document.querySelectorAll('a[href*="#tab="]').forEach(function (link) {
      const href = link.getAttribute('href');
      const isSamePage = href.startsWith('#') ||
        href.includes(window.location.pathname + '#');
      if (isSamePage) {
        link.addEventListener('click', function (e) {
          const tabKey = href.split('#tab=')[1] || '';
          if (VALID_TABS.includes(tabKey)) {
            e.preventDefault();
            activateTab(tabKey);
          }
        });
      }
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 2. Status Update Modal
   * ──────────────────────────────────────────────────────────── */
  function initStatusModal() {
    const modal      = document.getElementById('statusModal');
    const statusSel  = document.getElementById('smStatus');
    const soldWrap   = document.getElementById('smSoldWrap');
    const submitBtn  = document.getElementById('smSubmitBtn');
    const form       = document.getElementById('statusForm');
    if (!modal) return;

    const origBtnText  = submitBtn ? submitBtn.textContent.trim() : 'Update Status';
    const origBtnClass = submitBtn ? submitBtn.className : '';

    // Sold-to toggle
    function toggleSoldWrap() {
      if (!statusSel) return;
      const isSold = statusSel.value === 'sold';
      if (soldWrap) soldWrap.classList.toggle('hidden', !isSold);
      if (submitBtn) {
        if (isSold) {
          submitBtn.textContent = 'Confirm Sale';
          submitBtn.className   = origBtnClass.replace('btn-primary', '').replace('btn-blue', '') + ' btn-danger';
        } else {
          submitBtn.textContent = origBtnText;
          submitBtn.className   = origBtnClass;
        }
      }
    }

    if (statusSel) {
      statusSel.addEventListener('change', toggleSoldWrap);
      toggleSoldWrap(); // run on load in case modal has pre-filled value
    }

    // AJAX form submit (optional — falls back to regular form submit if form has action)
    if (form && form.getAttribute('data-ajax') === 'true') {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving…'; }

        const formData = new FormData(form);
        const payload  = Object.fromEntries(formData.entries());

        apiFetch(form.action || `/assets/${getAssetId()}/status`, { method: 'POST', body: payload })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.error) {
              showFlash(data.error, 'error');
            } else {
              if (data.redirect) { window.location.href = data.redirect; return; }
              showFlash(data.message || 'Status updated.', 'success');
              closeModal(modal);
              // Refresh status pill on page without full reload if possible
              const pill = document.getElementById('assetStatusPill');
              if (pill && data.status) {
                const s = data.status.toLowerCase();
                pill.className = `status-pill status-${s}`;
                pill.textContent = data.status.toUpperCase();
              } else {
                window.location.reload();
              }
            }
          })
          .catch(function () {
            showFlash('Failed to update status. Please try again.', 'error');
          })
          .finally(function () {
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = statusSel && statusSel.value === 'sold' ? 'Confirm Sale' : origBtnText; }
          });
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 3. Assign Modal (AJAX POST)
   * ──────────────────────────────────────────────────────────── */
  function initAssignModal() {
    const modal = document.getElementById('assignModal');
    const form  = document.getElementById('assignForm');
    if (!modal || !form) return;

    // Pre-fill asset_id hidden field if not already present
    const assetId = getAssetId();
    let hiddenAssetId = form.querySelector('input[name="asset_id"]');
    if (!hiddenAssetId && assetId) {
      hiddenAssetId = document.createElement('input');
      hiddenAssetId.type  = 'hidden';
      hiddenAssetId.name  = 'asset_id';
      hiddenAssetId.value = assetId;
      form.appendChild(hiddenAssetId);
    }

    const submitBtn = form.querySelector('[type="submit"]');

    form.addEventListener('submit', function (e) {
      // Only intercept if data-ajax="true"
      if (form.getAttribute('data-ajax') !== 'true') return;
      e.preventDefault();

      if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Assigning…'; }

      const formData = new FormData(form);
      const payload  = Object.fromEntries(formData.entries());

      apiFetch('/assignments/assign', { method: 'POST', body: payload })
        .then(function (r) {
          if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
          return r.json();
        })
        .then(function (data) {
          if (data.error) { showFlash(data.error, 'error'); return; }
          if (data.redirect) { window.location.href = data.redirect; return; }
          showFlash(data.message || 'Asset assigned successfully.', 'success');
          closeModal(modal);
          setTimeout(function () { window.location.reload(); }, 600);
        })
        .catch(function (err) {
          showFlash(err.message || 'Assignment failed. Please try again.', 'error');
        })
        .finally(function () {
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Assign'; }
        });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 4. Return Asset Modal (AJAX POST)
   * ──────────────────────────────────────────────────────────── */
  function initReturnModal() {
    const modal = document.getElementById('returnModal');
    const form  = document.getElementById('returnForm');
    if (!modal || !form) return;

    const submitBtn = form.querySelector('[type="submit"]');

    // When "Return Asset" button is clicked, populate assignment ID
    document.addEventListener('click', function (e) {
      const trigger = e.target.closest('[data-action="openReturnModal"]');
      if (!trigger) return;
      const assignId = trigger.getAttribute('data-assign-id');
      if (assignId) {
        const hiddenInput = form.querySelector('input[name="assignment_id"]');
        if (hiddenInput) hiddenInput.value = assignId;
      }
    });

    form.addEventListener('submit', function (e) {
      if (form.getAttribute('data-ajax') !== 'true') return;
      e.preventDefault();

      if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Returning…'; }

      const formData   = new FormData(form);
      const assignId   = formData.get('assignment_id');
      const notes      = formData.get('return_notes') || '';
      const condition  = formData.get('return_condition') || '';

      if (!assignId) {
        showFlash('No assignment found to return.', 'error');
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Confirm Return'; }
        return;
      }

      apiFetch(`/assignments/${assignId}/return`, {
        method: 'POST',
        body: { assignment_id: assignId, notes, condition },
      })
        .then(function (r) {
          if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
          return r.json();
        })
        .then(function (data) {
          if (data.error) { showFlash(data.error, 'error'); return; }
          if (data.redirect) { window.location.href = data.redirect; return; }
          showFlash(data.message || 'Asset returned successfully.', 'success');
          closeModal(modal);
          setTimeout(function () { window.location.reload(); }, 600);
        })
        .catch(function (err) {
          showFlash(err.message || 'Return failed. Please try again.', 'error');
        })
        .finally(function () {
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Confirm Return'; }
        });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 5. Start Repair (AJAX POST from issue row)
   * ──────────────────────────────────────────────────────────── */
  function initStartRepair() {
    document.addEventListener('click', function (e) {
      const btn = e.target.closest('.start-repair-btn[data-issue-id]');
      if (!btn) return;
      e.preventDefault();

      const issueId = btn.getAttribute('data-issue-id');
      if (!issueId) return;

      if (!confirm('Mark this issue as a repair and open a repair record?')) return;

      btn.disabled    = true;
      btn.textContent = 'Starting…';

      apiFetch(`/issues/${issueId}/start_repair`, { method: 'POST', body: {} })
        .then(function (r) {
          if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
          return r.json();
        })
        .then(function (data) {
          if (data.error) { showFlash(data.error, 'error'); return; }
          if (data.redirect) { window.location.href = data.redirect; return; }
          showFlash(data.message || 'Repair started.', 'success');
          setTimeout(function () { window.location.reload(); }, 600);
        })
        .catch(function (err) {
          showFlash(err.message || 'Failed to start repair.', 'error');
          btn.disabled    = false;
          btn.textContent = 'Start Repair';
        });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 6. Add Issue Inline Form Toggle
   * ──────────────────────────────────────────────────────────── */
  function initAddIssueForm() {
    const form    = document.getElementById('addIssueForm');
    const showBtn = document.getElementById('showAddIssueBtn');
    const hideBtn = document.getElementById('hideAddIssueBtn');
    if (!form) return;

    if (showBtn) {
      showBtn.addEventListener('click', function (e) {
        e.preventDefault();
        form.classList.remove('hidden');
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const firstInput = form.querySelector('input, select, textarea');
        if (firstInput) setTimeout(() => firstInput.focus(), 200);
      });
    }

    if (hideBtn) {
      hideBtn.addEventListener('click', function (e) {
        e.preventDefault();
        form.classList.add('hidden');
        form.reset();
      });
    }

    // AJAX form submit (optional — only if data-ajax="true")
    if (form.getAttribute('data-ajax') === 'true') {
      const submitBtn = form.querySelector('[type="submit"]');
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving…'; }

        const formData = new FormData(form);
        const payload  = Object.fromEntries(formData.entries());

        apiFetch(`/assets/${getAssetId()}/issues`, { method: 'POST', body: payload })
          .then(function (r) {
            if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
            return r.json();
          })
          .then(function (data) {
            if (data.error) { showFlash(data.error, 'error'); return; }
            if (data.redirect) { window.location.href = data.redirect; return; }
            showFlash(data.message || 'Issue reported.', 'success');
            form.classList.add('hidden');
            form.reset();
            setTimeout(function () { window.location.reload(); }, 600);
          })
          .catch(function (err) {
            showFlash(err.message || 'Failed to save issue.', 'error');
          })
          .finally(function () {
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Report Issue'; }
          });
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 7. PO File Upload Validation
   * ──────────────────────────────────────────────────────────── */
  function initPoFileUpload() {
    const poFileInput = document.getElementById('poFile');
    const fileNameEl  = document.getElementById('poFileName');
    const poForm      = document.getElementById('poUploadForm');

    if (!poFileInput) return;

    poFileInput.addEventListener('change', function () {
      const file = poFileInput.files && poFileInput.files[0];
      if (!file) {
        if (fileNameEl) fileNameEl.textContent = '';
        return;
      }

      const ext = file.name.split('.').pop().toLowerCase();
      if (ext !== 'pdf') {
        showFlash('Only PDF files are accepted for purchase orders.', 'error');
        poFileInput.value = '';
        if (fileNameEl) fileNameEl.textContent = '';
        return;
      }

      const maxSizeMB = 10;
      if (file.size > maxSizeMB * 1024 * 1024) {
        showFlash(`File is too large. Maximum size is ${maxSizeMB}MB.`, 'error');
        poFileInput.value = '';
        if (fileNameEl) fileNameEl.textContent = '';
        return;
      }

      if (fileNameEl) {
        fileNameEl.textContent = file.name;
        fileNameEl.style.display = '';
      }
    });

    // Also validate before form submit
    if (poForm) {
      poForm.addEventListener('submit', function (e) {
        const file = poFileInput.files && poFileInput.files[0];
        if (file) {
          const ext = file.name.split('.').pop().toLowerCase();
          if (ext !== 'pdf') {
            e.preventDefault();
            showFlash('Only PDF files are accepted for purchase orders.', 'error');
            return;
          }
        }
      });
    }

    // Drag-drop on upload zone
    const uploadZone = document.getElementById('poUploadZone');
    if (uploadZone) {
      ['dragenter', 'dragover'].forEach(function (evt) {
        uploadZone.addEventListener(evt, function (e) {
          e.preventDefault();
          uploadZone.classList.add('drag-over');
        });
      });

      ['dragleave', 'drop'].forEach(function (evt) {
        uploadZone.addEventListener(evt, function (e) {
          e.preventDefault();
          uploadZone.classList.remove('drag-over');
          if (evt === 'drop') {
            const files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length) {
              poFileInput.files = files;
              poFileInput.dispatchEvent(new Event('change'));
            }
          }
        });
      });

      // Click zone to open file picker
      uploadZone.addEventListener('click', function (e) {
        if (e.target !== poFileInput) poFileInput.click();
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 8. Repair inline form toggle (per-repair-row)
   * ──────────────────────────────────────────────────────────── */
  function initRepairForms() {
    document.querySelectorAll('[data-toggle-repair]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        const repairId = btn.getAttribute('data-toggle-repair');
        const formRow  = document.getElementById(`repair-form-row-${repairId}`);
        if (formRow) {
          formRow.classList.toggle('hidden');
          if (!formRow.classList.contains('hidden')) {
            formRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 9. Close Repair / Maintenance record (AJAX)
   * ──────────────────────────────────────────────────────────── */
  function initRepairSubmit() {
    document.querySelectorAll('.repair-close-form[data-ajax="true"]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        const submitBtn = form.querySelector('[type="submit"]');
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Saving…'; }

        const formData  = new FormData(form);
        const payload   = Object.fromEntries(formData.entries());
        const repairId  = form.getAttribute('data-repair-id');

        apiFetch(`/repairs/${repairId}/close`, { method: 'POST', body: payload })
          .then(function (r) {
            if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
            return r.json();
          })
          .then(function (data) {
            if (data.error) { showFlash(data.error, 'error'); return; }
            if (data.redirect) { window.location.href = data.redirect; return; }
            showFlash(data.message || 'Repair closed.', 'success');
            setTimeout(function () { window.location.reload(); }, 600);
          })
          .catch(function (err) {
            showFlash(err.message || 'Failed to close repair.', 'error');
          })
          .finally(function () {
            if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Close Repair'; }
          });
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 10. Edit Assignment modal — populate fields
   * ──────────────────────────────────────────────────────────── */
  function initEditAssignmentModal() {
    const modal = document.getElementById('editAssignmentModal');
    if (!modal) return;

    // When the edit button is clicked, fill form fields from data attributes
    document.addEventListener('click', function (e) {
      const trigger = e.target.closest('[data-action="openEditAssignment"]');
      if (!trigger) return;

      const assignId  = trigger.getAttribute('data-assign-id');
      const userName  = trigger.getAttribute('data-user-name')  || '';
      const userEmail = trigger.getAttribute('data-user-email') || '';
      const dept      = trigger.getAttribute('data-dept')       || '';
      const notes     = trigger.getAttribute('data-notes')      || '';

      const form = modal.querySelector('form');
      if (!form) return;

      const setVal = (name, val) => {
        const el = form.querySelector(`[name="${name}"]`);
        if (el) el.value = val;
      };

      setVal('assignment_id', assignId || '');
      setVal('user_name',     userName);
      setVal('user_email',    userEmail);
      setVal('department',    dept);
      setVal('notes',         notes);
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 11. Maintenance task complete toggle
   * ──────────────────────────────────────────────────────────── */
  function initMaintenanceToggles() {
    document.querySelectorAll('.maintenance-complete-btn[data-task-id]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        const taskId = btn.getAttribute('data-task-id');
        if (!taskId) return;

        btn.disabled = true;

        apiFetch(`/maintenance/${taskId}/complete`, { method: 'POST', body: {} })
          .then(function (r) {
            if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
            return r.json();
          })
          .then(function (data) {
            if (data.error) { showFlash(data.error, 'error'); return; }
            showFlash(data.message || 'Task marked complete.', 'success');
            setTimeout(function () { window.location.reload(); }, 500);
          })
          .catch(function (err) {
            showFlash(err.message || 'Failed to update task.', 'error');
            btn.disabled = false;
          });
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 12. Health bar animation on load
   * ──────────────────────────────────────────────────────────── */
  function animateHealthBar() {
    document.querySelectorAll('[data-health]').forEach(function (el) {
      try {
        const val = Number(el.getAttribute('data-health')) || 0;
        // Set to 0 first, then animate to target
        el.style.width = '0%';
        setTimeout(function () {
          el.style.width = Math.min(100, Math.max(0, val)) + '%';
        }, 120);
      } catch (e) { /* ignore */ }
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 13. Close issue / mark resolved
   * ──────────────────────────────────────────────────────────── */
  function initIssueActions() {
    document.addEventListener('click', function (e) {
      const closeBtn = e.target.closest('.close-issue-btn[data-issue-id]');
      if (!closeBtn) return;
      e.preventDefault();

      const issueId = closeBtn.getAttribute('data-issue-id');
      if (!confirm('Mark this issue as resolved/closed?')) return;

      closeBtn.disabled    = true;
      closeBtn.textContent = 'Closing…';

      apiFetch(`/issues/${issueId}/close`, { method: 'POST', body: {} })
        .then(function (r) {
          if (!r.ok) return r.json().then(d => { throw new Error(d.error || `HTTP ${r.status}`); });
          return r.json();
        })
        .then(function (data) {
          if (data.error) { showFlash(data.error, 'error'); return; }
          showFlash(data.message || 'Issue closed.', 'success');
          setTimeout(function () { window.location.reload(); }, 600);
        })
        .catch(function (err) {
          showFlash(err.message || 'Failed to close issue.', 'error');
          closeBtn.disabled    = false;
          closeBtn.textContent = 'Close';
        });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Boot
   * ──────────────────────────────────────────────────────────── */
  function boot() {
    initTabs();
    initStatusModal();
    initAssignModal();
    initReturnModal();
    initStartRepair();
    initAddIssueForm();
    initPoFileUpload();
    initRepairForms();
    initRepairSubmit();
    initEditAssignmentModal();
    initMaintenanceToggles();
    animateHealthBar();
    initIssueActions();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
