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

    window.CintaDatDetail = window.CintaDatDetail || {};
    window.CintaDatDetail.initTooltips = init;

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
    document.addEventListener("turbolinks:load", init);
  })();

(function () {
  if (window.__cintaDatModalHandlersBound) {
    bindOverlay();
    return;
  }

  window.__cintaDatModalHandlersBound = true;

  function getOverlay() {
    return document.getElementById("dat-modal-overlay");
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.add("open");
    const overlay = getOverlay();
    if (overlay) {
      overlay.classList.add("visible");
    }
    document.body.classList.add("dat-modal-open");
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove("open");
    if (!document.querySelector(".dat-modal.open")) {
      const overlay = getOverlay();
      if (overlay) {
        overlay.classList.remove("visible");
      }
      document.body.classList.remove("dat-modal-open");
    }
  }

  function closeAllModals() {
    document.querySelectorAll(".dat-modal.open").forEach((modal) => {
      modal.classList.remove("open");
    });
    const overlay = getOverlay();
    if (overlay) {
      overlay.classList.remove("visible");
    }
    document.body.classList.remove("dat-modal-open");
  }

  function handleTriggerClick(event) {
    const trigger = event.target.closest(".dat-modal-trigger");
    if (!trigger) {
      return;
    }
    if (trigger.hasAttribute("aria-disabled") || trigger.classList.contains("disabled")) {
      return;
    }
    event.preventDefault();
    const href = trigger.getAttribute("href");
    const targetId = trigger.dataset.modalTarget || (href && href.startsWith("#") ? href.substring(1) : null);
    if (!targetId) {
      return;
    }
    const modal = document.getElementById(targetId);
    openModal(modal);
  }

  function handleCloseClick(event) {
    const closeButton = event.target.closest(".dat-modal-close");
    if (!closeButton) {
      return;
    }
    event.preventDefault();
    const modal = closeButton.closest(".dat-modal");
    closeModal(modal);
  }

  function bindOverlay() {
    const overlay = getOverlay();
    if (overlay && !overlay.__datModalOverlayBound) {
      overlay.addEventListener("click", closeAllModals);
      overlay.__datModalOverlayBound = true;
    }
  }

  bindOverlay();
  document.addEventListener("turbolinks:load", bindOverlay);
  document.addEventListener("turbolinks:before-cache", closeAllModals);
  document.addEventListener("click", handleTriggerClick);
  document.addEventListener("click", handleCloseClick);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllModals();
    }
  });
})();

(function () {
  function bindHistoryFilters() {
    const modal = document.getElementById("dat-reserve-history-modal");
    if (!modal || modal.__datHistoryFiltersBound) {
      return;
    }
    const typeSelect = modal.querySelector('[data-history-filter="type"]');
    const userSelect = modal.querySelector('[data-history-filter="user"]');
    const rows = modal.querySelectorAll("[data-history-entry]");
    const emptyMessage = modal.querySelector(".dat-history-empty");
    if (!rows.length) {
      modal.__datHistoryFiltersBound = true;
      return;
    }
    function applyFilters() {
      const typeValue = typeSelect ? typeSelect.value : "all";
      const userValue = userSelect ? userSelect.value : "all";
      let visibleCount = 0;
      rows.forEach((row) => {
        const rowType = row.getAttribute("data-history-type");
        const rowUser = row.getAttribute("data-history-user");
        const matchesType = typeValue === "all" || rowType === typeValue;
        const matchesUser = userValue === "all" || rowUser === userValue;
        if (matchesType && matchesUser) {
          row.style.display = "";
          visibleCount += 1;
        } else {
          row.style.display = "none";
        }
      });
      if (emptyMessage) {
        emptyMessage.style.display = visibleCount ? "none" : "block";
      }
    }
    if (typeSelect) {
      typeSelect.addEventListener("change", applyFilters);
    }
    if (userSelect) {
      userSelect.addEventListener("change", applyFilters);
    }
    applyFilters();
    modal.__datHistoryFiltersBound = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindHistoryFilters, { once: true });
  } else {
    bindHistoryFilters();
  }
  document.addEventListener("turbolinks:load", bindHistoryFilters);
})();

(function () {
  let pendingForm = null;

  function getOverlay() {
    return document.getElementById("dat-modal-overlay");
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.add("open");
    const overlay = getOverlay();
    if (overlay) {
      overlay.classList.add("visible");
    }
    document.body.classList.add("dat-modal-open");
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove("open");
    if (!document.querySelector(".dat-modal.open")) {
      const overlay = getOverlay();
      if (overlay) {
        overlay.classList.remove("visible");
      }
      document.body.classList.remove("dat-modal-open");
    }
  }

  function ensureReserveModal() {
    const modal = document.getElementById("dat-reserve-modal");
    const textarea = document.getElementById("dat-reserve-textarea");
    const confirmBtn = document.getElementById("dat-reserve-confirm");
    const title = document.getElementById("dat-reserve-section-title");
    const error = document.getElementById("dat-reserve-error");
    if (!modal || !textarea || !confirmBtn || !title || !error) {
      return null;
    }
    return { modal, textarea, confirmBtn, title, error };
  }

  function openReserveModal(form) {
    const ui = ensureReserveModal();
    if (!ui) {
      return;
    }
    pendingForm = form;
    ui.title.textContent = form.dataset.sectionTitle || "cette section";
    ui.textarea.value = "";
    ui.error.style.display = "none";
    openModal(ui.modal);
    ui.textarea.focus();
    if (window.M && typeof M.textareaAutoResize === "function") {
      M.textareaAutoResize(ui.textarea);
    }
  }

  function bindReserveForms() {
    const ui = ensureReserveModal();
    if (!ui) {
      return;
    }
    if (!ui.confirmBtn.__datReserveConfirmBound) {
      ui.confirmBtn.addEventListener("click", () => {
        if (!pendingForm) {
          closeModal(ui.modal);
          return;
        }
        const message = (ui.textarea.value || "").trim();
        if (!message) {
          ui.error.style.display = "block";
          ui.textarea.focus();
          return;
        }
        const hidden = pendingForm.querySelector('input[name="reserve_message"]');
        if (hidden) {
          hidden.value = message;
        }
        pendingForm.__reserveConfirmed = true;
        closeModal(ui.modal);
        pendingForm.submit();
        pendingForm = null;
      });
      ui.confirmBtn.__datReserveConfirmBound = true;
    }

    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!form || !(form instanceof HTMLFormElement)) {
        return;
      }
      if (!form.classList.contains("dat-reserve-create-form")) {
        return;
      }
      if (form.__reserveConfirmed) {
        form.__reserveConfirmed = false;
        return;
      }
      event.preventDefault();
      openReserveModal(form);
    });
  }

  document.addEventListener("DOMContentLoaded", bindReserveForms);
  document.addEventListener("turbolinks:load", bindReserveForms);
})();

(function () {
  let pendingForm = null;

  function getOverlay() {
    return document.getElementById("dat-modal-overlay");
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.add("open");
    const overlay = getOverlay();
    if (overlay) {
      overlay.classList.add("visible");
    }
    document.body.classList.add("dat-modal-open");
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove("open");
    if (!document.querySelector(".dat-modal.open")) {
      const overlay = getOverlay();
      if (overlay) {
        overlay.classList.remove("visible");
      }
      document.body.classList.remove("dat-modal-open");
    }
  }

  function ensureBlockModal() {
    const modal = document.getElementById("dat-block-comment-modal");
    const textarea = document.getElementById("dat-block-comment-textarea");
    const confirmBtn = document.getElementById("dat-block-comment-confirm");
    const title = document.getElementById("dat-block-comment-section-title");
    const error = document.getElementById("dat-block-comment-error");
    if (!modal || !textarea || !confirmBtn || !title || !error) {
      return null;
    }
    return { modal, textarea, confirmBtn, title, error };
  }

  function openBlockModal(form) {
    const ui = ensureBlockModal();
    if (!ui) {
      return;
    }
    pendingForm = form;
    ui.title.textContent = form.dataset.sectionTitle || "cette section";
    ui.textarea.value = "";
    ui.error.style.display = "none";
    openModal(ui.modal);
    ui.textarea.focus();
    if (window.M && typeof M.textareaAutoResize === "function") {
      M.textareaAutoResize(ui.textarea);
    }
  }

  function bindBlockForms() {
    const ui = ensureBlockModal();
    if (!ui) {
      return;
    }
    if (ui.confirmBtn.__datBlockConfirmBound) {
      return;
    }
    ui.confirmBtn.addEventListener("click", () => {
      if (!pendingForm) {
        closeModal(ui.modal);
        return;
      }
      const comment = (ui.textarea.value || "").trim();
      if (!comment) {
        ui.error.style.display = "block";
        ui.textarea.focus();
        return;
      }
      const hidden = pendingForm.querySelector('input[name="commentaire"]');
      if (hidden) {
        hidden.value = comment;
      }
      pendingForm.__blockConfirmed = true;
      closeModal(ui.modal);
      pendingForm.submit();
      pendingForm = null;
    });
    ui.confirmBtn.__datBlockConfirmBound = true;

    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!form || !(form instanceof HTMLFormElement)) {
        return;
      }
      if (!form.classList.contains("dat-block-status-form")) {
        return;
      }
      if (form.__blockConfirmed) {
        form.__blockConfirmed = false;
        return;
      }
      event.preventDefault();
      openBlockModal(form);
    });
  }

  document.addEventListener("DOMContentLoaded", bindBlockForms);
  document.addEventListener("turbolinks:load", bindBlockForms);
})();

(function () {
  let pendingForm = null;

  function getOverlay() {
    return document.getElementById("dat-modal-overlay");
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.add("open");
    const overlay = getOverlay();
    if (overlay) {
      overlay.classList.add("visible");
    }
    document.body.classList.add("dat-modal-open");
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.classList.remove("open");
    if (!document.querySelector(".dat-modal.open")) {
      const overlay = getOverlay();
      if (overlay) {
        overlay.classList.remove("visible");
      }
      document.body.classList.remove("dat-modal-open");
    }
  }

  function ensureUi() {
    const modal = document.getElementById("dat-devalidate-responsible-modal");
    const confirmBtn = document.getElementById("dat-devalidate-responsible-confirm");
    const title = document.getElementById("dat-devalidate-section-title");
    if (!modal || !confirmBtn || !title) {
      return null;
    }
    return { modal, confirmBtn, title };
  }

  function openDevalidateModal(form) {
    const ui = ensureUi();
    if (!ui) {
      return;
    }
    pendingForm = form;
    ui.title.textContent = form.dataset.sectionTitle || "cette section";
    openModal(ui.modal);
  }

  function bindDevalidateForms() {
    const ui = ensureUi();
    if (!ui) {
      return;
    }
    if (!ui.confirmBtn.__datDevalidateBound) {
      ui.confirmBtn.addEventListener("click", () => {
        if (!pendingForm) {
          closeModal(ui.modal);
          return;
        }
        const hidden = pendingForm.querySelector('input[name="confirm_responsable_reset"]');
        if (hidden) {
          hidden.value = "1";
        }
        pendingForm.__devalidateConfirmed = true;
        closeModal(ui.modal);
        pendingForm.submit();
        pendingForm = null;
      });
      ui.confirmBtn.__datDevalidateBound = true;
    }

    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!form || !(form instanceof HTMLFormElement)) {
        return;
      }
      if (!form.classList.contains("dat-devalidate-form")) {
        return;
      }
      if (form.__devalidateConfirmed) {
        form.__devalidateConfirmed = false;
        return;
      }
      if (form.dataset.hasResponsibleValidation !== "1") {
        return;
      }
      event.preventDefault();
      openDevalidateModal(form);
    });
  }

  document.addEventListener("DOMContentLoaded", bindDevalidateForms);
  document.addEventListener("turbolinks:load", bindDevalidateForms);
})();

(function () {
  let timerId = null;

  function formatElapsed(ms) {
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;
    const days = Math.floor(ms / day);
    const hours = Math.floor((ms % day) / hour);
    const minutes = Math.floor((ms % hour) / minute);

    if (days > 0) {
      return `${days}j ${hours}h`;
    }
    if (hours > 0) {
      return `${hours}h ${minutes}min`;
    }
    if (minutes > 0) {
      return `${minutes}min`;
    }
    return "< 1 min";
  }

  function updateDatAge() {
    const target = document.querySelector("[data-dat-created-at]");
    if (!target) {
      return;
    }
    const valueEl = target.querySelector("[data-dat-created-value]") || target;
    const createdAt = target.dataset.datCreatedAt;
    if (!createdAt) {
      valueEl.textContent = "—";
      return;
    }
    const createdDate = new Date(createdAt);
    if (Number.isNaN(createdDate.getTime())) {
      valueEl.textContent = "—";
      return;
    }
    const elapsedMs = Math.max(0, Date.now() - createdDate.getTime());
    valueEl.textContent = formatElapsed(elapsedMs);
  }

  function startTimer() {
    updateDatAge();
    if (timerId) {
      clearInterval(timerId);
    }
    timerId = setInterval(updateDatAge, 60 * 1000);
  }

  function stopTimer() {
    if (!timerId) {
      return;
    }
    clearInterval(timerId);
    timerId = null;
  }

  document.addEventListener("DOMContentLoaded", startTimer);
  document.addEventListener("turbolinks:load", startTimer);
  document.addEventListener("turbolinks:before-cache", stopTimer);
})();

(function () {
  if (window.__datInlineEditInitialized) {
    return;
  }
  window.__datInlineEditInitialized = true;
  const editState = { slug: null };

  function emitSectionEditEvent(name, slug) {
    if (!name) {
      return;
    }
    const detail = slug ? { slug } : {};
    document.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function refreshTooltips() {
    if (window.CintaDatDetail && typeof window.CintaDatDetail.initTooltips === "function") {
      window.CintaDatDetail.initTooltips();
    }
  }

  function executeEmbeddedScripts(target) {
    if (!target) {
      return;
    }
    const scripts = target.querySelectorAll("script");
    scripts.forEach((oldScript) => {
      const newScript = document.createElement("script");
      Array.from(oldScript.attributes).forEach((attr) => {
        newScript.setAttribute(attr.name, attr.value);
      });
      newScript.textContent = oldScript.textContent;
      oldScript.replaceWith(newScript);
    });
  }

  function refreshMaterializeFields(target) {
    if (!target) {
      return;
    }
    if (window.M && typeof M.updateTextFields === "function") {
      M.updateTextFields();
    }
    if (window.M && typeof M.textareaAutoResize === "function") {
      target.querySelectorAll("textarea").forEach((textarea) => {
        M.textareaAutoResize(textarea);
      });
    }
  }

  function disableOtherEditButtons(activeSlug) {
    document.querySelectorAll(".dat-subsection-edit-btn").forEach((button) => {
      if (button.dataset.subSectionSlug !== activeSlug) {
        button.classList.add("disabled");
        button.setAttribute("aria-disabled", "true");
        button.setAttribute("tabindex", "-1");
      } else {
        button.classList.add("editing");
      }
    });
  }

  function enableAllEditButtons() {
    document.querySelectorAll(".dat-subsection-edit-btn").forEach((button) => {
      button.classList.remove("disabled", "editing");
      button.removeAttribute("aria-disabled");
      button.removeAttribute("tabindex");
    });
  }

  function restoreView(container) {
    if (!container || typeof container.dataset.viewHtml === "undefined") {
      return;
    }
    const slug = (container.dataset && container.dataset.subSectionSlug) || editState.slug || null;
    container.innerHTML = container.dataset.viewHtml;
    refreshTooltips();
    delete container.dataset.viewHtml;
    container.classList.remove("dat-sub-section-editing");
    emitSectionEditEvent("dat:section-edit-end", slug);
    editState.slug = null;
    enableAllEditButtons();
  }

  function cancelActiveInlineEdit() {
    if (!editState.slug) {
      return;
    }
    const activeContainer = document.querySelector(`.dat-sub-section[data-sub-section-slug="${editState.slug}"]`);
    if (activeContainer) {
      restoreView(activeContainer);
    } else {
      emitSectionEditEvent("dat:section-edit-end", editState.slug);
      editState.slug = null;
      enableAllEditButtons();
    }
  }

  function attachInlineFormHandlers(container, slug) {
    if (window.CintaDatRepeater && typeof window.CintaDatRepeater.scan === "function") {
      window.CintaDatRepeater.scan();
    }
    const cancelBtn = container.querySelector(".dat-sub-section-cancel-btn");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", (event) => {
        event.preventDefault();
        restoreView(container);
      });
    }
    const form = container.querySelector("form");
    if (!form) {
      return;
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      form.classList.add("is-saving");
      fetch(form.action, {
        method: "POST",
        body: formData,
        headers: {"X-Requested-With": "XMLHttpRequest"},
      })
        .then((response) => response.json())
        .then((data) => {
          if (!data.success) {
            if (data.form_html) {
              renderInlineForm(container, data.form_html, slug);
            }
            return;
          }
          const nextSlug = data.sub_section_slug || slug;
          const nextHtml = data.sub_section_html;
          delete container.dataset.viewHtml;
          container.classList.remove("dat-sub-section-editing");
          enableAllEditButtons();
          editState.slug = null;
          if (nextHtml) {
            container.outerHTML = nextHtml;
            refreshTooltips();
            const updated = document.querySelector(`[data-sub-section-slug="${nextSlug}"]`);
            if (updated) {
              updated.classList.add("dat-sub-section-updated");
              setTimeout(() => updated.classList.remove("dat-sub-section-updated"), 1800);
            }
            emitSectionEditEvent("dat:section-edit-end", nextSlug);
          } else {
            restoreView(container);
          }
          if (data.message && window.M && M.toast) {
            M.toast({html: data.message});
          }
        })
        .catch(() => {
          alert("Une erreur est survenue lors de l'enregistrement de la sous-section.");
          restoreView(container);
        })
        .finally(() => {
          form.classList.remove("is-saving");
        });
    });
  }

  function renderInlineForm(container, html, slug) {
    container.innerHTML = html;
    executeEmbeddedScripts(container); // ensure widgets (e.g. repeaters) initialise correctly
    refreshMaterializeFields(container);
    container.classList.add("dat-sub-section-editing");
    attachInlineFormHandlers(container, slug);
  }

  function beginInlineEdit(button) {
    const slug = button.dataset.subSectionSlug;
    if (!slug || (editState.slug && editState.slug !== slug)) {
      return;
    }
    const container = button.closest(".dat-sub-section");
    if (!container || typeof container.dataset.viewHtml !== "undefined") {
      return;
    }
    editState.slug = slug;
    disableOtherEditButtons(slug);
    container.dataset.viewHtml = container.innerHTML;
    container.classList.add("dat-sub-section-editing");
    emitSectionEditEvent("dat:section-edit-start", slug);
    container.innerHTML = `
      <div class="dat-sub-section-loading">
        <div class="preloader-wrapper small active">
          <div class="spinner-layer spinner-blue-only">
            <div class="circle-clipper left"><div class="circle"></div></div>
            <div class="gap-patch"><div class="circle"></div></div>
            <div class="circle-clipper right"><div class="circle"></div></div>
          </div>
        </div>
        <p class="u-mt-0-5 text-muted-dark">Chargement du formulaire…</p>
      </div>
    `;
    const url = button.dataset.editUrl || button.getAttribute("href");
    fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
      .then((response) => response.json())
      .then((data) => {
        if (data.form_html) {
          renderInlineForm(container, data.form_html, slug);
        } else {
          throw new Error("missing_form");
        }
      })
      .catch(() => {
        alert("Impossible de charger cette sous-section.");
        restoreView(container);
      });
  }

  document.addEventListener("click", (event) => {
    const editBtn = event.target.closest(".dat-subsection-edit-btn");
    if (!editBtn) {
      return;
    }
    event.preventDefault();
    if (editBtn.classList.contains("disabled")) {
      return;
    }
    beginInlineEdit(editBtn);
  });

  document.addEventListener("click", (event) => {
    const sectionLink = event.target.closest(".dat-section-link");
    if (sectionLink) {
      cancelActiveInlineEdit();
    }
  });

  document.addEventListener("click", (event) => {
    const viewerBtn = event.target.closest(".dat-viewer-trigger");
    if (!viewerBtn) {
      return;
    }
    event.preventDefault();
    const likec4ViewsUrl = viewerBtn.getAttribute("data-likec4-views-url");
    const likec4Url = viewerBtn.getAttribute("data-likec4-preview-url");
    if (likec4ViewsUrl || likec4Url) {
      const title = viewerBtn.getAttribute("data-likec4-title") || "Diagramme LikeC4";
      if (likec4ViewsUrl) {
        fetch(likec4ViewsUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then((response) => {
            if (!response.ok) {
              throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
          })
          .then((data) => {
            const paths = Array.isArray(data.paths) ? data.paths : [];
            if (window.CintaDatViewer && typeof window.CintaDatViewer.openImages === "function" && paths.length) {
              window.CintaDatViewer.openImages(paths, title);
              return;
            }
            const fallbackUrl = data.thumbnail_url || likec4Url;
            if (fallbackUrl) {
              if (window.CintaDatViewer && typeof window.CintaDatViewer.openImage === "function") {
                window.CintaDatViewer.openImage(fallbackUrl, title);
              } else {
                window.open(fallbackUrl, "_blank", "noopener");
              }
            }
          })
          .catch(() => {
            if (likec4Url) {
              if (window.CintaDatViewer && typeof window.CintaDatViewer.openImage === "function") {
                window.CintaDatViewer.openImage(likec4Url, title);
              } else {
                window.open(likec4Url, "_blank", "noopener");
              }
            }
          });
      } else if (likec4Url) {
        if (window.CintaDatViewer && typeof window.CintaDatViewer.openImage === "function") {
          window.CintaDatViewer.openImage(likec4Url, title);
        } else {
          window.open(likec4Url, "_blank", "noopener");
        }
      }
      return;
    }
    const diagramId = viewerBtn.getAttribute("data-diagram-id");
    if (window.CintaDatViewer && typeof window.CintaDatViewer.open === "function") {
      window.CintaDatViewer.open(diagramId);
    } else if (diagramId) {
      alert("Affichage du diagramme indisponible pour le moment.");
    }
  });

  function parseAllowedExtensions(value) {
    return (value || "")
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter((item) => item);
  }

  function fileExtension(filename) {
    const index = filename.lastIndexOf(".");
    if (index < 0) {
      return "";
    }
    return filename.slice(index).toLowerCase();
  }

  function showAttachmentMessage(message) {
    if (window.M && M.toast) {
      M.toast({html: message});
      return;
    }
    alert(message);
  }

  function showAttachmentMessages(messages) {
    if (!Array.isArray(messages)) {
      return;
    }
    messages.forEach((message) => {
      if (message) {
        showAttachmentMessage(message);
      }
    });
  }

  function supportsAttachmentAjax() {
    return typeof window.FormData === "function" && typeof window.XMLHttpRequest === "function";
  }

  function validateAttachmentFiles(files, allowedExtensions, maxSize, maxSizeLabel) {
    const errors = [];
    files.forEach((file) => {
      if (maxSize && file.size > maxSize) {
        errors.push(`Le fichier ${file.name} depasse la taille maximale (${maxSizeLabel}).`);
      }
      const ext = fileExtension(file.name);
      if (!ext || (allowedExtensions.length && !allowedExtensions.includes(ext))) {
        errors.push(`Le fichier ${file.name} a une extension non autorisee.`);
      }
    });
    return errors;
  }

  function getAttachmentsOpenState(container) {
    if (!container) {
      return null;
    }
    const details = container.querySelector(".dat-attachments-collapsible");
    if (!details) {
      return null;
    }
    return details.open;
  }

  function updateAttachmentsContainer(container, html, wasOpen) {
    if (!container) {
      return;
    }
    if (!html) {
      container.remove();
      return;
    }
    const parent = container.parentElement;
    const slug = container.dataset.sectionSlug || "";
    container.outerHTML = html;
    if (!parent) {
      return;
    }
    const selector = slug
      ? `.dat-section-attachments[data-section-slug="${slug}"]`
      : ".dat-section-attachments";
    const nextContainer = parent.querySelector(selector);
    if (!nextContainer) {
      return;
    }
    if (typeof wasOpen === "boolean") {
      const details = nextContainer.querySelector(".dat-attachments-collapsible");
      if (details) {
        details.open = wasOpen;
      }
    }
    attachAttachmentForms(nextContainer);
  }

  function handleAttachmentPayload(container, payload, wasOpen) {
    if (!payload) {
      return;
    }
    const messages = Array.isArray(payload.messages) ? payload.messages : [];
    showAttachmentMessages(messages);
    if (typeof payload.attachments_html === "string") {
      updateAttachmentsContainer(container, payload.attachments_html, wasOpen);
    }
  }

  function parseJsonXhrResponse(xhr) {
    if (!xhr) {
      return null;
    }
    const contentType = xhr.getResponseHeader("content-type") || "";
    if (!contentType.includes("application/json")) {
      return null;
    }
    try {
      return JSON.parse(xhr.responseText || "");
    } catch (error) {
      return null;
    }
  }

  function getAttachmentProgressElements(form) {
    if (!form) {
      return null;
    }
    const wrapper = form.querySelector("[data-attachment-progress]");
    if (!wrapper) {
      return null;
    }
    return {
      wrapper,
      bar: wrapper.querySelector("[data-attachment-progress-bar]"),
      label: wrapper.querySelector("[data-attachment-progress-label]"),
    };
  }

  function setAttachmentProgressValue(form, percent, label) {
    const elements = getAttachmentProgressElements(form);
    if (!elements) {
      return;
    }
    const safePercent = Math.max(0, Math.min(100, percent));
    elements.wrapper.hidden = false;
    if (elements.bar) {
      elements.bar.classList.remove("indeterminate");
      elements.bar.classList.add("determinate");
      elements.bar.style.width = `${safePercent}%`;
    }
    if (elements.label) {
      elements.label.textContent = label || `Envoi ${safePercent}%`;
    }
  }

  function setAttachmentProgressIndeterminate(form, label) {
    const elements = getAttachmentProgressElements(form);
    if (!elements) {
      return;
    }
    elements.wrapper.hidden = false;
    if (elements.bar) {
      elements.bar.classList.remove("determinate");
      elements.bar.classList.add("indeterminate");
      elements.bar.style.removeProperty("width");
    }
    if (elements.label) {
      elements.label.textContent = label || "Analyse en cours...";
    }
  }

  function resetAttachmentProgress(form) {
    const elements = getAttachmentProgressElements(form);
    if (!elements) {
      return;
    }
    elements.wrapper.hidden = true;
    if (elements.bar) {
      elements.bar.classList.remove("indeterminate");
      elements.bar.classList.add("determinate");
      elements.bar.style.width = "0%";
    }
    if (elements.label) {
      elements.label.textContent = "";
    }
  }

  function setFormButtonsDisabled(form, disabled) {
    if (!form) {
      return;
    }
    const trigger = form.querySelector("[data-attachment-trigger]");
    if (trigger) {
      trigger.disabled = disabled;
      trigger.classList.toggle("disabled", disabled);
    }
    form.querySelectorAll("button").forEach((button) => {
      button.disabled = disabled;
      button.classList.toggle("disabled", disabled);
    });
  }

  function submitAttachmentForm(form) {
    if (!form || form.dataset.attachmentSubmitting === "1") {
      return;
    }
    const container = form.closest(".dat-section-attachments");
    const wasOpen = getAttachmentsOpenState(container);
    const formData = new FormData(form);
    form.dataset.attachmentSubmitting = "1";
    setFormButtonsDisabled(form, true);
    setAttachmentProgressValue(form, 0, "Envoi 0%");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        setAttachmentProgressValue(form, percent, `Envoi ${percent}%`);
      } else {
        setAttachmentProgressIndeterminate(form, "Envoi en cours...");
      }
    });
    xhr.upload.addEventListener("load", () => {
      setAttachmentProgressIndeterminate(form, "Analyse en cours...");
    });
    xhr.addEventListener("load", () => {
      const payload = parseJsonXhrResponse(xhr);
      if (!payload) {
        window.location.href = xhr.responseURL || form.action;
        return;
      }
      handleAttachmentPayload(container, payload, wasOpen);
    });
    xhr.addEventListener("error", () => {
      showAttachmentMessage("Impossible d'envoyer la piece jointe.");
    });
    xhr.addEventListener("loadend", () => {
      form.dataset.attachmentSubmitting = "0";
      setFormButtonsDisabled(form, false);
      resetAttachmentProgress(form);
      const input = form.querySelector("[data-attachment-input]");
      if (input) {
        input.value = "";
      }
    });
    xhr.send(formData);
  }

  function submitAttachmentDeleteForm(form) {
    if (!form || form.dataset.attachmentSubmitting === "1") {
      return;
    }
    const container = form.closest(".dat-section-attachments");
    const wasOpen = getAttachmentsOpenState(container);
    const formData = new FormData(form);
    form.dataset.attachmentSubmitting = "1";
    setFormButtonsDisabled(form, true);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.addEventListener("load", () => {
      const payload = parseJsonXhrResponse(xhr);
      if (!payload) {
        window.location.href = xhr.responseURL || form.action;
        return;
      }
      handleAttachmentPayload(container, payload, wasOpen);
    });
    xhr.addEventListener("error", () => {
      showAttachmentMessage("Impossible de supprimer la piece jointe.");
    });
    xhr.addEventListener("loadend", () => {
      form.dataset.attachmentSubmitting = "0";
      setFormButtonsDisabled(form, false);
    });
    xhr.send(formData);
  }

  function attachAttachmentForm(form) {
    if (form.dataset.attachmentBound === "1") {
      return;
    }
    const input = form.querySelector("[data-attachment-input]");
    const trigger = form.querySelector("[data-attachment-trigger]");
    if (!input || !trigger) {
      return;
    }
    form.dataset.attachmentBound = "1";
    const allowedExtensions = parseAllowedExtensions(form.dataset.allowedExtensions);
    const maxSize = parseInt(form.dataset.maxSize || "0", 10);
    const maxSizeLabel = form.dataset.maxSizeLabel || "";

    function handleFiles() {
      const files = Array.from(input.files || []);
      if (!files.length) {
        return;
      }
      const errors = validateAttachmentFiles(files, allowedExtensions, maxSize, maxSizeLabel);
      if (errors.length) {
        showAttachmentMessage(errors[0]);
        input.value = "";
        return;
      }
      if (!supportsAttachmentAjax()) {
        form.submit();
        return;
      }
      submitAttachmentForm(form);
    }

    trigger.addEventListener("click", () => input.click());
    input.addEventListener("change", handleFiles);
    form.addEventListener("submit", (event) => {
      if (!supportsAttachmentAjax()) {
        return;
      }
      const files = Array.from(input.files || []);
      if (!files.length) {
        event.preventDefault();
        showAttachmentMessage("Veuillez selectionner au moins un fichier.");
        return;
      }
      const errors = validateAttachmentFiles(files, allowedExtensions, maxSize, maxSizeLabel);
      if (errors.length) {
        event.preventDefault();
        showAttachmentMessage(errors[0]);
        input.value = "";
        return;
      }
      event.preventDefault();
      submitAttachmentForm(form);
    });
  }

  function attachAttachmentDeleteForm(form) {
    if (form.dataset.attachmentDeleteBound === "1") {
      return;
    }
    form.dataset.attachmentDeleteBound = "1";
    if (supportsAttachmentAjax()) {
      form.removeAttribute("onsubmit");
    }
    form.addEventListener("submit", (event) => {
      if (!supportsAttachmentAjax()) {
        return;
      }
      event.preventDefault();
      const confirmMessage = form.dataset.confirm || "Supprimer cette piece jointe ?";
      if (!window.confirm(confirmMessage)) {
        return;
      }
      submitAttachmentDeleteForm(form);
    });
  }

  function attachAttachmentForms(scope) {
    const root = scope && typeof scope.querySelectorAll === "function" ? scope : document;
    root.querySelectorAll(".dat-attachments-form").forEach(attachAttachmentForm);
    root.querySelectorAll(".dat-attachments-delete-form").forEach(attachAttachmentDeleteForm);
  }

  attachAttachmentForms();
  document.addEventListener("turbolinks:load", attachAttachmentForms);

  document.addEventListener("turbolinks:before-visit", cancelActiveInlineEdit);
  document.addEventListener("turbolinks:before-cache", cancelActiveInlineEdit);
})();
