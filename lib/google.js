const { google } = require("googleapis");

// Auth via service account JSON no env GOOGLE_CREDENTIALS.
// trim() remove BOM que o PowerShell injeta ao gravar o secret.
async function getAuthClient(scopes) {
  const raw = process.env.GOOGLE_CREDENTIALS;
  if (!raw) throw new Error("Faltou env GOOGLE_CREDENTIALS (JSON da service account).");
  const auth = new google.auth.GoogleAuth({
    credentials: JSON.parse(raw.trim()),
    scopes,
  });
  return auth.getClient();
}

// Timestamp "dd/MM/yyyy HH:mm:ss" no timezone alvo.
function stampBR(tz = "America/Sao_Paulo") {
  const parts = new Intl.DateTimeFormat("pt-BR", {
    timeZone: tz,
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}:${p.second}`;
}

// Retenta fn em erro transiente (429/5xx, reset/timeout de rede) com backoff exponencial + jitter.
async function withRetry(fn, { label = "api", tries = 5, baseMs = 500 } = {}) {
  let lastErr;
  for (let attempt = 1; attempt <= tries; attempt++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      const status = Number(e.status ?? e.code ?? e.response?.status);
      const transient =
        [429, 500, 502, 503, 504].includes(status) ||
        ["ECONNRESET", "ETIMEDOUT", "ENOTFOUND", "EAI_AGAIN", "ECONNREFUSED"].includes(e.code);
      if (!transient || attempt === tries) throw e;
      const wait = Math.round(baseMs * 2 ** (attempt - 1) * (1 + Math.random()));
      console.warn(`[retry] ${label}: tentativa ${attempt}/${tries} falhou (${status || e.code}); aguardando ${wait}ms`);
      await new Promise((r) => setTimeout(r, wait));
    }
  }
  throw lastErr;
}

module.exports = { getAuthClient, stampBR, withRetry };
