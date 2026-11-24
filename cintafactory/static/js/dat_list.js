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
