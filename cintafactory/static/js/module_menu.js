"use strict";

(function () {
  function normalizePath(path) {
    if (typeof window === "undefined" || !path) {
      return null;
    }
    try {
      var url = new URL(path, window.location.origin);
      var pathname = url.pathname || "/";
      if (pathname.length > 1 && pathname.endsWith("/")) {
        var end = pathname.length;
        while (end > 1 && pathname[end - 1] === "/") {
          end -= 1;
        }
        pathname = pathname.slice(0, end);
      }
      return pathname || "/";
    } catch (error) {
      return null;
    }
  }

  function pathsMatch(current, candidate) {
    if (!current || !candidate) {
      return false;
    }
    if (candidate === "/") {
      return current === "/";
    }
    return current === candidate || current.indexOf(candidate + "/") === 0;
  }

  function menuShouldAutoExpand(root, body) {
    if (typeof window === "undefined" || !body) {
      return false;
    }
    var currentPath = normalizePath(window.location.pathname);
    if (!currentPath) {
      return false;
    }

    var prefix = normalizePath(root.getAttribute("data-module-menu-prefix"));
    if (pathsMatch(currentPath, prefix)) {
      return true;
    }

    if (body.querySelector("[data-menu-active],[aria-current='page'],.is-active")) {
      return true;
    }

    var prefixedNodes = body.querySelectorAll("[data-menu-match-prefix]");
    for (var i = 0; i < prefixedNodes.length; i += 1) {
      var customPrefix = normalizePath(prefixedNodes[i].getAttribute("data-menu-match-prefix"));
      if (pathsMatch(currentPath, customPrefix)) {
        return true;
      }
    }

    var links = body.querySelectorAll("a[href]");
    for (var j = 0; j < links.length; j += 1) {
      var href = links[j].getAttribute("href");
      if (!href || href[0] === "#" || href.indexOf("javascript:") === 0) {
        continue;
      }
      var linkPath = normalizePath(href);
      if (pathsMatch(currentPath, linkPath)) {
        return true;
      }
    }

    return false;
  }

  function setExpanded(toggle, expanded) {
    var root = toggle.closest("[data-module-menu]");
    if (!root) {
      return;
    }
    var body = root.querySelector("[data-module-menu-body]");
    toggle.setAttribute("aria-expanded", expanded);
    toggle.classList.toggle("is-open", expanded);
    root.classList.toggle("is-expanded", expanded);
    if (body) {
      if (expanded) {
        body.removeAttribute("hidden");
      } else {
        body.setAttribute("hidden", "hidden");
      }
    }
    var icon = toggle.querySelector(".module-menu__toggle-icon");
    if (icon) {
      icon.style.transform = expanded ? "rotate(180deg)" : "";
    }
  }

  function initMenu(root) {
    if (!root || root.__cintaModuleMenuReady) {
      return;
    }
    var toggle = root.querySelector("[data-module-menu-toggle]");
    var body = root.querySelector("[data-module-menu-body]");
    if (!toggle || !body) {
      return;
    }
    root.__cintaModuleMenuReady = true;
    var initial = root.getAttribute("data-module-menu-initial");
    var startOpen = initial === "open";
    if (!startOpen && menuShouldAutoExpand(root, body)) {
      startOpen = true;
    }
    setExpanded(toggle, startOpen);
    toggle.addEventListener("click", function () {
      var expanded = toggle.getAttribute("aria-expanded") === "true";
      setExpanded(toggle, !expanded);
    });
  }

  function applyMenuIconClass() {
    var icons = document.querySelectorAll(".module-menu .material-icons");
    Array.prototype.forEach.call(icons, function (icon) {
      if (!icon.classList.contains("icon-custom-menu")) {
        icon.classList.add("icon-custom-menu");
      }
    });
  }

  function initAll() {
    var moduleItems = document.querySelectorAll(".sidenav-modules__list > li");
    Array.prototype.forEach.call(moduleItems, function (item) {
      if (!item.innerHTML.trim()) {
        item.remove();
      }
    });

    var menus = document.querySelectorAll("[data-module-menu]");
    Array.prototype.forEach.call(menus, initMenu);
    applyMenuIconClass();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  if (typeof window !== "undefined" && !window.__cintaModuleMenuEventsBound) {
    window.__cintaModuleMenuEventsBound = true;
    document.addEventListener("turbolinks:load", initAll);
  }
})();
