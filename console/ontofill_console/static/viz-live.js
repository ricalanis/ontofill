// Ontofill Console · live streaming for the viz views (operation, needs you). Progressive enhancement, no libraries.
// While the page's [data-live-root] carries data-live="1" (a run in motion), re-fetch this same URL every few seconds,
// parse it, and swap only the [data-live-region] elements whose HTML changed, keeping focus and open <details>.
// Polling pauses while the tab is hidden, backs off on errors, and stops for good once the fetched page is no longer
// live (the run finished): recorded and finished runs never poll.
(() => {
  const first = document.querySelector("[data-live-root]");
  if (!first || first.dataset.live !== "1") return;
  const base = Number(first.dataset.livePoll) || 2500;
  const MAX = 30000;
  const QUIET = ".strip__meta, [data-live-quiet]"; // ticking ages: swapped in, never highlighted
  const CHROME = ['.masthead .tabs a[href="/inbox"]', '.subnav a[href$="/approvals"]']; // counts outside the regions
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  let delay = base;
  let timer = null;
  let busy = false;
  let stopped = false;

  const regions = (doc) => new Map([...doc.querySelectorAll("[data-live-region]")].map((el) => [el.dataset.liveRegion, el]));

  // Comparable HTML: without our own highlight class, the reader's open/closed <details>, and optionally the ages.
  function norm(el, quiet) {
    const c = el.cloneNode(true);
    for (const n of [c, ...c.querySelectorAll(".live-changed")]) {
      n.classList.remove("live-changed");
      if (!n.getAttribute("class")) n.removeAttribute("class");
    }
    for (const d of c.querySelectorAll("details")) d.removeAttribute("open");
    if (quiet) for (const q of c.querySelectorAll(QUIET)) q.remove();
    return c.outerHTML.replace(/\s+/g, " ");
  }

  const detailsKey = (d, i) => d.id || d.querySelector("summary")?.textContent.trim() || String(i);

  function focusKey(region) {
    const el = document.activeElement;
    if (!el || el === document.body || !region.contains(el)) return null;
    if (el.id) return { sel: `#${CSS.escape(el.id)}` };
    const href = el.getAttribute("href");
    if (href) return { sel: `${el.tagName.toLowerCase()}[href="${CSS.escape(href)}"]` };
    const path = [];
    for (let n = el; n && n !== region; n = n.parentElement) path.unshift([...n.parentElement.children].indexOf(n));
    return { path };
  }

  function restoreFocus(region, key) {
    let el = key.sel ? region.querySelector(key.sel) : null;
    if (!el && key.path) {
      el = region;
      for (const i of key.path) el = el && el.children[i];
    }
    if (el && typeof el.focus === "function") el.focus({ preventScroll: true });
  }

  function swap(cur, next) {
    if (norm(cur, false) === norm(next, false)) return;
    const changed = norm(cur, true) !== norm(next, true);
    const fk = focusKey(cur);
    const open = new Map([...cur.querySelectorAll("details")].map((d, i) => [detailsKey(d, i), d.open]));
    const fresh = document.importNode(next, true);
    fresh.querySelectorAll("details").forEach((d, i) => {
      const k = detailsKey(d, i);
      if (open.has(k)) d.open = open.get(k);
    });
    cur.replaceWith(fresh);
    if (fk) restoreFocus(fresh, fk);
    if (changed && !reduced.matches) {
      fresh.classList.add("live-changed");
      setTimeout(() => fresh.classList.remove("live-changed"), 1600);
    }
  }

  function apply(doc) {
    const nextRoot = doc.querySelector("[data-live-root]");
    const cur = regions(document);
    const next = regions(doc);
    const same = cur.size === next.size && [...next.keys()].every((k) => cur.has(k));
    if (same) {
      for (const [k, el] of next) swap(cur.get(k), el);
    } else {
      // The page changed shape (a section appeared or went away): swap the whole main content, still no reload.
      const main = document.getElementById("main");
      const nextMain = doc.getElementById("main");
      if (main && nextMain) main.replaceChildren(...[...nextMain.childNodes].map((n) => document.importNode(n, true)));
    }
    for (const sel of CHROME) {
      const a = document.querySelector(sel);
      const b = doc.querySelector(sel);
      if (a && b && a.innerHTML !== b.innerHTML) a.innerHTML = b.innerHTML;
    }
    const root = document.querySelector("[data-live-root]");
    if (!nextRoot || nextRoot.dataset.live !== "1") {
      stopped = true;
      clearTimeout(timer);
      if (root && nextRoot) root.replaceWith(document.importNode(nextRoot, true));
      return;
    }
    // Only the time changes inside the aria-live span, so screen readers hear "updated hh:mm:ss" and nothing else.
    const u = root && root.querySelector("[data-live-updated]");
    const nu = nextRoot.querySelector("[data-live-updated]");
    if (u && nu && u.innerHTML !== nu.innerHTML) u.innerHTML = nu.innerHTML;
  }

  function schedule(ms) {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(tick, ms);
  }

  async function tick() {
    if (stopped || busy || document.hidden) return; // visibilitychange resumes a hidden tab
    busy = true;
    try {
      const res = await fetch(window.location.href, {
        headers: { Accept: "text/html", "X-Live-Poll": "1" },
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(String(res.status));
      apply(new DOMParser().parseFromString(await res.text(), "text/html"));
      delay = base;
    } catch {
      delay = Math.min(delay * 2, MAX); // keep what is on screen; try again later
    } finally {
      busy = false;
      schedule(delay);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(timer);
    else schedule(0);
  });
  schedule(base);
})();
