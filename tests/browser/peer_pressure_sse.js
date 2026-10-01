"use strict";

const { chromium } = require("playwright");

const origin = (process.argv[2] || "http://127.0.0.1:8000").replace(/\/$/, "");
const room = `ci-${Date.now().toString(36)}`;
const names = ["Alice", "Bob", "Carol", "Dave"];

async function waitForPhase(page, phase) {
  await page.waitForFunction(
    (expected) => document.querySelector("#phase")?.textContent === expected,
    phase,
    { timeout: 20_000 },
  );
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const players = [];
  const statuses = [];
  try {
    for (const name of names) {
      const context = await browser.newContext();
      const page = await context.newPage();
      const responses = [];
      page.on("response", (response) => {
        if (response.url().includes("/peer-pressure/rooms/")) responses.push(response.status());
      });
      await page.goto(`${origin}/peerpressure?room=${room}`, { waitUntil: "domcontentloaded" });
      await page.locator("#name-input").fill(name);
      await page.waitForFunction(() => !document.querySelector("#join-room").disabled);
      await page.locator("#join-room").click();
      await page.locator("#table-view").waitFor({ state: "visible" });
      await page.waitForFunction(() => document.querySelector("#connection").textContent.includes("Live updates connected"));
      players.push({ context, page, responses });
    }

    const host = players[0].page;
    await host.waitForFunction(() => document.querySelectorAll("#players .player").length === 4);
    await host.locator("#start-room").click();
    await Promise.all(players.map(({ page }) => waitForPhase(page, "PLAYING")));

    const submitters = players.slice(1);
    await Promise.all(submitters.map(async ({ page }) => {
      const count = await page.locator("#selection-count").textContent();
      const needed = Number((count.match(/\/\s*(\d+)/) || [])[1] || 1);
      for (let index = 0; index < needed; index += 1) await page.locator("#choices .choice").nth(index).click();
    }));
    // Deliberately race all three mutations against the same room revision.
    await Promise.all(submitters.map(({ page }) => page.locator("#submit-choice").click()));
    await Promise.all(submitters.map(({ page }) => page.waitForFunction(
      () => !document.querySelector("#submit-choice").classList.contains("visible"),
    )));
    await Promise.all(players.map(({ page }) => waitForPhase(page, "JUDGING")));

    await host.locator("#choices .choice").first().click();
    await host.locator("#judge-choice").click();
    await Promise.all(players.map(({ page }) => waitForPhase(page, "ROUND RESULT")));

    const results = await Promise.all(players.map(({ page }) => page.locator("#result-text").textContent()));
    if (new Set(results.map((value) => value.trim())).size !== 1) throw new Error(`clients disagreed on result: ${results}`);
    for (const [index, player] of players.entries()) {
      if (!player.responses.includes(200)) throw new Error(`${names[index]} never established room state`);
      statuses.push(...player.responses);
    }
    if (!statuses.includes(409)) throw new Error("the concurrent submissions did not exercise stale-revision recovery");
    console.log(`four-player SSE round passed; stale revision recovered; result: ${results[0].trim()}`);
  } finally {
    await Promise.all(players.map(({ context }) => context.close()));
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
