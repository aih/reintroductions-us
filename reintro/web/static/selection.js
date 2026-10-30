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
    document.querySelectorAll(".sel-toggle").forEach(function (b) {
      var on = has(list, b.dataset.sel);
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.textContent = on ? "Remove from selection" : "Add to selection";
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
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".sel-toggle");
    if (b) {
      var list = read();
      if (has(list, b.dataset.sel)) list = list.filter(function (x) { return x.id !== b.dataset.sel; });
      else list.push({ id: b.dataset.sel, label: b.dataset.label });
      write(list);
    }
    if (e.target.closest("[data-sel-clear]")) write([]);
  });
  document.addEventListener("DOMContentLoaded", update);
})();
