(() => {
  const KEY = "anchor-currency";
  const body = document.body;
  const buttons = document.querySelectorAll(".currency-toggle button[data-currency]");

  const apply = (cur) => {
    body.dataset.currency = cur;
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.currency === cur));
    try { localStorage.setItem(KEY, cur); } catch (_) {}
  };

  const initial = (() => {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === "usd" || saved === "jpy") return saved;
    } catch (_) {}
    return body.dataset.currency || "usd";
  })();
  apply(initial);

  buttons.forEach((b) => b.addEventListener("click", () => apply(b.dataset.currency)));
})();
