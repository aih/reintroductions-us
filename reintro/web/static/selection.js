// The selection of bills, kept in this browser's localStorage.
(function () {
  var KEY = "reintro-selection";
  function read() {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
  }
  function write(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) { /* storage unavailable */ }
    update();
  }
  function has(list, id) { return list.some(function (x) { return x.id === id; }); }
  function update() {
    var list = read();
    document.querySelectorAll("[data-sel-count]").forEach(function (el) { el.textContent = list.length; });
    var boxes = Array.prototype.slice.call(document.querySelectorAll(".sel-check"));
    boxes.forEach(function (c) { c.checked = has(list, c.dataset.sel); });
    document.querySelectorAll("[data-sel-all]").forEach(function (all) {
      var n = boxes.filter(function (c) { return c.checked; }).length;
      all.disabled = boxes.length === 0;
      all.checked = boxes.length > 0 && n === boxes.length;
      all.indeterminate = n > 0 && n < boxes.length;
    });
    var ul = document.querySelector("[data-sel-list]");
    if (ul) {
      ul.innerHTML = "";
      list.forEach(function (x) {
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.href = "/bills/" + encodeURIComponent(x.id);
        a.textContent = x.label || x.id;
        var rm = document.createElement("button");
        rm.type = "button"; rm.className = "button--plain"; rm.textContent = "Remove";
        rm.setAttribute("aria-label", "Remove " + (x.label || x.id));
        rm.addEventListener("click", function () { write(read().filter(function (y) { return y.id !== x.id; })); });
        li.appendChild(a); li.appendChild(document.createTextNode(" ")); li.appendChild(rm);
        ul.appendChild(li);
      });
      var ids = list.slice(0, 50).map(function (x) { return x.id; }).join(",");
      var empty = document.querySelector("[data-sel-empty]");
      var actions = document.querySelector("[data-sel-actions]");
      if (empty) empty.hidden = list.length > 0;
      if (actions) actions.hidden = list.length === 0;
      var dl = document.querySelector("[data-sel-download]");
      if (dl) dl.href = "/download?ids=" + encodeURIComponent(ids);
      var dx = document.querySelector("[data-sel-download-xml]");
      if (dx) dx.href = "/download?pdf=0&ids=" + encodeURIComponent(ids);
    }
  }
  function set(list, box, on) {
    var rest = list.filter(function (x) { return x.id !== box.dataset.sel; });
    if (on) rest.push({ id: box.dataset.sel, label: box.dataset.label });
    return rest;
  }
  document.addEventListener("change", function (e) {
    if (e.target.matches(".sel-check")) write(set(read(), e.target, e.target.checked));
    if (e.target.matches("[data-sel-all]")) {
      var on = e.target.checked, list = read();
      document.querySelectorAll(".sel-check").forEach(function (c) { list = set(list, c, on); });
      write(list);
    }
  });
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-sel-clear]")) write([]);
  });
  // Another tab changed the selection.
  window.addEventListener("storage", function (e) { if (e.key === KEY) update(); });
  window.addEventListener("pageshow", update);
  document.addEventListener("DOMContentLoaded", update);
})();
