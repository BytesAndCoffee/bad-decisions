const packsElement = document.querySelector("#packs");
const feedbackElement = document.querySelector("#feedback");
const feedbackStatus = document.querySelector("#feedback-status");
let currentFeedback = null;
const identityKey = "bad-decisions-regret-client-id";
const identityOffKey = "bad-decisions-regret-identity-off";
const consequencesKey = "bad-decisions-consequences";
const consequencesModal = document.querySelector("#consequences-modal");
function consequencesEnabled() { return localStorage.getItem(consequencesKey) === "enjoy"; }
let sessionId = crypto.randomUUID ? crypto.randomUUID() : null;
function clientHeaders() {
  if (!consequencesEnabled()) return { Accept: "application/json" };
  if (localStorage.getItem(identityOffKey) === "true") return { Accept: "application/json" };
  let clientId = null;
  try { clientId = localStorage.getItem(identityKey) || crypto.randomUUID(); localStorage.setItem(identityKey, clientId); } catch (_) { clientId = crypto.randomUUID ? crypto.randomUUID() : null; }
  return { Accept: "application/json", ...(clientId ? {"X-Regret-Client-ID":clientId} : {}), ...(sessionId ? {"X-Regret-Session-ID":sessionId} : {}) };
}
function renderFeedback() {
  const available = consequencesEnabled() && Boolean(currentFeedback && currentFeedback.token);
  feedbackElement.hidden = !available;
  feedbackElement.querySelectorAll("[data-vote]").forEach((button) => { button.disabled = !available; button.setAttribute("aria-pressed", String((button.dataset.vote === "true" && currentFeedback?.choice === true) || (button.dataset.vote === "false" && currentFeedback?.choice === false))); });
}
async function vote(choice) {
  const feedback = currentFeedback;
  if (!feedback) return;
  feedbackStatus.textContent = "Recording your consequences...";
  try {
    const response = await fetch(feedback.url, {method:choice === "clear" ? "DELETE" : "PUT", headers:{"Content-Type":"application/json","X-Regret-Feedback-Token":feedback.token}, body:choice === "clear" ? undefined : JSON.stringify({enjoyed:choice === "true"})});
    if (!response.ok && response.status !== 204) throw new Error();
    if (currentFeedback !== feedback) return;
    feedback.choice = choice === "clear" ? null : choice === "true";
    feedbackStatus.textContent = choice === "clear" ? "Feedback withdrawn." : "Noted. We will not judge.";
    renderFeedback();
  } catch (_) { if (currentFeedback === feedback) feedbackStatus.textContent = "Feedback was not saved. You can retry."; }
}
const dealButton = document.querySelector("#deal");
const statusElement = document.querySelector("#status");
const roundElement = document.querySelector("#round");
const blackElement = document.querySelector("#black");
const answersElement = document.querySelector("#answers");
const resultElement = document.querySelector("#result");
const emptyRoundElement = document.querySelector("#empty-round");
const packSummaryElement = document.querySelector("#pack-summary");
const indexedPacksElement = document.querySelector("#indexed-packs");
const indexedPackSelection = document.querySelector("#indexed-pack-selection");
const indexedPacksModal = document.querySelector("#indexed-packs-modal");
const indexedPackOptions = document.querySelector("#indexed-pack-options");
let indexedPacks = [];
let indexedSelection = new Set();
let indexedDraft = new Set();
let indexedModalReturnFocus = null;
const apiBase = window.location.pathname.replace(/\/web\/?$/, "/v1");

function apiUrl(path) {
  return `${apiBase}/${path}`;
}

function setStatus(message) {
  statusElement.textContent = message;
}

function selectedPacks() {
  const selected = [...document.querySelectorAll("input[name=pack]:checked")].map((input) => input.value);
  return [...selected, ...indexedSelection];
}

function updateIndexedPackSelection() {
  const count = indexedSelection.size;
  indexedPackSelection.textContent = count ? `${count} indexed pack${count === 1 ? "" : "s"} selected` : "No indexed packs selected";
}

function renderIndexedPackOptions() {
  indexedPackOptions.replaceChildren(...indexedPacks.map((pack) => {
    const label = document.createElement("label");
    label.className = "indexed-pack-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = pack.id;
    input.checked = indexedDraft.has(pack.id);
    input.addEventListener("change", () => {
      if (input.checked) indexedDraft.add(pack.id);
      else indexedDraft.delete(pack.id);
    });
    const text = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = pack.name;
    const counts = document.createElement("small");
    counts.textContent = `${pack.counts.black} prompts · ${pack.counts.white} answers`;
    text.append(name, counts);
    label.append(input, text);
    return label;
  }));
}

function closeIndexedPackModal() {
  indexedPacksModal.hidden = true;
  indexedModalReturnFocus?.focus();
  indexedModalReturnFocus = null;
}

function openIndexedPackModal() {
  if (!indexedPacks.length) return;
  indexedDraft = new Set(indexedSelection);
  renderIndexedPackOptions();
  indexedModalReturnFocus = document.activeElement;
  indexedPacksModal.hidden = false;
  indexedPackOptions.querySelector("input")?.focus();
}

function updatePackSummary() {
  const selected = selectedPacks();
  packSummaryElement.textContent = selected.length ? `${selected.length} pack${selected.length === 1 ? "" : "s"} selected. Taste is optional.` : "No packs selected. Bold strategy.";
}

async function loadPacks() {
  try {
    const response = await fetch(apiUrl("packs"), { headers: clientHeaders() });
    if (!response.ok) throw new Error("Could not load packs");
    const packs = await response.json();
    indexedPacks = packs.filter((pack) => pack.id.startsWith("pyx-"));
    const regularPacks = packs.filter((pack) => !pack.id.startsWith("pyx-"));
    packsElement.replaceChildren(...regularPacks.map((pack) => {
      const label = document.createElement("label");
      label.className = "pack";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "pack";
      input.value = pack.id;
      input.checked = true;
      const text = document.createElement("span");
      text.textContent = `${pack.name} (${pack.counts.black}/${pack.counts.white})`;
      label.append(input, text);
      input.addEventListener("change", updatePackSummary);
      return label;
    }));
    indexedSelection = new Set([...indexedSelection].filter((id) => indexedPacks.some((pack) => pack.id === id)));
    indexedPacksElement.disabled = !indexedPacks.length;
    indexedPacksElement.querySelector("b").textContent = indexedPacks.length ? "Choose" : "Unavailable";
    updateIndexedPackSelection();
    updatePackSummary();
    setStatus("");
  } catch (error) {
    setStatus("The API declined to provide bad decisions. Try refreshing.");
    dealButton.disabled = true;
  }
}

async function deal() {
  const packs = selectedPacks();
  if (!packs.length) {
    setStatus("Pick at least one pack. Coward.");
    return;
  }
  dealButton.disabled = true;
  setStatus("Consulting the machine…");
  try {
    const response = await fetch(`${apiUrl("round")}?packs=${encodeURIComponent(packs.join(","))}`, { headers: clientHeaders() });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || "Round failed");
    blackElement.textContent = body.black.repr;
    answersElement.replaceChildren(...body.white.map((card) => {
      const answer = document.createElement("p");
      answer.textContent = card.text;
      return answer;
    }));
    resultElement.textContent = body.result;
    const token = response.headers.get("X-Regret-Feedback-Token");
    currentFeedback = consequencesEnabled() && body.feedback?.available && token ? { url: body.feedback.url, token, choice: null } : null;
    feedbackStatus.textContent = "";
    renderFeedback();
    roundElement.hidden = false;
    emptyRoundElement.hidden = true;
    setStatus("");
  } catch (error) {
    setStatus(error.message || "Something went terribly, professionally wrong.");
  } finally {
    dealButton.disabled = false;
  }
}

document.querySelector("#all-packs").addEventListener("click", () => {
  document.querySelectorAll("input[name=pack]").forEach((input) => { input.checked = true; });
  indexedSelection = new Set(indexedPacks.map((pack) => pack.id));
  updateIndexedPackSelection();
  updatePackSummary();
});
indexedPacksElement.addEventListener("click", openIndexedPackModal);
document.querySelector("#close-indexed-packs").addEventListener("click", closeIndexedPackModal);
document.querySelector("#cancel-indexed-packs").addEventListener("click", closeIndexedPackModal);
document.querySelector("#all-indexed-packs").addEventListener("click", () => { indexedDraft = new Set(indexedPacks.map((pack) => pack.id)); renderIndexedPackOptions(); });
document.querySelector("#clear-indexed-packs").addEventListener("click", () => { indexedDraft.clear(); renderIndexedPackOptions(); });
document.querySelector("#apply-indexed-packs").addEventListener("click", () => { indexedSelection = new Set(indexedDraft); updateIndexedPackSelection(); updatePackSummary(); closeIndexedPackModal(); });
indexedPacksModal.addEventListener("click", (event) => { if (event.target === indexedPacksModal) closeIndexedPackModal(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !indexedPacksModal.hidden) closeIndexedPackModal(); });
dealButton.addEventListener("click", deal);
loadPacks();
feedbackElement.querySelectorAll("[data-vote]").forEach((button) => button.addEventListener("click", () => vote(button.dataset.vote)));
document.querySelector("#reset-identity").addEventListener("click", () => { try { localStorage.removeItem(identityKey); localStorage.removeItem(identityOffKey); } catch (_) {} sessionId = crypto.randomUUID ? crypto.randomUUID() : null; feedbackStatus.textContent = "Analytics identity reset for future deals."; });
document.querySelector("#omit-identity").addEventListener("click", () => { try { localStorage.setItem(identityOffKey, "true"); } catch (_) {} feedbackStatus.textContent = "Analytics identity will be omitted for future deals."; });

function setConsequences(value) {
  try { localStorage.setItem(consequencesKey, value); } catch (_) {}
  consequencesModal.hidden = true;
  renderFeedback();
}
document.querySelector("#enjoy-consequences").addEventListener("click", () => setConsequences("enjoy"));
document.querySelector("#change-consequences").addEventListener("click", () => { consequencesModal.hidden = false; document.querySelector("#enjoy-consequences").focus(); });
document.querySelector("#regret-consequences").addEventListener("click", () => setConsequences("regret"));
if (!localStorage.getItem(consequencesKey)) consequencesModal.hidden = false;
