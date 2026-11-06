    (function () {
      function initializeApplicationRefresh() {
        var button = document.querySelector(".application-refresh-btn");
        var select = document.querySelector('select[name="application"]');
        if (!button || !select) {
          return;
        }
        if (button.dataset.bound === "1") {
          return;
        }
        button.dataset.bound = "1";
        button.addEventListener("click", function (event) {
          event.preventDefault();
          var url = button.dataset.refreshUrl;
          if (!url) {
            return;
          }
          var originalHtml = button.innerHTML;
          button.dataset.originalHtml = originalHtml;
          button.disabled = true;
          button.innerHTML = '<i class="material-icons left" aria-hidden="true">autorenew</i>Actualisation...';
          fetch(url, {
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" },
          })
            .then(function (response) {
              if (!response.ok) {
                throw new Error("bad response");
              }
              return response.json();
            })
            .then(function (payload) {
              var data = Array.isArray(payload.options) ? payload.options : [];
              var previousValue = select.value;
              var placeholderOption = Array.from(select.options).find(function (option) {
                return option.value === "";
              });
              while (select.options.length) {
                select.remove(0);
              }
              if (placeholderOption) {
                select.add(new Option(placeholderOption.text, ""));
              }
              data.forEach(function (item) {
                var value = item.value !== undefined ? String(item.value) : String(item.id);
                var label = item.label !== undefined ? item.label : item.name;
                select.add(new Option(label, value));
              });
              var availableValues = Array.from(select.options).map(function (option) {
                return option.value;
              });
              if (availableValues.includes(previousValue)) {
                select.value = previousValue;
              } else if (select.options.length) {
                select.selectedIndex = 0;
              }
              var changeEvent = new Event("change", { bubbles: true });
              select.dispatchEvent(changeEvent);
              if (window.M && M.FormSelect) {
                var instance = M.FormSelect.getInstance(select);
                if (instance) {
                  instance.destroy();
                }
                M.FormSelect.init(select);
              }
              if (window.M && M.toast) {
                M.toast({ html: "Liste des applications mise à jour" });
              }
            })
            .catch(function (error) {
              console.error("Application refresh failed", error);
              if (window.M && M.toast) {
                M.toast({ html: "Actualisation impossible", classes: "red" });
              }
            })
            .finally(function () {
              button.disabled = false;
              button.innerHTML = button.dataset.originalHtml || originalHtml;
            });
        });
      }
      document.addEventListener("DOMContentLoaded", initializeApplicationRefresh);
      document.addEventListener("turbolinks:load", initializeApplicationRefresh);
    })();
  
