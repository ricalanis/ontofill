// Site graph: select a page type and switch the coverage overlay without a reload.
// Progressive enhancement only: every state is also reachable by link (?type=, ?overlay=).
(function () {
  "use strict";
  var root = document.querySelector("[data-sg]");
  if (!root) return;
  var GLYPH = { covered: "●", hinted: "◐", none: "○" };
  var nodes = Array.prototype.slice.call(root.querySelectorAll(".sg-node"));
  var drills = Array.prototype.slice.call(document.querySelectorAll(".sg-drill"));
  var pick = document.getElementById("sg-pick");
  var live = document.getElementById("sg-live");
  var form = root.querySelector(".sg-overlay");
  var select = document.getElementById("sg-ov");
  var state = { type: null, overlay: select ? select.value || null : null };
  nodes.forEach(function (n) { if (n.classList.contains("is-sel")) state.type = n.dataset.type; });

  function syncUrl() {
    var u = new URL(window.location.href);
    ["type", "overlay"].forEach(function (k) {
      if (state[k]) u.searchParams.set(k, state[k]); else u.searchParams.delete(k);
    });
    history.replaceState(null, "", u.pathname + u.search + u.hash);
    var hidden = form && form.querySelector("input[name=type]");
    if (form && state.type) {
      if (!hidden) { hidden = document.createElement("input"); hidden.type = "hidden"; hidden.name = "type"; form.appendChild(hidden); }
      hidden.value = state.type;
    }
  }

  function selectType(tid) {
    state.type = tid;
    var label = "";
    nodes.forEach(function (n) {
      var on = n.dataset.type === tid;
      n.classList.toggle("is-sel", on);
      if (on) { n.setAttribute("aria-current", "true"); label = n.dataset.aria || ""; }
      else n.removeAttribute("aria-current");
    });
    drills.forEach(function (d) { d.hidden = d.dataset.type !== tid; });
    if (pick) pick.hidden = !!tid;
    document.querySelectorAll(".sg-list details").forEach(function (d) { if (d.dataset.type === tid) d.open = true; });
    if (live) live.textContent = tid ? "Selected " + label.split(".")[0] + ". Its pages are listed in the selected page type panel." : "";
    syncUrl();
  }

  function applyOverlay(pid) {
    state.overlay = pid || null;
    nodes.forEach(function (n) {
      var map = {};
      try { map = JSON.parse(n.dataset.cov || "{}"); } catch (e) { map = {}; }
      var s = pid ? map[pid] || "none" : null;
      n.classList.remove("cov-covered", "cov-hinted", "cov-none");
      if (s) n.classList.add("cov-" + s);
      var g = n.querySelector(".sg-glyph");
      if (g) g.textContent = s ? GLYPH[s] : "";
      n.setAttribute("aria-label", (n.dataset.aria || "") + (s ? " Coverage: " + s + "." : ""));
    });
    document.querySelectorAll(".sg-covsum").forEach(function (p) { p.hidden = p.dataset.prop !== pid; });
    var legend = document.getElementById("sg-covlegend");
    if (legend) legend.hidden = !pid;
    syncUrl();
  }

  nodes.forEach(function (n) {
    n.addEventListener("click", function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return;
      e.preventDefault();
      selectType(n.dataset.type);
    });
  });
  if (select) {
    if (form) form.classList.add("is-live");
    select.addEventListener("change", function () { applyOverlay(select.value); });
  }
})();
