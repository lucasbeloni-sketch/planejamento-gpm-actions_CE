// Automacao do GPM CE via Playwright (headless).
// Replica os passos da Skill "baixar-consulta-servico-gpm":
//   login -> Consulta Servicos -> Data Inicio = dia 1 do mes -> Contrato
//   -> Pesquisar -> exportar Excel/CSV (.zip) -> devolve os bytes do CSV.
//
// O GPM e uma SPA com tema Falcon: cada tela carrega DENTRO de um iframe
// (#frameTelasGPM). O login fica no documento de topo; o formulario da Consulta
// Servicos fica no iframe. Por isso: login opera em `page`; o resto opera no
// `frame` (contentFrame do iframe). Downloads disparam no nivel de `page`.
//
// Seletores: login confirmado via inspect (#idLogin/#idSenha/botao "Entrar").
// Os de dentro da tela usam override de config.json -> selectors quando
// presente, senao caem em heuristica por texto/papel. Em falha gravamos
// screenshot + HTML em ./debug.

const fs = require("fs");
const path = require("path");
const AdmZip = require("adm-zip");
const crypto = require("crypto");
const { anoMesVigente, contarLinhasDados } = require("./util");

const DEBUG_DIR = path.join(process.cwd(), "debug");
const FRAME_SEL = "#frameTelasGPM";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const NO_RECORDS = /nenhum registro encontrado/i;

// Pesquisa que nao retornou linhas — NAO e falha: o GPM simplesmente nao
// renderiza a toolbar de export quando a tabela esta vazia (acontece todo inicio
// de mes, antes do primeiro servico ser lancado). Quem chama trata como skip.
class SemResultados extends Error {
  constructor(msg) {
    super(msg);
    this.name = "SemResultados";
    this.semResultados = true;
  }
}

// Extrai o valor de `alerta_aviso` (SweetAlert que o GPM renderiza no script da
// tela) do HTML. Casa so a ATRIBUICAO, nunca texto solto: "Nenhum registro
// encontrado" tambem aparece nas strings de i18n da tela (`MSG_NO_RECORDS`, lang
// do DataTables) em TODA pesquisa, com ou sem resultado — um regex no innerHTML
// daria falso-positivo e mascararia export realmente quebrado.
// Devolve a string do aviso, "" se o GPM nao setou aviso, ou null se nao achou.
function extrairAvisoDaTela(html) {
  const m = String(html || "").match(/\balerta_aviso\s*=\s*(['"])((?:(?!\1)[^\\]|\\.)*)\1/);
  return m ? m[2] : null;
}

// Le o aviso da tela: primeiro a variavel viva no realm do iframe, com fallback
// pro HTML. Fail-safe: se nao der pra ler, devolve null e quem chama trata como
// falha de verdade (nunca como "vazio").
async function avisoDaTela(frame) {
  const viaJs = await frame
    .evaluate(() => (typeof alerta_aviso === "string" ? alerta_aviso : null))
    .catch(() => null);
  if (typeof viaJs === "string") return viaJs;
  return extrairAvisoDaTela(await frame.content().catch(() => ""));
}

// Tenta achar um locator visivel por uma lista de candidatos. Cada candidato e
// uma string (CSS/seletor Playwright) ou uma funcao (root) => Locator.
// `root` pode ser uma Page ou um Frame (ambos tem locator/getByX).
async function primeiroVisivel(root, candidatos, { timeout = 8000 } = {}) {
  const deadline = Date.now() + timeout;
  let ultimoErro;
  while (Date.now() < deadline) {
    for (const c of candidatos) {
      if (!c) continue;
      try {
        const loc = typeof c === "function" ? c(root) : root.locator(c);
        const first = loc.first();
        if (await first.isVisible().catch(() => false)) return first;
      } catch (e) {
        ultimoErro = e;
      }
    }
    await sleep(250);
  }
  throw new Error("Nenhum candidato visivel encontrado." + (ultimoErro ? ` Ultimo erro: ${ultimoErro.message}` : ""));
}

async function dump(page, tag) {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    await page.screenshot({ path: path.join(DEBUG_DIR, `${tag}.png`), fullPage: true }).catch(() => {});
    const html = await page.content().catch(() => "");
    fs.writeFileSync(path.join(DEBUG_DIR, `${tag}.html`), html);
    console.warn(`[debug] artefatos salvos: debug/${tag}.png e debug/${tag}.html`);
  } catch (_) {}
}

// Salva o HTML de DENTRO do iframe (a tela real), nao do shell.
async function dumpFrame(frame, tag) {
  try {
    fs.mkdirSync(DEBUG_DIR, { recursive: true });
    fs.writeFileSync(path.join(DEBUG_DIR, `${tag}.html`), await frame.content());
    console.warn(`[debug] HTML do iframe salvo: debug/${tag}.html`);
  } catch (_) {}
}

// Sessao ativa = campo de senha do login NAO esta mais visivel.
async function estaLogado(page) {
  const senhaVisivel = await page.locator("#idSenha, input[type=password]").first()
    .isVisible().catch(() => false);
  return !senhaVisivel;
}

// Mostra a credencial no log sem vazar: 1o caractere + tamanho. Serve pra
// diagnosticar secret vazio ou com espaco em volta — o GPM recusa os dois com a
// mesma mensagem de senha errada ("Usuario e/ou senha invalida!").
function mascararCred(v) {
  if (!v) return "(vazio)";
  return `${v[0]}${"*".repeat(Math.max(0, v.length - 1))} (len=${v.length})`;
}

async function login(page, cfg) {
  const { baseUrl, selectors: s } = cfg;
  const user = (process.env.GPM_USER || "").trim();
  const pass = (process.env.GPM_PASS || "").trim();

  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await sleep(1500);

  if (await estaLogado(page)) {
    console.log("[login] sessao ja ativa.");
    return;
  }
  if (!user || !pass) {
    await dump(page, "login-sem-credenciais");
    throw new Error("Tela de login detectada mas faltam GPM_USER/GPM_PASS no ambiente.");
  }
  console.log(`[login] usuario=${mascararCred(user)} em ${baseUrl}`);

  try {
    const campoUser = await primeiroVisivel(page, [
      s.loginUser, "#idLogin", 'input[name="login"]', 'input[type="text"]',
    ]);
    await campoUser.fill(user);

    const campoPass = await primeiroVisivel(page, [
      s.loginPass, "#idSenha", 'input[name="password"]', 'input[type="password"]',
    ]);
    await campoPass.fill(pass);

    const botao = await primeiroVisivel(page, [
      s.loginSubmit, "button:has-text('Entrar')",
      (p) => p.getByRole("button", { name: /entrar|acessar|login/i }),
    ]);
    await Promise.all([
      page.waitForLoadState("networkidle").catch(() => {}),
      botao.click(),
    ]);
    await sleep(2000);
  } catch (e) {
    await dump(page, "login-falha");
    throw new Error(`Falha ao preencher/enviar o login: ${e.message}`);
  }

  if (!(await estaLogado(page))) {
    await dump(page, "login-pos-submit");
    throw new Error("Login enviado mas a area interna nao apareceu (credenciais invalidas, captcha ou seletor errado?).");
  }
  console.log("[login] autenticado com sucesso.");
}

// Abre a Consulta Servicos e devolve o Frame do iframe onde a tela vive.
async function abrirConsulta(page, cfg) {
  await page.goto(cfg.consultaUrl, { waitUntil: "domcontentloaded" });
  const iframeEl = await page.waitForSelector(FRAME_SEL, { timeout: 20000 });
  const frame = await iframeEl.contentFrame();
  if (!frame) throw new Error("iframe #frameTelasGPM nao tem contentFrame.");
  await frame.waitForLoadState("domcontentloaded").catch(() => {});
  // Confirma que a tela carregou (botao Pesquisar / titulo).
  await primeiroVisivel(frame, [
    cfg.selectors.pesquisar,
    (f) => f.getByRole("button", { name: /Pesquisar/i }),
    "text=/Consulta Servi[cç]os/i",
  ], { timeout: 20000 });
  console.log("[gpm] Consulta Servicos aberta (dentro do iframe).");
  return frame;
}

// Garante que o calendario flatpickr aberto mostra ano/mes alvo (mes 1-12).
async function ajustarMesAnoCalendario(cal, ano, mes) {
  for (let i = 0; i < 24; i++) {
    const atual = await cal.evaluate((el) => {
      const sel = el.querySelector(".flatpickr-monthDropdown-months");
      const yr = el.querySelector(".numInput.cur-year");
      return { m: sel ? sel.selectedIndex + 1 : null, y: yr ? Number(yr.value) : null };
    }).catch(() => ({ m: null, y: null }));
    if (atual.m == null || atual.y == null) return; // sem como ler; assume ok
    const diff = (ano - atual.y) * 12 + (mes - atual.m);
    if (diff === 0) return;
    const btn = diff > 0 ? ".flatpickr-next-month" : ".flatpickr-prev-month";
    await cal.locator(btn).click();
    await sleep(150);
  }
}

// Data Servico Inicio = dia 1 do mes vigente. O campo e um flatpickr cuja
// instancia vive no input VISIVEL (.flatpickr-input). Tentamos setar pela API
// (setDate) nesse elemento; se nao houver instancia, abrimos o calendario e
// clicamos no dia 1. Deixamos data_final vazio (como a Skill).
async function setDataInicio(frame, cfg) {
  const { ano, mes } = anoMesVigente(cfg.timezone);

  // Espera o flatpickr inicializar (no CI a JS pode demorar mais que a tela
  // aparecer — sem isso, comFp=0 e caímos num fallback fragil).
  await frame.waitForFunction(() => {
    return [...document.querySelectorAll("input")].some(
      (i) => i._flatpickr && (/data_inicial/i.test(i.className || "") || /in[ií]cio/i.test(i.placeholder || ""))
    );
  }, { timeout: 25000 }).catch(() => { /* segue; o evaluate abaixo trata/diagnostica */ });

  // 1) Caminho preferido: API do flatpickr. Procura o input de inicio (classe
  // data_inicial / placeholder Inicio) que tenha instancia flatpickr, e seta.
  // Devolve diagnostico se nao houver instancia (ajuda a calibrar).
  const r = await frame.evaluate(({ ano, mes }) => {
    const inputs = [...document.querySelectorAll("input")];
    const comFp = inputs.filter((i) => i._flatpickr);
    const ehInicio = (i) =>
      /data_inicial/i.test(i.className || "") || /in[ií]cio/i.test(i.placeholder || "");
    let el = comFp.find(ehInicio) || comFp[0];
    if (!el) {
      return {
        ok: false,
        total: inputs.length,
        comFp: comFp.length,
        amostra: inputs.filter((i) => ehInicio(i)).map((i) => ({
          cls: (i.className || "").slice(0, 60), ph: i.placeholder || null, type: i.type, temFp: !!i._flatpickr,
        })),
      };
    }
    el._flatpickr.setDate(new Date(ano, mes - 1, 1), true);
    return { ok: true, valor: el.value, ph: el.placeholder, cls: (el.className || "").slice(0, 60), instancias: comFp.length };
  }, { ano, mes });

  if (r.ok) {
    await sleep(400);
    console.log(`[gpm] Data Servico Inicio = 01/${String(mes).padStart(2, "0")}/${ano} via API (valor: ${r.valor}; ${r.instancias} flatpickrs na tela).`);
    return;
  }

  // 2) Sem instancia flatpickr acessivel: fallback pelo calendario (UI).
  console.warn(`[gpm] flatpickr API indisponivel (inputs=${r.total}, comFp=${r.comFp}). Diag: ${JSON.stringify(r.amostra)}. Tentando calendario...`);
  const campo = await primeiroVisivel(frame, [
    cfg.selectors.dataServicoInicio,
    "input.red-border-data_inicial",
    'input.flatpickr-input[placeholder*="Início" i]',
    (f) => f.getByPlaceholder(/Data\s+Servi[cç]o\s+In[ií]cio/i),
  ]);
  await campo.scrollIntoViewIfNeeded().catch(() => {});
  await campo.click();
  const cal = frame.locator(".flatpickr-calendar.open").first();
  try {
    await cal.waitFor({ state: "visible", timeout: 8000 });
  } catch (e) {
    await dump(frame.page(), "data-inicio-falha");
    throw new Error(`Data Servico Inicio: nem API nem calendario disponiveis (${e.message})`);
  }
  await ajustarMesAnoCalendario(cal, ano, mes);
  const dia1 = cal
    .locator('.flatpickr-day:not(.prevMonthDay):not(.nextMonthDay):not(.flatpickr-disabled)', { hasText: /^1$/ })
    .first();
  await dia1.click();
  await frame.locator("body").click({ position: { x: 4, y: 4 } }).catch(() => {});
  await sleep(400);
  const valor = await campo.inputValue().catch(() => "");
  console.log(`[gpm] Data Servico Inicio = 01/${String(mes).padStart(2, "0")}/${ano} via calendario (valor: ${valor}).`);
}

// Le {value,text} do <select> nativo do contrato (e o que o submit usa).
async function lerContratoSelecionado(frame) {
  return frame.evaluate(() => {
    const s = document.querySelector("#contrato");
    return { value: s ? s.value : "", text: s && s.options[s.selectedIndex] ? s.options[s.selectedIndex].text.trim() : "" };
  });
}

// Contrato via Choices.js (#contrato). As opcoes ja existem ao abrir (nao e
// busca remota). Abrir = clicar em .choices__inner (clicar no wrapper externo
// nao abre). NAO digitar a string completa (a busca fuzzy a filtra pra fora):
// digita um token curto, seleciona com Enter e confere pelo <select> nativo.
async function selecionarContrato(frame, cfg, contrato) {
  const wrap = frame.locator('div.choices:has(#contrato)').first();
  const inner = wrap.locator(".choices__inner").first();
  const busca = wrap.locator("input.choices__input--cloned").first();
  const token = contrato.search
    || (contrato.dropdown.match(/\d{5,}/) || [])[0]
    || contrato.dropdown.slice(0, 6);
  const baterTexto = (t) => t && t.includes(contrato.dropdown.slice(0, 10));

  // Espera o Choices.js montar o widget (no CI pode demorar).
  await inner.waitFor({ state: "visible", timeout: 20000 }).catch(() => {});
  await wrap.scrollIntoViewIfNeeded().catch(() => {});

  // Abre o dropdown (ate 3 tentativas; confirma pela classe is-open).
  for (let i = 0; i < 3; i++) {
    const aberto = await wrap.evaluate((el) => el.classList.contains("is-open")).catch(() => false);
    if (aberto) break;
    await inner.click({ force: true }).catch(() => {});
    await sleep(400);
  }

  // Filtra por token e seleciona a opcao destacada com Enter.
  if (await busca.isVisible().catch(() => false)) {
    await busca.fill(token);
    await sleep(900);
    await busca.press("Enter").catch(() => {});
    await sleep(400);
  }

  let sel = await lerContratoSelecionado(frame);

  // Fallback: clica na opcao diretamente (dropdown aberto -> item visivel).
  if (!sel.value || !baterTexto(sel.text)) {
    const opcao = frame
      .locator("#contrato")
      .locator("xpath=ancestor::div[contains(@class,'choices')][1]")
      .locator('.choices__list[role="listbox"] .choices__item--choice', { hasText: contrato.dropdown })
      .first();
    await opcao.click({ timeout: 8000 }).catch(() => {});
    await sleep(400);
    sel = await lerContratoSelecionado(frame);
  }

  if (!sel.value || !baterTexto(sel.text)) {
    await dump(frame.page(), `contrato-falha-${contrato.prefixo}`);
    throw new Error(`Contrato nao selecionado (value="${sel.value}", text="${sel.text}").`);
  }
  console.log(`[gpm] Contrato selecionado: ${sel.text} (value=${sel.value}).`);
}

async function pesquisar(frame, cfg) {
  const { selectors: s } = cfg;
  const formId = cfg.exportFormId || "form_principal";
  const botao = await primeiroVisivel(frame, [
    s.pesquisar, (f) => f.getByRole("button", { name: /Pesquisar/i }),
  ]);
  // Clicar em Pesquisar re-renderiza o iframe (POST .../pesquisar) trazendo a
  // tabela + a toolbar de export (form_principal). O sinal confiavel de que os
  // resultados chegaram e a PRESENCA do form_principal — NAO o texto "registros"
  // (que so aparece se houver linhas). Sem isso, no CI (mais lento) o export
  // rodava antes da toolbar existir -> "form_principal nao encontrado".
  await botao.click();
  try {
    await frame.waitForSelector(`#${formId}`, { state: "attached", timeout: 45000 });
  } catch (e) {
    // Sem toolbar: pode ser pesquisa vazia (normal) ou export quebrado (grave).
    // O que separa os dois e o GPM ter setado o aviso "Nenhum registro
    // encontrado". Se nao der pra ler o aviso, tratamos como quebrado.
    const aviso = await avisoDaTela(frame);
    const vazio = !!aviso && NO_RECORDS.test(aviso);
    await dumpFrame(frame, vazio ? "pesquisa-vazia" : "pesquisa-sem-toolbar");
    if (vazio) {
      throw new SemResultados(`GPM respondeu "${aviso}": nenhum servico no periodo/contrato pesquisado.`);
    }
    throw new Error(`Pesquisa: toolbar de export (#${formId}) nao apareceu em 45s apos Pesquisar. Resultados nao renderizaram? (${e.message})`);
  }
  // Espera o texto de contagem tambem (best-effort), so pra log/estabilizacao.
  await primeiroVisivel(frame, [
    "text=/Mostrando de .* registros/i", "text=/registros/i",
  ], { timeout: 5000 }).catch(() => {});
  await sleep(500);
  console.log("[gpm] Pesquisa concluida.");
}

// Dispara o export "Excel/CSV". O botao verde chama change_lnk(), que seta
// form_principal.action e submete num POPUP (por isso o download nao aparecia na
// pagina principal). Aqui submetemos o MESMO form na propria janela do iframe
// (target=_self) para o endpoint de export, e capturamos o download na page.
// Salva o arquivo em ./debug e devolve o caminho.
async function exportar(page, frame, cfg) {
  const action = cfg.exportAction || "/gpm/geral/consulta_servico_csv.php";
  const formId = cfg.exportFormId || "form_principal";

  // Captura download tanto na page atual quanto em eventual popup (fallback).
  // Captura download tanto na page atual quanto em eventual popup (fallback).
  // Listener removido no finally pra nao vazar entre contratos.
  // Guard: no CI a toolbar de export pode ainda nao ter renderizado. Espera o
  // form existir antes de tentar submeter (pesquisar() ja espera, isto e defesa
  // em profundidade caso a ordem de chamada mude).
  await frame.waitForSelector(`#${formId}`, { state: "attached", timeout: 30000 }).catch(() => {});

  const ctx = page.context();
  let onPage;
  const viaPopup = new Promise((resolve) => {
    onPage = (p) => p.waitForEvent("download", { timeout: 28000 }).then(resolve).catch(() => {});
    ctx.on("page", onPage);
  });

  const sub = await frame.evaluate(({ action, formId }) => {
    const form = document.getElementById(formId) || document.forms[formId] || document.form_principal;
    if (!form) return { ok: false, reason: `form "${formId}" nao encontrado` };
    form.action = action;
    form.target = "_self"; // submeter na propria janela do iframe -> download na page
    form.submit();
    return { ok: true };
  }, { action, formId });

  if (!sub.ok) {
    ctx.off("page", onPage);
    await dumpFrame(frame, "export-sem-form");
    throw new Error(`Export: ${sub.reason}`);
  }

  let download;
  try {
    download = await Promise.race([
      page.waitForEvent("download", { timeout: 30000 }),
      viaPopup,
    ]);
  } catch (e) {
    await dumpFrame(frame, "export-sem-download");
    await dump(page, "export-sem-download-shell");
    throw new Error(`Submeti o export (${action}) mas nenhum download veio em 30s. ${e.message}`);
  } finally {
    ctx.off("page", onPage);
  }
  if (!download) throw new Error("Export sem objeto de download.");

  const sug = download.suggestedFilename();
  fs.mkdirSync(DEBUG_DIR, { recursive: true });
  const destino = path.join(DEBUG_DIR, `ultimo-download-${sug || "arquivo"}`);
  await download.saveAs(destino);
  console.log(`[gpm] download recebido: "${sug}" -> ${destino}`);
  return destino;
}

// Extrai os bytes do CSV do arquivo baixado, detectando o tipo:
//  - ZIP (assinatura "PK"): se tiver .csv dentro, extrai; se for xlsx, erro claro.
//  - texto (CSV direto): usa como veio.
function extrairCsv(arqPath) {
  const raw = fs.readFileSync(arqPath);
  const ehZip = raw.length >= 2 && raw[0] === 0x50 && raw[1] === 0x4b; // "PK"

  let buffer;
  if (ehZip) {
    const zip = new AdmZip(raw);
    const nomes = zip.getEntries().map((e) => e.entryName);
    const entry = zip.getEntries().find((e) => e.entryName.toLowerCase().endsWith(".csv"));
    if (!entry) {
      const ehXlsx = nomes.some((n) => /^xl\//i.test(n));
      throw new Error(ehXlsx
        ? `O download foi um XLSX (Excel), nao o .zip com CSV. Clicamos no controle errado. Conteudo: ${nomes.join(", ")}`
        : `Zip sem .csv. Conteudo: ${nomes.join(", ")}`);
    }
    buffer = entry.getData(); // bytes crus, inclui BOM se houver
  } else {
    // Provavelmente CSV direto (ou HTML de erro). Heuristica simples:
    const inicio = raw.slice(0, 64).toString("utf8").toLowerCase();
    if (inicio.includes("<!doctype") || inicio.includes("<html")) {
      throw new Error("O download veio como HTML (provavel pagina de erro/sessao), nao CSV/zip.");
    }
    buffer = raw; // trata como CSV cru (preserva BOM/bytes)
  }

  const md5 = crypto.createHash("md5").update(buffer).digest("hex");
  return { buffer, md5, bytes: buffer.length, linhas: contarLinhasDados(buffer), origem: ehZip ? "zip" : "csv-direto" };
}

async function baixarContrato(page, cfg, contrato, mesAno) {
  console.log(`\n=== Contrato ${contrato.dropdown} (${contrato.prefixo}) ===`);
  const frame = await abrirConsulta(page, cfg);
  await setDataInicio(frame, cfg);
  await selecionarContrato(frame, cfg, contrato);
  await pesquisar(frame, cfg);

  let zipPath;
  try {
    zipPath = await exportar(page, frame, cfg);
  } catch (e) {
    await dump(page, `export-falha-${contrato.prefixo}`);
    throw new Error(`Falha ao exportar ${contrato.prefixo}: ${e.message}`);
  }

  const { buffer, md5, bytes, linhas } = extrairCsv(zipPath);
  const nomeFinal = `${contrato.prefixo} - ${mesAno}.csv`;
  console.log(`[gpm] ${nomeFinal} extraido: ${bytes} bytes, ${linhas} linhas de dados, md5=${md5}`);
  return { buffer, md5, bytes, linhas, nomeFinal };
}

module.exports = {
  login, baixarContrato, extrairCsv, dump, dumpFrame,
  SemResultados, extrairAvisoDaTela,
  // expostos p/ debug/calibracao (tools/*):
  abrirConsulta, setDataInicio, selecionarContrato, pesquisar, exportar,
};
