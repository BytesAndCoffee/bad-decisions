// Management sign-in by tailnet liveness (TailBind v1). docs/MANAGEMENT_AUTH.md is the specification.
"use strict";

const ManageProtocol = (() => {
  const encoder = new TextEncoder();
  const subtle = globalThis.crypto.subtle;
  const H1_LABEL = encoder.encode("tailbind/v1/h1");
  const H2_LABEL = encoder.encode("tailbind/v1/h2");
  const REDEEM_LABEL = encoder.encode("tailbind/v1/redeem");
  const SIZES = { cid: 16, secret: 32, ip: 16, h2: 76 };

  function concat(...parts) {
    const out = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
    let offset = 0;
    for (const part of parts) { out.set(part, offset); offset += part.length; }
    return out;
  }

  function b64encode(bytes) {
    let text = "";
    for (const byte of bytes) text += String.fromCharCode(byte);
    return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function b64decode(text, size) {
    if (typeof text !== "string" || !/^[A-Za-z0-9_-]*$/.test(text)) throw new Error("malformed base64url");
    const binary = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    if (bytes.length !== size) throw new Error("unexpected field length");
    return bytes;
  }

  async function hmac(key, message) {
    const imported = await subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return new Uint8Array(await subtle.sign("HMAC", imported, message));
  }

  // Step 2: proves possession of C without sending C over the tailnet.
  const computeH1 = (c, cid, n) => hmac(c, concat(H1_LABEL, cid, n));

  // Step 3: AES-256-GCM, key = HKDF-SHA256(ikm=C, salt=H1, info=label). The tag is checked before any plaintext.
  async function openH2(c, cid, n, h1, h2) {
    if (h2.length !== SIZES.h2) throw new Error("malformed H2");
    const ikm = await subtle.importKey("raw", c, "HKDF", false, ["deriveKey"]);
    const key = await subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: h1, info: H2_LABEL }, ikm, { name: "AES-GCM", length: 256 }, false, ["decrypt"],
    );
    const plain = new Uint8Array(await subtle.decrypt(
      { name: "AES-GCM", iv: h2.slice(0, 12), additionalData: concat(H2_LABEL, cid, n, h1), tagLength: 128 }, key, h2.slice(12),
    ));
    return { ip: plain.slice(0, SIZES.ip), s: plain.slice(SIZES.ip) };
  }

  // Step 4: proves the browser that got C over HTTPS also got S over the tailnet.
  const computeR = (s, cid, c, ip) => hmac(s, concat(REDEEM_LABEL, cid, c, ip));

  class HandshakeFailure extends Error {
    constructor(step, status) { super(`${step} failed`); this.step = step; this.status = status; }
  }

  async function handshake(apiBase, fetchImpl = globalThis.fetch.bind(globalThis)) {
    const post = (path, body) => fetchImpl(`${apiBase}${path}`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    const issued = await post("/auth/challenge", {});
    if (!issued.ok) throw new HandshakeFailure("challenge", issued.status);
    const challenge = await issued.json();
    const cid = b64decode(challenge.cid, SIZES.cid);
    const c = b64decode(challenge.C, SIZES.secret);
    const n = globalThis.crypto.getRandomValues(new Uint8Array(SIZES.secret));
    const h1 = await computeH1(c, cid, n);
    let attested;
    try {
      attested = await fetchImpl(`${challenge.authority}/attest`, {
        method: "POST", mode: "cors", credentials: "omit", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cid: challenge.cid, N: b64encode(n), H1: b64encode(h1) }),
      });
    } catch (_) {
      throw new HandshakeFailure("tailnet", 0);  // unreachable: not on the tailnet, or blocked by the browser
    }
    if (!attested.ok) throw new HandshakeFailure("attest", attested.status);
    const { ip, s } = await openH2(c, cid, n, h1, b64decode((await attested.json()).H2, SIZES.h2));
    const redeemed = await post("/auth/redeem", { cid: challenge.cid, R: b64encode(await computeR(s, cid, c, ip)) });
    if (!redeemed.ok) throw new HandshakeFailure("redeem", redeemed.status);
    return redeemed.json();
  }

  return { b64encode, b64decode, computeH1, openH2, computeR, handshake, HandshakeFailure };
})();

if (typeof module !== "undefined" && module.exports) module.exports = ManageProtocol;

if (typeof document !== "undefined") {
  const apiBase = window.location.pathname.replace(/\/manage\/?$/, "/v2/manage");
  const $ = (selector) => document.querySelector(selector);
  let renewTimer = null;
  let leaseExpiresAt = 0;

  function setLease(title, detail, denied) {
    $("#lease-title").textContent = title;
    $("#lease-detail").textContent = detail;
    $(".lease").classList.toggle("denied", Boolean(denied));
  }

  function row(cells) {
    const tr = document.createElement("tr");
    for (const value of cells) { const td = document.createElement("td"); td.textContent = String(value); tr.append(td); }
    return tr;
  }

  function definitions(target, entries) {
    target.replaceChildren();
    for (const [term, value] of entries) {
      const dt = document.createElement("dt"); dt.textContent = term;
      const dd = document.createElement("dd"); dd.textContent = String(value);
      target.append(dt, dd);
    }
  }

  function render(data) {
    $("#service-version").textContent = data.service.version;
    $("#service-packs").textContent = data.service.pack_count;
    $("#service-rooms").textContent = data.peer_pressure.room_files;
    $("#service-time").textContent = new Date(data.service.server_time * 1000).toLocaleString();
    $("#packs").replaceChildren(...data.packs.map((pack) => row([pack.id, pack.name, pack.version, pack.counts.prompts, pack.counts.answers])));
    const report = data.consequences;
    definitions($("#consequences"), report ? [
      ["Requests", report.requests.count], ["Errors", report.requests.errors], ["Average ms", report.requests.average_duration_ms],
      ["Draws recorded", report.draws.recorded], ["Votes", `${report.feedback.votes} (${report.feedback.enjoy} enjoyed)`],
    ] : [["Status", "Not recording"]]);
    $("#overview").hidden = false;
  }

  function clearOverview() {
    $("#overview").hidden = true;
    $("#sign-out").hidden = true;
  }

  async function loadOverview() {
    const response = await fetch(`${apiBase}/overview`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (response.status === 401) { clearOverview(); return false; }
    if (!response.ok) throw new Error(`overview ${response.status}`);
    render(await response.json());
    return true;
  }

  function describeFailure(error) {
    if (error.step === "tailnet") return "This device can't reach the attest service. Connect to your tailnet and allow local network access if the browser asks.";
    if (error.step === "attest") return error.status === 503 ? "The attest service can't reach tailscaled right now." : "This device isn't an allowed management device.";
    if (error.step === "challenge" && error.status === 404) return "Management is not enabled on this server.";
    return "Sign-in failed. Try again.";
  }

  async function renew() {
    clearTimeout(renewTimer);
    try {
      const session = await ManageProtocol.handshake(apiBase);
      leaseExpiresAt = session.lease_expires_at * 1000;
      setLease(`Signed in from ${session.node}`, `Tags: ${session.tags.join(", ")}. The lease renews every ${session.renew_after_seconds} seconds while this device stays on the tailnet.`, false);
      $("#sign-out").hidden = false;
      await loadOverview();
      renewTimer = setTimeout(renew, session.renew_after_seconds * 1000);
    } catch (error) {
      const remaining = Math.max(0, Math.round((leaseExpiresAt - Date.now()) / 1000));
      setLease(remaining > 0 ? `Renewal failed; lease ends in ${remaining}s` : "Not signed in", describeFailure(error), true);
      if (remaining > 0) {
        renewTimer = setTimeout(renew, Math.min(15, remaining) * 1000);
      } else {
        clearOverview();
      }
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#sign-in").addEventListener("click", renew);
    $("#sign-out").addEventListener("click", async () => {
      clearTimeout(renewTimer);
      leaseExpiresAt = 0;
      await fetch(`${apiBase}/auth/logout`, { method: "POST", credentials: "same-origin" });
      clearOverview();
      setLease("Signed out", "Sign in again from an allowed tailnet device.", false);
    });
    renew();
  });
}
