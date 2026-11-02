/* global document, window */
(function () {
  "use strict";

  var collapsedClass = "is-collapsed";
  var bodyCollapsedClass = "sidenav-collapsed";
  var mobileQuery = window.matchMedia ? window.matchMedia("(max-width: 850px)") : null;

  function getSidenav() {
    return document.querySelector("dmc-sidenav.app-sidebar");
  }

  function getToggles() {
    return document.querySelectorAll("[data-sidenav-width-toggle]");
  }

  function sync() {
    var sidenav = getSidenav();
    var toggles = getToggles();
    if (!sidenav || !toggles.length) {
      return;
    }

    var collapsed = sidenav.classList.contains(collapsedClass);
    document.body.classList.toggle(bodyCollapsedClass, collapsed);
    for (var i = 0; i < toggles.length; i += 1) {
      var toggle = toggles[i];
      var labelText = collapsed ? toggle.dataset.labelCollapsed : toggle.dataset.labelExpanded;
      var icon = toggle.querySelector(".material-icons");
      var label = toggle.querySelector(".sr-only");

      toggle.setAttribute("aria-expanded", String(!collapsed));
      if (labelText) {
        toggle.setAttribute("aria-label", labelText);
        if (label) {
          label.textContent = labelText;
        }
      }
      if (icon) {
        icon.textContent = collapsed ? "menu" : "menu_open";
      }
    }
  }

  function setCollapsed(collapsed) {
    var sidenav = getSidenav();
    if (!sidenav) {
      return;
    }
    sidenav.classList.toggle(collapsedClass, collapsed);
    sync();
  }

  function initSidenavToggle() {
    var sidenav = getSidenav();
    if (!sidenav || !getToggles().length) {
      return;
    }

    if (mobileQuery) {
      sidenav.classList.toggle(collapsedClass, mobileQuery.matches);
    }
    sync();

    if (window.__cintaSidenavEventsBound) {
      return;
    }
    window.__cintaSidenavEventsBound = true;

    document.addEventListener("click", function (event) {
      var target = event.target;
      if (!target || typeof target.closest !== "function") {
        return;
      }
      if (target.closest("[data-sidenav-width-toggle]")) {
        var currentSidenav = getSidenav();
        if (currentSidenav) {
          setCollapsed(!currentSidenav.classList.contains(collapsedClass));
        }
      } else if (target.closest("[data-sidebar-backdrop]")) {
        setCollapsed(true);
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && mobileQuery && mobileQuery.matches) {
        setCollapsed(true);
      }
    });

    if (mobileQuery) {
      var handleViewportChange = function (event) {
        setCollapsed(event.matches);
      };
      if (mobileQuery.addEventListener) {
        mobileQuery.addEventListener("change", handleViewportChange);
      } else if (mobileQuery.addListener) {
        mobileQuery.addListener(handleViewportChange);
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSidenavToggle);
  } else {
    initSidenavToggle();
  }
  document.addEventListener("turbolinks:load", initSidenavToggle);
})();
