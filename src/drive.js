// Envia o CSV ao Google Drive via service account (Drive API).
// Substitui o "Write na pasta do Drive Desktop" da Skill: aqui escrevemos
// direto na pasta-destino pelo ID, sobrescrevendo o arquivo do mes.
//
// A pasta-destino fica num Shared Drive; por isso resolvemos o driveId e
// passamos supportsAllDrives / includeItemsFromAllDrives em tudo.

const { Readable } = require("stream");
const { google } = require("googleapis");
const { getAuthClient, withRetry } = require("../lib/google");

const SCOPES = ["https://www.googleapis.com/auth/drive"];

async function getDrive() {
  const auth = await getAuthClient(SCOPES);
  return google.drive({ version: "v3", auth });
}

function escapaQuery(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

// Sobe/atualiza o CSV. Retorna { acao: "updated"|"created", id, duplicatas }.
async function uploadCsv(buffer, nomeFinal, cfg) {
  const drive = await getDrive();
  const folderId = cfg.destFolderId;

  const folder = await withRetry(
    () => drive.files.get({ fileId: folderId, fields: "id,name,driveId", supportsAllDrives: true }),
    { label: "get folder" }
  );
  const driveId = folder.data.driveId;

  const q = `'${folderId}' in parents and trashed = false and name = '${escapaQuery(nomeFinal)}'`;
  const list = await withRetry(
    () => drive.files.list({
      q,
      fields: "files(id,name)",
      pageSize: 10,
      supportsAllDrives: true,
      includeItemsFromAllDrives: true,
      corpora: "drive",
      driveId,
    }),
    { label: "list" }
  );
  const existentes = list.data.files || [];

  const media = { mimeType: "text/csv", body: Readable.from(buffer) };

  if (existentes.length) {
    const alvo = existentes[0].id;
    await withRetry(
      () => drive.files.update({ fileId: alvo, media, supportsAllDrives: true }),
      { label: "update" }
    );
    console.log(`[drive] "${nomeFinal}" atualizado (id=${alvo}).`);

    // Auto-dedup: se houver mais de uma copia com o mesmo nome, manda as extras
    // pra lixeira (reversivel; some das buscas que filtram trashed=false). Assim
    // o downstream (compilador) nunca le o mes em duplicado.
    const extras = existentes.slice(1).map((f) => f.id);
    const removidas = [];
    for (const id of extras) {
      try {
        await withRetry(
          () => drive.files.update({ fileId: id, requestBody: { trashed: true }, supportsAllDrives: true }),
          { label: "trash-dup" }
        );
        removidas.push(id);
      } catch (e) {
        console.warn(`[drive] nao consegui mover duplicata ${id} pra lixeira: ${e.message}`);
      }
    }
    if (extras.length) {
      console.warn(`[drive] auto-dedup: ${removidas.length}/${extras.length} duplicata(s) de "${nomeFinal}" movidas pra lixeira (${removidas.join(", ") || "nenhuma"}).`);
    }
    return { acao: "updated", id: alvo, duplicatas: removidas };
  }

  const created = await withRetry(
    () => drive.files.create({
      requestBody: { name: nomeFinal, parents: [folderId] },
      media,
      fields: "id",
      supportsAllDrives: true,
    }),
    { label: "create" }
  );
  console.log(`[drive] "${nomeFinal}" criado (id=${created.data.id}).`);
  return { acao: "created", id: created.data.id, duplicatas: [] };
}

module.exports = { uploadCsv };
