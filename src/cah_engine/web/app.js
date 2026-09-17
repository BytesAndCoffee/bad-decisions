const packsElement = document.querySelector("#packs");
const dealButton = document.querySelector("#deal");
const statusElement = document.querySelector("#status");
const roundElement = document.querySelector("#round");
const blackElement = document.querySelector("#black");
const answersElement = document.querySelector("#answers");
const resultElement = document.querySelector("#result");

function setStatus(message) {
  statusElement.textContent = message;
}

function selectedPacks() {
  return [...document.querySelectorAll("input[name=pack]:checked")].map((input) => input.value);
}

async function loadPacks() {
  try {
    const response = await fetch("../v1/packs", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Could not load packs");
    const packs = await response.json();
    packsElement.replaceChildren(...packs.map((pack) => {
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
      return label;
    }));
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
    const response = await fetch(`../v1/round?packs=${encodeURIComponent(packs.join(","))}`, { headers: { Accept: "application/json" } });
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
    setStatus("");
  } catch (error) {
    setStatus(error.message || "Something went terribly, professionally wrong.");
  } finally {
    dealButton.disabled = false;
  }
}

document.querySelector("#all-packs").addEventListener("click", () => {
  document.querySelectorAll("input[name=pack]").forEach((input) => { input.checked = true; });
});
dealButton.addEventListener("click", deal);
loadPacks();
