(function () {
  if (window.__datTablePaginationBound) {
    return;
  }
  window.__datTablePaginationBound = true;

  const searchTimers = new WeakMap();

  function normalizeText(value) {
    return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase().replace(/\s+/g, " ").trim();
  }

  function renderTable(container, step = 0) {
    const rows = Array.from(container.querySelector("tbody").rows);
    const pageSize = Number(container.dataset.pageSize);
    const search = container.querySelector("[data-table-search]");
    const value = search ? normalizeText(search.value) : "";
    const query = search && value.length >= search.minLength ? value : "";
    const matches = query ? rows.filter((row) => normalizeText(row.textContent).includes(query)) : rows;
    const numPages = Math.max(1, Math.ceil(matches.length / pageSize));
    const requestedPage = query !== (container.dataset.tableQuery || "")
      ? 1 : Number(container.dataset.page) + step;
    const page = Math.max(1, Math.min(numPages, requestedPage));
    const start = (page - 1) * pageSize;
    const visible = new Set(matches.slice(start, start + pageSize));

    rows.forEach((row) => {
      row.hidden = !visible.has(row);
    });
    container.dataset.page = String(page);
    container.dataset.tableQuery = query;
    container.querySelector("[data-table-page-status]").textContent =
      `Page ${page} sur ${numPages}`;
    container.querySelector('[data-table-page-step="-1"]').disabled = page === 1;
    container.querySelector('[data-table-page-step="1"]').disabled = page === numPages;

    const empty = container.querySelector("[data-table-search-empty]");
    if (empty) {
      empty.hidden = matches.length !== 0;
    }
  }

  // Shared controls work with any paginated table, including tables replaced after editing.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-table-page-step]");
    if (!button || button.disabled) {
      return;
    }
    const container = button.closest("[data-dat-table-pagination]");
    if (container) {
      renderTable(container, Number(button.dataset.tablePageStep));
    }
  });

  document.addEventListener("input", (event) => {
    const search = event.target.closest("[data-table-search]");
    const container = search && search.closest("[data-dat-table-pagination]");
    if (!container) {
      return;
    }
    window.clearTimeout(searchTimers.get(container));
    if (event.isComposing) {
      return;
    }
    searchTimers.set(container, window.setTimeout(() => {
      if (container.isConnected) {
        renderTable(container);
      }
    }, 250));
  });
})();
