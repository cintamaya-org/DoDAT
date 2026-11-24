(function () {
  var DEBOUNCE_MS = 250;
  var DEFAULT_MAX_RESULTS = 30;

  function debounce(callback, delay) {
    var timeoutId = null;
    return function () {
      var args = arguments;
      window.clearTimeout(timeoutId);
      timeoutId = window.setTimeout(function () {
        callback.apply(null, args);
      }, delay);
    };
  }

  function initRemoteSelect(select) {
    // Turbolinks snapshots clone DOM attributes, not element event listeners.
    // A node property lets restored clones bind again without duplicating controls.
    if (select.__cintaRemoteSelectInitialized) {
      return;
    }
    select.__cintaRemoteSelectInitialized = true;
    select.classList.add("browser-default", "remote-select__control");

    var endpoint = select.dataset.remoteSelectUrl || "";
    if (!endpoint) {
      return;
    }
    var configuredLimit = Number.parseInt(select.dataset.remoteSelectLimit || "", 10);
    var maxResults = Number.isFinite(configuredLimit) && configuredLimit > 0
      ? Math.min(configuredLimit, DEFAULT_MAX_RESULTS)
      : DEFAULT_MAX_RESULTS;
    var emptyOption = Array.from(select.options).find(function (option) {
      return option.value === "";
    });
    var emptyLabel = emptyOption ? emptyOption.text : "---------";

    var search = select.previousElementSibling;
    if (!search || !search.classList.contains("remote-select__search")) {
      search = document.createElement("input");
      select.parentNode.insertBefore(search, select);
    }
    search.type = "search";
    search.autocomplete = "off";
    search.className = "remote-select__search";
    search.placeholder = select.dataset.remoteSelectPlaceholder || "Rechercher...";
    search.setAttribute("aria-label", search.placeholder);

    var status = select.nextElementSibling;
    if (!status || !status.classList.contains("remote-select__status")) {
      status = document.createElement("small");
      select.insertAdjacentElement("afterend", status);
    }
    status.className = "remote-select__status text-muted";
    status.setAttribute("aria-live", "polite");
    status.setAttribute("role", "status");

    var abortController = null;
    var loadingTimer = null;
    var requestVersion = 0;

    function clearLoadingTimer() {
      if (loadingTimer !== null) {
        window.clearTimeout(loadingTimer);
        loadingTimer = null;
      }
    }

    function showLoadingSkeleton() {
      status.replaceChildren();
      var announcement = document.createElement("span");
      announcement.className = "sr-only";
      announcement.textContent = "Recherche en cours...";
      var skeleton = document.createElement("span");
      skeleton.className = "cinta-skeleton cinta-skeleton--line remote-select__skeleton-line";
      skeleton.setAttribute("aria-hidden", "true");
      status.append(announcement, skeleton);
    }

    function replaceOptions(options, hasMore) {
      var selectedValue = select.value;
      var selectedOption = Array.from(select.options).find(function (option) {
        return option.value === selectedValue;
      });
      var selectedLabel = selectedOption ? selectedOption.text : "";
      var normalized = Array.isArray(options) ? options.slice(0, maxResults) : [];

      select.replaceChildren();
      select.add(new Option(emptyLabel, ""));

      var insertedValues = new Set();
      if (selectedValue) {
        select.add(new Option(selectedLabel || selectedValue, selectedValue, true, true));
        insertedValues.add(selectedValue);
      }
      normalized.forEach(function (item) {
        if (insertedValues.size >= maxResults) {
          return;
        }
        var value = String(item.value !== undefined ? item.value : item.id || "");
        if (!value || insertedValues.has(value)) {
          return;
        }
        var label = String(item.label !== undefined ? item.label : item.name || value);
        select.add(new Option(label, value));
        insertedValues.add(value);
      });
      select.value = selectedValue || "";
      status.textContent = insertedValues.size + " résultat" + (insertedValues.size > 1 ? "s" : "")
        + (hasMore ? " — affinez la recherche" : "");
      status.setAttribute("aria-busy", "false");
      select.dispatchEvent(new Event("remote-options-loaded", { bubbles: true }));
    }

    function fetchOptions() {
      clearLoadingTimer();
      if (abortController) {
        abortController.abort();
      }
      var currentRequestVersion = ++requestVersion;
      abortController = new AbortController();
      var controller = abortController;
      status.textContent = "Recherche en cours...";
      status.setAttribute("aria-busy", "true");
      loadingTimer = window.setTimeout(function () {
        if (currentRequestVersion === requestVersion) {
          showLoadingSkeleton();
        }
      }, 300);
      var params = new URLSearchParams();
      params.set("q", (search.value || "").trim());

      fetch(endpoint + "?" + params.toString(), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("remote-select-request-failed");
          }
          return response.json();
        })
        .then(function (payload) {
          if (currentRequestVersion !== requestVersion) {
            return;
          }
          clearLoadingTimer();
          replaceOptions(payload.options || [], Boolean(payload.has_more));
        })
        .catch(function (error) {
          if (currentRequestVersion !== requestVersion) {
            return;
          }
          clearLoadingTimer();
          if (error && error.name === "AbortError") {
            return;
          }
          status.textContent = "Chargement impossible.";
          status.setAttribute("aria-busy", "false");
        });
    }

    var debouncedFetch = debounce(fetchOptions, DEBOUNCE_MS);
    search.addEventListener("input", function () {
      clearLoadingTimer();
      requestVersion += 1;
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      status.textContent = "Recherche en cours...";
      status.setAttribute("aria-busy", "true");
      debouncedFetch();
    });
    fetchOptions();
  }

  function initRemoteSelects() {
    document.querySelectorAll("select[data-remote-select-url]").forEach(initRemoteSelect);
  }

  document.addEventListener("DOMContentLoaded", initRemoteSelects);
  document.addEventListener("turbolinks:load", initRemoteSelects);
})();
