(function () {
  "use strict";

  const DETAIL_HEIGHT_LIMIT = 280;

  function refreshDetailDisclosure() {
    document.querySelectorAll("[data-notification-details]").forEach((content) => {
      const container = content.closest(".wf-notification__details");
      const button = container && container.querySelector("[data-notification-expand]");
      if (!button) return;

      const expanded = button.getAttribute("aria-expanded") === "true";
      content.classList.remove("is-collapsed", "is-expanded");
      const exceedsLimit = content.scrollHeight > DETAIL_HEIGHT_LIMIT;
      const hasBeforeAfter = content.querySelector("[data-notification-before-after]") !== null;

      if (expanded) {
        content.classList.add("is-expanded");
        content.querySelectorAll("[data-notification-before-after]").forEach((panel) => {
          panel.hidden = false;
        });
        button.hidden = false;
        button.textContent = "Afficher moins";
      } else if (exceedsLimit || hasBeforeAfter) {
        content.classList.add("is-collapsed");
        content.querySelectorAll("[data-notification-before-after]").forEach((panel) => {
          panel.hidden = true;
        });
        button.hidden = false;
        button.setAttribute("aria-expanded", "false");
        button.textContent = "Afficher plus";
      } else {
        button.hidden = true;
        button.setAttribute("aria-expanded", "false");
        button.textContent = "Afficher plus";
      }
    });
  }

  document.addEventListener("click", (event) => {
    const expandButton = event.target.closest("[data-notification-expand]");
    if (expandButton) {
      const content = document.getElementById(expandButton.getAttribute("aria-controls"));
      if (!content) return;
      const expanded = expandButton.getAttribute("aria-expanded") === "true";
      content.classList.toggle("is-expanded", !expanded);
      content.classList.toggle("is-collapsed", expanded);
      content.querySelectorAll("[data-notification-before-after]").forEach((panel) => {
        panel.hidden = expanded;
      });
      expandButton.setAttribute("aria-expanded", String(!expanded));
      expandButton.textContent = expanded ? "Afficher plus" : "Afficher moins";
      return;
    }

    const rawButton = event.target.closest("[data-notification-raw-toggle]");
    if (!rawButton) return;
    const rawBlock = document.getElementById(rawButton.getAttribute("aria-controls"));
    if (!rawBlock) return;
    const opening = rawBlock.hidden;
    rawBlock.hidden = !opening;
    rawButton.setAttribute("aria-expanded", String(opening));
    rawButton.setAttribute(
      "aria-label",
      opening ? "Masquer le JSON brut" : "Afficher le JSON brut",
    );
  });

  document.addEventListener("DOMContentLoaded", refreshDetailDisclosure);
  document.addEventListener("turbolinks:load", refreshDetailDisclosure);
  window.addEventListener("resize", refreshDetailDisclosure);
  refreshDetailDisclosure();
})();
