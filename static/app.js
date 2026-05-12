(function () {
  var themeKey = "imdb-theme";
  var sidebarKey = "imdb-filters-sidebar";
  var darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function resolvedTheme(choice) {
    if (choice === "dark" || choice === "light") {
      return choice;
    }
    return darkQuery && darkQuery.matches ? "dark" : "light";
  }

  function applyTheme(choice) {
    var theme = choice || "system";
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.resolvedTheme = resolvedTheme(theme);
    document.querySelectorAll("[data-theme-select]").forEach(function (select) {
      select.value = theme;
    });
  }

  function isMobile() {
    return window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
  }

  function applySidebarState(state) {
    var closed = state === "closed";
    document.body.classList.toggle("filters-collapsed", closed);
    if (!isMobile()) {
      document.body.classList.remove("filters-open");
    }
  }

  function setSidebarState(state) {
    localStorage.setItem(sidebarKey, state);
    applySidebarState(state);
  }

  function openFilters() {
    if (isMobile()) {
      document.body.classList.add("filters-open");
      document.body.classList.remove("filters-collapsed");
    } else {
      setSidebarState("open");
    }
  }

  function closeFilters() {
    document.body.classList.remove("filters-open");
    setSidebarState("closed");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var savedTheme = localStorage.getItem(themeKey) || "system";
    applyTheme(savedTheme);

    document.querySelectorAll("[data-theme-select]").forEach(function (select) {
      select.addEventListener("change", function () {
        localStorage.setItem(themeKey, select.value);
        applyTheme(select.value);
      });
    });

    if (darkQuery) {
      darkQuery.addEventListener("change", function () {
        if ((localStorage.getItem(themeKey) || "system") === "system") {
          applyTheme("system");
        }
      });
    }

    applySidebarState(localStorage.getItem(sidebarKey) || "open");

    document.querySelectorAll("[data-filters-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (isMobile()) {
          if (document.body.classList.contains("filters-open")) {
            closeFilters();
          } else {
            openFilters();
          }
        } else if (document.body.classList.contains("filters-collapsed")) {
          setSidebarState("open");
        } else {
          setSidebarState("closed");
        }
      });
    });

    document.querySelectorAll("[data-filters-close], [data-filters-backdrop]").forEach(function (button) {
      button.addEventListener("click", closeFilters);
    });

    window.addEventListener("resize", function () {
      applySidebarState(localStorage.getItem(sidebarKey) || "open");
    });
  });
})();
