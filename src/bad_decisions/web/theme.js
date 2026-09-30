// Loaded without defer so a stored theme applies before the first paint.
(() => {
  const key = "bad-decisions-theme";
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  let stored = null;
  try { stored = localStorage.getItem(key); } catch (_) {}
  if (stored === "light" || stored === "dark") root.dataset.theme = stored;
  const current = () => root.dataset.theme || (media && media.matches ? "dark" : "light");
  function label(button) {
    const next = current() === "dark" ? "light" : "dark";
    button.textContent = next === "dark" ? "Dark mode" : "Light mode";
    button.setAttribute("aria-label", `Switch to ${next} mode`);
  }
  document.addEventListener("DOMContentLoaded", () => {
    const button = document.querySelector("#theme-toggle");
    if (!button) return;
    label(button);
    button.addEventListener("click", () => {
      const next = current() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem(key, next); } catch (_) {}
      label(button);
    });
    if (media && media.addEventListener) media.addEventListener("change", () => label(button));
  });
})();
