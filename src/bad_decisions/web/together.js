const $ = (selector) => document.querySelector(selector);
const apiBase = window.location.pathname.replace(/\/peerpressure\/?$/, "/v2");
const sessionPrefix = "bad-decisions-peer-pressure:";
const joinView = $("#join-view");
const tableView = $("#table-view");
const joinForm = $("#join-form");
const joinButton = $("#join-room");
const joinStatus = $("#join-status");
const connection = $("#connection");
const choices = $("#choices");
const packModal = $("#pack-modal");
const PYX_EDITION = "Pretend You're Xyzzy SQL card set ";
let room = "";
let token = "";
let state = null;
let revision = 0;
let selectedCards = [];
let selectedSubmission = null;
let busy = false;
let heartbeatTimer = null;
let eventController = null;
let eventStreamOpen = false;
let availablePacks = [];
let indexedPacks = [];
let selectedPacks = new Set();
let indexedDraft = new Set();
let modalReturnFocus = null;

function endpoint(path = "") { return `${apiBase}/peer-pressure/rooms/${encodeURIComponent(room)}${path}`; }
function requestId() { return `req_${crypto.randomUUID().replaceAll("-", "")}`; }
function sessionKey(value) { return `${sessionPrefix}${apiBase}|${value}`; }
function savedSession(value) { try { return JSON.parse(localStorage.getItem(sessionKey(value))); } catch (_) { return null; } }
function saveSession(value) { try { localStorage.setItem(sessionKey(value), JSON.stringify({token, name:$("#name-input").value.trim()})); } catch (_) {} }
function forgetSession() { try { localStorage.removeItem(sessionKey(room)); } catch (_) {} }
function setConnection(ok, text) { connection.classList.toggle("offline", !ok); connection.lastChild.textContent = ` ${text}`; }
function isIndexedPack(pack) {
  return (pack.sources || []).some((source) => String(source?.edition ?? "").startsWith(PYX_EDITION)) || pack.id.startsWith("pyx-");
}
function updateIndexedSummary() {
  const count = indexedPacks.filter((pack) => selectedPacks.has(pack.id)).length;
  $("#indexed-summary").textContent = count ? `${count} indexed pack${count === 1 ? "" : "s"} selected` : "No indexed packs selected";
}
function corePackLabel(pack) {
  const label = document.createElement("label");
  const input = document.createElement("input"); input.type="checkbox"; input.value=pack.id; input.checked=selectedPacks.has(pack.id);
  input.addEventListener("change",()=>{if(input.checked)selectedPacks.add(pack.id);else selectedPacks.delete(pack.id);joinButton.disabled=selectedPacks.size===0;});
  const text = document.createElement("span"); text.textContent=`${pack.name} (${pack.counts.prompts}/${pack.counts.answers})`;
  label.append(input,text); return label;
}
function indexedPackLabel(pack) {
  const label=document.createElement("label"); const input=document.createElement("input"); input.type="checkbox"; input.value=pack.id; input.checked=indexedDraft.has(pack.id);
  input.addEventListener("change",()=>{if(input.checked)indexedDraft.add(pack.id);else indexedDraft.delete(pack.id);});
  const text=document.createElement("span"); text.textContent=pack.name; const counts=document.createElement("small"); counts.textContent=`${pack.id} · ${pack.counts.prompts} prompts · ${pack.counts.answers} answers`; text.append(counts); label.append(input,text); return label;
}
function closePackModal(apply=false) {
  if (apply) { indexedPacks.forEach((pack)=>selectedPacks.delete(pack.id)); indexedDraft.forEach((id)=>selectedPacks.add(id)); updateIndexedSummary(); }
  packModal.hidden=true; modalReturnFocus?.focus(); modalReturnFocus=null; joinButton.disabled=selectedPacks.size===0;
}
function openPackModal() {
  indexedDraft=new Set(indexedPacks.filter((pack)=>selectedPacks.has(pack.id)).map((pack)=>pack.id));
  $("#indexed-options").replaceChildren(...indexedPacks.map(indexedPackLabel)); modalReturnFocus=document.activeElement; packModal.hidden=false; $("#close-packs").focus();
}
async function loadPacks() {
  try {
    const response=await fetch(`${apiBase}/packs`,{headers:{Accept:"application/json"}}); if(!response.ok)throw new Error();
    availablePacks=await response.json(); indexedPacks=availablePacks.filter(isIndexedPack); const core=availablePacks.filter((pack)=>!isIndexedPack(pack));
    const defaultPack=core.find((pack)=>pack.custom===false)||core[0]||availablePacks[0]; if(defaultPack)selectedPacks.add(defaultPack.id);
    $("#core-packs").replaceChildren(...core.map(corePackLabel)); $("#choose-indexed").disabled=indexedPacks.length===0; updateIndexedSummary(); joinButton.disabled=selectedPacks.size===0;
    if(!availablePacks.length)throw new Error();
  } catch (_) { $("#core-packs").textContent="Packs could not be loaded."; $("#indexed-summary").textContent="Indexed packs unavailable"; joinStatus.textContent="The deck is unavailable. Refresh to try again."; }
}

async function api(path, {method = "POST", body, auth = true} = {}) {
  const response = await fetch(endpoint(path), {
    method,
    headers:{Accept:"application/json", ...(body ? {"Content-Type":"application/json"} : {}), ...(auth && token ? {Authorization:`Bearer ${token}`} : {})},
    body:body ? JSON.stringify(body) : undefined,
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    const error = new Error(payload.error?.message || `The table returned ${response.status}.`);
    error.code = payload.error?.code;
    error.resync = Boolean(payload.error?.details?.resync);
    throw error;
  }
  return payload;
}

// Before the first round there is no Responsible Adult; the room host has table control.
function isAdult() { const adult = state?.responsible_adult; return adult ? adult.id === state?.you?.id : Boolean(state?.you?.room_owner); }
function phase() { return state?.room?.state || "WAITING"; }
function setBusy(value, message) { busy = value; if (message) $("#instruction").textContent = message; renderActions(); }
function apply(payload) {
  if (Number(payload.revision) < revision) return;
  revision = Number(payload.revision);
  if (payload.state) state = payload.state;
  render();
}

function parseEventBlock(block) {
  let event = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return;
  let payload;
  try { payload = JSON.parse(data.join("\n")); } catch (_) { return; }
  if (event === "state") {
    apply(payload);
    setConnection(true, "At the table. Live updates connected.");
  } else if (event === "error") {
    if (["invalid_session","room_expired","room_not_found"].includes(payload.code)) return leaveLocal(payload.message);
    $("#instruction").textContent = payload.message || "The table stopped sending updates.";
  }
}

async function streamEvents(controller) {
  let delay = 500;
  while (token && eventController === controller && !controller.signal.aborted) {
    try {
      const response = await fetch(endpoint("/events"), {
        headers:{Accept:"text/event-stream", Authorization:`Bearer ${token}`},
        cache:"no-store",
        signal:controller.signal,
      });
      if (!response.ok || !response.body) {
        let payload = {};
        try { payload = await response.json(); } catch (_) {}
        const error = new Error(payload.error?.message || `The update stream returned ${response.status}.`);
        error.code = payload.error?.code;
        throw error;
      }
      if (eventController !== controller || controller.signal.aborted) return;
      eventStreamOpen = true;
      delay = 500;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!controller.signal.aborted) {
        const {value, done} = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), {stream:!done}).replaceAll("\r\n", "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          parseEventBlock(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
        }
        if (done) { if (eventController === controller) eventStreamOpen = false; break; }
      }
    } catch (error) {
      if (eventController === controller) eventStreamOpen = false;
      if (controller.signal.aborted) return;
      if (["invalid_session","room_expired","room_not_found"].includes(error.code)) return leaveLocal(error.message);
      setConnection(false, "Reconnecting live updates…");
    }
    await new Promise((resolve)=>setTimeout(resolve, delay));
    delay = Math.min(delay * 2, 5000);
  }
}

function startEvents() {
  eventController?.abort();
  eventController = new AbortController();
  streamEvents(eventController);
}

async function join(event) {
  event.preventDefault();
  room = $("#room-input").value.trim().toLowerCase();
  const name = $("#name-input").value.trim();
  const saved = savedSession(room);
  token = saved?.token || "";
  joinButton.disabled = true;
  joinStatus.textContent = token ? "Rejoining your regrettable acquaintances…" : "Finding or creating that room…";
  try {
    let joined;
    try { joined = await api("/join", {body:{display_name:name, create:true, packs:[...selectedPacks]}}); }
    catch (error) {
      if (!token || !["invalid_session","room_expired","room_not_found"].includes(error.code)) throw error;
      token = ""; forgetSession();
      joined = await api("/join", {body:{display_name:name, create:true, packs:[...selectedPacks]}, auth:false});
    }
    token = joined.session_token;
    saveSession(room);
    apply(joined);
    const url = new URL(window.location.href); url.searchParams.set("room", room); history.replaceState(null, "", url);
    joinView.hidden = true; tableView.hidden = false;
    setConnection(true, "Connecting live updates…");
    heartbeatTimer = window.setInterval(heartbeat, 5000);
    startEvents();
  } catch (error) {
    joinStatus.textContent = error.message;
    token = saved?.token || "";
  } finally { joinButton.disabled = selectedPacks.size === 0; }
}

async function sync() {
  try { apply(await api("/sync")); setConnection(true, "At the table."); }
  catch (error) { setConnection(false, "Trying to find the table…"); $("#instruction").textContent = error.message; }
}

async function heartbeat() {
  if (!token || document.hidden) return;
  try {
    await api("/heartbeat", {body:{revision}});
    if (eventStreamOpen) setConnection(true, "At the table. Live updates connected.");
  } catch (error) {
    setConnection(false, "Connection questionable.");
    if (["invalid_session","room_expired","room_not_found"].includes(error.code)) return leaveLocal(error.message);
  }
}

function mutationStillValid(action, extra, expectedRound) {
  if (!state || state.room.round !== expectedRound) return false;
  if (action === "start") return phase() === "WAITING" && isAdult();
  if (action === "submit") {
    const hand = new Set(state.you.hand.map((card)=>card.card_instance_id));
    return phase() === "PLAYING" && !isAdult() && !state.you.submitted && extra.card_instance_ids.every((id)=>hand.has(id));
  }
  if (action === "judge") return phase() === "JUDGING" && isAdult() && state.judging.decisions.some((decision)=>decision.submission_id===extra.submission_id);
  if (action === "advance") return phase() === "ROUND_RESULT" && isAdult();
  if (action === "end") return Boolean(state.you.room_owner);
  return action === "leave";
}

async function mutate(action, extra = {}, syncAfter = true) {
  if (busy) return false;
  setBusy(true, "Asking the table to make this official…");
  try {
    const expectedRound = state.room.round;
    let accepted = false;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        revision = Number((await api(`/${action}`, {body:{request_id:requestId(), revision, ...extra}})).revision);
        accepted = true;
        break;
      } catch (error) {
        if (error.code !== "stale_revision") throw error;
        await sync();
        if (!mutationStillValid(action, extra, expectedRound)) throw new Error("The table moved before you did. Check the new state and try again.");
      }
    }
    if (!accepted) throw new Error("The table keeps moving. Check the new state and try again.");
    selectedCards = []; selectedSubmission = null;
    if (syncAfter) await sync();
    return true;
  } catch (error) { $("#instruction").textContent = error.message; return false; }
  finally { setBusy(false); }
}

function instruction() {
  if (!state) return "Synchronizing…";
  if (phase() === "WAITING") return isAdult() ? "Start when at least three people are present." : "Waiting for the table host to start.";
  if (phase() === "PLAYING" && isAdult()) return "Everyone else is choosing a terrible answer.";
  if (phase() === "PLAYING" && state.you.submitted) return "Decision submitted. Peer pressure is processing.";
  if (phase() === "PLAYING") return `Choose ${state.prompt?.slots || 1} answer${state.prompt?.slots === 1 ? "" : "s"}, then submit.`;
  if (phase() === "JUDGING" && isAdult()) return "Choose the consequence. This is apparently your responsibility.";
  if (phase() === "JUDGING") return "The Responsible Adult is making a deeply responsible selection.";
  if (phase() === "ROUND_RESULT" && isAdult()) return "Advance when the table has absorbed the consequences.";
  if (phase() === "ROUND_RESULT") return "Awaiting further peer pressure.";
  return "This room has ended.";
}

function renderPlayers() {
  const adult = state?.responsible_adult?.id;
  $("#players").replaceChildren(...(state?.players || []).map((player) => {
    const item = document.createElement("div"); item.className = `player${player.id === adult ? " adult" : ""}${player.connected ? "" : " away"}`;
    const avatar = document.createElement("span"); avatar.className = "player-avatar"; avatar.textContent = player.name.slice(0,1).toUpperCase();
    const identity = document.createElement("div"); const name = document.createElement("b"); name.textContent = player.name; const role = document.createElement("small"); role.textContent = player.id === adult ? "Responsible Adult" : player.connected ? "At the table" : "Away"; identity.append(name,role);
    const score = document.createElement("span"); score.className = "player-score"; score.textContent = String(player.score);
    item.append(avatar,identity,score); return item;
  }));
  $("#seat-note").textContent = state?.you?.room_owner ? "You opened this room. Apparently that makes you responsible." : `You are ${state?.players?.find((p)=>p.id===state.you.id)?.name || "here"}.`;
}

function choiceButton(key, text, selected, onClick) {
  const button = document.createElement("button"); button.type="button"; button.className=`choice${selected ? " selected" : ""}`; button.dataset.key=key; button.textContent=text; button.addEventListener("click",onClick); return button;
}
function renderChoices() {
  let nodes = [];
  const availableCards = new Set((state?.you?.hand || []).map((card)=>card.card_instance_id));
  selectedCards = selectedCards.filter((id)=>availableCards.has(id));
  if (phase() === "PLAYING" && !isAdult() && !state.you.submitted) {
    $("#choice-label").textContent="YOUR HAND"; $("#choice-title").textContent="Choose irresponsibly.";
    nodes = state.you.hand.map((card)=>choiceButton(card.card_instance_id,card.text,selectedCards.includes(card.card_instance_id),()=>{
      if (selectedCards.includes(card.card_instance_id)) selectedCards=selectedCards.filter((id)=>id!==card.card_instance_id);
      else if (selectedCards.length < state.prompt.slots) selectedCards.push(card.card_instance_id);
      else $("#instruction").textContent=`This prompt needs exactly ${state.prompt.slots} answers.`;
      renderChoices(); renderActions();
    }));
    $("#selection-count").textContent=`${selectedCards.length} / ${state.prompt.slots} selected`;
  } else if (phase() === "JUDGING" && isAdult()) {
    $("#choice-label").textContent="THE DECISIONS"; $("#choice-title").textContent="Choose the consequence.";
    const decisions=state.judging?.decisions || [];
    if (!decisions.some((d)=>d.submission_id===selectedSubmission)) selectedSubmission=null;
    nodes=decisions.map((decision)=>choiceButton(decision.submission_id,decision.answers.join(" / "),selectedSubmission===decision.submission_id,()=>{selectedSubmission=decision.submission_id;renderChoices();renderActions();}));
    $("#selection-count").textContent=selectedSubmission ? "1 selected" : "Anonymous, as nature intended";
  } else {
    selectedCards=[]; selectedSubmission=null;
    $("#choice-label").textContent=phase()==="WAITING" ? "THE LOBBY" : "THE TABLE";
    $("#choice-title").textContent=instruction(); $("#selection-count").textContent="";
  }
  if (!nodes.length) { const empty=document.createElement("p"); empty.className="empty-choice"; empty.textContent=instruction(); nodes=[empty]; }
  choices.replaceChildren(...nodes);
}

function showButton(id, visible, enabled=true) { const button=$(id); button.classList.toggle("visible",visible); button.disabled=busy || !enabled; }
function renderActions() {
  if (!state) return;
  showButton("#start-room",phase()==="WAITING"&&isAdult());
  showButton("#submit-choice",phase()==="PLAYING"&&!isAdult()&&!state.you.submitted,selectedCards.length===(state.prompt?.slots||1));
  showButton("#judge-choice",phase()==="JUDGING"&&isAdult(),Boolean(selectedSubmission));
  showButton("#advance-room",phase()==="ROUND_RESULT"&&isAdult());
  showButton("#refresh-room",true); showButton("#leave-room",true); showButton("#end-room",Boolean(state.you.room_owner));
}
function render() {
  if (!state) return;
  $("#room-code").textContent=state.room.code; $("#round-number").textContent=state.room.round; $("#phase").textContent=state.room.state.replaceAll("_"," ");
  $("#room-packs").textContent=String(state.room.packs?.length||0); $("#room-packs").title=(state.room.packs||[]).join(", ");
  $("#peer-prompt-text").textContent=state.prompt?.text || "Waiting for someone to make the first mistake.";
  $("#result-card").hidden=!state.result; $("#result-text").textContent=state.result?.rendered || ""; $("#winner-text").textContent=state.result ? `Peer pressure worked on ${state.result.winning_player.name}.` : "";
  renderPlayers(); renderChoices(); if (!busy) $("#instruction").textContent=instruction(); renderActions();
}
function leaveLocal(message="You left the room.") { clearInterval(heartbeatTimer); eventController?.abort(); eventController=null; eventStreamOpen=false; token="";state=null;revision=0;tableView.hidden=true;joinView.hidden=false;joinStatus.textContent=message;setConnection(false,"Ready to make acquaintances worse."); }

joinForm.addEventListener("submit",join);
$("#start-room").addEventListener("click",()=>mutate("start"));
$("#submit-choice").addEventListener("click",()=>mutate("submit",{card_instance_ids:[...selectedCards]}));
$("#judge-choice").addEventListener("click",()=>mutate("judge",{submission_id:selectedSubmission}));
$("#advance-room").addEventListener("click",()=>mutate("advance"));
$("#refresh-room").addEventListener("click",sync);
$("#leave-room").addEventListener("click",async()=>{try{await mutate("leave",{},false);}finally{forgetSession();leaveLocal();}});
// Only a confirmed end forgets the session: a refused or unsent end leaves the host in control.
$("#end-room").addEventListener("click",async()=>{if(!confirm("End this room for everyone?"))return;if(await mutate("end",{},false)){forgetSession();leaveLocal("The room has ended.");}});
$("#copy-room").addEventListener("click",async()=>{const url=new URL(window.location.href);url.searchParams.set("room",room);await navigator.clipboard.writeText(url.toString());$("#copy-room").textContent="Copied";setTimeout(()=>$("#copy-room").textContent="Copy invite",1500);});
$("#choose-indexed").addEventListener("click",openPackModal);
$("#close-packs").addEventListener("click",()=>closePackModal());
$("#cancel-packs").addEventListener("click",()=>closePackModal());
$("#apply-packs").addEventListener("click",()=>closePackModal(true));
$("#select-all-indexed").addEventListener("click",()=>{indexedDraft=new Set(indexedPacks.map((pack)=>pack.id));$("#indexed-options").replaceChildren(...indexedPacks.map(indexedPackLabel));});
$("#clear-indexed").addEventListener("click",()=>{indexedDraft.clear();$("#indexed-options").replaceChildren(...indexedPacks.map(indexedPackLabel));});
packModal.addEventListener("click",(event)=>{if(event.target===packModal)closePackModal();});
document.addEventListener("keydown",(event)=>{if(event.key==="Escape"&&!packModal.hidden)closePackModal();});
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&token&&!eventController)startEvents();});
const invited=new URLSearchParams(window.location.search).get("room"); if(invited) $("#room-input").value=invited.toLowerCase();
loadPacks();
