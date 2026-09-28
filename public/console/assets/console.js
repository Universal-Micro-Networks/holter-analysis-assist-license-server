// Loaded as an external file because the console CSP forbids inline scripts.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copyTarget);
  if (!source) return;
  try {
    await navigator.clipboard.writeText(source.textContent.trim());
    button.textContent = "コピーしました";
  } catch {
    button.textContent = "コピーできませんでした";
  }
});

// Server-side request ids are the real double-submit guard; this only avoids accidental double clicks.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.dataset.submitted === "true") {
    event.preventDefault();
    return;
  }
  form.dataset.submitted = "true";
  for (const button of form.querySelectorAll("button[type=submit]")) button.disabled = true;
});

// Search forms have no button: Enter in the text field submits natively, and choosing an option submits here.
document.addEventListener("change", (event) => {
  const form = event.target.closest("form[data-auto-submit]");
  if (form && event.target.matches("select")) form.requestSubmit();
});

// A page restored with the back button keeps the submitted mark, which would block the next submission.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  for (const form of document.querySelectorAll("form[data-submitted]")) {
    delete form.dataset.submitted;
    for (const button of form.querySelectorAll("button[type=submit]")) button.disabled = false;
  }
});
