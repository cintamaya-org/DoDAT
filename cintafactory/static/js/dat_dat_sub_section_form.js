(function () {
  function ensureNamespace() {
    if (window.CintaDatEditActionsCard) {
      return window.CintaDatEditActionsCard;
    }
    if (typeof window.IntersectionObserver === "undefined") {
      window.CintaDatEditActionsCard = { scan: function () {} };
      return window.CintaDatEditActionsCard;
    }
    const floatingStates = new Set();
    const requestFrame = window.requestAnimationFrame
      ? window.requestAnimationFrame.bind(window)
      : (callback) => window.setTimeout(callback, 16);
    let resizeScheduled = false;

    function updateFloatingPosition(state) {
      if (
        !state ||
        !state.container ||
        !state.inner ||
        !document.body.contains(state.container)
      ) {
        floatingStates.delete(state);
        return;
      }
      const rect = state.container.getBoundingClientRect();
      state.inner.style.width = rect.width + "px";
      state.inner.style.left = rect.left + "px";
    }

    function scheduleResizeUpdate() {
      if (!floatingStates.size || resizeScheduled) {
        return;
      }
      resizeScheduled = true;
      requestFrame(() => {
        floatingStates.forEach(updateFloatingPosition);
        resizeScheduled = false;
      });
    }

    window.addEventListener("resize", scheduleResizeUpdate);

    function setFloating(state, shouldFloat) {
      if (
        !state ||
        !state.container ||
        !state.inner ||
        !document.body.contains(state.container)
      ) {
        floatingStates.delete(state);
        return;
      }
      if (state.isFloating === shouldFloat) {
        if (shouldFloat) {
          updateFloatingPosition(state);
        }
        return;
      }
      state.isFloating = shouldFloat;
      if (shouldFloat) {
        floatingStates.add(state);
        state.container.style.minHeight = state.inner.offsetHeight + "px";
        state.container.setAttribute("data-actions-floating", "1");
        state.inner.classList.add("is-floating");
        updateFloatingPosition(state);
      } else {
        floatingStates.delete(state);
        state.container.removeAttribute("data-actions-floating");
        state.container.style.minHeight = "";
        state.inner.classList.remove("is-floating");
        state.inner.style.left = "";
        state.inner.style.width = "";
      }
    }

    function initCard(card) {
      if (!card || card.dataset.actionsCardReady === "1") {
        return;
      }
      const inner = card.querySelector(".dat-sub-section-edit-actions__card");
      if (!inner) {
        return;
      }
      card.dataset.actionsCardReady = "1";
      const state = {
        container: card,
        inner,
        observer: null,
        isFloating: false,
      };
      const observer = new IntersectionObserver(([entry]) => {
        if (!entry) {
          return;
        }
        const fullyVisible = entry.intersectionRatio >= 0.999;
        setFloating(state, !fullyVisible);
      }, {threshold: [0.999, 1]});
      observer.observe(card);
      state.observer = observer;
    }

    function scan() {
      document.querySelectorAll("[data-actions-card]").forEach(initCard);
    }

    window.CintaDatEditActionsCard = { scan };
    return window.CintaDatEditActionsCard;
  }

  const runtime = ensureNamespace();
  if (runtime && typeof runtime.scan === "function") {
    runtime.scan();
  }
})();

(function () {
  if (window.CintaDatSchemaBulkImport) {
    return;
  }

  const runtime = {
    fileInput: null,
    pending: null,
  };
  const BUTTON_SELECTOR = ".dat-schema-import-trigger";
  const ACCEPTED_TYPES = ".drawio,.xml,.c4,text/xml,text/plain";

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
      button.dataset.schemaBusy = "1";
      button.disabled = true;
      button.classList.add("disabled");
      button.setAttribute("aria-busy", "true");
    } else {
      button.disabled = false;
      button.classList.remove("disabled");
      button.removeAttribute("aria-busy");
      delete button.dataset.schemaBusy;
    }
  }

  function ensureFileInput() {
    if (runtime.fileInput) {
      return runtime.fileInput;
    }
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ACCEPTED_TYPES;
    input.style.display = "none";
    input.addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      const context = runtime.pending;
      runtime.pending = null;
      if (!context) {
        return;
      }
      if (!file) {
        setButtonBusy(context.button, false);
        return;
      }
      importSchemaFile(context, file);
    });
    document.body.appendChild(input);
    runtime.fileInput = input;
    return input;
  }

  function getRepeater(button) {
    const form = button.closest(".dat-sub-section-form");
    if (!form) {
      return null;
    }
    const target = button.dataset.schemaTarget;
    if (target) {
      const scoped = form.querySelector(`.dat-repeater[data-field-id="${target}"]`);
      if (scoped) {
        return scoped;
      }
    }
    return form.querySelector('.dat-repeater[data-schema-repeater="true"]');
  }

  function guessTitle(file) {
    if (!file || !file.name) {
      return "";
    }
    const name = file.name.replace(/\.[^.]+$/, "");
    return name.trim();
  }

  function isLikeC4File(file) {
    if (!file || !file.name) {
      return false;
    }
    return String(file.name).toLowerCase().endsWith(".c4");
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

  function createDiagram(createUrl, requestedTitle) {
    const headers = {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }
    const payload = requestedTitle ? { title: requestedTitle } : {};
    return fetch(createUrl, {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: JSON.stringify(payload),
    })
      .then((response) => {
        if (!response.ok) {
          return response
            .json()
            .catch(() => ({}))
            .then((data) => {
              const detail = data && (data.message || data.error);
              const message = detail || `Erreur ${response.status}`;
              throw new Error(message);
            });
        }
        return response.json();
      })
      .then((data) => {
        const diagram = data && data.diagram ? data.diagram : null;
        if (!diagram || !diagram.id) {
          throw new Error("Réponse invalide du serveur.");
        }
        return diagram;
      });
  }

  function appendSchemaRow(repeater, diagram) {
    if (!repeater || !diagram) {
      return null;
    }
    if (!window.CintaDatRepeater || typeof window.CintaDatRepeater.appendRow !== "function") {
      showToast("Le tableau des schémas n'est pas prêt.", true);
      return null;
    }
    const initialValues = {
      nom_schema: diagram.title || "",
      schema_systeme: "drawio",
      diagramme_id: String(diagram.id),
    };
    const row = window.CintaDatRepeater.appendRow(repeater, initialValues);
    if (!row) {
      return null;
    }
    const toolInput = row.querySelector('[data-column-key="schema_systeme"]');
    if (toolInput && String(toolInput.value || "").trim().toLowerCase() !== "drawio") {
      toolInput.value = "drawio";
      toolInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const drawioWrapper = row.querySelector("[data-drawio-control=\"true\"]");
    if (drawioWrapper && typeof window.CintaDatRepeater.updateDrawioChip === "function") {
      window.CintaDatRepeater.updateDrawioChip(drawioWrapper, { id: diagram.id, title: diagram.title }, { container: repeater });
    }
    row.classList.add("dat-schema-import-row");
    requestAnimationFrame(() => {
      row.classList.add("dat-schema-import-row--visible");
      setTimeout(() => row.classList.remove("dat-schema-import-row--visible"), 2000);
    });
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    return row;
  }

  function appendLikeC4Row(repeater, file) {
    if (!repeater || !file) {
      return null;
    }
    if (!window.CintaDatRepeater || typeof window.CintaDatRepeater.appendRow !== "function") {
      showToast("Le tableau des schémas n'est pas prêt.", true);
      return null;
    }
    const title = guessTitle(file);
    const initialValues = {
      nom_schema: title,
      schema_systeme: "likec4",
      schema_reference: "",
    };
    const row = window.CintaDatRepeater.appendRow(repeater, initialValues);
    if (!row) {
      return null;
    }
    row.classList.add("dat-schema-import-row");
    requestAnimationFrame(() => {
      row.classList.add("dat-schema-import-row--visible");
      setTimeout(() => row.classList.remove("dat-schema-import-row--visible"), 2000);
    });
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    return row;
  }

  function importSchemaFile(context, file) {
    const { button, repeater, createUrl } = context;
    if (isLikeC4File(file)) {
      const row = appendLikeC4Row(repeater, file);
      if (!row) {
        showToast("Impossible d'ajouter la ligne correspondant au schéma importé.", true);
        setButtonBusy(button, false);
        return;
      }
      const importBtn = row.querySelector(".dat-drawio-import-button");
      if (
        importBtn &&
        window.CintaDatDrawioFileActions &&
        typeof window.CintaDatDrawioFileActions.importFileForButton === "function"
      ) {
        window.CintaDatDrawioFileActions.importFileForButton(importBtn, file)
          .catch((error) => {
            const message = error && error.message ? error.message : "L'import du schéma a échoué.";
            showToast(message, true);
          })
          .finally(() => setButtonBusy(button, false));
        return;
      }
      showToast("Le module d'import LikeC4 est indisponible.", true);
      setButtonBusy(button, false);
      return;
    }
    if (!createUrl) {
      showToast("Création de diagramme indisponible pour ce DAT.", true);
      setButtonBusy(button, false);
      return;
    }
    const requestedTitle = guessTitle(file);
    createDiagram(createUrl, requestedTitle)
      .then((diagram) => {
        const row = appendSchemaRow(repeater, diagram);
        if (!row) {
          throw new Error("Impossible d'ajouter la ligne correspondant au schéma importé.");
        }
        const importBtn = row.querySelector(".dat-drawio-import-button");
        if (
          importBtn &&
          window.CintaDatDrawioFileActions &&
          typeof window.CintaDatDrawioFileActions.importFileForButton === "function"
        ) {
          return window.CintaDatDrawioFileActions.importFileForButton(importBtn, file);
        }
        throw new Error("Le module d'import Draw.io est indisponible.");
      })
      .catch((error) => {
        const message = error && error.message ? error.message : "L'import du schéma a échoué.";
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
    if (button.dataset.schemaBusy === "1") {
      return;
    }
    const repeater = getRepeater(button);
    if (!repeater) {
      showToast("Impossible de trouver le tableau des schémas.", true);
      return;
    }
    const createUrl = repeater.getAttribute("data-drawio-create-url");
    if (!createUrl) {
      showToast("Création de diagramme indisponible pour ce DAT.", true);
      return;
    }
    runtime.pending = { button, repeater, createUrl };
    setButtonBusy(button, true);
    ensureFileInput().click();
  });

  window.CintaDatSchemaBulkImport = true;
})();
