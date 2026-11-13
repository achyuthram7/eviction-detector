// Progressive enhancement only: every form works without JavaScript.
// Elements with data-show-when="field=value" are shown only when that radio/select has the value.
// While hidden, their fields are disabled, so they are neither required nor sent.
(function () {
  function setShown(el, shown) {
    el.hidden = !shown;
    if (el.tagName === "FIELDSET") {
      el.disabled = !shown;
      return;
    }
    el.querySelectorAll("input, select, textarea").forEach(function (c) { c.disabled = !shown; });
  }
  function update(form) {
    form.querySelectorAll("[data-show-when]").forEach(function (el) {
      var parts = el.getAttribute("data-show-when").split("=");
      var checked = form.querySelector('[name="' + parts[0] + '"]:checked');
      var select = form.querySelector('select[name="' + parts[0] + '"]');
      var value = checked ? checked.value : select ? select.value : "";
      setShown(el, value === parts[1]);
    });
    // A ticked "Not sure" box switches off the amount next to it (so it isn't required or sent).
    form.querySelectorAll("[data-unsure-for]").forEach(function (box) {
      var input = document.getElementById(box.getAttribute("data-unsure-for"));
      if (input && !box.disabled) input.disabled = box.checked;
    });
  }
  // Required questions: follow-ups and amounts with a "Not sure" box carry data-required. They
  // become required here, where they can also be switched off while hidden or "Not sure".
  // The browser's "fill out this field" message uses the page's language.
  function requireAnswers(form) {
    form.querySelectorAll("[data-required]").forEach(function (c) { c.required = true; });
    var msg = form.getAttribute("data-required-msg");
    if (!msg) return;
    form.addEventListener("invalid", function (e) {
      if (e.target.validity.valueMissing) e.target.setCustomValidity(msg);
    }, true);
    function clear(e) {
      var t = e.target;
      var group = t.type === "radio" && t.name
        ? form.querySelectorAll('input[type="radio"][name="' + t.name + '"]')
        : [t];
      Array.prototype.forEach.call(group, function (c) { c.setCustomValidity(""); });
    }
    form.addEventListener("input", clear, true);
    form.addEventListener("change", clear, true);
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.intake").forEach(function (form) {
      update(form);
      requireAnswers(form);
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
