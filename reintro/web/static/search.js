// The search form: a "Searching…" state while the next page loads, the sort menu
// submitting the form when it changes, and empty fields left out of the address.
(function () {
  function trim(form) {
    Array.prototype.forEach.call(form.elements, function (el) {
      if (el.name && el.value === "" && !el.disabled) { el.disabled = true; el.dataset.trimmed = "1"; }
    });
  }
  function untrim() {
    document.querySelectorAll("[data-trimmed]").forEach(function (el) {
      el.disabled = false; delete el.dataset.trimmed;
    });
  }
  function busy() {
    var form = document.querySelector("[data-busy-form]");
    var status = document.querySelector("[data-busy]");
    if (status) status.hidden = false;
    if (form) {
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll("[data-busy-label]").forEach(function (b) {
        b.dataset.idle = b.textContent; b.textContent = b.dataset.busyLabel; b.disabled = true;
      });
    }
    document.body.classList.add("is-busy");
  }
  function idle() {
    var form = document.querySelector("[data-busy-form]");
    var status = document.querySelector("[data-busy]");
    if (status) status.hidden = true;
    if (form) {
      form.removeAttribute("aria-busy");
      form.querySelectorAll("[data-busy-label]").forEach(function (b) {
        if (b.dataset.idle) { b.textContent = b.dataset.idle; b.disabled = false; }
      });
    }
    document.body.classList.remove("is-busy");
  }
  document.addEventListener("submit", function (e) {
    if (e.target.matches("[data-busy-form]")) { trim(e.target); busy(); }
  });
  document.addEventListener("click", function (e) {
    var a = e.target.closest("[data-busy-link]");
    if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) busy();
  });
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-autosubmit]") && e.target.form) {
      trim(e.target.form);
      busy();
      e.target.form.submit();
    }
  });
  // A page restored from the back/forward cache comes back in its busy state.
  window.addEventListener("pageshow", function () { untrim(); idle(); });
})();
