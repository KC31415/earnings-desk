// Client-side ticker search: jumps to a company's page. Data: tickers.json [{t, n, note}].
(function () {
  const box = document.getElementById("search");
  if (!box) return;
  const input = box.querySelector("input");
  const list = box.querySelector("ul");
  const root = box.dataset.root || "";
  let items = [], sel = 0, shown = [];
  fetch(root + "tickers.json").then(r => r.json()).then(d => { items = d; });

  function url(it) { return root + "company/" + it.t + ".html"; }
  function render() {
    const q = input.value.trim().toUpperCase();
    if (!q) { list.style.display = "none"; return; }
    const starts = items.filter(it => it.t.startsWith(q));
    const named = items.filter(it => !it.t.startsWith(q) && it.n.toUpperCase().includes(q));
    shown = starts.concat(named).slice(0, 8);
    sel = 0;
    list.innerHTML = shown.length ? shown.map((it, i) =>
      `<li><a class="${i === 0 ? "sel" : ""}" href="${url(it)}"><span class="t">${it.t}</span>` +
      `<span>${it.n}</span>${it.note ? ' <span class="badge">verified AI note</span>' : ""}</a></li>`).join("")
      : '<li class="muted small" style="padding:8px 10px">No S&amp;P 500 or showcase company matches.</li>';
    list.style.display = "block";
  }
  input.addEventListener("input", render);
  input.addEventListener("keydown", e => {
    const links = list.querySelectorAll("a");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!links.length) return;
      links[sel].classList.remove("sel");
      sel = (sel + (e.key === "ArrowDown" ? 1 : links.length - 1)) % links.length;
      links[sel].classList.add("sel");
    } else if (e.key === "Enter" && shown.length) {
      window.location.href = url(shown[sel]);
    } else if (e.key === "Escape") {
      list.style.display = "none";
    }
  });
  document.addEventListener("click", e => { if (!box.contains(e.target)) list.style.display = "none"; });
})();
