// Renders reintro/web/static/favicon.svg to the PNGs beside it, light scheme:
//   favicon-32.png (also served at /favicon.ico) and apple-touch-icon.png (180 px, white, padded).
// NODE_PATH="$(npm root -g)" node scripts/favicons.js   (needs Playwright's Chromium)
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const dir = path.join(__dirname, "..", "reintro", "web", "static");
const svg = fs.readFileSync(path.join(dir, "favicon.svg"), "utf8");
const src = "data:image/svg+xml;base64," + Buffer.from(svg).toString("base64");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ colorScheme: "light" });
  for (const [name, size, pad, bg] of [["favicon-32.png", 32, 0, "transparent"], ["apple-touch-icon.png", 180, 18, "#fff"]]) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(`<body style="margin:0;background:${bg}"><img src="${src}" style="display:block;margin:${pad}px;width:${size - 2 * pad}px;height:${size - 2 * pad}px"></body>`);
    await page.waitForTimeout(100);
    await page.screenshot({ path: path.join(dir, name), omitBackground: bg === "transparent" });
  }
  await browser.close();
})();
