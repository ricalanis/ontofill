/* Entity graph: progressive enhancement only. Without this file every node is a link to its entity page and every
 * edge or cluster a link that reloads the page with the selection panel filled (query parameter `sel`).
 * With it: hover or focus highlights a node's neighbours, a pointer click (or Space) selects in place, Enter still
 * opens the entity page, and arrow keys move focus to the nearest node in that direction. */
(function () {
  "use strict";
  var svg = document.getElementById("ggraph");
  var panel = document.getElementById("graph-panel");
  if (!svg || !panel) return;
  var nodes = Array.prototype.slice.call(svg.querySelectorAll(".gnode"));
  var edges = Array.prototype.slice.call(svg.querySelectorAll(".gedge"));
  var byId = {}, adj = {}, edgesOf = {};
  nodes.forEach(function (n) { byId[n.dataset.id] = n; adj[n.dataset.id] = []; edgesOf[n.dataset.id] = []; });
  edges.forEach(function (e) {
    var s = e.dataset.s, t = e.dataset.t;
    if (adj[s] && adj[t]) { adj[s].push(t); adj[t].push(s); edgesOf[s].push(e); edgesOf[t].push(e); }
  });

  function clear() {
    svg.classList.remove("hl");
    svg.querySelectorAll(".is-nb, .is-hot, .is-hl").forEach(function (el) { el.classList.remove("is-nb", "is-hot", "is-hl"); });
  }
  function highlight(id) {
    clear();
    if (!byId[id]) return;
    svg.classList.add("hl");
    byId[id].classList.add("is-hot");
    adj[id].forEach(function (o) { byId[o].classList.add("is-nb"); });
    edgesOf[id].forEach(function (e) { e.classList.add("is-hl"); });
  }
  nodes.forEach(function (n) {
    n.addEventListener("mouseenter", function () { highlight(n.dataset.id); });
    n.addEventListener("mouseleave", clear);
    n.addEventListener("focus", function () { highlight(n.dataset.id); });
    n.addEventListener("blur", clear);
  });

  function markSelected(id) {
    svg.querySelectorAll(".is-sel").forEach(function (el) { el.classList.remove("is-sel"); });
    var el = byId[id] || svg.querySelector('.gedge[data-id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
    if (el) el.classList.add("is-sel");
  }
  var pending = null;
  function select(url, id) {
    var clean = url.split("#")[0];
    if (pending) pending.abort();
    pending = new AbortController();
    panel.setAttribute("aria-busy", "true");
    fetch(clean, { signal: pending.signal, headers: { "Accept": "text/html" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.getElementById("graph-panel");
        if (!fresh) throw new Error("no panel");
        panel.innerHTML = fresh.innerHTML;
        panel.removeAttribute("aria-busy");
        markSelected(id);
        try { history.replaceState(null, "", clean); } catch (e) { /* file:// or sandboxed */ }
        if (window.matchMedia("(max-width: 900px)").matches) panel.scrollIntoView({ block: "nearest" });
      })
      .catch(function (err) { if (err.name !== "AbortError") window.location.href = url; });
  }

  nodes.forEach(function (n) {
    n.addEventListener("click", function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;  // new tab / window: leave it
      if (ev.detail === 0 && n.classList.contains("gnode--entity")) return;   // keyboard Enter opens the entity
      ev.preventDefault();
      select(n.dataset.sel, n.dataset.id);
    });
    n.addEventListener("dblclick", function () { if (n.classList.contains("gnode--entity")) window.location.href = n.getAttribute("href"); });
  });
  edges.forEach(function (e) {
    e.addEventListener("click", function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
      ev.preventDefault();
      if (e.classList.contains("gedge--spoke")) { var hub = byId[e.dataset.t]; if (hub) select(hub.dataset.sel, hub.dataset.id); return; }
      select(e.getAttribute("href"), e.dataset.id);
    });
  });

  // keyboard: Space selects, arrows move to the nearest node in that direction
  var DIRS = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
  svg.addEventListener("keydown", function (ev) {
    var n = ev.target.closest && ev.target.closest(".gnode");
    if (!n) return;
    if (ev.key === " " || ev.key === "Spacebar") { ev.preventDefault(); select(n.dataset.sel, n.dataset.id); return; }
    var d = DIRS[ev.key];
    if (!d) return;
    ev.preventDefault();
    var x = +n.dataset.x, y = +n.dataset.y, best = null, bestScore = Infinity;
    nodes.forEach(function (o) {
      if (o === n) return;
      var dx = +o.dataset.x - x, dy = +o.dataset.y - y;
      var along = dx * d[0] + dy * d[1];
      if (along <= 0) return;
      var across = Math.abs(dx * d[1] - dy * d[0]);
      var score = along + 2 * across;
      if (score < bestScore) { bestScore = score; best = o; }
    });
    if (best) {
      best.focus();
      var r = best.getBoundingClientRect(), wrap = svg.parentElement.getBoundingClientRect();
      if (r.left < wrap.left || r.right > wrap.right || r.top < wrap.top || r.bottom > wrap.bottom) {
        best.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }
  });
})();
