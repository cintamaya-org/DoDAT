window.CintaDatRepeater = window.CintaDatRepeater || (function () {
  const runtime = {
    drawioModal: null,
    drawioState: null,
    drawioListenerAttached: false,
    likec4ListenerAttached: false,
    likec4Context: null,
  };
  const LIKEC4_EMBED_URL = (function () {
    const host = document.querySelector(".dat-repeater[data-likec4-embed-url]");
    return host ? host.getAttribute("data-likec4-embed-url") || "" : "";
  })();
  const LIKEC4_ORIGIN = (() => {
    try {
      return new URL(LIKEC4_EMBED_URL, window.location.origin).origin;
    } catch (error) {
      return null;
    }
  })();

  function getLoadingSpinnerMarkup() {
    const template = document.querySelector("template[data-dat-loading-spinner-template]");
    return template ? template.innerHTML : "";
  }


  function parseJSON(value) {
    if (!value) {
      return [];
    }
    try {
      const parsed = typeof value === "string" ? JSON.parse(value) : value;
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      console.warn("Impossible de parser la valeur du tableau dynamique:", error);
      return [];
    }
  }

  function setIconLabel(button, iconName, label) {
    const icon = document.createElement("i");
    icon.className = "material-icons left";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = iconName;
    button.replaceChildren(icon, document.createTextNode(String(label ?? "")));
  }

  function isRenderOnlyColumn(column) {
    return Boolean(column.render_only || column.renderOnly || column.render === "drawio_actions");
  }

  function serialiseRows(tbody, columns) {
    const rows = [];
    tbody.querySelectorAll("tr").forEach((row) => {
      const payload = {};
      columns.forEach((column) => {
        if (isRenderOnlyColumn(column)) {
          return;
        }
        const input = row.querySelector(`[data-column-key="${column.key}"]`);
        payload[column.key] = input ? input.value : "";
      });
      rows.push(payload);
    });
    return rows;
  }

  function syncHiddenInput(container, columns) {
    const input = container.querySelector("input[type=hidden]");
    const tbody = container.querySelector("tbody");
    const rows = serialiseRows(tbody, columns);
    input.value = JSON.stringify(rows);
  }

  function updateRowOrderControls(tbody) {
    if (!tbody) {
      return;
    }
    const rows = Array.from(tbody.querySelectorAll("tr"));
    rows.forEach((row, index) => {
      const moveUp = row.querySelector(".dat-repeater-move-up");
      const moveDown = row.querySelector(".dat-repeater-move-down");
      if (moveUp) {
        const disabled = index === 0;
        moveUp.disabled = disabled;
        moveUp.classList.toggle("disabled", disabled);
      }
      if (moveDown) {
        const disabled = index === rows.length - 1;
        moveDown.disabled = disabled;
        moveDown.classList.toggle("disabled", disabled);
      }
    });
  }

  function moveRow(row, direction, container, columns) {
    if (!row) {
      return;
    }
    const tbody = row.closest("tbody");
    if (!tbody) {
      return;
    }
    if (direction === "up") {
      const previous = row.previousElementSibling;
      if (!previous) {
        return;
      }
      tbody.insertBefore(row, previous);
    } else {
      const next = row.nextElementSibling;
      if (!next) {
        return;
      }
      tbody.insertBefore(row, next.nextElementSibling);
    }
    syncHiddenInput(container, columns);
    updateRowOrderControls(tbody);
  }


  function buildDrawioUrl(template, id) {
    if (!template || !id) {
      return "";
    }
    return template.replace("{id}", id);
  }

  function buildLikeC4Url(baseUrl, filePath) {
    if (!baseUrl || !filePath) {
      return "";
    }
    const url = new URL(baseUrl, window.location.origin);
    url.searchParams.set("file", filePath);
    return url.toString();
  }

  function buildLikeC4PngUrl(prefix, filePath) {
    if (!prefix || !filePath) {
      return "";
    }
    let normalizedPrefix = String(prefix);
    while (normalizedPrefix.endsWith("/")) {
      normalizedPrefix = normalizedPrefix.slice(0, -1);
    }
    if (normalizedPrefix.includes("/likec4/png")) {
      return buildLikeC4Url(normalizedPrefix, filePath);
    }
    const segments = String(filePath).replace(/^\/+/, "").split("/");
    if (segments.length >= 3 && segments[0] === "diagrams") {
      const folder = segments[1];
      if (!folder) {
        return "";
      }
      return `${normalizedPrefix}/${encodeURIComponent(folder)}/views/thumb.png`;
    }
    const filename = segments[segments.length - 1] || "";
    const base = filename.replace(/\.c4$/i, "");
    if (!base) {
      return "";
    }
    return `${normalizedPrefix}/likec4/${encodeURIComponent(base)}/views/thumb.png`;
  }

  function updateDrawioChip(wrapper, diagram, options) {
    const opts = options || {};
    const container = opts.container || wrapper.closest(".dat-repeater");
    const chip = wrapper.querySelector(".dat-repeater-drawio-chip");
    if (!chip) {
      return;
    }
    const row = wrapper.closest("tr");
    const toolInput = row ? row.querySelector('[data-column-key="schema_systeme"]') : null;
    const toolValue = toolInput ? String(toolInput.value || toolInput.textContent || "").trim().toLowerCase() : "";
    const referenceInput = row ? row.querySelector('[data-column-key="schema_reference"]') : null;
    const referenceValue = referenceInput ? String(referenceInput.value || referenceInput.textContent || "").trim() : "";
    const identifier = diagram && diagram.id ? String(diagram.id).trim() : "";
    const title = diagram && diagram.title ? diagram.title : null;
    if (toolValue === "likec4") {
      chip.textContent = "Diagramme LikeC4";
      chip.title = referenceValue || "";
      chip.classList.remove("grey", "lighten-3");
      chip.classList.add("teal", "lighten-5", "teal-text", "text-darken-3");
    } else if (identifier) {
      chip.textContent = title ? `${title} (#${identifier})` : `Diagramme #${identifier}`;
      chip.title = "";
      chip.classList.remove("grey", "lighten-3");
      chip.classList.add("blue", "lighten-5", "blue-text", "text-darken-4");
    } else {
      chip.textContent = "Aucun diagramme";
      chip.title = "";
      chip.classList.add("grey", "lighten-3");
      chip.classList.remove("blue", "lighten-5", "blue-text", "text-darken-4");
      chip.classList.remove("teal", "lighten-5", "teal-text", "text-darken-3");
    }

    const viewBtn = wrapper.querySelector(".dat-repeater-drawio-view");
    if (viewBtn) {
      const likec4Prefix = container ? container.getAttribute("data-likec4-png-public-prefix") : null;
      const likec4PngUrl = buildLikeC4PngUrl(likec4Prefix, referenceValue);
      const likec4ViewsTemplate = container ? container.getAttribute("data-likec4-views-template") : null;
      const likec4ViewsUrl = buildLikeC4Url(likec4ViewsTemplate, referenceValue);
      if (toolValue === "likec4" && referenceValue && (likec4PngUrl || likec4ViewsUrl)) {
        viewBtn.disabled = false;
        viewBtn.classList.remove("disabled");
        if (likec4PngUrl) {
          viewBtn.dataset.likec4PreviewUrl = likec4PngUrl;
        } else {
          delete viewBtn.dataset.likec4PreviewUrl;
        }
        if (likec4ViewsUrl) {
          viewBtn.dataset.likec4ViewsUrl = likec4ViewsUrl;
        } else {
          delete viewBtn.dataset.likec4ViewsUrl;
        }
        delete viewBtn.dataset.diagramId;
      } else if (identifier) {
        viewBtn.disabled = false;
        viewBtn.classList.remove("disabled");
        viewBtn.dataset.diagramId = identifier;
        delete viewBtn.dataset.likec4PreviewUrl;
        delete viewBtn.dataset.likec4ViewsUrl;
      } else {
        viewBtn.disabled = true;
        viewBtn.classList.add("disabled");
        delete viewBtn.dataset.diagramId;
        delete viewBtn.dataset.likec4PreviewUrl;
        delete viewBtn.dataset.likec4ViewsUrl;
      }
    }

    const importBtn = wrapper.querySelector(".dat-drawio-import-button");
    if (importBtn) {
      const importTemplate = container ? container.getAttribute("data-drawio-import-template") : null;
      const importUrl = buildDrawioUrl(importTemplate, identifier);
      const likec4ImportUrl = container ? container.getAttribute("data-likec4-import-url") : null;
      if (toolValue === "likec4" && likec4ImportUrl) {
        importBtn.disabled = false;
        importBtn.classList.remove("disabled");
        importBtn.dataset.importUrl = likec4ImportUrl;
        importBtn.dataset.likec4Import = "1";
        if (referenceValue) {
          importBtn.dataset.likec4Path = referenceValue;
        } else {
          delete importBtn.dataset.likec4Path;
        }
        delete importBtn.dataset.diagramId;
      } else if (toolValue !== "likec4" && identifier && importUrl) {
        importBtn.disabled = false;
        importBtn.classList.remove("disabled");
        importBtn.dataset.importUrl = importUrl;
        importBtn.dataset.diagramId = identifier;
        delete importBtn.dataset.likec4Import;
        delete importBtn.dataset.likec4Path;
      } else {
        importBtn.disabled = true;
        importBtn.classList.add("disabled");
        delete importBtn.dataset.importUrl;
        delete importBtn.dataset.diagramId;
        delete importBtn.dataset.likec4Import;
        delete importBtn.dataset.likec4Path;
      }
    }

    const exportBtn = wrapper.querySelector(".dat-drawio-export-button");
    if (exportBtn) {
      const exportTemplate = container ? container.getAttribute("data-drawio-export-template") : null;
      const exportUrl = buildDrawioUrl(exportTemplate, identifier);
      const likec4Template = container ? container.getAttribute("data-likec4-export-template") : null;
      const likec4ExportUrl = buildLikeC4Url(likec4Template, referenceValue);
      if (toolValue === "likec4" && referenceValue && likec4ExportUrl) {
        exportBtn.disabled = false;
        exportBtn.classList.remove("disabled");
        exportBtn.dataset.exportUrl = likec4ExportUrl;
        delete exportBtn.dataset.diagramId;
      } else if (toolValue !== "likec4" && identifier && exportUrl) {
        exportBtn.disabled = false;
        exportBtn.classList.remove("disabled");
        exportBtn.dataset.exportUrl = exportUrl;
        exportBtn.dataset.diagramId = identifier;
      } else {
        exportBtn.disabled = true;
        exportBtn.classList.add("disabled");
        delete exportBtn.dataset.exportUrl;
        delete exportBtn.dataset.diagramId;
      }
    }
  }

  function createDrawioActionsElement(column, container) {
    const wrapper = document.createElement("div");
    wrapper.className = "dat-repeater-drawio-actions";
    wrapper.dataset.columnKey = column.key;
    wrapper.dataset.drawioSourceKey = column.drawio_source_key || "diagramme_id";

    const info = document.createElement("span");
    info.className = "chip grey lighten-3";
    info.textContent = "Aucun diagramme";
    info.style.marginRight = "0.5rem";

    const viewBtn = document.createElement("a");
    viewBtn.className = "btn btn-flat waves-effect cinta-btn-secondary";
    viewBtn.target = "_blank";
    viewBtn.rel = "noopener";
    setIconLabel(viewBtn, "visibility", "Voir");
    viewBtn.dataset.action = "view";
    viewBtn.disabled = true;

    wrapper.appendChild(info);
    wrapper.appendChild(viewBtn);
    return wrapper;
  }

  function updateDrawioActions(wrapper) {
    if (!wrapper) {
      return;
    }
    const container = wrapper.closest(".dat-repeater");
    const detailTemplate = container ? container.getAttribute("data-drawio-detail-template") : null;
    const editTemplate = container ? container.getAttribute("data-drawio-edit-template") : null;
    const row = wrapper.closest("tr");
    const sourceKey = wrapper.dataset.drawioSourceKey || "diagramme_id";
    const sourceInput = row ? row.querySelector(`[data-column-key="${sourceKey}"]`) : null;
    const diagramId = sourceInput && sourceInput.value ? sourceInput.value.trim() : "";
    const info = wrapper.querySelector(".chip");
    const viewBtn = wrapper.querySelector('[data-action="view"]');
    const editBtn = wrapper.querySelector('[data-action="edit"]');

    if (!diagramId) {
      if (info) {
        info.textContent = "Aucun diagramme";
        info.className = "chip grey lighten-3";
      }
      if (viewBtn) {
        viewBtn.classList.add("disabled");
        viewBtn.removeAttribute("href");
      }
      if (editBtn) {
        editBtn.classList.add("disabled");
        editBtn.removeAttribute("href");
      }
      return;
    }

    if (info) {
      info.textContent = `Diagramme #${diagramId}`;
      info.className = "chip blue lighten-5 blue-text text-darken-4";
    }
    const detailUrl = buildDrawioUrl(detailTemplate, diagramId);
    const editUrl = buildDrawioUrl(editTemplate, diagramId);
    if (viewBtn) {
      if (detailUrl) {
        viewBtn.href = detailUrl;
        viewBtn.classList.remove("disabled");
      } else {
        viewBtn.classList.add("disabled");
        viewBtn.removeAttribute("href");
      }
    }
    if (editBtn) {
      if (editUrl) {
        editBtn.href = editUrl;
        editBtn.classList.remove("disabled");
      } else {
        editBtn.classList.add("disabled");
        editBtn.removeAttribute("href");
      }
    }
  }

  function updateRowDrawioActions(row) {
    if (!row) {
      return;
    }
    row.querySelectorAll(".dat-repeater-drawio-actions").forEach((wrapper) => updateDrawioActions(wrapper));
  }

  function getDiagramToolInput(row) {
    if (!row) {
      return null;
    }
    return row.querySelector('[data-column-key="schema_systeme"]');
  }

  function getDiagramReferenceInput(row) {
    if (!row) {
      return null;
    }
    return row.querySelector('[data-column-key="schema_reference"]');
  }

  function updateRowDiagramState(row, container) {
    if (!row) {
      return;
    }
    const wrappers = Array.from(row.querySelectorAll(".dat-repeater-drawio-field"));
    let hasDrawioId = false;
    const diagramIds = wrappers.map((wrapper) => {
      const hidden = wrapper.querySelector('input[type="hidden"][data-column-key]');
      const diagramId = hidden && hidden.value ? String(hidden.value).trim() : "";
      if (diagramId) {
        hasDrawioId = true;
      }
      return { wrapper, diagramId };
    });
    const toolInput = getDiagramToolInput(row);
    const referenceInput = getDiagramReferenceInput(row);
    const referenceValue = referenceInput ? String(referenceInput.value || "").trim() : "";
    let toolValue = toolInput ? String(toolInput.value || "").trim().toLowerCase() : "";
    if (toolInput && !toolValue) {
      if (hasDrawioId) {
        toolInput.value = "drawio";
        toolValue = "drawio";
      } else if (referenceValue) {
        toolInput.value = "likec4";
        toolValue = "likec4";
      }
    }
    diagramIds.forEach(({ wrapper, diagramId }) => {
      updateDrawioChip(wrapper, { id: diagramId, title: null }, { container });
    });
    const shouldLockTool =
      (toolValue === "drawio" && hasDrawioId) ||
      (toolValue === "likec4" && referenceValue);
    if (toolInput) {
      toolInput.disabled = shouldLockTool;
      toolInput.classList.toggle("disabled", shouldLockTool);
    }
  }

  function attachRowDiagramHandlers(row, container) {
    if (!row) {
      return;
    }
    const toolInput = getDiagramToolInput(row);
    if (toolInput && toolInput.dataset.diagramToolListener !== "1") {
      toolInput.dataset.diagramToolListener = "1";
      toolInput.addEventListener("change", () => updateRowDiagramState(row, container));
    }
    const referenceInput = getDiagramReferenceInput(row);
    if (referenceInput && referenceInput.dataset.diagramReferenceListener !== "1") {
      referenceInput.dataset.diagramReferenceListener = "1";
      referenceInput.addEventListener("change", () => updateRowDiagramState(row, container));
    }
    row.querySelectorAll('input[type="hidden"][data-column-key]').forEach((input) => {
      if (input.dataset.diagramIdListener === "1") {
        return;
      }
      input.dataset.diagramIdListener = "1";
      input.addEventListener("change", () => updateRowDiagramState(row, container));
    });
    updateRowDiagramState(row, container);
  }

  function createInput(column, value, container, columns) {
    let element;
    const baseValue = value ?? "";
    const initialDiagramId = baseValue ? String(baseValue).trim() : "";
    const isEditMode = container.closest("form") !== null;
    if (column.drawio) {
      element = document.createElement("div");
      element.className = "dat-repeater-drawio-field";
      element.dataset.drawioControl = "true";

      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.dataset.columnKey = column.key;
      hidden.classList.add("dat-repeater-input");
      hidden.value = baseValue;
      hidden.addEventListener("change", () => syncHiddenInput(container, columns));

      const chip = document.createElement("span");
      chip.className = "chip grey lighten-3 dat-repeater-drawio-chip";
      chip.style.marginRight = "0.5rem";

      const buttonGroup = document.createElement("div");
      buttonGroup.style.display = "flex";
      buttonGroup.style.gap = "0.5rem";
      buttonGroup.style.flexWrap = "wrap";

      const viewBtn = document.createElement("button");
      viewBtn.type = "button";
      viewBtn.className = "btn btn-flat waves-effect cinta-btn-secondary dat-repeater-drawio-view";
      setIconLabel(viewBtn, "visibility", "Voir");
      if (initialDiagramId) {
        viewBtn.dataset.diagramId = initialDiagramId;
      } else {
        viewBtn.classList.add("disabled");
        viewBtn.disabled = true;
      }

      let editBtn = null;
      let importBtn = null;
      let exportBtn = null;
      if (isEditMode) {
        editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "btn waves-effect waves-light cinta-btn-primary dat-repeater-drawio-button";
        setIconLabel(editBtn, "edit", column.button_label || "Éditer");

        importBtn = document.createElement("button");
        importBtn.type = "button";
        importBtn.className = "btn waves-effect waves-light cinta-btn-primary dat-drawio-import-button dat-repeater-drawio-button";
        setIconLabel(importBtn, "file_upload", "Importer");
        if (initialDiagramId) {
          importBtn.dataset.diagramId = initialDiagramId;
        }

        exportBtn = document.createElement("button");
        exportBtn.type = "button";
        exportBtn.className = "btn btn-flat waves-effect cinta-btn-secondary dat-drawio-export-button dat-repeater-drawio-view";
        setIconLabel(exportBtn, "file_download", "Exporter");
        if (initialDiagramId) {
          exportBtn.dataset.diagramId = initialDiagramId;
        }
      }

      element.appendChild(hidden);
      element.appendChild(chip);
      buttonGroup.appendChild(viewBtn);
      if (editBtn) {
        buttonGroup.appendChild(editBtn);
        buttonGroup.appendChild(importBtn);
        buttonGroup.appendChild(exportBtn);
      }
      element.appendChild(buttonGroup);

      updateDrawioChip(element, { id: baseValue, title: null }, { container });

      viewBtn.addEventListener("click", () => {
        const likec4ViewsUrl = viewBtn.dataset.likec4ViewsUrl;
        const likec4PreviewUrl = viewBtn.dataset.likec4PreviewUrl;
        if (likec4ViewsUrl || likec4PreviewUrl) {
          openLikeC4Preview({ viewsUrl: likec4ViewsUrl, previewUrl: likec4PreviewUrl });
          return;
        }
        const diagramId = hidden.value ? hidden.value.trim() : "";
        if (diagramId) {
          openDiagramViewer(diagramId);
        }
      });

      if (editBtn) {
        editBtn.addEventListener("click", () =>
          handleDrawioEdit({
            container,
            columns,
            wrapper: element,
            hiddenInput: hidden,
            column,
          })
        );
      }
      return element;
    }
    if (column.render === "drawio_actions" || isRenderOnlyColumn(column)) {
      return createDrawioActionsElement(column, container);
    }
    switch (column.type) {
      case "textarea":
        element = document.createElement("textarea");
        element.className = "materialize-textarea";
        element.rows = column.rows || 2;
        element.style.minHeight = `${column.minHeight || 160}px`;
        element.value = baseValue;
        element.textContent = baseValue;
        break;
      case "select":
        element = document.createElement("select");
        element.classList.add("browser-default");
        (column.choices || []).forEach((choice) => {
          const option = document.createElement("option");
          option.value = choice.value;
          option.textContent = choice.label || choice.value;
          const disabled = choice.disabled === true || choice.enabled === false;
          if (disabled) {
            option.disabled = true;
          }
          if (choice.value === baseValue) {
            option.selected = true;
          }
          element.appendChild(option);
        });
        break;
      case "date":
        element = document.createElement("input");
        element.type = "date";
        element.classList.add("validate");
        element.value = baseValue;
        break;
      default:
        element = document.createElement("input");
        element.type = "text";
        element.classList.add("validate");
        element.value = baseValue;
        break;
    }
    element.dataset.columnKey = column.key;
    element.classList.add("dat-repeater-input");
    if (column.placeholder) {
      element.placeholder = column.placeholder;
    }
    return element;
  }

  function addRow(container, columns, initialValues) {
    const config = container.__datRepeaterConfig || {};
    const tbody = container.querySelector("tbody");
    const row = document.createElement("tr");
    const isEditMode = container.closest("form") !== null;
    columns.forEach((column) => {
      const cell = document.createElement("td");
      const input = createInput(column, initialValues ? initialValues[column.key] : "", container, columns);
      if (input && input.dataset && input.dataset.drawioControl === "true") {
        cell.appendChild(input);
      } else if (input) {
        input.addEventListener("change", () => syncHiddenInput(container, columns));
        cell.appendChild(input);
      }
      row.appendChild(cell);
    });
    if (isEditMode) {
      const actionCell = document.createElement("td");
      actionCell.className = "dat-repeater-actions-cell";
      const actionWrapper = document.createElement("div");
      actionWrapper.className = "dat-repeater-row-actions";

      const moveUpButton = document.createElement("button");
      moveUpButton.type = "button";
      moveUpButton.className = "btn-flat waves-effect dat-repeater-move dat-repeater-move-up";
      moveUpButton.innerHTML = '<i class="material-icons" aria-hidden="true">arrow_upward</i>';
      moveUpButton.setAttribute("aria-label", "Monter la ligne");
      moveUpButton.title = "Monter la ligne";
      moveUpButton.addEventListener("click", () => moveRow(row, "up", container, columns));
      actionWrapper.appendChild(moveUpButton);

      const moveDownButton = document.createElement("button");
      moveDownButton.type = "button";
      moveDownButton.className = "btn-flat waves-effect dat-repeater-move dat-repeater-move-down";
      moveDownButton.innerHTML = '<i class="material-icons" aria-hidden="true">arrow_downward</i>';
      moveDownButton.setAttribute("aria-label", "Descendre la ligne");
      moveDownButton.title = "Descendre la ligne";
      moveDownButton.addEventListener("click", () => moveRow(row, "down", container, columns));
      actionWrapper.appendChild(moveDownButton);

      if (config.allowRemove !== false) {
        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "btn-flat waves-effect red-text dat-repeater-remove-row";
        removeButton.innerHTML = '<i class="material-icons" aria-hidden="true">delete</i>';
        removeButton.addEventListener("click", () => {
          const minRows = Number.isInteger(config.minRows) ? config.minRows : 0;
          const currentRows = getRowCount(tbody);
          if (minRows && currentRows <= minRows) {
            return;
          }
          row.remove();
          syncHiddenInput(container, columns);
          updateAddButton(container);
          updateRowOrderControls(tbody);
        });
        actionWrapper.appendChild(removeButton);
      }
      actionCell.appendChild(actionWrapper);
      row.appendChild(actionCell);
    }
    tbody.appendChild(row);
    updateRowDrawioActions(row);
    attachRowDiagramHandlers(row, container);
    updateRowOrderControls(tbody);
    return row;
  }

  function getRowCount(tbody) {
    return tbody ? tbody.querySelectorAll("tr").length : 0;
  }

  function updateAddButton(container) {
    const config = container.__datRepeaterConfig || {};
    const addBtn = container.querySelector(".dat-repeater-add-row");
    if (!addBtn) {
      return;
    }
    if (config.allowAdd === false) {
      addBtn.style.display = "none";
      return;
    }
    const tbody = container.querySelector("tbody");
    const maxRows = Number.isInteger(config.maxRows) ? config.maxRows : null;
    const currentRows = getRowCount(tbody);
    const disabled = maxRows !== null && currentRows >= maxRows;
    addBtn.disabled = disabled;
    addBtn.classList.toggle("disabled", disabled);
    addBtn.style.display = "";
  }

  function initialise(container) {
    if (!container || container.dataset.initialised) {
      return;
    }
    container.dataset.initialised = "true";
    const columns = parseJSON(container.getAttribute("data-columns") || "[]");
    const minRows = parseInt(container.getAttribute("data-min-rows") || "", 10);
    const maxRows = parseInt(container.getAttribute("data-max-rows") || "", 10);
    const allowAdd = container.getAttribute("data-allow-add") !== "0";
    const allowRemove = container.getAttribute("data-allow-remove") !== "0";
    container.__datRepeaterColumns = columns;
    container.__datRepeaterConfig = {
      columns,
      minRows: Number.isNaN(minRows) ? null : minRows,
      maxRows: Number.isNaN(maxRows) ? null : maxRows,
      allowAdd,
      allowRemove,
    };
    const input = container.querySelector("input[type=hidden]");
    const tbody = container.querySelector("tbody");
    let existingRows = parseJSON(input.value);
    if (container.__datRepeaterConfig.maxRows !== null && existingRows.length > container.__datRepeaterConfig.maxRows) {
      existingRows = existingRows.slice(0, container.__datRepeaterConfig.maxRows);
    }
    existingRows.forEach((row) => {
      addRow(container, columns, row);
    });
    const minRowsEnforced = Number.isInteger(container.__datRepeaterConfig.minRows)
      ? container.__datRepeaterConfig.minRows
      : 0;
    const maxRowsLimit = Number.isInteger(container.__datRepeaterConfig.maxRows)
      ? container.__datRepeaterConfig.maxRows
      : null;
    if (!existingRows.length && (!maxRowsLimit || maxRowsLimit > 0)) {
      addRow(container, columns);
    }
    while (minRowsEnforced && getRowCount(tbody) < minRowsEnforced) {
      const currentRows = getRowCount(tbody);
      if (maxRowsLimit !== null && currentRows >= maxRowsLimit) {
        break;
      }
      addRow(container, columns);
    }
    tbody.querySelectorAll("tr").forEach(updateRowDrawioActions);
    const addBtn = container.querySelector(".dat-repeater-add-row");
    if (addBtn) {
      addBtn.addEventListener("click", () => {
        const currentRows = getRowCount(tbody);
        const maxRows = Number.isInteger(container.__datRepeaterConfig.maxRows)
          ? container.__datRepeaterConfig.maxRows
          : null;
        if (maxRows !== null && currentRows >= maxRows) {
          return;
        }
        if (container.__datRepeaterConfig.allowAdd === false) {
          return;
        }
        addRow(container, columns);
        syncHiddenInput(container, columns);
        updateAddButton(container);
      });
    }
    syncHiddenInput(container, columns);
    updateAddButton(container);
  }

  function scanAndInit() {
    document.querySelectorAll(".dat-repeater").forEach((container) => initialise(container));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scanAndInit);
  } else {
    scanAndInit();
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

  function getDrawioState() {
    return runtime.drawioState;
  }

  function setDrawioState(state) {
    runtime.drawioState = state;
  }

  function resetDrawioState() {
    runtime.drawioState = null;
  }

  function setDrawioModalOpen(isOpen) {
    if (!document || !document.body) {
      return;
    }
    if (isOpen) {
      document.body.classList.add("dat-drawio-modal-open");
    } else {
      document.body.classList.remove("dat-drawio-modal-open");
    }
  }

  function ensureDrawioModal() {
    if (runtime.drawioModal) {
      return runtime.drawioModal;
    }
    const modal = document.createElement("div");
    modal.id = "dat-drawio-modal";
    modal.className = "modal dat-drawio-modal";
    modal.innerHTML = `
      <div class="modal-content dat-drawio-modal-content">
        <div class="dat-drawio-modal-header">
          <div>
            <span class="grey-text">Schémas</span>
            <h5 class="dat-drawio-modal-title">Éditeur Draw.io</h5>
          </div>
        </div>
        <div class="dat-drawio-modal-body">
          <div class="dat-drawio-modal-loader">
            <div class="dat-drawio-modal-spinner">
              ${getLoadingSpinnerMarkup()}
            </div>
            <p class="grey-text text-darken-1">Initialisation de l'éditeur…</p>
          </div>
          <iframe title="Éditeur Draw.io"
                  src="about:blank"
                  class="dat-drawio-modal-iframe"
                  allowfullscreen></iframe>
        </div>
      </div>
      <div class="modal-footer dat-drawio-modal-footer">
        <button type="button" class="modal-close btn waves-effect waves-light cinta-btn-secondary">
          Fermer
        </button>
      </div>
    `;
    document.body.appendChild(modal);
    let instance = null;
    const iframe = modal.querySelector("iframe");
    const loader = modal.querySelector(".dat-drawio-modal-loader");
    const title = modal.querySelector(".dat-drawio-modal-title");
    const initModal = () => {
      if (typeof M !== "undefined" && M.Modal) {
        instance = M.Modal.init(modal, {
          onOpenStart: () => setDrawioModalOpen(true),
          onCloseEnd: () => {
            iframe.src = "about:blank";
            resetDrawioState();
            showDrawioLoader();
            setDrawioModalOpen(false);
            runtime.likec4Context = null;
          },
        });
      }
    };
    initModal();

    runtime.drawioModal = {
      modal,
      iframe,
      loader,
      title,
      get instance() {
        if (!instance) {
          initModal();
        }
        return instance;
      },
    };
    return runtime.drawioModal;
  }

  function showDrawioLoader() {
    const modal = runtime.drawioModal;
    if (modal && modal.loader) {
      modal.loader.style.display = "flex";
    }
  }

  function hideDrawioLoader() {
    const modal = runtime.drawioModal;
    if (modal && modal.loader) {
      modal.loader.style.display = "none";
    }
  }

  function ensureDrawioMessageHandler() {
    if (runtime.drawioListenerAttached) {
      return;
    }
    window.addEventListener("message", handleDrawioMessage);
    runtime.drawioListenerAttached = true;
  }

  function ensureLikeC4MessageHandler() {
    if (runtime.likec4ListenerAttached) {
      return;
    }
    window.addEventListener("message", handleLikeC4Message);
    runtime.likec4ListenerAttached = true;
  }

  function sendToDrawio(payload) {
    const modal = runtime.drawioModal;
    const state = getDrawioState();
    if (!modal || !modal.iframe || !state) {
      return;
    }
    const target = modal.iframe.contentWindow;
    if (!target) {
      return;
    }
    try {
      target.postMessage(JSON.stringify(payload), state.origin);
    } catch (error) {
      console.error("Impossible d'envoyer le message à Draw.io:", error);
    }
  }

  function fetchDiagramEmbedContext(diagramId) {
    return fetch(`/diagrams/${diagramId}/embed-context/`, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then((response) => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    });
  }

  function handleDrawioMessage(evt) {
    const state = getDrawioState();
    if (!state || evt.origin !== state.origin) {
      return;
    }
    let payload = evt.data;
    if (typeof payload === "string") {
      try {
        payload = JSON.parse(payload);
      } catch {
        return;
      }
    }
    if (!payload || typeof payload !== "object") {
      return;
    }
    if (payload.event === "init") {
      sendToDrawio({ action: "configure", config: { autosave: 1 } });
      sendToDrawio({ action: "load", xml: state.xml || "<mxGraphModel/>" });
      hideDrawioLoader();
    } else if (payload.event === "save") {
      handleDrawioSave(payload.xml);
    } else if (payload.event === "export" && payload.data) {
      persistDrawioThumbnail(payload.data);
    }
  }

  function handleLikeC4Message(evt) {
    if (LIKEC4_ORIGIN && evt.origin !== LIKEC4_ORIGIN) {
      return;
    }
    let payload = evt.data;
    if (typeof payload === "string") {
      try {
        payload = JSON.parse(payload);
      } catch {
        return;
      }
    }
    if (!payload || typeof payload !== "object") {
      return;
    }
    if (payload.source !== "likec4-editor" || payload.event !== "saved") {
      return;
    }
    const context = runtime.likec4Context;
    if (!context || !context.row) {
      return;
    }
    const nextPath = String(payload.file || payload.path || payload.storage_path || "").trim();
    const row = context.row;
    const container = context.container || row.closest(".dat-repeater");
    const referenceInput = getDiagramReferenceInput(row);
    if (referenceInput && nextPath && referenceInput.value !== nextPath) {
      referenceInput.value = nextPath;
      referenceInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const toolInput = getDiagramToolInput(row);
    if (toolInput && String(toolInput.value || "").trim().toLowerCase() !== "likec4") {
      toolInput.value = "likec4";
      toolInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
    updateRowDrawioActions(row);
    if (container) {
      updateRowDiagramState(row, container);
      syncHiddenInput(container, getContainerColumns(container));
    }
  }

  function handleDrawioSave(xml) {
    const state = getDrawioState();
    if (!state) {
      return;
    }
    const csrfToken = getCsrfToken();
    fetch(state.saveXmlUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({ xml }),
    })
      .then(() => {
        state.xml = xml;
        sendToDrawio({ action: "export", format: "png", scale: 1, grid: 0 });
        hideDrawioLoader();
        if (state.resolveSave) {
          state.resolveSave();
          state.resolveSave = null;
        }
      })
      .catch((error) => {
        console.error("Sauvegarde du diagramme échouée:", error);
        hideDrawioLoader();
        alert("La sauvegarde du diagramme a échoué. Merci de réessayer.");
        if (state.rejectSave) {
          state.rejectSave(error);
          state.rejectSave = null;
        }
      });
  }

  function persistDrawioThumbnail(dataUri) {
    const state = getDrawioState();
    if (!state) {
      return;
    }
    const csrfToken = getCsrfToken();
    fetch(state.saveThumbUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify({ data_uri: dataUri }),
    }).catch((error) => console.warn("Miniature Draw.io non générée:", error));
  }

  function openDiagramViewer(diagramId) {
    if (!diagramId) {
      return;
    }
    if (window.CintaDatViewer && typeof window.CintaDatViewer.open === "function") {
      window.CintaDatViewer.open(diagramId);
    } else {
      alert("Affichage du diagramme indisponible pour le moment.");
    }
  }

  function openLikeC4Preview(payload, title) {
    if (!payload) {
      return;
    }
    const previewUrl = typeof payload === "string" ? payload : payload.previewUrl;
    const viewsUrl = typeof payload === "object" ? payload.viewsUrl : "";
    if (viewsUrl) {
      fetch(viewsUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          return response.json();
        })
        .then((data) => {
          const paths = Array.isArray(data.paths) ? data.paths : [];
          const fallbackUrl = data.thumbnail_url || previewUrl;
          const viewerTitle = title || "Diagramme LikeC4";
          if (window.CintaDatViewer && typeof window.CintaDatViewer.openImages === "function" && paths.length) {
            window.CintaDatViewer.openImages(paths, viewerTitle);
            return;
          }
          if (fallbackUrl) {
            window.CintaDatViewer && typeof window.CintaDatViewer.openImage === "function"
              ? window.CintaDatViewer.openImage(fallbackUrl, viewerTitle)
              : window.open(fallbackUrl, "_blank", "noopener");
          }
        })
        .catch((error) => {
          console.warn("LikeC4 preview failed:", error);
          if (previewUrl) {
            window.CintaDatViewer && typeof window.CintaDatViewer.openImage === "function"
              ? window.CintaDatViewer.openImage(previewUrl, title || "Diagramme LikeC4")
              : window.open(previewUrl, "_blank", "noopener");
          }
        });
      return;
    }
    if (previewUrl) {
      if (window.CintaDatViewer && typeof window.CintaDatViewer.openImage === "function") {
        window.CintaDatViewer.openImage(previewUrl, title || "Diagramme LikeC4");
      } else {
        window.open(previewUrl, "_blank", "noopener");
      }
    }
  }

  function resolveDiagramTool(column, row) {
    if (!row) {
      return "";
    }
    const toolKey = column.diagram_tool_key || column.diagramToolKey || "schema_systeme";
    const toolInput = row.querySelector(`[data-column-key="${toolKey}"]`);
    if (!toolInput) {
      return "";
    }
    const rawValue = toolInput.value || toolInput.textContent || "";
    return String(rawValue || "").trim().toLowerCase();
  }

  function slugifyFileName(value) {
    if (!value) {
      return "";
    }
    const normalized = String(value).trim().toLowerCase().normalize("NFD");
    let slug = "";
    let needsSeparator = false;

    for (const character of normalized) {
      const code = character.charCodeAt(0);
      if (code >= 0x0300 && code <= 0x036f) {
        continue;
      }
      const isAsciiLetter = code >= 97 && code <= 122;
      const isDigit = code >= 48 && code <= 57;
      if (isAsciiLetter || isDigit) {
        if (needsSeparator && slug) {
          slug += "-";
        }
        slug += character;
        needsSeparator = false;
      } else {
        needsSeparator = Boolean(slug);
      }
    }

    return slug;
  }

  async function ensureLikeC4Reference(row, column, container, title) {
    if (!row) return "";
    const referenceKey = column.diagram_reference_key || column.diagramReferenceKey || "schema_reference";
    const referenceInput = row.querySelector(`[data-column-key="${referenceKey}"]`);
    if (!referenceInput) return "";
    let referenceValue = String(referenceInput.value || referenceInput.textContent || "").trim();
    if (referenceValue) return referenceValue;
    const createUrl = container && container.getAttribute("data-likec4-create-url");
    if (!createUrl) throw new Error("Création LikeC4 indisponible.");
    const payload = {
      title: title || "",
      dat_id: container.getAttribute("data-dat-id") || "",
      section_id: container.getAttribute("data-diagram-section-id") || "",
    };
    const headers = { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" };
    const csrfToken = getCsrfToken();
    if (csrfToken) headers["X-CSRFToken"] = csrfToken;
    const response = await fetch(createUrl, {
      method: "POST", credentials: "same-origin", headers, body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.path) {
      throw new Error(result.message || result.error || `Erreur ${response.status}`);
    }
    referenceValue = result.path;
    referenceInput.value = referenceValue;
    referenceInput.dispatchEvent(new Event("change", { bubbles: true }));
    return referenceValue;
  }

  function openLikeC4Editor(filePath, title, context) {
    ensureLikeC4MessageHandler();
    runtime.likec4Context = context || null;
    const modal = ensureDrawioModal();
    resetDrawioState();
    showDrawioLoader();
    setDrawioModalOpen(true);
    if (modal.instance && modal.instance.open) {
      modal.instance.open();
    } else {
      modal.modal.style.display = "block";
      modal.modal.style.width = "90vw";
      modal.modal.style.maxWidth = "1500px";
    }
    if (modal.title) {
      modal.title.textContent = title || "Éditeur LikeC4";
    }
    modal.iframe.addEventListener("load", hideDrawioLoader, { once: true });
    const url = new URL(LIKEC4_EMBED_URL, window.location.origin);
    if (filePath) {
      url.searchParams.set("file", filePath);
    }
    modal.iframe.src = url.toString();
  }


  function openDrawioEditor(diagramId) {
    ensureDrawioMessageHandler();
    const modal = ensureDrawioModal();
    showDrawioLoader();
    setDrawioModalOpen(true);
    if (modal.instance && modal.instance.open) {
      modal.instance.open();
    } else {
      modal.modal.style.display = "block";
      modal.modal.style.width = "90vw";
      modal.modal.style.maxWidth = "1500px";
    }
    if (modal.title) {
      modal.title.textContent = "Chargement…";
    }
    modal.iframe.src = "about:blank";
    const savePromise = new Promise((resolve, reject) => {
      const state = getDrawioState() || {};
      state.resolveSave = resolve;
      state.rejectSave = reject;
      setDrawioState(state);
    });

    return fetchDiagramEmbedContext(diagramId)
      .then((data) => {
        const drawio = data.drawio || {};
        setDrawioState({
          diagramId,
          origin: drawio.origin,
          xml: drawio.xml || "<mxGraphModel/>",
          saveXmlUrl: drawio.save_xml_url,
          saveThumbUrl: drawio.save_thumbnail_url,
          resolveSave: getDrawioState()?.resolveSave,
          rejectSave: getDrawioState()?.rejectSave,
        });
        if (modal.title) {
          modal.title.textContent = data.diagram?.title || `Diagramme #${diagramId}`;
        }
        if (drawio.embed_url) {
          modal.iframe.src = drawio.embed_url;
        } else {
          throw new Error("URL d'intégration manquante.");
        }
        return savePromise;
      })
      .catch((error) => {
        console.error("Impossible d'ouvrir l'éditeur Draw.io:", error);
        alert("Ouverture de l'éditeur impossible. Merci de réessayer.");
        if (modal.instance && modal.instance.close) {
          modal.instance.close();
        } else {
          modal.modal.style.display = "none";
        }
        setDrawioModalOpen(false);
      });
  }

  async function handleDrawioEdit({ container, wrapper, hiddenInput, column }) {
    const row = hiddenInput.closest("tr");
    const tool = resolveDiagramTool(column, row);
    if (tool && tool !== "drawio") {
      if (tool === "likec4") {
        let title = "";
        if (row) {
          const titleKey = column.drawio_name_key || column.drawioNameKey || "nom_schema";
          const titleInput = row.querySelector(`[data-column-key="${titleKey}"]`);
          if (titleInput) title = String(titleInput.value || titleInput.textContent || "").trim();
        }
        try {
          const filePath = await ensureLikeC4Reference(row, column, container, title);
          openLikeC4Editor(filePath, title, { row, column, container });
        } catch (error) {
          console.error("Impossible de créer le diagramme LikeC4:", error);
          alert("Création du diagramme LikeC4 impossible. Merci de réessayer.");
        }
      } else {
        alert("Cet outil de diagramme n'est pas intégré. Merci d'utiliser la référence externe.");
      }
      return;
    }
    ensureDrawioMessageHandler();
    const existingId = hiddenInput.value ? hiddenInput.value.trim() : "";
    if (existingId) {
      openDrawioEditor(existingId);
      return;
    }
    const createUrl = container.getAttribute("data-drawio-create-url");
    if (!createUrl) {
      console.warn("Aucune URL de création de diagramme n'est définie pour ce champ.");
      return;
    }
    let schemaTitle = "";
    if (column.drawio_name_key) {
      const row = hiddenInput.closest("tr");
      if (row) {
        const sourceInput = row.querySelector(`[data-column-key="${column.drawio_name_key}"]`);
        if (sourceInput) {
          schemaTitle = sourceInput.value || sourceInput.textContent || "";
        }
      }
    }
    const payload = {
      title: schemaTitle.trim(),
      section_slug: container.getAttribute("data-drawio-section-slug") || "",
      sub_section_slug: container.getAttribute("data-drawio-sub-section-slug") || "",
    };
    const headers = {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    };
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers["X-CSRFToken"] = csrfToken;
    }
    fetch(createUrl, {
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
            .then((payload) => {
              const detail = payload && (payload.message || payload.error);
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
        hiddenInput.value = diagram.id;
        hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
        updateDrawioChip(wrapper, diagram, { container });
        updateRowDrawioActions(wrapper.closest("tr"));
        openDrawioEditor(diagram.id);
      })
      .catch((error) => {
        console.error("Impossible de créer le diagramme Draw.io:", error);
        const message = error && error.message ? error.message : "La création du diagramme a échoué. Merci de réessayer.";
        alert(message);
      });
  }

  function getContainerColumns(container) {
    if (!container) {
      return [];
    }
    if (Array.isArray(container.__datRepeaterColumns)) {
      return container.__datRepeaterColumns;
    }
    const columns = parseJSON(container.getAttribute("data-columns") || "[]");
    container.__datRepeaterColumns = columns;
    return columns;
  }

  function appendRowWithValues(container, initialValues) {
    if (!container) {
      return null;
    }
    const columns = getContainerColumns(container);
    const row = addRow(container, columns, initialValues);
    syncHiddenInput(container, columns);
    return row;
  }

  return { init: initialise, scan: scanAndInit, appendRow: appendRowWithValues, updateDrawioChip };
})();

(function () {
  if (!window.CintaDatRepeater) {
    return;
  }

  function triggerScan() {
    window.CintaDatRepeater.scan();
  }

  if (!window.CintaDatRepeater.__turbolinksHandlerAttached) {
    document.addEventListener("turbolinks:load", triggerScan);
    window.CintaDatRepeater.__turbolinksHandlerAttached = true;
  }

  if (document.readyState !== "loading") {
    triggerScan();
  }
})();
