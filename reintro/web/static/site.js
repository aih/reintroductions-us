// A button with data-toggle="ID" shows and hides the element with that id.
(function () {
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-toggle]");
    if (!b) return;
    var el = document.getElementById(b.dataset.toggle);
    if (!el) return;
    var open = el.hidden;
    el.hidden = !open;
    b.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();
