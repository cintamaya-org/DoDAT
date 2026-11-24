(function () {
  if (window.__datSubSectionToggleBound) {
    return;
  }
  window.__datSubSectionToggleBound = true;

  const activeAnimations = new WeakMap();

  function setExpanded(button, content, expanded, animate) {
    const height = content.getBoundingClientRect().height;
    const opacity = content.hidden ? 0 : getComputedStyle(content).opacity;
    activeAnimations.get(content)?.cancel();
    activeAnimations.delete(content);

    button.setAttribute("aria-expanded", String(expanded));
    content.inert = !expanded;
    if (!animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      content.hidden = !expanded;
      return;
    }

    content.hidden = false;
    const targetHeight = expanded ? content.getBoundingClientRect().height : 0;
    const animation = content.animate([
      { height: `${height}px`, opacity, overflow: "hidden" },
      { height: `${targetHeight}px`, opacity: expanded ? 1 : 0, overflow: "hidden" },
    ], { duration: 240, easing: "ease-in-out", fill: "both" });

    activeAnimations.set(content, animation);
    animation.onfinish = () => {
      content.hidden = !expanded;
      animation.cancel();
      activeAnimations.delete(content);
    };
  }

  // Delegation keeps toggles working after inline saves replace subsection cards.
  document.addEventListener("click", (event) => {
    const editButton = event.target.closest(".dat-subsection-edit-btn");
    if (editButton && editButton.classList.contains("disabled")) {
      return;
    }
    const button = editButton
      ? editButton.closest(".dat-sub-section")?.querySelector(".dat-sub-section__toggle")
      : event.target.closest(".dat-sub-section__toggle");
    if (!button) {
      return;
    }
    const content = document.getElementById(button.getAttribute("aria-controls"));
    if (!content) {
      return;
    }

    const expanded = Boolean(editButton) || button.getAttribute("aria-expanded") !== "true";
    setExpanded(button, content, expanded, !editButton);
  });
})();
