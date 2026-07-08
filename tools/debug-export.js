// Debug do EXPORT novo (site GPM redesenhado). Roda o fluxo ate a pesquisa,
// intercepta requests de rede, despeja botoes/forms/onclick da tela pos-pesquisa
// e MANTEM o browser aberto pra voce clicar no botao Excel/CSV e ver a request.
//
// Uso local (headed):
//   GPM_USER=... GPM_PASS=... node tools/debug-export.js
//
// Enquanto a janela fica aberta: abra o botao verde Excel/CSV manualmente.
// Toda navegacao/download/POST aparece no console (marcado [NET]/[DL]).

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");
const cfg = require("../config.json");
const {
  login, abrirConsulta, setDataInicio, selecionarContrato, pesquisar,
} = require("../src/gpm");

const DEBUG_DIR = path.join(process.cwd(), "debug");

(async () => {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);

  // Log de rede: qualquer request pra endpoints suspeitos de export.
  const interesse = /csv|xls|excel|export|relat|pesquisa|servico_/i;
  context.on("request", (req) => {
    const u = req.url();
    if (interesse.test(u)) console.log(`[NET ${req.method()}] ${u}`);
  });
  context.on("page", (p) => {
    console.log(`[POPUP] nova aba: ${p.url()}`);
    p.on("download", (d) => console.log(`[DL popup] ${d.suggestedFilename()} <- ${d.url()}`));
  });
  page.on("download", (d) => console.log(`[DL] ${d.suggestedFilename()} <- ${d.url()}`));

  try {
    await login(page, cfg);
    const contrato = cfg.contratos[0];
    const frame = await abrirConsulta(page, cfg);
    await setDataInicio(frame, cfg);
    await selecionarContrato(frame, cfg, contrato);
    await pesquisar(frame, cfg);

    // Dump da tela pos-pesquisa (iframe): tudo que parece export/botao/form.
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    fs.writeFileSync(path.join(DEBUG_DIR, "debug-export-frame.html"), await frame.content());

    const controles = await frame.evaluate(() => {
      const desc = (el) => ({
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        name: el.getAttribute("name") || null,
        cls: (el.className && el.className.toString().slice(0, 70)) || null,
        title: el.title || el.getAttribute("data-bs-title") || el.getAttribute("data-original-title") || null,
        onclick: el.getAttribute("onclick") || null,
        href: el.getAttribute("href") || null,
        text: (el.innerText || el.value || "").trim().slice(0, 50) || null,
      });
      const exportish = (d) =>
        /csv|xls|excel|export|baixar|gerar|download/i.test(
          [d.title, d.onclick, d.href, d.text, d.id, d.cls].filter(Boolean).join(" ")
        );
      return {
        forms: [...document.querySelectorAll("form")].map((f) => ({
          id: f.id || null, name: f.getAttribute("name") || null,
          action: f.getAttribute("action") || null, method: f.method || null,
        })),
        botoesExport: [...document.querySelectorAll("button,a,input[type=submit],[onclick]")]
          .map(desc).filter(exportish),
        temResultados: /Mostrando de .* registros/i.test(document.body.innerText),
      };
    });
    console.log("\n=== FORMS na tela ===");
    console.log(JSON.stringify(controles.forms, null, 2));
    console.log("\n=== CANDIDATOS a botao de export ===");
    console.log(JSON.stringify(controles.botoesExport, null, 2));
    console.log(`\n=== Tem resultados? ${controles.temResultados} ===`);
    console.log("\n>>> Browser ABERTO. Clique no botao verde Excel/CSV. Veja [NET]/[DL] acima.");
    console.log(">>> Ctrl+C aqui pra encerrar quando terminar.\n");

    await new Promise(() => {}); // segura o processo (browser fica aberto)
  } catch (e) {
    console.error(`[debug-export] ERRO: ${e.message}`);
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    await page.screenshot({ path: path.join(DEBUG_DIR, "debug-export-erro.png"), fullPage: true }).catch(() => {});
    console.log(">>> Browser fica aberto pra inspecao mesmo com erro. Ctrl+C pra sair.");
    await new Promise(() => {});
  }
})();
