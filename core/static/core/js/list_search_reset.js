(function () {
  "use strict";

  function initializeListSearchForm(form) {
    const searchInput = form.querySelector('input[name="q"]');
    if (!searchInput) {
      return;
    }

    const clearUrl = form.dataset.clearUrl || window.location.pathname;
    const startedWithSearch = searchInput.value.trim().length > 0;
    let resetStarted = false;

    form.addEventListener("submit", function () {
      const normalizedSearch = searchInput.value.trim();
      searchInput.value = normalizedSearch;
      if (normalizedSearch.length === 0) {
        searchInput.disabled = true;
        window.setTimeout(function () {
          searchInput.disabled = false;
        }, 0);
      }
    });

    if (startedWithSearch) {
      searchInput.addEventListener("input", function () {
        if (!resetStarted && searchInput.value.trim().length === 0) {
          resetStarted = true;
          window.location.assign(clearUrl);
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".js-list-search-form").forEach(initializeListSearchForm);
  });
})();
