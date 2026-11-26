(function () {
  "use strict";

  function initSectionTooltips() {
    if (!window.CintaTooltip) {
      return;
    }
    window.CintaTooltip.initWorkflowTooltip({
      tooltipId: "dat-list-section-tooltip",
      nodeSelector: ".dat-list__section-dot",
      chipSelector: ".dat-list__section-dot",
      placement: "top",
      offsetY: 8,
      instantText: true
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSectionTooltips);
  } else {
    initSectionTooltips();
  }
  document.addEventListener("turbolinks:load", initSectionTooltips);
})();

(function () {
  "use strict";

  if (window.__datListDetailsBound) {
    return;
  }
  window.__datListDetailsBound = true;

  function updateToggleLabel(button, expanded) {
    button.setAttribute(
      "aria-label",
      expanded ? button.dataset.labelExpanded : button.dataset.labelCollapsed
    );
    const icon = button.querySelector(".material-icons");
    if (icon) {
      icon.textContent = expanded ? "expand_less" : "expand_more";
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dat-detail-toggle]");
    if (!button) {
      return;
    }

    const detailsRow = document.getElementById(button.dataset.detailTarget);
    const panel = detailsRow && detailsRow.querySelector(".dat-list__detail-panel");
    if (!detailsRow || !panel) {
      return;
    }

    event.preventDefault();
    const expanded = button.getAttribute("aria-expanded") !== "true";
    const reducedMotion = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const transitionId = Number(detailsRow.dataset.transitionId || 0) + 1;
    detailsRow.dataset.transitionId = String(transitionId);
    button.setAttribute("aria-expanded", String(expanded));
    detailsRow.setAttribute("aria-hidden", String(!expanded));
    detailsRow.inert = !expanded;
    detailsRow.classList.toggle("is-expanded", expanded);
    updateToggleLabel(button, expanded);

    if (expanded) {
      detailsRow.hidden = false;
      panel.style.maxHeight = "0px";
      if (reducedMotion) {
        panel.style.maxHeight = "none";
        return;
      }
      window.requestAnimationFrame(() => {
        const stillExpanded = detailsRow.classList.contains("is-expanded");
        if (detailsRow.dataset.transitionId === String(transitionId) && stillExpanded) {
          panel.style.maxHeight = `${panel.scrollHeight}px`;
        }
      });
      return;
    }

    if (reducedMotion) {
      panel.style.maxHeight = "0px";
      detailsRow.hidden = true;
      return;
    }
    panel.style.maxHeight = `${panel.getBoundingClientRect().height}px`;
    window.requestAnimationFrame(() => {
      const stillCollapsed = !detailsRow.classList.contains("is-expanded");
      if (detailsRow.dataset.transitionId === String(transitionId) && stillCollapsed) {
        panel.style.maxHeight = "0px";
      }
    });
  });

  document.addEventListener("transitionend", (event) => {
    const panel = event.target;
    if (!panel.matches(".dat-list__detail-panel") || event.propertyName !== "max-height") {
      return;
    }
    const detailsRow = panel.closest(".dat-list__details-row");
    if (!detailsRow) {
      return;
    }
    if (detailsRow.classList.contains("is-expanded")) {
      panel.style.maxHeight = "none";
    } else {
      detailsRow.hidden = true;
    }
  });
})();
