(function () {
  if (window.CintaDatViewer) {
    return;
  }

  const runtime = {
    resourcePromise: null,
    container: null,
    viewer: null,
  };
  const VIEWERJS_CSS_URL = "https://cdnjs.cloudflare.com/ajax/libs/viewerjs/1.11.6/viewer.min.css";
  const VIEWERJS_CSS_INTEGRITY =
    "sha384-k+swX2i6jE7YLgIRI5Gs81wcyww8As59DuESi6wnY3SpqHyNAEIZ+AAcOW4RPLYA";
  const VIEWERJS_SCRIPT_URL = "https://cdnjs.cloudflare.com/ajax/libs/viewerjs/1.11.6/viewer.min.js";
  const VIEWERJS_SCRIPT_INTEGRITY =
    "sha384-JlDbB7XeuzJcCwdtPD4ud7U5dVRWYjL9AnghIKgx9yeidvfELnZcpgqe930fjTHM";
  const EASTER_EGG_DURATION_MS = 3500;
  const EASTER_EGG_INTERVAL_MS = 90;
  const K_CODE_SEQUENCE = ["up", "up", "down", "down", "left", "right", "left", "right", "b", "a"];
  let easterEggInterval = null;
  let KcIndex = 0;
  let KcActive = false;

  function getViewerOverlay() {
    return runtime.viewer && runtime.viewer.viewer;
  }

  function setViewerAccessibility(isVisible) {
    const overlay = getViewerOverlay();
    if (!overlay) {
      return;
    }
    if (isVisible) {
      overlay.removeAttribute("aria-hidden");
      overlay.removeAttribute("inert");
    } else {
      overlay.setAttribute("aria-hidden", "true");
      overlay.setAttribute("inert", "");
      if (overlay.contains(document.activeElement)) {
        document.activeElement.blur();
      }
    }
  }

  function handleViewerShown() {
    setViewerAccessibility(true);
    setKcActive(true);
  }

  function handleViewerHidden() {
    setViewerAccessibility(false);
    setKcActive(false);
    stopEasterEgg();
  }

  function ensureViewerAccessibilityHooks(wrapper) {
    if (!wrapper || wrapper.dataset.viewerA11yBound === "1") {
      return;
    }
    wrapper.dataset.viewerA11yBound = "1";
    wrapper.addEventListener("shown", handleViewerShown);
    wrapper.addEventListener("hidden", handleViewerHidden);
  }

  function stopEasterEgg() {
    if (easterEggInterval) {
      window.clearInterval(easterEggInterval);
      easterEggInterval = null;
    }
    const viewer = runtime.viewer;
    if (viewer) {
      if (typeof viewer.scaleX === "function") {
        viewer.scaleX(1);
      }
      if (typeof viewer.scaleY === "function") {
        viewer.scaleY(1);
      }
    }
  }

  function normalizeKcKey(event) {
    if (!event || typeof event.key !== "string") {
      return "";
    }
    switch (event.key) {
      case "ArrowUp":
        return "up";
      case "ArrowDown":
        return "down";
      case "ArrowLeft":
        return "left";
      case "ArrowRight":
        return "right";
      default:
        return event.key.toLowerCase();
    }
  }

  function handleKcKey(event) {
    if (!KcActive) {
      return;
    }
    const key = normalizeKcKey(event);
    if (!key) {
      return;
    }
    const expected = K_CODE_SEQUENCE[KcIndex];
    if (key === expected) {
      KcIndex += 1;
      if (KcIndex >= K_CODE_SEQUENCE.length) {
        KcIndex = 0;
        startEasterEgg();
      }
      return;
    }
    KcIndex = key === K_CODE_SEQUENCE[0] ? 1 : 0;
  }

  function setKcActive(isActive) {
    if (KcActive === isActive) {
      return;
    }
    KcActive = isActive;
    KcIndex = 0;
    if (KcActive) {
      document.addEventListener("keydown", handleKcKey);
    } else {
      document.removeEventListener("keydown", handleKcKey);
    }
  }

  function startEasterEgg() {
    const viewer = runtime.viewer;
    if (!viewer) {
      return;
    }
    stopEasterEgg();
    const maxTicks = Math.ceil(EASTER_EGG_DURATION_MS / EASTER_EGG_INTERVAL_MS);
    let ticks = 0;
    easterEggInterval = window.setInterval(() => {
      ticks += 1;
      const flipX = Math.random() < 0.5;
      const flipValue = Math.random() < 0.5 ? 1 : -1;
      if (flipX && typeof viewer.scaleX === "function") {
        viewer.scaleX(flipValue);
      } else if (typeof viewer.scaleY === "function") {
        viewer.scaleY(flipValue);
      }
      if (ticks >= maxTicks) {
        stopEasterEgg();
      }
    }, EASTER_EGG_INTERVAL_MS);
  }

  function loadViewerResources() {
    if (typeof window.Viewer !== "undefined") {
      return Promise.resolve();
    }
    if (runtime.resourcePromise) {
      return runtime.resourcePromise;
    }
    runtime.resourcePromise = new Promise((resolve, reject) => {
      const head = document.head || document.getElementsByTagName("head")[0];
      if (!document.getElementById("viewerjs-style")) {
        const link = document.createElement("link");
        link.id = "viewerjs-style";
        link.rel = "stylesheet";
        link.href = VIEWERJS_CSS_URL;
        link.integrity = VIEWERJS_CSS_INTEGRITY;
        link.crossOrigin = "anonymous";
        head.appendChild(link);
      }
      const existingScript = document.getElementById("viewerjs-script");
      const attach = (scriptEl) => {
        scriptEl.addEventListener("load", () => resolve(), { once: true });
        scriptEl.addEventListener(
          "error",
          (error) => {
            runtime.resourcePromise = null;
            reject(error);
          },
          { once: true }
        );
      };
      if (existingScript) {
        attach(existingScript);
        if (existingScript.dataset.loaded === "true") {
          resolve();
        }
        return;
      }
      const script = document.createElement("script");
      script.id = "viewerjs-script";
      script.src = VIEWERJS_SCRIPT_URL;
      script.integrity = VIEWERJS_SCRIPT_INTEGRITY;
      script.crossOrigin = "anonymous";
      script.async = true;
      script.dataset.loaded = "false";
      script.addEventListener("load", () => {
        script.dataset.loaded = "true";
      });
      attach(script);
      head.appendChild(script);
    });
    return runtime.resourcePromise;
  }

  function ensureViewerContainer() {
    if (runtime.container) {
      return runtime.container;
    }
    const wrapper = document.createElement("div");
    wrapper.id = "dat-viewerjs-container";
    wrapper.style.display = "none";
    document.body.appendChild(wrapper);
    runtime.container = { wrapper };
    return runtime.container;
  }

  function normalizeViewerImages(payload, fallbackTitle) {
    if (!Array.isArray(payload)) {
      return [];
    }
    const list = [];
    payload.forEach((entry) => {
      if (typeof entry === "string") {
        list.push({ url: entry, title: fallbackTitle || "" });
        return;
      }
      if (entry && typeof entry.url === "string") {
        list.push({ url: entry.url, title: entry.title || fallbackTitle || "" });
      }
    });
    return list;
  }

  function renderViewerImages(images) {
    const { wrapper } = ensureViewerContainer();
    wrapper.innerHTML = "";
    images.forEach((entry) => {
      const image = document.createElement("img");
      const resolvedTitle = entry.title || "Diagramme";
      image.src = cacheBust(entry.url);
      image.alt = resolvedTitle;
      image.dataset.title = resolvedTitle;
      image.dataset.thumbnailUrl = image.src;
      wrapper.appendChild(image);
    });
    return wrapper;
  }

  function cacheBust(url) {
    const helper = window.CintaDrawioPreview;
    if (helper && typeof helper.withCacheBuster === "function") {
      return helper.withCacheBuster(url);
    }
    if (!url) {
      return url;
    }
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}_=${Date.now()}`;
  }

  function openViewer(diagramId) {
    if (!diagramId) {
      return;
    }
    loadViewerResources()
      .then(() =>
        fetch(`/diagrams/${diagramId}/viewer-context/`, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
      )
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
      })
      .then((data) => {
        const thumbnailUrl = data.diagram?.thumbnail_url;
        const imageUrls = Array.isArray(data.diagram?.image_urls) ? data.diagram.image_urls : [];
        const images = imageUrls.length ? imageUrls : (thumbnailUrl ? [thumbnailUrl] : []);
        if (!images.length) {
          alert("Aucun aperçu n'est disponible pour ce diagramme.");
          return;
        }
        const title = data.diagram?.title || `Diagramme #${diagramId}`;
        openImages(images, title);
      })
      .catch((error) => {
        console.error("Impossible d'afficher le diagramme:", error);
        alert("Affichage du diagramme impossible. Merci de réessayer.");
      });
  }

  function openImages(urls, title) {
    const normalized = normalizeViewerImages(urls, title);
    if (!normalized.length) {
      return;
    }
    loadViewerResources()
      .then(() => {
        const wrapper = renderViewerImages(normalized);
        if (!runtime.viewer) {
          runtime.viewer = new window.Viewer(wrapper, {
            navbar: normalized.length > 1,
            tooltip: false,
            toolbar: {
              zoomIn: 4,
              zoomOut: 4,
              oneToOne: 4,
              reset: 4,
              prev: normalized.length > 1 ? 4 : 0,
              play: normalized.length > 1 ? 4 : 0,
              next: normalized.length > 1 ? 4 : 0,
              rotateLeft: 4,
              rotateRight: 4,
              flipHorizontal: 4,
              flipVertical: 4,
            },
            title: (image) => image.dataset.title || "",
          });
          ensureViewerAccessibilityHooks(wrapper);
          setViewerAccessibility(false);
        } else {
          runtime.viewer.update();
        }
        runtime.viewer.view(0);
        handleViewerShown();
      })
      .catch((error) => {
        console.error("Impossible d'afficher l'aperçu:", error);
        alert("Affichage du diagramme impossible. Merci de réessayer.");
      });
  }

  function openImage(url, title) {
    if (!url) {
      return;
    }
    openImages([url], title);
  }

  window.CintaDatViewer = {
    open: openViewer,
    openImage: openImage,
    openImages: openImages,
  };
})();

(function () {
  if (window.CintaDatDrawioFileActions) {
    return;
  }

  const runtime = {
    fileInput: null,
    activeButton: null,
  };
  const BUTTON_SPINNER_STYLE_ID = "dat-drawio-import-spinner-style";
  const BUTTON_SPINNER_SRC = (function () {
    const cfg = document.getElementById("cinta-drawio-viewer-config");
    if (!cfg) {
      return "";
    }
    return cfg.getAttribute("data-button-spinner-src") || "";
  })();

  function ensureButtonSpinnerStyles() {
    if (document.getElementById(BUTTON_SPINNER_STYLE_ID)) {
      return;
    }
    const style = document.createElement("style");
    style.id = BUTTON_SPINNER_STYLE_ID;
    style.textContent = `
      .dat-drawio-import-button {
        position: relative;
        min-width: min(100%, 140px);
        justify-content: center;
      }
      .dat-drawio-import-button .dat-drawio-button-label {
        display: inline-flex;
        align-items: center;
        gap: 0.25rem;
      }
      .dat-drawio-import-button.is-loading,
      .dat-drawio-import-button.is-loading .material-icons {
        color: transparent !important;
      }
      .dat-drawio-import-button.is-loading .material-icons {
        visibility: hidden;
      }
      .dat-drawio-import-button.is-loading .dat-drawio-button-label {
        display: none;
      }
      .dat-drawio-button-spinner {
        position: absolute;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        pointer-events: none;
      }
      .dat-drawio-button-spinner img {
        width: 28px;
        height: 28px;
        animation: datDrawioButtonSpin 1s ease-in-out infinite;
      }
      .dat-drawio-import-button.is-loading .dat-drawio-button-spinner {
        display: flex;
      }
      @keyframes datDrawioButtonSpin {
        0% { transform: rotate(0deg); }
        70% { transform: rotate(360deg); }
        100% { transform: rotate(360deg); }
      }
    `;
    document.head.appendChild(style);
  }

  function ensureButtonSpinner(button) {
    if (!button) {
      return;
    }
    ensureButtonSpinnerStyles();
    if (button.querySelector(".dat-drawio-button-spinner")) {
      return;
    }
    const spinner = document.createElement("span");
    spinner.className = "dat-drawio-button-spinner";
    const img = document.createElement("img");
    img.src = BUTTON_SPINNER_SRC;
    img.alt = "";
    img.setAttribute("aria-hidden", "true");
    spinner.appendChild(img);
    button.appendChild(spinner);
  }

  function ensureFileInput() {
    if (runtime.fileInput) {
      return runtime.fileInput;
    }
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".drawio,.xml,.c4,text/xml,text/plain";
    input.style.display = "none";
    input.addEventListener("change", (event) => {
      const target = event.target;
      const files = target.files;
      const button = runtime.activeButton;
      runtime.activeButton = null;
      if (!button || !files || !files.length) {
        target.value = "";
        return;
      }
      uploadDiagramFile(button, files[0]);
      target.value = "";
    });
    document.body.appendChild(input);
    runtime.fileInput = input;
    return input;
  }

  function getCsrfToken() {
    const cookieMatch = document.cookie
      .split(";")
      .map((entry) => entry.trim())
      .find((entry) => entry.startsWith("csrftoken="));
    if (cookieMatch) {
      return decodeURIComponent(cookieMatch.split("=", 2)[1]);
    }
    const formInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return formInput && formInput.value ? formInput.value : "";
  }

  function showToast(message, isError) {
    if (window.M && M.toast) {
      M.toast({
        html: message,
        displayLength: 4000,
        classes: isError ? "red darken-2" : "green darken-2",
      });
    } else {
      window.alert(message);
    }
  }

  function setButtonBusy(button, isBusy) {
    if (!button) {
      return;
    }
    if (isBusy) {
      button.dataset.prevDisabled = button.disabled ? "1" : "0";
      button.disabled = true;
      button.classList.add("disabled");
      button.classList.add("is-loading");
      button.dataset.loading = "1";
      if (button.classList.contains("dat-drawio-import-button")) {
        ensureButtonSpinner(button);
        button.setAttribute("aria-busy", "true");
      }
    } else {
      const wasDisabled = button.dataset.prevDisabled === "1";
      if (!wasDisabled) {
        button.disabled = false;
        button.classList.remove("disabled");
      }
      button.classList.remove("is-loading");
      button.removeAttribute("aria-busy");
      delete button.dataset.prevDisabled;
      delete button.dataset.loading;
    }
  }

  function uploadDiagramFile(button, file) {
    if (!button) {
      return Promise.reject(new Error("missing_button"));
    }
    const importUrl = button.dataset.importUrl;
    if (!importUrl) {
      showToast("Import indisponible pour ce diagramme.", true);
      return Promise.reject(new Error("missing_import_url"));
    }
    const isLikeC4Import = button.dataset.likec4Import === "1";
    const fileName = (file && file.name ? String(file.name) : "").toLowerCase();
    const isLikeC4File = fileName.endsWith(".c4");
    if (isLikeC4Import && !isLikeC4File) {
      showToast("Veuillez sélectionner un fichier LikeC4 (.c4).", true);
      return Promise.reject(new Error("invalid_likec4_file"));
    }
    if (!isLikeC4Import && isLikeC4File) {
      showToast("Fichier LikeC4 non compatible avec Draw.io.", true);
      return Promise.reject(new Error("invalid_drawio_file"));
    }
    const formData = new FormData();
    const fallbackName = isLikeC4Import ? "diagram.c4" : "diagram.drawio";
    formData.append("file", file, file.name || fallbackName);
    if (isLikeC4Import && button.dataset.likec4Path) {
      formData.append("path", button.dataset.likec4Path);
    }
    setButtonBusy(button, true);
    const request = fetch(importUrl, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": getCsrfToken(),
      },
    })
      .then((response) => {
        if (!response.ok) {
          return response
            .json()
            .catch(() => ({}))
            .then((payload) => {
              const detailMessage = payload && payload.message ? payload.message : null;
              const code = payload && payload.error ? payload.error : null;
              const details = payload && payload.details ? payload.details : null;
              const rawTag = details && details.raw_tag ? details.raw_tag : null;
              const fallback = code ? `Import impossible: ${code}.` : `Import impossible (HTTP ${response.status}).`;
              const message = detailMessage || fallback;
              if (window.console && typeof console.log === "function") {
                console.log("[drawio import] error", {
                  status: response.status,
                  error: code,
                  message: detailMessage,
                  rawTag,
                  payload,
                });
              }
              throw new Error(message);
            });
        }
        return response.json().catch(() => ({}));
    })
      .then((payload) => {
        const diagramData = payload && payload.diagram ? payload.diagram : {};
        const thumbnailUrl = diagramData.thumbnail_url || diagramData.thumbnailUrl || null;
        if (isLikeC4Import) {
          showToast("Diagramme LikeC4 importé avec succès.");
        } else {
          showToast("Diagramme importé avec succès.");
        }
        if (isLikeC4Import) {
          const likec4Path = payload && (payload.path || payload.storage_path);
          if (likec4Path) {
            button.dataset.likec4Path = likec4Path;
            const row = button.closest("tr");
            if (row) {
              const referenceInput = row.querySelector('[data-column-key="schema_reference"]');
              if (referenceInput) {
                referenceInput.value = likec4Path;
                referenceInput.dispatchEvent(new Event("change", { bubbles: true }));
              }
              const toolInput = row.querySelector('[data-column-key="schema_systeme"]');
              if (toolInput && String(toolInput.value || "").trim().toLowerCase() !== "likec4") {
                toolInput.value = "likec4";
                toolInput.dispatchEvent(new Event("change", { bubbles: true }));
              }
            }
          }
        }
        const detail = {
          diagramId: button.dataset.diagramId || null,
          fileName: file.name || null,
          thumbnailUrl,
          thumbnail_url: thumbnailUrl,
          likec4Path: isLikeC4Import ? (payload && (payload.path || payload.storage_path)) : null,
        };
        document.dispatchEvent(new CustomEvent("dat:diagram-imported", { detail }));
      })
      .catch((error) => {
        const message = error && error.message ? error.message : "Import du diagramme impossible.";
        showToast(message, true);
      })
      .finally(() => setButtonBusy(button, false));
    return request;
  }

  function handleImport(button, event) {
    if (!button || button.disabled || button.classList.contains("disabled")) {
      return;
    }
    const editForm = button.closest(".dat-sub-section-edit-form");
    if (!editForm) {
      showToast("L'import est disponible uniquement en mode édition.", true);
      return;
    }
    if (event) {
      event.preventDefault();
    }
    if (!button.dataset.importUrl) {
      showToast("Import indisponible pour ce diagramme.", true);
      return;
    }
    const input = ensureFileInput();
    runtime.activeButton = button;
    input.value = "";
    input.click();
  }

  function handleExport(button, event) {
    if (!button || button.disabled || button.classList.contains("disabled")) {
      return;
    }
    if (event) {
      event.preventDefault();
    }
    const exportUrl = button.dataset.exportUrl;
    if (!exportUrl) {
      showToast("Export indisponible pour ce diagramme.", true);
      return;
    }
    window.open(exportUrl, "_blank", "noopener");
  }

  document.addEventListener("click", (event) => {
    const importBtn = event.target.closest(".dat-drawio-import-button");
    if (importBtn) {
      handleImport(importBtn, event);
      return;
    }
    const exportBtn = event.target.closest(".dat-drawio-export-button");
    if (exportBtn) {
      handleExport(exportBtn, event);
    }
  });

  window.CintaDatDrawioFileActions = {
    triggerImport(button) {
      handleImport(button);
    },
    triggerExport(button) {
      handleExport(button);
    },
    importFileForButton(button, file) {
      if (!button || !file) {
        return Promise.reject(new Error("missing_import_parameters"));
      }
      return uploadDiagramFile(button, file);
    },
  };
})();

// Draw.io schema parser trigger (architecture -> schemas)
(function () {
  if (window.CintaDatSchemaParser) {
    return;
  }

  const BUTTON_SELECTOR = ".dat-schema-parse-trigger";

  function showToast(message, isError) {
    if (window.M && M.toast) {
      M.toast({
        html: message,
        displayLength: 4000,
        classes: isError ? "red darken-2" : "green darken-2",
      });
    } else {
      window.alert(message);
    }
  }

  function setButtonBusy(button, isBusy) {
    if (!button) {
      return;
    }
    if (isBusy) {
      button.dataset.parseBusy = "1";
      button.disabled = true;
      button.classList.add("disabled");
      button.setAttribute("aria-busy", "true");
    } else {
      button.disabled = false;
      button.classList.remove("disabled");
      button.removeAttribute("aria-busy");
      delete button.dataset.parseBusy;
    }
  }

  function getCsrfToken() {
    const cookieMatch = document.cookie
      .split(";")
      .map((entry) => entry.trim())
      .find((entry) => entry.startsWith("csrftoken="));
    if (cookieMatch) {
      return decodeURIComponent(cookieMatch.split("=", 2)[1]);
    }
    const formInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return formInput && formInput.value ? formInput.value : "";
  }

  function replaceSubSection(slug, html) {
    if (!slug || !html) {
      return;
    }
    const container = document.querySelector(`.dat-sub-section[data-sub-section-slug="${slug}"]`);
    if (!container) {
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    const next = wrapper.firstElementChild;
    if (!next) {
      return;
    }
    container.replaceWith(next);
    next.classList.add("dat-sub-section-updated");
    setTimeout(() => next.classList.remove("dat-sub-section-updated"), 1800);
  }

  function handleParse(button) {
    if (!button) {
      return;
    }
    const parseUrl = button.dataset.parseUrl;
    if (!parseUrl) {
      showToast("Analyse indisponible pour ce DAT.", true);
      return;
    }
    setButtonBusy(button, true);
    fetch(parseUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify({}),
    })
      .then((response) => {
        return response
          .json()
          .catch(() => ({}))
          .then((payload) => {
            if (!response.ok) {
              const message = payload && payload.message ? payload.message : `Erreur ${response.status}`;
              throw new Error(message);
            }
            return payload;
          });
      })
      .then((payload) => {
        const subSections = payload && payload.sub_sections ? payload.sub_sections : {};
        Object.keys(subSections).forEach((slug) => replaceSubSection(slug, subSections[slug]));
        const message = payload && payload.message ? payload.message : "Analyse terminée.";
        showToast(message, false);
      })
      .catch((error) => {
        const message = error && error.message ? error.message : "Analyse impossible.";
        showToast(message, true);
      })
      .finally(() => setButtonBusy(button, false));
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(BUTTON_SELECTOR);
    if (!button) {
      return;
    }
    event.preventDefault();
    if (button.dataset.parseBusy === "1") {
      return;
    }
    handleParse(button);
  });

  window.CintaDatSchemaParser = true;
})();

// Inline Draw.io preview toggler (Afficher/Cacher)
(function () {
  const TOGGLE_SELECTOR = ".dat-drawio-toggle-input";
  const TARGET_SELECTOR = '[data-drawio-table="true"]';
  const PREVIEW_ROW_CLASS = "dat-drawio-preview-row";
  const STYLE_ID = "dat-drawio-preview-style";
  const LOG_PREFIX = "[DAT drawio inline]";
  const previewCache = new Map();
  const editLocks = new Set();

  function logWith(level, message, payload) {
    if (!window.console) {
      return;
    }
    const fn = console[level];
    if (typeof fn !== "function") {
      return;
    }
    if (payload !== undefined) {
      fn(`${LOG_PREFIX} ${message}`, payload);
      return;
    }
    fn(`${LOG_PREFIX} ${message}`);
  }

  function logDebug(message, payload) {
    logWith("info", message, payload);
  }

  function logWarn(message, payload) {
    logWith("warn", message, payload);
  }

  function getSection(element) {
    return element ? element.closest(".dat-sub-section") : null;
  }

  function getSectionSlug(elementOrSection) {
    const section =
      elementOrSection && elementOrSection.classList && elementOrSection.classList.contains("dat-sub-section")
        ? elementOrSection
        : getSection(elementOrSection);
    if (!section || !section.dataset) {
      return null;
    }
    const slug = section.dataset.subSectionSlug || section.dataset.subSection || null;
    return slug ? String(slug) : null;
  }

  function isSectionLocked(section) {
    if (!section) {
      return false;
    }
    const slug = getSectionSlug(section);
    return section.classList.contains("dat-sub-section-editing") || (slug && editLocks.has(slug));
  }

  function escapeSelector(value) {
    if (window.CSS && typeof CSS.escape === "function") {
      return CSS.escape(value);
    }
    return String(value).replace(/(["'#.:\[\],=<>+~*^$|!?(){}])/g, "\\$1");
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .${PREVIEW_ROW_CLASS} td {
        padding-top: 0;
        padding-bottom: 0;
      }
      .dat-drawio-inline-preview {
        margin-bottom: 0.35rem;
        padding: 0.75rem;
        border-radius: 8px;
        border: 1px dashed #cfd8dc;
        background: #f7f9fb;
      }
      .dat-drawio-inline-preview__title {
        margin: 0 0 0.5rem 0;
        font-weight: 600;
        color: #37474f;
        font-size: 0.95rem;
      }
      .dat-drawio-inline-preview__body {
        color: #546e7a;
        font-size: 0.9rem;
      }
      .dat-drawio-inline-preview__image {
        display: block;
        width: 100%;
        max-height: 440px;
        object-fit: contain;
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
      }
      .dat-drawio-inline-preview__image-wrapper + .dat-drawio-inline-preview__image-wrapper {
        margin-top: 0.65rem;
      }
      .dat-drawio-inline-preview__image-label {
        margin: 0 0 0.35rem 0;
        font-size: 0.82rem;
        color: #607d8b;
      }
      .dat-drawio-inline-preview__error {
        color: #c62828;
        margin: 0;
      }
    `;
    document.head.appendChild(style);
  }

  function resolveTarget(toggle) {
    if (!toggle) {
      return null;
    }
    const section = getSection(toggle);
    if (!section) {
      logWarn("Switch miniature sans section parent", {
        dataset: { ...toggle.dataset },
      });
      return null;
    }
    const targetId = toggle.dataset.drawioTarget;
    let target = null;
    if (targetId) {
      const escaped = escapeSelector(targetId);
      target = section.querySelector(`#${escaped}`);
      if (!target) {
        const byId = document.getElementById(targetId);
        if (byId && section.contains(byId)) {
          target = byId;
        }
      }
      if (!target) {
        logWarn("Cible draw.io id introuvable dans la section", {
          targetId,
          sectionId: section.id || null,
        });
      }
    }
    if (!target) {
      target = section.querySelector(TARGET_SELECTOR);
    }
    if (!target) {
      logWarn("Aucune cible draw.io trouvée pour le switch", {
        dataset: { ...toggle.dataset },
      });
    }
    return target;
  }

  function getDiagramDescriptor(row) {
    if (!row) {
      return null;
    }
    const likec4Holder = row.querySelector("[data-likec4-preview-url]");
    if (likec4Holder) {
      const url = likec4Holder.getAttribute("data-likec4-preview-url") || likec4Holder.dataset.likec4PreviewUrl;
      const viewsUrl = likec4Holder.getAttribute("data-likec4-views-url") || likec4Holder.dataset.likec4ViewsUrl;
      if (url) {
        const title =
          likec4Holder.getAttribute("data-likec4-title") ||
          likec4Holder.dataset.likec4Title ||
          "Diagramme LikeC4";
        return {
          key: `likec4:${viewsUrl || url}`,
          type: "likec4",
          url,
          viewsUrl: viewsUrl || null,
          title,
        };
      }
    }
    const holder = row.querySelector("[data-diagram-id]");
    if (!holder) {
      return null;
    }
    const value = holder.getAttribute("data-diagram-id") || holder.dataset.diagramId;
    if (!value) {
      return null;
    }
    const trimmed = String(value).trim();
    if (!trimmed) {
      return null;
    }
    return {
      key: `drawio:${trimmed}`,
      type: "drawio",
      id: trimmed,
      title: `Diagramme #${trimmed}`,
    };
  }

  function setToggleState(toggle, expanded) {
    if (!toggle) {
      return;
    }
    toggle.dataset.drawioExpanded = expanded ? "1" : "0";
    toggle.checked = expanded;
    toggle.setAttribute("aria-checked", expanded ? "true" : "false");
  }

  function getPreviewState(target) {
    if (!target) {
      return { visible: false, loaded: false, domCount: 0 };
    }
    const domCount = target.querySelectorAll(`.${PREVIEW_ROW_CLASS}`).length;
    const visible = target.dataset.drawioPreviewVisible === "1" || domCount > 0;
    const loaded = target.dataset.drawioPreviewLoaded === "1" || domCount > 0;
    return { visible, loaded, domCount };
  }

  function setPreviewState(target, state) {
    if (!target || !state) {
      return;
    }
    if (state.visible !== undefined) {
      target.dataset.drawioPreviewVisible = state.visible ? "1" : "0";
    }
    if (state.loaded !== undefined) {
      target.dataset.drawioPreviewLoaded = state.loaded ? "1" : "0";
    }
  }

  function renderPreview(previewRow, descriptor, payload) {
    if (!previewRow || !previewRow.isConnected) {
      return;
    }
    const diagramId = descriptor && descriptor.id ? descriptor.id : descriptor && descriptor.key ? descriptor.key : "";
    const td = previewRow.querySelector("td") || previewRow.appendChild(document.createElement("td"));
    const sibling = previewRow.nextElementSibling;
    const siblingCells = sibling && sibling.children ? sibling.children.length : 0;
    td.colSpan = Math.max(1, td.colSpan || siblingCells || 1);
    const wrapper = document.createElement("div");
    wrapper.className = "dat-drawio-inline-preview";
    const title = document.createElement("p");
    title.className = "dat-drawio-inline-preview__title";
    title.textContent =
      payload && payload.title
        ? payload.title
        : descriptor && descriptor.title
          ? descriptor.title
          : `Diagramme #${diagramId}`;
    wrapper.appendChild(title);
    const body = document.createElement("div");
    body.className = "dat-drawio-inline-preview__body";
    if (payload && payload.error) {
      logWarn("Erreur lors du rendu de la miniature", {
        diagramId,
        error: payload.error,
      });
      const error = document.createElement("p");
      error.className = "dat-drawio-inline-preview__error";
      error.textContent = "Impossible de charger l'aperçu du diagramme.";
      body.appendChild(error);
    } else if (payload && Array.isArray(payload.images) && payload.images.length) {
      payload.images.forEach((image) => {
        if (!image || !image.src) {
          return;
        }
        const wrapper = document.createElement("div");
        wrapper.className = "dat-drawio-inline-preview__image-wrapper";
        if (image.label) {
          const label = document.createElement("p");
          label.className = "dat-drawio-inline-preview__image-label";
          label.textContent = image.label;
          wrapper.appendChild(label);
        }
        const img = document.createElement("img");
        img.className = "dat-drawio-inline-preview__image";
        img.alt = payload.title || (descriptor && descriptor.title) || `Diagramme #${diagramId}`;
        const helper = window.CintaDrawioPreview;
        const url = image.src;
        img.src = helper && typeof helper.withCacheBuster === "function" ? helper.withCacheBuster(url) : url;
        wrapper.appendChild(img);
        body.appendChild(wrapper);
      });
    } else if (payload && payload.thumbnail) {
      logDebug("Miniature récupérée", {
        diagramId,
        thumbnail: payload.thumbnail,
      });
      const img = document.createElement("img");
      img.className = "dat-drawio-inline-preview__image";
      img.alt = payload.title || (descriptor && descriptor.title) || `Diagramme #${diagramId}`;
      const helper = window.CintaDrawioPreview;
      const url = payload.thumbnail;
      img.src = helper && typeof helper.withCacheBuster === "function" ? helper.withCacheBuster(url) : url;
      body.appendChild(img);
    } else {
      body.textContent = "Aucun aperçu disponible pour ce diagramme.";
    }
    wrapper.appendChild(body);
    td.innerHTML = "";
    td.appendChild(wrapper);
  }

  function renderLoading(previewRow, descriptor) {
    const title = descriptor && descriptor.title ? descriptor.title : "Diagramme";
    renderPreview(previewRow, descriptor, { title, thumbnail: null, loading: true });
    const body = previewRow.querySelector(".dat-drawio-inline-preview__body");
    if (body) {
      body.textContent = "Chargement du diagramme...";
    }
  }

  function ensurePreviewRow(row, previewKey) {
    let previewRow = row.previousElementSibling;
    if (!previewRow || !previewRow.classList.contains(PREVIEW_ROW_CLASS)) {
      previewRow = document.createElement("tr");
      previewRow.className = PREVIEW_ROW_CLASS;
      if (previewKey) {
        previewRow.dataset.previewKey = previewKey;
      }
      const td = document.createElement("td");
      td.colSpan = Math.max(1, row.children.length || 1);
      previewRow.appendChild(td);
      row.parentNode.insertBefore(previewRow, row);
    }
    return previewRow;
  }

  function collectRows(target) {
    let rows = [];
    const nestedBodies = target.querySelectorAll("td .responsive-table table tbody");
    if (nestedBodies.length) {
      nestedBodies.forEach((tbody) => {
        rows = rows.concat(
          Array.from(tbody.querySelectorAll("tr")).filter(
            (row) => !row.classList.contains(PREVIEW_ROW_CLASS) && getDiagramDescriptor(row)
          )
        );
      });
    } else {
      rows = Array.from(target.querySelectorAll("table tbody tr")).filter(
        (row) => !row.classList.contains(PREVIEW_ROW_CLASS) && getDiagramDescriptor(row)
      );
    }
    return rows;
  }

  function likec4LabelFromUrl(url, index) {
    if (!url) {
      return `Vue ${index + 1}`;
    }
    const marker = "/views/";
    const pos = url.indexOf(marker);
    if (pos !== -1) {
      return url.slice(pos + marker.length);
    }
    const parts = url.split("/");
    const last = parts[parts.length - 1] || "";
    return last || `Vue ${index + 1}`;
  }

  function fetchPreview(descriptor) {
    if (!descriptor) {
      return Promise.resolve({ error: new Error("missing_descriptor") });
    }
    const key = String(descriptor.key || "");
    if (previewCache.has(key)) {
      logDebug("Utilisation du cache pour la miniature", { diagramId: key });
      return previewCache.get(key);
    }
    let promise = null;
    if (descriptor.type === "likec4") {
      logDebug("Chargement de la miniature LikeC4", { diagramId: key });
      if (descriptor.viewsUrl) {
        promise = fetch(descriptor.viewsUrl, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        })
          .then((response) => {
            if (!response.ok) {
              throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
          })
          .then((data) => {
            const paths = Array.isArray(data && data.paths) ? data.paths : [];
            const images = paths.map((path, index) => ({
              src: path,
              label: likec4LabelFromUrl(path, index),
            }));
            const fallback = data && data.thumbnail_url ? data.thumbnail_url : descriptor.url || null;
            return {
              title: descriptor.title || "Diagramme LikeC4",
              images,
              thumbnail: images.length ? null : fallback,
            };
          })
          .catch((error) => {
            logWarn("Echec du chargement des vues LikeC4", {
              diagramId: key,
              error,
            });
            return {
              title: descriptor.title || "Diagramme LikeC4",
              thumbnail: descriptor.url || null,
            };
          });
      } else {
        promise = Promise.resolve({
          title: descriptor.title || "Diagramme LikeC4",
          thumbnail: descriptor.url || null,
        });
      }
    } else {
      logDebug("Chargement du contexte viewer pour miniature", { diagramId: key });
      promise = fetch(`/diagrams/${descriptor.id}/viewer-context/`, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          return response.json();
        })
        .then((data) => {
          const diagram = data && data.diagram ? data.diagram : {};
          const imageUrls = Array.isArray(diagram.image_urls) ? diagram.image_urls : [];
          const images = imageUrls.map((url, index) => ({
            src: url,
            label: `Page ${index + 1}`,
          }));
          logDebug("Contexte preview chargé", {
            diagramId: key,
            hasThumbnail: Boolean(diagram.thumbnail_url || diagram.thumbnail),
          });
          return {
            title: diagram.title || descriptor.title || `Diagramme #${descriptor.id}`,
            images,
            thumbnail: images.length ? null : diagram.thumbnail_url || diagram.thumbnail || null,
          };
        })
        .catch((error) => {
          logWarn("Echec du chargement du contexte preview", {
            diagramId: key,
            error,
          });
          return { error: error || new Error("preview_load_failed") };
        });
    }
    previewCache.set(key, promise);
    return promise;
  }

  function showPreviews(target, toggle) {
    if (!target) {
      return;
    }
    ensureStyle();
    const rows = collectRows(target);
    const descriptors = rows.map((row) => getDiagramDescriptor(row)).filter(Boolean);
    logDebug("Préparation de l'affichage des miniatures", {
      targetId: target.id || null,
      rows: rows.length,
      diagramIds: descriptors.map((descriptor) => descriptor.key),
    });
    if (!rows.length || !descriptors.length) {
      logWarn("Aucune ligne éligible pour affichage des miniatures", {
        targetId: target.id || null,
      });
    }
    const tasks = [];
    rows.forEach((row) => {
      const descriptor = getDiagramDescriptor(row);
      if (!descriptor) {
        logWarn("Ligne ignorée car aucun diagramme détecté", {
          targetId: target.id || null,
        });
        return;
      }
      const previewRow = ensurePreviewRow(row, descriptor.key);
      renderLoading(previewRow, descriptor);
      tasks.push(
        fetchPreview(descriptor).then((payload) => {
          renderPreview(previewRow, descriptor, payload);
        })
      );
    });
    logDebug("Affichage des miniatures demandé", {
      targetId: target.id || null,
      rows: rows.length,
    });
    setPreviewState(target, { visible: true, loaded: true });
    setToggleState(toggle, true);
    return Promise.allSettled(tasks).then(() => {
      setPreviewState(target, { visible: true, loaded: true });
      setToggleState(toggle, true);
    });
  }

  function hidePreviews(target, toggle) {
    if (!target) {
      logWarn("Masquage des miniatures sans wrapper cible");
      setToggleState(toggle, false);
      return;
    }
    const toRemove = target.querySelectorAll(`.${PREVIEW_ROW_CLASS}`);
    const count = toRemove.length;
    toRemove.forEach((row) => row.remove());
    logDebug("Masquage des miniatures demandé", {
      targetId: target.id || null,
      removedRows: count,
    });
    setPreviewState(target, { visible: false, loaded: false });
    setToggleState(toggle, false);
  }

  function initToggle(toggle) {
    if (!toggle) {
      return;
    }
    const section = getSection(toggle);
    if (isSectionLocked(section)) {
      logDebug("Switch ignoré car section en édition", {
        slug: getSectionSlug(section),
        dataset: { ...toggle.dataset },
      });
      return;
    }
    const target = resolveTarget(toggle);
    if (!target) {
      logWarn("Impossible d'initialiser le switch draw.io sans cible", {
        dataset: { ...toggle.dataset },
      });
      return;
    }
    const state = getPreviewState(target);
    if (state.domCount > 0 && !state.visible) {
      setPreviewState(target, { visible: true, loaded: true });
    } else if (state.domCount === 0 && (target.dataset.drawioPreviewVisible || target.dataset.drawioPreviewLoaded)) {
      setPreviewState(target, { visible: false, loaded: false });
    }
    setToggleState(toggle, state.visible);
  }

  function initAll() {
    document.querySelectorAll(TOGGLE_SELECTOR).forEach(initToggle);
  }

  document.addEventListener("change", (event) => {
    const toggle = event.target.closest(TOGGLE_SELECTOR);
    if (!toggle) {
      return;
    }
    logDebug("--------------------------");
    logDebug("Changement de switch miniature", {
      checked: toggle.checked,
      targetDataset: { ...toggle.dataset },
    });
    const target = resolveTarget(toggle);
    if (!target) {
      logWarn("Interaction sur le switch draw.io sans cible associée", {
        dataset: { ...toggle.dataset },
      });
      return;
    }
    logDebug("Cible trouvée pour la miniature", {
      targetId: target.id || null,
      targetTag: target.tagName || null,
    });
    const section = getSection(toggle);
    if (isSectionLocked(section)) {
      logWarn("Clic ignoré car la section est en édition", {
        slug: getSectionSlug(section),
        targetId: target.id || null,
      });
      return;
    }
    logDebug("Section ok, action autorisée", {
      slug: getSectionSlug(section),
      sectionId: section ? section.id || null : null,
    });
    const state = getPreviewState(target);
    logDebug("Etat miniatures avant action", {
      visible: state.visible,
      loaded: state.loaded,
      domRows: state.domCount,
      targetId: target.id || null,
    });
    if (toggle.checked) {
      logDebug("Action choisie: afficher les miniatures", {
        targetId: target.id || null,
      });
      showPreviews(target, toggle);
    } else {
      logDebug("Action choisie: masquer les miniatures", {
        targetId: target.id || null,
      });
      hidePreviews(target, toggle);
    }
    logDebug("Action terminée (appel lancée)", {
      expandedBefore: state.visible,
      targetId: target.id || null,
    });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll, { once: true });
  } else {
    initAll();
  }
  document.addEventListener("turbolinks:load", initAll);

  function lockSection(slug) {
    if (!slug) {
      return;
    }
    editLocks.add(slug);
    const section = document.querySelector(`.dat-sub-section[data-sub-section-slug=\"${slug}\"]`);
    if (!section) {
      return;
    }
    const toggle = section.querySelector(TOGGLE_SELECTOR);
    const target = section.querySelector(TARGET_SELECTOR);
    if (toggle) {
      toggle.dataset.drawioLocked = "1";
      toggle.disabled = true;
      toggle.setAttribute("aria-disabled", "true");
    }
    if (target) {
      hidePreviews(target, toggle);
    }
  }

  function unlockSection(slug) {
    if (!slug) {
      return;
    }
    editLocks.delete(slug);
    const section = document.querySelector(`.dat-sub-section[data-sub-section-slug=\"${slug}\"]`);
    if (!section) {
      return;
    }
    const toggle = section.querySelector(TOGGLE_SELECTOR);
    if (toggle) {
      toggle.disabled = false;
      toggle.removeAttribute("aria-disabled");
      toggle.dataset.drawioLocked = "0";
      initToggle(toggle);
    }
  }

  document.addEventListener("dat:section-edit-start", (event) => {
    const slug = event && event.detail && event.detail.slug ? String(event.detail.slug) : null;
    console.log(`[${Date.now()}] TIME_TIME`); 
    logDebug("Verrouillage des miniatures pour section en édition", { slug });
    lockSection(slug);
  });

  document.addEventListener("dat:section-edit-end", (event) => {
    const slug = event && event.detail && event.detail.slug ? String(event.detail.slug) : null;
    logDebug("Déverrouillage des miniatures après édition", { slug });
    unlockSection(slug);
  });
})();
