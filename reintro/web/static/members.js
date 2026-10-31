// The sponsor and cosponsor filters on the search page: a type-ahead over everyone who
// sponsored or cosponsored a bill in the database (/api/v1/members). Choosing a member
// searches by bioguide id; text typed without choosing searches names, as the plain field
// does. The list follows the ARIA combobox pattern: arrow keys move through it, Enter
// chooses, Escape closes it.
(function () {
  var loading = null;
  var LIMIT = 30;

  function load() {
    if (!loading) {
      loading = fetch("/api/v1/members", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : { members: [] }; })
        .then(function (d) { return d.members.map(prep); })
        .catch(function () { loading = null; return []; });
    }
    return loading;
  }

  function fold(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }
  function words(s) { return fold(s).split(/[^a-z0-9]+/).filter(Boolean); }

  function prep(m) {
    var name = m.name || m.id;
    m.last = words(name.split(",")[0]);
    m.words = words(name).concat([fold(m.id), fold(m.state)]);
    return m;
  }

  function ordinal(n) {
    var s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  // "House 118th · Senate 119th", or "House 118th, 119th".
  function served(m) {
    var by = {}, order = [];
    Object.keys(m.chambers).sort().forEach(function (c) {
      var ch = m.chambers[c];
      if (!by[ch]) { by[ch] = []; order.push(ch); }
      by[ch].push(ordinal(+c));
    });
    return order.map(function (ch) {
      return (ch === "senate" ? "Senate " : "House ") + by[ch].join(", ");
    }).join(" · ");
  }

  function count(m, role, congress) {
    var c = m.counts[congress];
    return c ? c[role] || 0 : 0;
  }

  // Every typed word must begin a word of the name, the state or the bioguide id. A match on
  // the last name comes first, then members with bills in this role in the chosen Congress.
  function matches(list, q, role, congress) {
    var toks = words(q);
    if (!toks.length) return [];
    var out = [];
    list.forEach(function (m) {
      var ok = toks.every(function (t) {
        return m.words.some(function (w) { return w.indexOf(t) === 0; });
      });
      if (!ok) return;
      var onLast = m.last.some(function (w) { return w.indexOf(toks[0]) === 0; });
      out.push({ m: m, rank: [onLast ? 0 : 1, count(m, role, congress) ? 0 : 1] });
    });
    out.sort(function (a, b) {
      return a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1] ||
        a.m.name.localeCompare(b.m.name);
    });
    return out.map(function (x) { return x.m; });
  }

  function enhance(input) {
    var role = input.dataset.memberCombo;
    var field = input.parentNode;
    var label = field.querySelector("label");
    var hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = input.name;
    hidden.value = input.value;
    input.removeAttribute("name");
    if (input.dataset.label) input.value = input.dataset.label;
    field.appendChild(hidden);

    var list = document.createElement("ul");
    list.id = input.id + "-list";
    list.className = "combo__list";
    list.setAttribute("role", "listbox");
    if (label) {
      label.id = label.id || input.id + "-label";
      list.setAttribute("aria-labelledby", label.id);
    }
    list.hidden = true;
    field.appendChild(list);

    var status = document.createElement("span");
    status.className = "sr-only";
    status.setAttribute("role", "status");
    field.appendChild(status);

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", list.id);

    var shown = [], active = -1, seq = 0;

    function congress() {
      var c = input.form && input.form.querySelector("[name=congress]");
      return c ? c.value : "";
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function setActive(i) {
      var opts = list.children;
      if (active >= 0 && opts[active]) opts[active].setAttribute("aria-selected", "false");
      active = i;
      if (i < 0 || !opts[i]) { input.removeAttribute("aria-activedescendant"); return; }
      opts[i].setAttribute("aria-selected", "true");
      input.setAttribute("aria-activedescendant", opts[i].id);
      opts[i].scrollIntoView({ block: "nearest" });
    }

    function render(found) {
      var c = congress();
      shown = found.slice(0, LIMIT);
      list.textContent = "";
      shown.forEach(function (m, i) {
        var li = document.createElement("li");
        li.id = input.id + "-opt-" + i;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        li.dataset.index = i;
        var name = document.createElement("span");
        name.className = "combo__name";
        name.textContent = m.name;
        var meta = document.createElement("span");
        meta.className = "combo__meta";
        var n = count(m, role, c);
        meta.textContent = [m.party && m.state ? m.party + "-" + m.state : m.state, served(m),
          c ? n + " " + (role === "sponsor" ? "sponsored" : "cosponsored") + " in the " + ordinal(+c) : ""]
          .filter(Boolean).join(" · ");
        li.appendChild(name);
        li.appendChild(document.createTextNode(" "));
        li.appendChild(meta);
        list.appendChild(li);
      });
      active = -1;
      input.removeAttribute("aria-activedescendant");
      var open = shown.length > 0;
      list.hidden = !open;
      input.setAttribute("aria-expanded", open ? "true" : "false");
      status.textContent = !input.value.trim() ? "" : found.length === 0 ? "No member matches" :
        found.length > LIMIT ? "First " + LIMIT + " of " + found.length + " members" :
        found.length + (found.length === 1 ? " member" : " members");
    }

    function update() {
      var mine = ++seq, q = input.value;
      load().then(function (all) {
        if (mine === seq && document.activeElement === input) render(matches(all, q, role, congress()));
      });
    }

    function choose(i) {
      var m = shown[i];
      if (!m) return;
      input.value = m.name;
      hidden.value = m.id;
      status.textContent = m.name + " chosen";
      close();
    }

    input.addEventListener("focus", load);
    input.addEventListener("input", function () {
      hidden.value = input.value.trim();
      update();
    });
    input.addEventListener("keydown", function (e) {
      var open = !list.hidden;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!open) { update(); return; }
        var n = shown.length;
        setActive(e.key === "ArrowDown" ? (active + 1) % n : (active <= 0 ? n - 1 : active - 1));
      } else if (e.key === "Enter" && open && active >= 0) {
        e.preventDefault();
        choose(active);
      } else if (e.key === "Escape" && open) {
        e.preventDefault();
        close();
      } else if (e.key === "Tab") {
        close();
      }
    });
    input.addEventListener("blur", close);
    // Keep the focus in the input while an option is clicked.
    list.addEventListener("mousedown", function (e) { e.preventDefault(); });
    list.addEventListener("click", function (e) {
      var li = e.target.closest("[role=option]");
      if (li) choose(+li.dataset.index);
    });
  }

  document.querySelectorAll("[data-member-combo]").forEach(enhance);
})();
