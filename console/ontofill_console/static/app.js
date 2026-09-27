// Ontofill Console: progressive enhancement only; every page works without JavaScript.

// Live run: poll the feed, prepend new steps, update the panel in place so bars glide instead of jumping.
(() => {
  const view = document.getElementById("runview");
  if (!view) return;
  const steps = document.getElementById("steps");
  const panel = document.getElementById("run-panel");
  const timeline = document.getElementById("timeline");
  const state = document.getElementById("run-state");
  const proof = document.getElementById("proof");
  let count = Number(view.dataset.count || 0);
  let busy = false;

  function morph(target, html) {
    const next = document.createElement("div");
    next.innerHTML = html;
    const keys = (root) => [...root.querySelectorAll("[data-k]")].map((el) => el.dataset.k).join("|");
    if (keys(next) !== keys(target)) {
      target.innerHTML = html;
      return;
    }
    for (const el of next.querySelectorAll("[data-k]")) {
      const cur = target.querySelector(`[data-k="${CSS.escape(el.dataset.k)}"]`);
      if (!cur) continue;
      if (el.getAttribute("style") !== cur.getAttribute("style")) cur.setAttribute("style", el.getAttribute("style") || "");
      if (el.className !== cur.className) cur.className = el.className;
      if (el.innerHTML !== cur.innerHTML) cur.innerHTML = el.innerHTML;
    }
  }

  async function tick() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const res = await fetch(`${view.dataset.api}?after=${count}`, { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      if (data.count > count && data.steps_html.trim()) {
        const tmp = document.createElement("ol");
        tmp.innerHTML = data.steps_html;
        for (const li of tmp.children) li.classList.add("step--new");
        steps.prepend(...tmp.children);
        while (steps.children.length > 150) steps.lastElementChild.remove();
      }
      // A loop thread that gained iterations is re-rendered in place, keeping whether the reader had it open.
      for (const t of data.threads_html || []) {
        const tmp = document.createElement("ol");
        tmp.innerHTML = t.html;
        const next = tmp.firstElementChild;
        if (!next) continue;
        const cur = document.getElementById(t.id);
        if (cur) {
          const was = cur.querySelector("details");
          const now = next.querySelector("details");
          if (was && now) now.open = was.open;
          cur.replaceWith(next);
        } else {
          steps.prepend(next);
        }
      }
      count = data.count;
      morph(panel, data.panel_html);
      morph(timeline, data.timeline_html);
      if (proof && proof.innerHTML !== data.proof_html) proof.innerHTML = data.proof_html;
      state.textContent = data.state;
      state.className = `run-state run-state--${data.state}`;
      view.dataset.state = data.state;
      view.dataset.count = String(count);
    } catch {
      /* keep what is on screen; try again next tick */
    } finally {
      busy = false;
    }
  }
  setInterval(() => {
    if (view.dataset.state !== "done" && view.dataset.state !== "failed") tick();
  }, 1200);
})();
