import playwright from "../frontend/node_modules/playwright-core/index.js";

const {chromium} = playwright;

const [baseUrl, outputPath, mode = "active"] = process.argv.slice(2);
if (!baseUrl || !outputPath) throw new Error("usage: node browser_runtime_smoke.mjs URL SCREENSHOT [active|restarted]");
const executablePath = process.env.WAR_ROOM_BROWSER_EXECUTABLE || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const browser = await chromium.launch({headless: true, executablePath});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await page.goto(baseUrl, {waitUntil: "networkidle"});
  const simulationButton = page.getByRole("button", {name: "Simulation Center"}).first();
  await simulationButton.click();
  await page.getByRole("heading", {name: "Simulation Center"}).waitFor();
  await page.getByText("Baseline season simulation").first().waitFor();

  await page.getByRole("button", {name: "League Overview"}).first().click();
  await page.getByText(/League intelligence|No completed baseline analysis/).first().waitFor();
  await page.getByRole("button", {name: "Simulation Center"}).first().click();
  await page.getByRole("heading", {name: "Simulation Center"}).waitFor();
  await page.getByText("Baseline season simulation").first().waitFor();

  if (mode === "active") {
    const running = page.locator(".history-list article").filter({has: page.locator(".status-running")}).first();
    await running.locator(".history-select").click();
    const cancel = page.getByRole("button", {name: "Request cooperative cancellation"});
    await cancel.waitFor();
    await cancel.click();
    await page.getByText(/Cancellation requested|already completed/i).first().waitFor();
  }

  const baseline = page.locator(".history-list article").filter({has: page.locator(".status-completed")}).first();
  await baseline.getByRole("button", {name: /Open/}).click();
  await page.getByRole("heading", {name: "Championship probability"}).waitFor();
  await page.screenshot({path: outputPath, fullPage: true});
  process.stdout.write(JSON.stringify({mode, browser: "Microsoft Edge", retainedResultOpened: true}));
} finally {
  await browser.close();
}
