// Orquestrador: login -> por contrato (baixar + extrair + enviar ao Drive).
// Roda igual local e no GitHub Actions. Headless por padrao; HEADED=1 abre o
// browser visivel (use no debug local). DRY_RUN=1 baixa mas nao envia ao Drive.

const path = require("path");
const { chromium } = require("playwright");
const cfg = require("../config.json");
const { login, baixarContrato, dump } = require("./gpm");
const { uploadCsv } = require("./drive");

// "mm.aaaa" do mes vigente no timezone alvo (mesmo mes da Data Servico Inicio).
function mesAnoVigente(tz) {
  const parts = new Intl.DateTimeFormat("pt-BR", { timeZone: tz, month: "2-digit", year: "numeric" })
    .formatToParts(new Date());
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return `${p.month}.${p.year}`;
}

(async () => {
  const headless = !process.env.HEADED;
  const dryRun = !!process.env.DRY_RUN;
  const mesAno = mesAnoVigente(cfg.timezone);
  console.log(`[run] mes vigente=${mesAno} | headless=${headless} | dryRun=${dryRun} | contratos=${cfg.contratos.length}`);

  const browser = await chromium.launch({ headless });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);

  const resultados = [];
  let falhou = false;
  try {
    await login(page, cfg);

    for (const contrato of cfg.contratos) {
      try {
        const { buffer, md5, bytes, nomeFinal } = await baixarContrato(page, cfg, contrato, mesAno);
        if (dryRun) {
          console.log(`[run] DRY_RUN: ${nomeFinal} (${bytes} bytes) NAO enviado ao Drive.`);
          resultados.push({ contrato: contrato.prefixo, nomeFinal, md5, bytes, acao: "dry-run" });
        } else {
          const r = await uploadCsv(buffer, nomeFinal, cfg);
          resultados.push({ contrato: contrato.prefixo, nomeFinal, md5, bytes, acao: r.acao });
        }
      } catch (e) {
        falhou = true;
        console.error(`[run] ERRO no contrato ${contrato.prefixo}: ${e.message}`);
      }
    }
  } catch (e) {
    falhou = true;
    console.error(`[run] ERRO fatal: ${e.message}`);
    await dump(page, "erro-fatal");
  } finally {
    await browser.close();
  }

  console.log("\n=== Resumo ===");
  for (const r of resultados) console.log(`  ${r.nomeFinal}: ${r.acao} (${r.bytes} bytes, md5=${r.md5})`);
  if (falhou || resultados.length < cfg.contratos.length) {
    console.error("[run] terminou COM falhas.");
    process.exit(1);
  }
  console.log("[run] terminou OK.");
})();
