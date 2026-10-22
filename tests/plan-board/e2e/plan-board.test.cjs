// Browser tests for the rendered report. Run: cd tests/plan-board/e2e && npm install && npm test
// Uses PW_CHROMIUM (path to a chromium binary) if set, else /opt/pw-browsers/chromium if present,
// else Playwright's own browser.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "../../..");
const SKILL = path.join(ROOT, "plugins/plan-board/skills/plan-board");
const SCRIPT = path.join(SKILL, "scripts/plan_board.py");
const EXAMPLE = path.join(SKILL, "examples/caching.plan.yaml");

let browser, tmp;
before(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "plan-board-e2e-"));
  const exe = process.env.PW_CHROMIUM || (fs.existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);
  browser = await chromium.launch(exe ? { executablePath: exe } : {});
});
after(async () => {
  await browser.close();
  fs.rmSync(tmp, { recursive: true, force: true });
});

let n = 0;
function render(yamlPath) {
  const out = path.join(tmp, `r${n++}.html`);
  execFileSync("python3", [SCRIPT, "render", yamlPath, "--out", out], { stdio: "pipe", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  return out;
}
function writePlan(text) {
  const p = path.join(tmp, `p${n++}.yaml`);
  fs.writeFileSync(p, text);
  return p;
}

async function open(yamlPath) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => m.type() === "error" && errors.push(m.text()));
  await page.goto("file://" + render(yamlPath));
  await page.waitForSelector('html[data-ready="1"]', { timeout: 30000 });
  page.errors = errors;
  return page;
}
const stageTransform = (page, id) => page.$eval(`#${id} .pz-stage`, e => e.style.transform);
async function clickNode(page, i) {
  await page.locator("#dep-pz").scrollIntoViewIfNeeded();
  const box = await page.locator("#dep-pz .node").nth(i).boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

test("example loads cleanly with all sections", async () => {
  const page = await open(EXAMPLE);
  assert.deepEqual(page.errors, []);
  assert.equal(await page.locator("h1").innerText(), "Add response caching to the API");
  assert.equal(await page.locator("#dep-pz .node").count(), 5);
  assert.ok(await page.locator("#arch-pz svg").count());
  assert.ok(await page.locator("#tree-pz svg").count());
  assert.equal(await page.locator(".win").count(), 4);
  assert.match(await page.locator(".bubble").innerText(), /1\/5 done/i);
  await page.close();
});

test("clicking a card opens details, clicking again closes", async () => {
  const page = await open(EXAMPLE);
  await clickNode(page, 1);
  assert.equal(await page.locator("#drawer .card").count(), 1);
  assert.equal(await page.locator(".mcard.sel").count(), 1);
  assert.match(await page.locator("#drawer h3").innerText(), /Cache service/);
  await clickNode(page, 1);
  assert.equal(await page.locator("#drawer .card").count(), 0);
  assert.equal(await page.locator(".mcard.sel").count(), 0);
  await page.close();
});

test("dragging the canvas pans and does not select", async () => {
  const page = await open(EXAMPLE);
  const before = await stageTransform(page, "dep-pz");
  await page.locator("#dep-pz").scrollIntoViewIfNeeded();
  const box = await page.locator("#dep-pz .node").nth(0).boundingBox();
  await page.mouse.move(box.x + 20, box.y + 20);
  await page.mouse.down();
  await page.mouse.move(box.x + 120, box.y + 80, { steps: 5 });
  await page.mouse.up();
  assert.notEqual(await stageTransform(page, "dep-pz"), before);
  assert.equal(await page.locator("#drawer .card").count(), 0);
  await page.close();
});

test("zoom buttons and wheel change the scale", async () => {
  const page = await open(EXAMPLE);
  const t0 = await stageTransform(page, "dep-pz");
  await page.locator('.tools[data-for="dep-pz"] button', { hasText: "+" }).click();
  const t1 = await stageTransform(page, "dep-pz");
  assert.notEqual(t1, t0);
  const box = await page.locator("#dep-pz").boundingBox();
  await page.mouse.move(box.x + 100, box.y + 100);
  await page.mouse.wheel(0, 300);
  assert.notEqual(await stageTransform(page, "dep-pz"), t1);
  await page.locator('.tools[data-for="dep-pz"] button', { hasText: "Fit" }).click();
  assert.equal(await stageTransform(page, "dep-pz"), t0);
  await page.close();
});

test("selecting another card keeps your pan and zoom", async () => {
  const page = await open(EXAMPLE);
  const fitted = await stageTransform(page, "dep-pz");
  await clickNode(page, 0); // opens the drawer, which refits once (the canvas gets narrower)
  await page.waitForFunction(t => document.querySelector("#dep-pz .pz-stage").style.transform !== t, fitted);
  await page.locator('.tools[data-for="dep-pz"] button', { hasText: "+" }).click();
  const zoomed = await stageTransform(page, "dep-pz");
  await clickNode(page, 1);
  await page.waitForFunction(() => /Cache service/.test(document.querySelector("#drawer h3")?.innerText || ""));
  assert.equal(await stageTransform(page, "dep-pz"), zoomed);
  await page.close();
});

test("right click does not select", async () => {
  const page = await open(EXAMPLE);
  await page.locator("#dep-pz").scrollIntoViewIfNeeded();
  const box = await page.locator("#dep-pz .node").nth(1).boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2, { button: "right" });
  assert.equal(await page.locator("#drawer .card").count(), 0);
  await page.close();
});

test("report footer names the plan file", async () => {
  const page = await open(EXAMPLE);
  assert.match(await page.locator("#foot").innerText(), /plan file: .*caching\.plan\.yaml/);
  await page.close();
});

test("list view: toggle works and dependency chips jump to the right step", async () => {
  const page = await open(EXAMPLE);
  await page.click("#v-list");
  assert.ok(await page.locator("#list").isVisible());
  assert.ok(!(await page.locator("#view-canvas").isVisible()));
  assert.equal(await page.locator("#list .card").count(), 5);
  await page.locator('#list .card[data-id="cache-service"] .chip.dep').click();
  assert.equal(await page.locator("#list .card.sel").getAttribute("data-id"), "redis-client");
  await page.click("#v-canvas");
  assert.ok(await page.locator("#dep-pz").isVisible());
  await page.close();
});

test("clicking a dependency chip for the already-selected step keeps it selected", async () => {
  const page = await open(EXAMPLE);
  await page.click("#v-list");
  await page.locator('#list .card[data-id="cache-service"]').click({ position: { x: 20, y: 10 } });
  assert.equal(await page.locator("#list .card.sel").getAttribute("data-id"), "cache-service");
  // chip on step "cache-products" points to cache-service, which is selected right now
  await page.locator('#list .card[data-id="cache-products"] .chip.dep').click();
  assert.equal(await page.locator("#list .card.sel").getAttribute("data-id"), "cache-service");
  await page.close();
});

test("fast Before/After clicks end on the diagram you asked for", async () => {
  const plan = writePlan(`plan:
  title: Race
  architecture:
    before: |
      flowchart LR
        BEFOREONE --> BEFORETWO
    after: |
      flowchart LR
        AFTERONE --> AFTERTWO
steps:
  - {id: a}
`);
  const page = await open(plan);
  await page.evaluate(() => { document.querySelector("#a-before").click(); document.querySelector("#a-after").click(); });
  await page.waitForTimeout(800); // give the slower first render time to (wrongly) land
  assert.equal(await page.locator("#a-after").getAttribute("aria-pressed"), "true");
  const svg = await page.locator("#arch-pz svg").innerHTML();
  assert.match(svg, /AFTERONE/);
  assert.doesNotMatch(svg, /BEFOREONE/);
  await page.close();
});

test("a broken diagram shows an error, never a stale one, and leaves no junk", async () => {
  const plan = writePlan(`plan:
  title: Bad diagram
  architecture:
    before: |
      flowchart LR
        A --> B
    after: |
      this is not mermaid at all
steps:
  - {id: a}
`);
  const page = await open(plan);
  assert.equal(await page.locator("#arch-pz .err").count(), 1);
  await page.click("#a-before");
  assert.equal(await page.locator("#arch-pz svg").count(), 1);
  await page.click("#a-after");
  assert.equal(await page.locator("#arch-pz svg").count(), 0, "stale 'before' diagram still shown");
  assert.equal(await page.locator("#arch-pz .err").count(), 1);
  assert.equal(await page.locator('body > [id^="darch"]').count(), 0, "mermaid left a stray node");
  await page.close();
});

test("missing architecture shows a friendly message", async () => {
  const page = await open(writePlan("steps:\n  - {id: a}\n"));
  assert.match(await page.locator("#arch-pz .pz-empty").innerText(), /No after diagram/);
  await page.click("#a-before");
  assert.match(await page.locator("#arch-pz .pz-empty").innerText(), /No before diagram/);
  assert.equal(await page.locator("#arch-pz .pz-empty").count(), 1);
  await page.close();
});

for (const [name, title] of [
  ["comment + script opener", "x <!-- <script> y"],
  ["closing script + img", "</script><img src=x onerror=\"window.__pwned=1\">"],
  ["quotes and ampersands", `"q" & <b>bold</b> #1`],
]) {
  test(`hostile text renders as plain text: ${name}`, async () => {
    const plan = writePlan(`plan:\n  title: ${JSON.stringify(title)}\n  summary: ${JSON.stringify(title)}\nsteps:\n  - id: a\n    title: ${JSON.stringify(title)}\n    notes: ${JSON.stringify(title)}\n`);
    const page = await open(plan);
    assert.equal(await page.locator("h1").innerText(), title);
    assert.equal(await page.evaluate(() => window.__pwned), undefined);
    assert.equal(await page.locator("img[src='x']").count(), 0);
    assert.equal(await page.locator("#dep-pz .mtitle b").count(), 0);
    await page.close();
  });
}

test("empty plan renders", async () => {
  const page = await open(writePlan("steps: []\n"));
  assert.match(await page.locator("#dep-pz .pz-empty").innerText(), /No steps/);
  assert.match(await page.locator("#tree-pz .pz-empty").innerText(), /No files/);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("inferred dialog: YES/NO change the message", async () => {
  const page = await open(EXAMPLE);
  await page.click(".dialog button:has-text('Yes')");
  assert.match(await page.locator(".dialog .hint").innerText(), /this view only/);
  await page.click(".dialog button:has-text('No')");
  assert.match(await page.locator(".dialog .hint").innerText(), /tell Claude/);
  await page.close();
});
