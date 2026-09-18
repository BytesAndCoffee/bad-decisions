const packsElement = document.querySelector("#packs");
const dealButton = document.querySelector("#deal");
const statusElement = document.querySelector("#status");
const roundElement = document.querySelector("#round");
const blackElement = document.querySelector("#black");
const answersElement = document.querySelector("#answers");
const resultElement = document.querySelector("#result");
const emptyRoundElement = document.querySelector("#empty-round");
const packSummaryElement = document.querySelector("#pack-summary");
const indexedPacksElement = document.querySelector("#indexed-packs");
let indexedPackIds = [];
const apiBase = window.location.pathname.replace(/\/web\/?$/, "/v1");

function apiUrl(path) {
  return `${apiBase}/${path}`;
}

function setStatus(message) {
  statusElement.textContent = message;
}

function selectedPacks() {
  const selected = [...document.querySelectorAll("input[name=pack]:checked")].map((input) => input.value);
  if (indexedPacksElement.value === "__all_indexed__") return [...selected, ...indexedPackIds];
  if (indexedPacksElement.value) selected.push(indexedPacksElement.value);
  return selected;
}

function updatePackSummary() {
  const selected = selectedPacks();
  packSummaryElement.textContent = selected.length ? `${selected.length} pack${selected.length === 1 ? "" : "s"} selected. Taste is optional.` : "No packs selected. Bold strategy.";
}

async function loadPacks() {
  try {
    const response = await fetch(apiUrl("packs"), { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Could not load packs");
    const packs = await response.json();
    const indexedPacks = packs.filter((pack) => pack.id.startsWith("pyx-"));
    const regularPacks = packs.filter((pack) => !pack.id.startsWith("pyx-"));
    indexedPackIds = indexedPacks.map((pack) => pack.id);
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
    const choices = [];
    const none = document.createElement("option");
    none.value = "";
    none.textContent = indexedPacks.length ? "No indexed pack" : "No indexed packs available";
    choices.push(none);
    if (indexedPacks.length) {
      const all = document.createElement("option");
      all.value = "__all_indexed__";
      all.textContent = `All indexed packs (${indexedPacks.length})`;
      choices.push(all);
      const group = document.createElement("optgroup");
      group.label = "Choose one imported expansion";
      indexedPacks.forEach((pack) => {
        const option = document.createElement("option");
        option.value = pack.id;
        option.textContent = `${pack.name} (${pack.counts.black}/${pack.counts.white})`;
        group.append(option);
      });
      choices.push(group);
    }
    indexedPacksElement.replaceChildren(...choices);
    indexedPacksElement.disabled = !indexedPacks.length;
    indexedPacksElement.addEventListener("change", updatePackSummary);
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
    const response = await fetch(`${apiUrl("round")}?packs=${encodeURIComponent(packs.join(","))}`, { headers: { Accept: "application/json" } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || "Round failed");
    blackElement.textContent = body.black.repr;
    answersElement.replaceChildren(...body.white.map((card) => {
      const answer = document.createElement("p");
      answer.textContent = card.text;
      return answer;
    }));
    resultElement.textContent = body.result;
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
  updatePackSummary();
  indexedPacksElement.value = indexedPackIds.length ? "__all_indexed__" : "";
});
dealButton.addEventListener("click", deal);
loadPacks();
