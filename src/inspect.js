// Helper de calibracao de seletores. Abre o GPM (HEADED por padrao aqui),
// e — se voce logar manualmente na janela — navega e despeja o HTML de cada
// tela em ./debug, alem de listar candidatos de seletor uteis no console.
//
// Uso local:
//   HEADED=1 npm run inspect     (abre o browser; faca o login na janela)
// Com credenciais (tenta logar sozinho):
//   GPM_USER=... GPM_PASS=... npm run inspect

const { chromium } = require("playwright");
const cfg = require("../config.json");
const { dump } = require("./gpm");

(async () => {
  const headless = process.env.HEADED ? false : !!process.env.CI;
  const browser = await chromium.launch({ headless });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);

  await page.goto(cfg.baseUrl, { waitUntil: "domcontentloaded" });
  await dump(page, "inspect-01-inicial");

  // Lista inputs e botoes da tela inicial (ajuda a achar campos de login).
  const campos = await page.evaluate(() => {
    const desc = (el) => ({
      tag: el.tagName.toLowerCase(),
      type: el.type || null,
      name: el.name || null,
      id: el.id || null,
      placeholder: el.placeholder || null,
      text: (el.innerText || el.value || "").trim().slice(0, 40) || null,
    });
    return {
      inputs: [...document.querySelectorAll("input,select")].map(desc),
      botoes: [...document.querySelectorAll("button,input[type=submit],a")].map(desc).slice(0, 40),
    };
  });
  console.log("\n[inspect] campos da tela inicial:");
  console.log(JSON.stringify(campos, null, 2));

  if (!headless) {
    console.log("\n[inspect] Janela aberta. Faca o LOGIN manualmente (so o login; eu");
    console.log("navego ate a Consulta Servicos sozinho). Voce tem ~60s...");
    await page.waitForTimeout(60000);
    await dump(page, "inspect-02-pos-login");

    // A tela vive num iframe (#frameTelasGPM). Vai na URL, pega o frame e
    // enumera DENTRO dele.
    console.log(`[inspect] indo para ${cfg.consultaUrl} ...`);
    await page.goto(cfg.consultaUrl, { waitUntil: "domcontentloaded" });
    const iframeEl = await page.waitForSelector("#frameTelasGPM", { timeout: 20000 });
    const frame = await iframeEl.contentFrame();
    await frame.waitForLoadState("domcontentloaded").catch(() => {});
    await page.waitForTimeout(5000);
    await dump(page, "inspect-03-consulta");
    try {
      const fs = require("fs");
      fs.writeFileSync("debug/inspect-04-frame.html", await frame.content());
      console.warn("[debug] HTML do iframe salvo: debug/inspect-04-frame.html");
    } catch (_) {}

    const tela = await frame.evaluate(() => {
      const desc = (el) => ({
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        name: el.name || null,
        id: el.id || null,
        cls: (el.className && el.className.toString().slice(0, 60)) || null,
        title: el.title || el.getAttribute("data-original-title") || null,
        placeholder: el.placeholder || null,
        text: (el.innerText || el.value || "").trim().slice(0, 50) || null,
      });
      return {
        inputs: [...document.querySelectorAll("input:not([type=hidden]),select,textarea")].map(desc),
        botoes: [...document.querySelectorAll("button,input[type=submit],a.btn")].map(desc).slice(0, 60),
        comTitle: [...document.querySelectorAll("[title],[data-original-title]")]
          .map(desc).filter((d) => /excel|csv/i.test(d.title || "")),
        selectOptions: [...document.querySelectorAll("select")].map((sel) => ({
          name: sel.name || sel.id,
          options: [...sel.options].slice(0, 30).map((o) => o.text.trim()),
        })),
      };
    });
    console.log("\n[inspect] controles da tela Consulta Servicos (dentro do iframe):");
    console.log(JSON.stringify(tela, null, 2));
  }

  await browser.close();
  console.log("\n[inspect] Pronto. Veja ./debug/*.html e ./debug/*.png e ajuste config.json -> selectors.");
})();
