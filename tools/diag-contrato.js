// Diagnostico do dropdown de Contrato: loga chamadas de rede (xhr/fetch)
// enquanto abre/digita no Choices, pra descobrir como os contratos carregam.
// Uso: GPM_USER=.. GPM_PASS=.. HEADED=1 node src/diag-contrato.js

const { chromium } = require("playwright");
const cfg = require("../config.json");
const { login } = require("../src/gpm");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ headless: !process.env.HEADED });
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);

  const xhrs = [];
  page.on("response", async (resp) => {
    try {
      const req = resp.request();
      if (!["xhr", "fetch"].includes(req.resourceType())) return;
      const url = resp.url();
      let amostra = "";
      const ct = (resp.headers()["content-type"] || "");
      if (/json|text|html/.test(ct)) {
        const t = await resp.text().catch(() => "");
        if (/contrato|JA10188071|SIRTEC|2026_SOC/i.test(t) || /contrato/i.test(url)) {
          amostra = t.slice(0, 300).replace(/\s+/g, " ");
        }
      }
      const linha = `[net] ${req.method()} ${resp.status()} ${url}`;
      if (amostra) {
        console.log(linha + "\n      >> " + amostra);
      } else if (/contrato|combo|filtro|listar|select/i.test(url)) {
        console.log(linha + "  (suspeito)");
      }
    } catch (_) {}
  });

  await login(page, cfg);
  await page.goto(cfg.consultaUrl, { waitUntil: "domcontentloaded" });
  const iframeEl = await page.waitForSelector("#frameTelasGPM");
  const frame = await iframeEl.contentFrame();
  await frame.waitForLoadState("domcontentloaded").catch(() => {});
  console.log("\n[diag] tela aberta. Esperando 5s pra eventuais AJAX de carga inicial...");
  await sleep(5000);

  // Quantas opcoes o Choices do contrato tem AGORA (antes de abrir)?
  const contar = async (tag) => {
    const n = await frame.evaluate(() => {
      const sel = document.querySelector("#contrato");
      const wrap = sel && sel.closest(".choices");
      const itens = wrap ? wrap.querySelectorAll('.choices__list[role="listbox"] .choices__item--choice') : [];
      return { nativeOptions: sel ? sel.options.length : -1, choiceItems: itens.length };
    });
    console.log(`[diag] ${tag}: nativeOptions=${n.nativeOptions} choiceItems=${n.choiceItems}`);
  };
  await contar("inicial");

  // Abre o dropdown.
  console.log("[diag] abrindo o dropdown de contrato...");
  await frame.locator("div.choices:has(#contrato)").first().click();
  await sleep(3000);
  await contar("apos-abrir");

  // Digita pra disparar busca remota, se houver.
  const busca = frame.locator("div.choices:has(#contrato) input.choices__input--cloned").first();
  if (await busca.isVisible().catch(() => false)) {
    for (const termo of ["JA", "10188071", "SOC"]) {
      console.log(`[diag] digitando "${termo}"...`);
      await busca.fill(termo);
      await sleep(2500);
      await contar(`apos-digitar-${termo}`);
    }
  } else {
    console.log("[diag] input de busca do Choices nao ficou visivel.");
  }

  // Lista os textos das opcoes que existirem.
  const textos = await frame.evaluate(() => {
    const sel = document.querySelector("#contrato");
    const wrap = sel && sel.closest(".choices");
    const itens = wrap ? [...wrap.querySelectorAll('.choices__list[role="listbox"] .choices__item--choice')] : [];
    return itens.slice(0, 20).map((i) => i.textContent.trim());
  });
  console.log("[diag] opcoes visiveis no Choices:", JSON.stringify(textos, null, 2));

  console.log("\n[diag] deixando aberto 20s pra inspecao manual...");
  await sleep(20000);
  await browser.close();
})();
