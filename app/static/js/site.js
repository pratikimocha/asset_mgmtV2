/**
 * Asset Management System v2 — Global JavaScript
 * Handles: CSRF, Flash messages, Modals, Tabs, Notifications,
 *          Bulk Actions, Data attributes, Role changes, Sold-To toggle
 */
(function () {
  'use strict';

  /* ────────────────────────────────────────────────────────────
   * 1. CSRF Token — attach to all mutating fetch requests
   * ──────────────────────────────────────────────────────────── */
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.content : '';

  const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

  /**
   * Fetch wrapper that automatically attaches the CSRF header and
   * handles JSON encoding for object bodies.
   *
   * @param {string} url
   * @param {RequestInit} [options]
   * @returns {Promise<Response>}
   */
  function apiFetch(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = Object.assign({}, options.headers || {});

    if (MUTATING_METHODS.has(method) && csrfToken) {
      headers['X-CSRFToken'] = csrfToken;
    }

    // Auto-encode plain objects as JSON
    let body = options.body;
    if (body && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof URLSearchParams)) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }

    return fetch(url, Object.assign({}, options, { headers, body, credentials: 'same-origin' }));
  }

  // Expose globally so other scripts can use it
  window.apiFetch = apiFetch;

  /* ────────────────────────────────────────────────────────────
   * 2. Flash Messages — auto-dismiss after 4 seconds
   * ──────────────────────────────────────────────────────────── */
  function initFlashMessages() {
    const container = document.querySelector('.flash-messages');
    if (!container) return;

    // Dismiss handler
    function dismissFlash(el) {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-6px)';
      el.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
      setTimeout(() => {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 320);
    }

    container.querySelectorAll('.flash').forEach(function (flash) {
      // Auto dismiss after 4 s
      const timer = setTimeout(() => dismissFlash(flash), 4000);

      // Close button
      const closeBtn = flash.querySelector('.flash-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', function () {
          clearTimeout(timer);
          dismissFlash(flash);
        });
      }
    });
  }

  /**
   * Programmatically show a flash message in .flash-messages container.
   * Creates the container if it doesn't exist.
   */
  function showFlash(message, type = 'info') {
    let container = document.querySelector('.flash-messages');
    if (!container) {
      container = document.createElement('div');
      container.className = 'flash-messages';
      const mainContent = document.querySelector('.container') || document.body;
      mainContent.insertAdjacentElement('afterbegin', container);
    }

    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    const div = document.createElement('div');
    div.className = `flash flash-${type}`;
    div.innerHTML = `<span class="flash-icon">${icons[type] || 'ℹ'}</span>
                     <span>${escHtml(message)}</span>
                     <button class="flash-close" aria-label="Dismiss">×</button>`;
    container.appendChild(div);

    const timer = setTimeout(() => {
      div.style.opacity = '0';
      div.style.transition = 'opacity 0.3s ease';
      setTimeout(() => { if (div.parentNode) div.parentNode.removeChild(div); }, 320);
    }, 4000);

    div.querySelector('.flash-close').addEventListener('click', () => {
      clearTimeout(timer);
      div.style.opacity = '0';
      div.style.transition = 'opacity 0.3s ease';
      setTimeout(() => { if (div.parentNode) div.parentNode.removeChild(div); }, 320);
    });
  }

  window.showFlash = showFlash;

  /* ────────────────────────────────────────────────────────────
   * 3. Modal System
   * ──────────────────────────────────────────────────────────── */
  const openModals = [];

  function openModal(modal) {
    if (!modal || modal.classList.contains('open')) return;
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    openModals.push(modal);
    // Move focus to first focusable element
    const focusable = modal.querySelector('input, select, textarea, button, [tabindex]');
    if (focusable) setTimeout(() => focusable.focus(), 60);
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.classList.remove('open');
    const idx = openModals.indexOf(modal);
    if (idx > -1) openModals.splice(idx, 1);
    if (openModals.length === 0) document.body.style.overflow = '';
  }

  function closeTopModal() {
    if (openModals.length > 0) closeModal(openModals[openModals.length - 1]);
  }

  function initModals() {
    // Open via [data-modal="modalId"] on any clickable element
    document.addEventListener('click', function (e) {
      // Close via [data-modal-close] or .modal-close button — checked FIRST
      const closeBtn = e.target.closest('[data-modal-close], .modal-close');
      if (closeBtn) {
        const overlay = closeBtn.closest('.modal-overlay');
        if (overlay) closeModal(overlay);
        return;
      }

      // Open trigger: any element with [data-modal] that is NOT a close button
      const trigger = e.target.closest('[data-modal]');
      if (trigger && !trigger.closest('[data-modal-close], .modal-close')) {
        const modalId = trigger.getAttribute('data-modal');
        const modal = document.getElementById(modalId);
        if (modal) {
          e.preventDefault();
          openModal(modal);
        }
      }

      // Click on overlay background (the .modal-overlay element itself)
      if (e.target.classList.contains('modal-overlay')) {
        closeModal(e.target);
      }
    });

    // Escape key closes top modal
    document.addEventListener('keydown', function (e) {
      if ((e.key === 'Escape' || e.key === 'Esc') && openModals.length > 0) {
        closeTopModal();
      }
    });
  }

  // Expose for imperative use — accept either a DOM element or a string ID
  window.openModal = function(modal) {
    if (typeof modal === 'string') modal = document.getElementById(modal);
    openModal(modal);
  };
  window.closeModal = function(modal) {
    if (typeof modal === 'string') modal = document.getElementById(modal);
    closeModal(modal);
  };

  /* ────────────────────────────────────────────────────────────
   * 4. Tab System
   * ──────────────────────────────────────────────────────────── */
  function initTabs() {
    // New-style .tab-btn[data-tab="X"] → .tab-pane[data-tab="X"]
    document.querySelectorAll('.tab-nav').forEach(function (nav) {
      const buttons = nav.querySelectorAll('.tab-btn[data-tab]');
      if (!buttons.length) return;

      function activateTab(tabKey, btn) {
        // Deactivate all buttons in this nav
        buttons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Find the parent scope (card or page container)
        const scope = nav.closest('[data-tab-scope]') || nav.parentElement;
        scope.querySelectorAll('.tab-pane').forEach(pane => {
          pane.classList.toggle('active', pane.getAttribute('data-tab') === tabKey);
        });
      }

      buttons.forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          const tabKey = btn.getAttribute('data-tab');
          activateTab(tabKey, btn);
        });
      });

      // Read URL hash on load to activate correct tab
      const hash = window.location.hash;
      if (hash && hash.startsWith('#tab=')) {
        const tabKey = hash.replace('#tab=', '');
        const matchBtn = nav.querySelector(`.tab-btn[data-tab="${tabKey}"]`);
        if (matchBtn) {
          activateTab(tabKey, matchBtn);
          return;
        }
      }

      // Default: activate first button
      const firstBtn = nav.querySelector('.tab-btn[data-tab]');
      if (firstBtn) {
        activateTab(firstBtn.getAttribute('data-tab'), firstBtn);
      }
    });

    // Legacy .tab-link → .tab-section (v1 compat)
    document.querySelectorAll('.tab-link').forEach(function (link) {
      link.addEventListener('click', function (e) {
        e.preventDefault();
        const target = this.getAttribute('href').replace('#', '');
        document.querySelectorAll('.tab-section').forEach(sec => sec.classList.add('hidden'));
        const el = document.getElementById(target);
        if (el) el.classList.remove('hidden');
        document.querySelectorAll('.tab-link').forEach(l => l.classList.remove('active'));
        this.classList.add('active');
        const addIssue = document.getElementById('addIssueForm');
        if (addIssue) addIssue.classList.add('hidden');
      });
    });

    // Legacy: show first tab section on load
    const firstTabLink = document.querySelector('.tab-link');
    if (firstTabLink) {
      const href = firstTabLink.getAttribute('href');
      if (href) {
        const id = href.replace('#', '');
        const el = document.getElementById(id);
        if (el) {
          el.classList.remove('hidden');
          firstTabLink.classList.add('active');
        }
      }
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 5. Notification Bell
   * ──────────────────────────────────────────────────────────── */
  function initNotifications() {
    const bell     = document.getElementById('notifBell');
    const badge    = document.getElementById('notifBadge');
    const dropdown = document.getElementById('notifDropdown');
    const list     = document.getElementById('notifList');
    if (!bell || !badge || !dropdown || !list) return;

    const icons = { warranty: '⚠️', maintenance: '🔧', issue: '🔴', repair: '🔴', info: 'ℹ️' };

    function renderNotifications(data) {
      const count = data.count || 0;
      if (count > 0) {
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }

      if (!data.items || !data.items.length) {
        list.innerHTML = '<div class="notif-empty">No alerts at this time.</div>';
        return;
      }

      list.innerHTML = data.items.slice(0, 10).map(function (item) {
        return `<a href="${escHtml(item.link || '#')}" class="notif-item notif-${escHtml(item.type || 'info')}">
                  <span class="notif-icon">${icons[item.type] || icons.info}</span>
                  <span class="notif-msg">${escHtml(item.message || '')}</span>
                </a>`;
      }).join('');
    }

    function fetchNotifications() {
      apiFetch('/api/notifications')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) { if (data) renderNotifications(data); })
        .catch(function () { /* silent */ });
    }

    // Toggle dropdown
    bell.addEventListener('click', function (e) {
      e.stopPropagation();
      const isOpen = dropdown.classList.contains('open');
      dropdown.classList.toggle('open', !isOpen);
    });

    document.addEventListener('click', function (e) {
      if (!bell.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.classList.remove('open');
      }
    });

    fetchNotifications();
    setInterval(fetchNotifications, 5 * 60 * 1000); // every 5 minutes
  }

  // Expose for dashboard use
  window.loadNotifications = function () {
    const bell = document.getElementById('notifBell');
    if (bell) bell.click && initNotifications();
    // Just trigger a fetch via apiFetch
    const badge = document.getElementById('notifBadge');
    if (!badge) return;
    apiFetch('/api/notifications')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        const count = data.count || 0;
        if (count > 0) {
          badge.textContent = count > 99 ? '99+' : String(count);
          badge.style.display = '';
        } else {
          badge.style.display = 'none';
        }
      })
      .catch(() => {});
  };

  /* ────────────────────────────────────────────────────────────
   * 6. Bulk Actions
   * ──────────────────────────────────────────────────────────── */
  function initBulkActions() {
    const selectAll   = document.getElementById('selectAll');
    const toolbar     = document.getElementById('bulkToolbar');
    const countLabel  = document.getElementById('bulkCount');
    const bulkSelect  = document.getElementById('bulkActionSelect');
    const bulkSubmit  = document.getElementById('bulkActionSubmit');
    const clearBtn    = document.getElementById('bulkClearBtn');

    function getChecked() {
      return Array.from(document.querySelectorAll('.asset-cb:checked'));
    }

    function updateBulkUI() {
      const checked = getChecked();
      const count = checked.length;

      if (countLabel) countLabel.textContent = `${count} selected`;
      if (toolbar) toolbar.classList.toggle('hidden', count === 0);

      if (selectAll) {
        const all = document.querySelectorAll('.asset-cb');
        selectAll.indeterminate = count > 0 && count < all.length;
        selectAll.checked = count > 0 && count === all.length;
      }
    }

    // Select All checkbox
    if (selectAll) {
      selectAll.addEventListener('change', function () {
        document.querySelectorAll('.asset-cb').forEach(cb => { cb.checked = selectAll.checked; });
        updateBulkUI();
      });
    }

    // Individual checkboxes — use event delegation
    document.addEventListener('change', function (e) {
      if (e.target.classList.contains('asset-cb')) {
        updateBulkUI();
      }
    });

    // Clear selection
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        document.querySelectorAll('.asset-cb').forEach(cb => { cb.checked = false; });
        if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
        updateBulkUI();
      });
    }

    // Bulk action submit
    if (bulkSubmit) {
      bulkSubmit.addEventListener('click', function () {
        const checked = getChecked();
        if (!checked.length) { showFlash('No assets selected.', 'warning'); return; }
        const action = bulkSelect ? bulkSelect.value : '';
        if (!action) { showFlash('Please select an action.', 'warning'); return; }

        const assetIds = checked.map(cb => cb.value);
        const payload = { action: action, asset_ids: assetIds };

        // If bulk status change, include new_status
        const statusSelect = document.getElementById('bulkStatusSelect');
        if (statusSelect && statusSelect.value) payload.new_status = statusSelect.value;

        bulkSubmit.disabled = true;
        bulkSubmit.textContent = 'Processing…';

        apiFetch('/assets/bulk-action', { method: 'POST', body: payload })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.redirect) { window.location.href = data.redirect; return; }
            if (data.error)    { showFlash(data.error, 'error'); }
            else               { showFlash(data.message || 'Action completed.', 'success'); window.location.reload(); }
          })
          .catch(function () { showFlash('Bulk action failed. Please try again.', 'error'); })
          .finally(function () {
            bulkSubmit.disabled = false;
            bulkSubmit.textContent = 'Apply';
          });
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * 7. Action System (data-action attribute)
   * ──────────────────────────────────────────────────────────── */
  const actions = {
    openStatusModal: function () {
      const m = document.getElementById('statusModal');
      if (m) openModal(m);
    },
    closeStatusModal: function () {
      const m = document.getElementById('statusModal');
      if (m) closeModal(m);
    },
    openAssignModal: function () {
      const m = document.getElementById('assignModal');
      if (m) openModal(m);
    },
    closeAssignModal: function () {
      const m = document.getElementById('assignModal');
      if (m) closeModal(m);
    },
    openReturnModal: function (el) {
      const m = document.getElementById('returnModal');
      if (!m) return;
      // Copy assignment ID to hidden input
      const assignId = el && el.getAttribute('data-assign-id');
      const hidden = m.querySelector('input[name="assignment_id"]');
      if (hidden && assignId) hidden.value = assignId;
      openModal(m);
    },
    closeReturnModal: function () {
      const m = document.getElementById('returnModal');
      if (m) closeModal(m);
    },
    openEditAssignment: function () {
      const m = document.getElementById('editAssignmentModal');
      if (m) openModal(m);
    },
    closeEditAssignment: function () {
      const m = document.getElementById('editAssignmentModal');
      if (m) closeModal(m);
    },
    openAddIssueForm: function () {
      const f = document.getElementById('addIssueForm');
      if (f) { f.classList.remove('hidden'); f.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    },
    closeAddIssueForm: function () {
      const f = document.getElementById('addIssueForm');
      if (f) f.classList.add('hidden');
    },
    toggleRepairForm: function (el, id) {
      const form = document.getElementById(`repair-form-row-${id}`);
      if (form) {
        form.classList.toggle('hidden');
        if (!form.classList.contains('hidden')) form.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    },
    openEditAllocation: function () {
      const m = document.getElementById('editAllocationModal');
      if (m) openModal(m);
    },
    closeEditAllocation: function () {
      const m = document.getElementById('editAllocationModal');
      if (m) closeModal(m);
    },
    openReassign: function () {
      const input = document.querySelector('.inline-form input[name="user_name"]');
      if (input) { input.scrollIntoView({ behavior: 'smooth', block: 'center' }); input.focus(); }
    },
    confirmDelete: function (el) {
      const msg = el && el.getAttribute('data-confirm-msg');
      return confirmDelete(msg);
    },
  };

  function initActions() {
    document.addEventListener('click', function (e) {
      const el = e.target.closest('[data-action]');
      if (!el) return;
      const name   = el.getAttribute('data-action');
      const dataId = el.getAttribute('data-id');
      if (name && actions[name]) {
        try {
          const result = actions[name](el, dataId);
          // If the action returns false (e.g. confirmDelete returns false), prevent default
          if (result === false) { e.preventDefault(); e.stopPropagation(); }
          else e.preventDefault();
        } catch (err) {
          console.error('[data-action] error:', err);
        }
      }
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 8. Confirm Delete Helper
   * ──────────────────────────────────────────────────────────── */
  function confirmDelete(msg) {
    return confirm(msg || 'Are you sure? This cannot be undone.');
  }
  window.confirmDelete = confirmDelete;

  // Attach to forms with data-confirm attribute
  function initConfirmForms() {
    document.addEventListener('submit', function (e) {
      const form = e.target;
      const msg  = form.getAttribute('data-confirm');
      if (msg && !confirm(msg)) {
        e.preventDefault();
      }
    });

    // Buttons/links with data-confirm-click
    document.addEventListener('click', function (e) {
      const el = e.target.closest('[data-confirm-click]');
      if (el) {
        const msg = el.getAttribute('data-confirm-click');
        if (!confirm(msg || 'Are you sure?')) {
          e.preventDefault();
          e.stopPropagation();
        }
      }
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 9. AJAX Role Change (admin/roles.html)
   * ──────────────────────────────────────────────────────────── */
  function initRoleSelects() {
    document.querySelectorAll('.role-select[data-oid]').forEach(function (select) {
      const oid         = select.getAttribute('data-oid');
      const feedbackEl  = document.getElementById(`role-feedback-${oid}`);

      select.addEventListener('change', function () {
        const newRole = select.value;
        if (!newRole) return;

        const originalValue = select.dataset.original;
        select.disabled = true;

        apiFetch(`/admin/roles/${oid}`, {
          method: 'POST',
          body: { role: newRole },
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.error) {
              showFlash(data.error, 'error');
              select.value = originalValue || '';
              if (feedbackEl) { feedbackEl.textContent = data.error; feedbackEl.className = 'role-feedback error'; }
            } else {
              select.dataset.original = newRole;
              if (feedbackEl) {
                feedbackEl.textContent = 'Saved';
                feedbackEl.className = 'role-feedback success';
                setTimeout(() => { feedbackEl.textContent = ''; }, 2500);
              }
              if (data.redirect) { window.location.href = data.redirect; return; }
              showFlash(data.message || 'Role updated.', 'success');
            }
          })
          .catch(function () {
            showFlash('Failed to update role. Please try again.', 'error');
            select.value = originalValue || '';
          })
          .finally(function () {
            select.disabled = false;
          });
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * 10. Sold-To Field Toggle (status modal)
   * ──────────────────────────────────────────────────────────── */
  function initSoldToToggle() {
    const statusSelect = document.getElementById('smStatus');
    const soldWrap     = document.getElementById('smSoldWrap');
    const submitBtn    = document.getElementById('smSubmitBtn');
    if (!statusSelect) return;

    const originalBtnText  = submitBtn ? submitBtn.textContent.trim() : 'Update Status';
    const originalBtnClass = submitBtn ? submitBtn.className : '';

    function toggleSoldWrap() {
      const isSold = statusSelect.value === 'sold';
      if (soldWrap) soldWrap.classList.toggle('hidden', !isSold);
      if (submitBtn) {
        if (isSold) {
          submitBtn.textContent = 'Confirm Sale';
          submitBtn.classList.remove('btn-primary', 'btn-blue');
          submitBtn.classList.add('btn-danger');
        } else {
          submitBtn.textContent = originalBtnText;
          submitBtn.className   = originalBtnClass;
        }
      }
    }

    statusSelect.addEventListener('change', toggleSoldWrap);
    // Run on initial load (in case modal pre-fills a value)
    toggleSoldWrap();
  }

  /* ────────────────────────────────────────────────────────────
   * 11. Smart Redirect After POST
   * ──────────────────────────────────────────────────────────── */
  function handleJsonRedirect(data) {
    if (data && data.redirect) {
      window.location.href = data.redirect;
      return true;
    }
    return false;
  }
  window.handleJsonRedirect = handleJsonRedirect;

  /* ────────────────────────────────────────────────────────────
   * Popover Menus (row action menus)
   * ──────────────────────────────────────────────────────────── */
  function initPopovers() {
    document.addEventListener('click', function (e) {
      const trigger = e.target.closest('.popover-trigger');

      // Close all open popovers not belonging to this trigger
      document.querySelectorAll('.popover-menu:not(.hidden)').forEach(function (menu) {
        const wrapper = menu.closest('.popover-wrapper');
        const t = wrapper && wrapper.querySelector('.popover-trigger');
        if (t && t !== trigger) {
          menu.classList.add('hidden');
          t.setAttribute('aria-expanded', 'false');
        }
      });

      if (trigger) {
        const wrapper = trigger.closest('.popover-wrapper');
        const menu    = wrapper && wrapper.querySelector('.popover-menu');
        if (menu) {
          const willShow = menu.classList.contains('hidden');
          menu.classList.toggle('hidden', !willShow);
          trigger.setAttribute('aria-expanded', String(willShow));
          e.stopPropagation();
        }
      } else if (!e.target.closest('.popover-menu')) {
        // Click outside — close all
        document.querySelectorAll('.popover-menu:not(.hidden)').forEach(m => m.classList.add('hidden'));
        document.querySelectorAll('.popover-trigger').forEach(t => t.setAttribute('aria-expanded', 'false'));
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' || e.key === 'Esc') {
        document.querySelectorAll('.popover-menu:not(.hidden)').forEach(m => m.classList.add('hidden'));
        document.querySelectorAll('.popover-trigger').forEach(t => t.setAttribute('aria-expanded', 'false'));
      }
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Data-color / health bar initialisation
   * ──────────────────────────────────────────────────────────── */
  function applyDataColors() {
    document.querySelectorAll('[data-color]').forEach(function (el) {
      try {
        const c = el.getAttribute('data-color');
        if (c) el.style.setProperty('--chip-color', c);
      } catch (e) { /* ignore */ }
    });

    document.querySelectorAll('[data-health]').forEach(function (el) {
      try {
        const val = el.getAttribute('data-health');
        if (val != null) el.style.width = (Number(val) || 0) + '%';
      } catch (e) { /* ignore */ }
    });
  }

  /* ────────────────────────────────────────────────────────────
   * Mobile sidebar toggle
   * ──────────────────────────────────────────────────────────── */
  function initMobileSidebar() {
    const sidebar  = document.querySelector('.sidebar');
    const overlay  = document.querySelector('.sidebar-overlay');
    const menuBtn  = document.querySelector('.mobile-menu-btn');

    if (!sidebar || !menuBtn) return;

    menuBtn.addEventListener('click', function () {
      sidebar.classList.toggle('mobile-open');
      if (overlay) overlay.classList.toggle('active');
    });

    if (overlay) {
      overlay.addEventListener('click', function () {
        sidebar.classList.remove('mobile-open');
        overlay.classList.remove('active');
      });
    }
  }

  /* ────────────────────────────────────────────────────────────
   * Upload zone drag-drop behaviour
   * ──────────────────────────────────────────────────────────── */
  function initUploadZones() {
    document.querySelectorAll('.upload-zone').forEach(function (zone) {
      const input    = zone.querySelector('input[type="file"]');
      const nameEl   = zone.querySelector('.upload-file-name');

      if (input && nameEl) {
        input.addEventListener('change', function () {
          if (input.files && input.files.length > 0) {
            nameEl.textContent = input.files[0].name;
            nameEl.style.display = '';
          } else {
            nameEl.style.display = 'none';
          }
        });
      }

      ['dragenter', 'dragover'].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          zone.classList.add('drag-over');
        });
      });

      ['dragleave', 'drop'].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          zone.classList.remove('drag-over');
          if (evt === 'drop' && input) {
            const files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length) {
              input.files = files;
              input.dispatchEvent(new Event('change'));
            }
          }
        });
      });
    });
  }

  /* ────────────────────────────────────────────────────────────
   * XSS-safe HTML escape helper
   * ──────────────────────────────────────────────────────────── */
  function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = String(str == null ? '' : str);
    return d.innerHTML;
  }
  window.escHtml = escHtml;

  /* ────────────────────────────────────────────────────────────
   * Selectable issues table (legacy v1 compat)
   * ──────────────────────────────────────────────────────────── */
  function initSelectableTables() {
    try {
      const selectAll   = document.getElementById('select-all-issues');
      const downloadBtn = document.getElementById('downloadSelectedBtn');
      const issuesForm  = document.getElementById('issuesTableForm');
      if (!selectAll || !downloadBtn || !issuesForm) return;

      const checkboxes  = () => Array.from(issuesForm.querySelectorAll('.row-checkbox'));
      const updateState = () => { downloadBtn.disabled = !checkboxes().some(cb => cb.checked); };

      selectAll.addEventListener('change', function () { checkboxes().forEach(cb => { cb.checked = selectAll.checked; }); updateState(); });
      checkboxes().forEach(function (cb) {
        cb.addEventListener('change', function () {
          if (!this.checked) selectAll.checked = false;
          if (checkboxes().every(c => c.checked)) selectAll.checked = true;
          updateState();
        });
      });

      const downloadForm = document.getElementById('downloadSelectedForm');
      if (downloadForm) {
        downloadForm.addEventListener('submit', function (e) {
          Array.from(downloadForm.querySelectorAll('input[name="selected_ids"]')).forEach(n => n.remove());
          checkboxes().filter(cb => cb.checked).forEach(function (cb) {
            const hid = document.createElement('input');
            hid.type  = 'hidden';
            hid.name  = 'selected_ids';
            hid.value = cb.value;
            downloadForm.appendChild(hid);
          });
          if (!downloadForm.querySelectorAll('input[name="selected_ids"]').length) e.preventDefault();
        });
      }
    } catch (e) { console.error('initSelectableTables', e); }
  }

  /* ────────────────────────────────────────────────────────────
   * Boot — run everything when DOM is ready
   * ──────────────────────────────────────────────────────────── */
  function boot() {
    applyDataColors();
    initFlashMessages();
    initModals();
    initTabs();
    initNotifications();
    initBulkActions();
    initActions();
    initConfirmForms();
    initRoleSelects();
    initSoldToToggle();
    initPopovers();
    initMobileSidebar();
    initUploadZones();
    initSelectableTables();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Expose for debugging
  window.__site = { applyDataColors, actions, escHtml, apiFetch, showFlash };

})();
