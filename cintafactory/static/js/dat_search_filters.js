(function () {
  function initSearchFilters() {
    var form = document.querySelector('.dat-search-page__form');
    if (!form || form.dataset.filtersInitialized === '1') {
      return;
    }
    form.dataset.filtersInitialized = '1';
    var toggle = form.querySelector('[data-search-dats-toggle]');
    var datFilters = form.querySelector('[data-search-dat-filters]');
    if (!toggle || !datFilters) {
      return;
    }
    function updateDatFilters() {
      datFilters.hidden = !toggle.checked;
      datFilters.disabled = !toggle.checked;
    }
    toggle.addEventListener('change', updateDatFilters);
    updateDatFilters();
  }

  document.addEventListener('DOMContentLoaded', initSearchFilters);
  document.addEventListener('turbolinks:load', initSearchFilters);
})();
