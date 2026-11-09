// Progressive enhancement only: every form works without JavaScript.
// Elements with data-show-when="field=value" are shown only when that radio/select has the value.
(function () {
  function update(form) {
    form.querySelectorAll("[data-show-when]").forEach(function (el) {
      var parts = el.getAttribute("data-show-when").split("=");
      var checked = form.querySelector('[name="' + parts[0] + '"]:checked');
      var select = form.querySelector('select[name="' + parts[0] + '"]');
      var value = checked ? checked.value : select ? select.value : "";
      el.hidden = value !== parts[1];
    });
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.intake").forEach(function (form) {
      update(form);
      form.addEventListener("change", function () { update(form); });
    });
    document.querySelectorAll("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = document.getElementById(btn.getAttribute("data-copy"));
        if (target && navigator.clipboard) {
          navigator.clipboard.writeText(target.innerText);
          btn.textContent = "Copied";
        }
      });
    });
    document.querySelectorAll("[data-print]").forEach(function (btn) {
      btn.addEventListener("click", function () { window.print(); });
    });
  });
})();
