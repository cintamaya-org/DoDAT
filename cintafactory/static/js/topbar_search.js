(function () {
  var MIN_QUERY_LENGTH = 3;
  var DEBOUNCE_MS = 220;

  function debounce(fn, wait) {
    var timeoutId = null;
    var debounced = function () {
      var args = arguments;
      var context = this;
      window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(function () {
        fn.apply(context, args);
      }, wait);
    };
    debounced.cancel = function () {
      window.clearTimeout(timeoutId);
      timeoutId = null;
    };
    return debounced;
  }

  function renderMessage(content, status, message) {
    content.innerHTML = '<p class="topbar-search__results-empty" aria-hidden="true"></p>';
    var messageNode = content.querySelector(".topbar-search__results-empty");
    messageNode.textContent = message;
    status.textContent = message;
  }

  function makeSkeleton(className) {
    var node = document.createElement("span");
    node.className = "cinta-skeleton " + className;
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function renderLoadingSkeleton(content, status) {
    var list = document.createElement("ul");
    list.className = "topbar-search__results-list";
    list.setAttribute("aria-hidden", "true");
    for (var index = 0; index < 3; index += 1) {
      var item = document.createElement("li");
      item.className = "topbar-search__skeleton-item";
      var main = document.createElement("span");
      main.className = "topbar-search__skeleton-main";
      main.appendChild(makeSkeleton("cinta-skeleton--line topbar-search__skeleton-label"));
      main.appendChild(makeSkeleton("cinta-skeleton--line topbar-search__skeleton-subtitle"));
      item.appendChild(main);
      item.appendChild(makeSkeleton("topbar-search__skeleton-badge"));
      list.appendChild(item);
    }
    content.replaceChildren(list);
    status.textContent = "Recherche en cours...";
  }

  function renderResults(content, status, results) {
    if (!results.length) {
      renderMessage(content, status, "Aucun résultat.");
      return;
    }
    var html = '<ul class="topbar-search__results-list">';
    results.forEach(function (result) {
      var typeLabel = result.type === "application" ? "Application" : "DAT";
      html += '<li class="topbar-search__result-item">';
      html += '<a class="topbar-search__result-link" href="' + result.url + '">';
      html += '<span class="topbar-search__result-main">';
      html += '<span class="topbar-search__result-label"></span>';
      html += '<span class="topbar-search__result-subtitle"></span>';
      html += "</span>";
      html += '<span class="topbar-search__result-badge">' + typeLabel + "</span>";
      html += "</a>";
      html += "</li>";
    });
    html += "</ul>";
    content.innerHTML = html;
    var labelNodes = content.querySelectorAll(".topbar-search__result-label");
    var subtitleNodes = content.querySelectorAll(".topbar-search__result-subtitle");
    results.forEach(function (result, index) {
      if (labelNodes[index]) {
        labelNodes[index].textContent = result.label || "";
      }
      if (subtitleNodes[index]) {
        subtitleNodes[index].textContent = result.subtitle || "";
      }
    });
    status.textContent = results.length + (results.length > 1 ? " résultats." : " résultat.");
  }

  function initTopbarSearch() {
    var input = document.getElementById("topbar-search");
    var resultContainer = document.getElementById("topbar-search-results");
    var applicationFilter = document.getElementById("topbar-search-filter-applications");
    var datFilter = document.getElementById("topbar-search-filter-dats");
    if (!input || !resultContainer) {
      return;
    }
    // Turbolinks snapshots clone DOM attributes, not element event listeners.
    // Keep this guard on the live node so restored clones bind again.
    if (input.__cintaTopbarSearchInitialized) {
      return;
    }
    input.__cintaTopbarSearchInitialized = true;

    var endpoint = input.dataset.searchUrl || "";
    var resultContent = resultContainer.querySelector("[data-topbar-search-content]");
    var statusNode = resultContainer.querySelector("[data-topbar-search-status]");
    if (!resultContent) {
      resultContent = document.createElement("div");
      resultContent.setAttribute("data-topbar-search-content", "");
      resultContainer.appendChild(resultContent);
    }
    if (!statusNode) {
      statusNode = document.createElement("p");
      statusNode.className = "sr-only";
      statusNode.setAttribute("data-topbar-search-status", "");
      statusNode.setAttribute("role", "status");
      statusNode.setAttribute("aria-live", "polite");
      resultContainer.insertBefore(statusNode, resultContent);
    }
    var abortController = null;
    var requestVersion = 0;
    var debouncedFetch = null;

    function setBusy(isBusy) {
      resultContainer.setAttribute("aria-busy", String(isBusy));
    }

    function cancelPendingRequest() {
      requestVersion += 1;
      if (debouncedFetch && debouncedFetch.cancel) {
        debouncedFetch.cancel();
      }
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      setBusy(false);
    }

    function closeResults() {
      cancelPendingRequest();
      resultContainer.hidden = true;
      resultContent.replaceChildren();
      statusNode.textContent = "";
    }

    function currentFilters() {
      var includeApplications = applicationFilter ? applicationFilter.checked : true;
      var includeDats = datFilter ? datFilter.checked : true;
      return {
        applications: includeApplications,
        dats: includeDats,
      };
    }

    function fetchResults() {
      var query = (input.value || "").trim();
      var filters = currentFilters();

      if (!query.length) {
        closeResults();
        return;
      }
      if (!filters.applications && !filters.dats) {
        setBusy(false);
        resultContainer.hidden = false;
        renderMessage(resultContent, statusNode, "Sélectionnez au moins un filtre.");
        return;
      }
      if (query.length < MIN_QUERY_LENGTH) {
        setBusy(false);
        resultContainer.hidden = false;
        renderMessage(resultContent, statusNode, "Saisissez au moins 3 caractères.");
        return;
      }
      if (!endpoint) {
        closeResults();
        return;
      }

      if (abortController) {
        abortController.abort();
      }
      abortController = new AbortController();
      var controller = abortController;
      var currentRequestVersion = ++requestVersion;
      setBusy(true);

      var params = new URLSearchParams();
      params.set("q", query);
      params.set("applications", filters.applications ? "1" : "0");
      params.set("dats", filters.dats ? "1" : "0");

      fetch(endpoint + "?" + params.toString(), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("search-request-failed");
          }
          return response.json();
        })
        .then(function (payload) {
          if (currentRequestVersion !== requestVersion) {
            return;
          }
          setBusy(false);
          renderResults(resultContent, statusNode, payload.results || []);
        })
        .catch(function (error) {
          if (currentRequestVersion !== requestVersion) {
            return;
          }
          setBusy(false);
          if (error && error.name === "AbortError") {
            return;
          }
          resultContainer.hidden = false;
          renderMessage(resultContent, statusNode, "Impossible de charger les résultats.");
        });
    }

    debouncedFetch = debounce(fetchResults, DEBOUNCE_MS);
    function onInputChanged() {
      cancelPendingRequest();
      var query = (input.value || "").trim();
      var filters = currentFilters();
      if (!query.length) {
        closeResults();
        return;
      }
      if (!filters.applications && !filters.dats) {
        resultContainer.hidden = false;
        renderMessage(resultContent, statusNode, "Sélectionnez au moins un filtre.");
        return;
      }
      if (query.length < MIN_QUERY_LENGTH) {
        resultContainer.hidden = false;
        renderMessage(resultContent, statusNode, "Saisissez au moins 3 caractères.");
        return;
      }
      resultContainer.hidden = false;
      setBusy(true);
      renderLoadingSkeleton(resultContent, statusNode);
      debouncedFetch();
    }

    input.addEventListener("input", onInputChanged);
    input.addEventListener("focus", function () {
      if ((input.value || "").trim().length) {
        onInputChanged();
      }
    });
    if (applicationFilter) {
      applicationFilter.addEventListener("change", onInputChanged);
    }
    if (datFilter) {
      datFilter.addEventListener("change", onInputChanged);
    }
    document.addEventListener("click", function (event) {
      if (event.target.closest(".topbar-search")) {
        return;
      }
      closeResults();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeResults();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", initTopbarSearch);
  document.addEventListener("turbolinks:load", initTopbarSearch);
})();
