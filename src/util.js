// Funcoes puras (sem browser/rede) — testaveis isoladamente.

// ano/mes (1-12) do mes vigente no timezone alvo.
function anoMesVigente(tz, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit" })
    .formatToParts(now);
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return { ano: Number(p.year), mes: Number(p.month) };
}

// "mm.aaaa" do mes vigente (mesmo mes da Data Servico Inicio).
function mesAnoVigente(tz, now = new Date()) {
  const { ano, mes } = anoMesVigente(tz, now);
  return `${String(mes).padStart(2, "0")}.${ano}`;
}

// Conta linhas de DADOS de um CSV (buffer com/sem BOM). Cabecalho nao conta.
function contarLinhasDados(buffer) {
  let txt = Buffer.isBuffer(buffer) ? buffer.toString("utf8") : String(buffer);
  if (txt.charCodeAt(0) === 0xfeff) txt = txt.slice(1); // tira BOM
  const linhas = txt.split(/\r?\n/).filter((l) => l.trim() !== "");
  return Math.max(0, linhas.length - 1);
}

module.exports = { anoMesVigente, mesAnoVigente, contarLinhasDados };
