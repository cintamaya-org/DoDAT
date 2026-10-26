(function () {
  if (window.__datTablePaginationBound) {
    return;
  }
  window.__datTablePaginationBound = true;

  // Delegation also handles tables replaced after editing or diagram generation.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-table-page-step]");
    if (!button || button.disabled) {
      return;
    }
    const container = button.closest("[data-dat-table-pagination]");
    if (!container) {
      return;
    }
    const rows = Array.from(container.querySelector("tbody").rows);
    const pageSize = Number(container.dataset.pageSize);
    const numPages = Math.max(1, Math.ceil(rows.length / pageSize));
    const page = Math.max(1, Math.min(numPages,
      Number(container.dataset.page) + Number(button.dataset.tablePageStep)));
    const start = (page - 1) * pageSize;

    rows.forEach((row, index) => {
      row.hidden = index < start || index >= start + pageSize;
    });
    container.dataset.page = String(page);
    container.querySelector("[data-table-page-status]").textContent =
      `Page ${page} sur ${numPages} · ${rows.length} ligne${rows.length > 1 ? "s" : ""}`;
    container.querySelector('[data-table-page-step="-1"]').disabled = page === 1;
    container.querySelector('[data-table-page-step="1"]').disabled = page === numPages;
  });
})();
