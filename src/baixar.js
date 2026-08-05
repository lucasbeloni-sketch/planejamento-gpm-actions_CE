// Orquestrador: login -> por contrato (baixar + extrair + enviar ao Drive).
// Roda igual local e no GitHub Actions. Headless por padrao; HEADED=1 abre o
// browser visivel (use no debug local). DRY_RUN=1 baixa mas nao envia ao Drive.

const { chromium } = require("playwright");
const cfg = require("../config.json");
const { login, baixarContrato, dump } = require("./gpm");
const { uploadCsv } = require("./drive");
const { mesAnoVigente } = require("./util");

// Retenta fn ate `tentativas` vezes (GPM e flaky). Loga cada tentativa.
async function comRetry(fn, label, tentativas = 2) {
  let err;
  for (let i = 1; i <= tentativas; i++) {
    try {
      return await fn();
    } catch (e) {
      err = e;
      // Pesquisa vazia e deterministica: retentar so queima outro timeout de 45s.
      if (e.semResultados) break;
      if (i < tentativas) console.warn(`[run] ${label}: tentativa ${i}/${tentativas} falhou (${e.message}); tentando de novo...`);
    }
  }
  throw err;
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
  const vazios = [];
  let falhou = false;
  try {
    await login(page, cfg);

    const minLinhas = cfg.minLinhasDados ?? 1;
    for (const contrato of cfg.contratos) {
      try {
        const { buffer, md5, bytes, linhas, nomeFinal } = await comRetry(
          () => baixarContrato(page, cfg, contrato, mesAno),
          `contrato ${contrato.prefixo}`
        );

        // Guard anti-clobber: nao sobrescrever o arquivo do mes com um CSV
        // vazio (so cabecalho) — provavel glitch/filtro errado do GPM.
        if (linhas < minLinhas) {
          throw new Error(`CSV com ${linhas} linha(s) de dados (< minimo ${minLinhas}). NAO sobrescrevo o arquivo do mes (provavel glitch do GPM).`);
        }

        if (dryRun) {
          console.log(`[run] DRY_RUN: ${nomeFinal} (${bytes} bytes, ${linhas} linhas) NAO enviado ao Drive.`);
          resultados.push({ contrato: contrato.prefixo, nomeFinal, md5, bytes, acao: "dry-run" });
        } else {
          const r = await uploadCsv(buffer, nomeFinal, cfg);
          resultados.push({ contrato: contrato.prefixo, nomeFinal, md5, bytes, acao: r.acao });
        }
      } catch (e) {
        // Pesquisa sem linhas nao e falha: preserva o arquivo do mes no Drive e
        // segue. Todo inicio de mes cai aqui ate o 1o servico ser lancado.
        if (e.semResultados) {
          vazios.push(contrato.prefixo);
          console.log(`[run] ${contrato.prefixo}: nenhum servico em ${mesAno}, nada a enviar (arquivo do mes preservado). ${e.message}`);
          continue;
        }
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
  for (const p of vazios) console.log(`  ${p}: sem servico em ${mesAno} — nada enviado.`);
  if (falhou || resultados.length + vazios.length < cfg.contratos.length) {
    console.error("[run] terminou COM falhas.");
    process.exit(1);
  }
  if (vazios.length === cfg.contratos.length) {
    console.log(`[run] terminou OK: nenhum contrato tinha servico em ${mesAno} (nada a fazer).`);
    return;
  }
  console.log("[run] terminou OK.");
})();
