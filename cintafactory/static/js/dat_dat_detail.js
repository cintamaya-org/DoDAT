  (function () {
    function init() {
      if (window.CintaTooltip && window.CintaTooltip.initWorkflowTooltip) {
        window.CintaTooltip.initWorkflowTooltip({
          nodeSelector: ".wf-tooltip-trigger",
          chipSelector: ".wf-tooltip-trigger",
          textBuilder: function (target) {
            return target.getAttribute("data-tooltip") || "Information";
          },
          instantText: true
        });
      }
    }

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
    document.addEventListener("turbolinks:load", init);
  })();

  (function () {
    function initFallback(modalElement) {
      if (!modalElement || modalElement.__datHistoryFallbackBound) {
        return;
      }
      modalElement.__datHistoryFallbackBound = true;
      var modalId = modalElement.getAttribute('id');
      if (!modalId) {
        return;
      }
      var selector = '[href="#' + modalId + '"], [data-target="' + modalId + '"], [data-modal-target="' + modalId + '"]';
      var triggers = document.querySelectorAll(selector);
      triggers.forEach(function (trigger) {
        trigger.addEventListener('click', function (event) {
          event.preventDefault();
          modalElement.style.display = 'block';
          modalElement.setAttribute('aria-hidden', 'false');
        });
      });
      var closeButtons = modalElement.querySelectorAll('.modal-close');
      closeButtons.forEach(function (btn) {
        btn.addEventListener('click', function (event) {
          event.preventDefault();
          modalElement.style.display = 'none';
          modalElement.setAttribute('aria-hidden', 'true');
        });
      });
    }

    function initModalById(modalId) {
      var modalElement = document.getElementById(modalId);
      if (!modalElement) {
        return;
      }
      if (window.M && M.Modal) {
        var existingInstance = M.Modal.getInstance(modalElement);
        if (existingInstance && typeof existingInstance.destroy === 'function') {
          existingInstance.destroy();
        }
        M.Modal.init(modalElement, { preventScrolling: true });
        modalElement.__datHistoryFallbackBound = false;
      } else {
        initFallback(modalElement);
      }
    }

    function scheduleInit() {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
          initModalById('dat-history-modal');
          initModalById('dat-reserve-history-modal');
        }, { once: true });
      } else {
        initModalById('dat-history-modal');
        initModalById('dat-reserve-history-modal');
      }
    }

    scheduleInit();
    document.addEventListener('turbolinks:load', function () {
      initModalById('dat-history-modal');
      initModalById('dat-reserve-history-modal');
    });
  })();

  (function () {
    function bindHistoryFilters() {
      var modal = document.getElementById('dat-reserve-history-modal');
      if (!modal || modal.__datHistoryFiltersBound) {
        return;
      }
      var typeSelect = modal.querySelector('[data-history-filter="type"]');
      var userSelect = modal.querySelector('[data-history-filter="user"]');
      var rows = modal.querySelectorAll('[data-history-entry]');
      var emptyMessage = modal.querySelector('.dat-history-empty');
      if (!rows.length) {
        modal.__datHistoryFiltersBound = true;
        return;
      }
      function applyFilters() {
        var typeValue = typeSelect ? typeSelect.value : 'all';
        var userValue = userSelect ? userSelect.value : 'all';
        var visibleCount = 0;
        rows.forEach(function (row) {
          var rowType = row.getAttribute('data-history-type');
          var rowUser = row.getAttribute('data-history-user');
          var matchesType = typeValue === 'all' || rowType === typeValue;
          var matchesUser = userValue === 'all' || rowUser === userValue;
          if (matchesType && matchesUser) {
            row.style.display = '';
            visibleCount += 1;
          } else {
            row.style.display = 'none';
          }
        });
        if (emptyMessage) {
          emptyMessage.style.display = visibleCount ? 'none' : 'block';
        }
      }
      if (typeSelect) {
        typeSelect.addEventListener('change', applyFilters);
      }
      if (userSelect) {
        userSelect.addEventListener('change', applyFilters);
      }
      applyFilters();
      modal.__datHistoryFiltersBound = true;
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', bindHistoryFilters, { once: true });
    } else {
      bindHistoryFilters();
    }
    document.addEventListener('turbolinks:load', bindHistoryFilters);
  })();

  document.addEventListener('click', function (event) {
    const viewerBtn = event.target.closest('.dat-viewer-trigger');
    if (!viewerBtn) {
      return;
    }
    event.preventDefault();
    const likec4ViewsUrl = viewerBtn.getAttribute('data-likec4-views-url');
    const likec4Url = viewerBtn.getAttribute('data-likec4-preview-url');
    if (likec4ViewsUrl || likec4Url) {
      const title = viewerBtn.getAttribute('data-likec4-title') || 'Diagramme LikeC4';
      if (window.CintaDatViewer && typeof window.CintaDatViewer.openLikeC4 === 'function') {
        window.CintaDatViewer.openLikeC4(likec4ViewsUrl, likec4Url, title);
      } else if (likec4Url) {
        if (window.CintaDatViewer && typeof window.CintaDatViewer.openImage === 'function') {
          window.CintaDatViewer.openImage(likec4Url, title);
        } else {
          window.open(likec4Url, '_blank', 'noopener');
        }
      }
      return;
    }
    const diagramId = viewerBtn.getAttribute('data-diagram-id');
    if (window.CintaDatViewer && typeof window.CintaDatViewer.open === 'function') {
      window.CintaDatViewer.open(diagramId);
    } else if (diagramId) {
      alert("Affichage du diagramme indisponible pour le moment.");
    }
  });
