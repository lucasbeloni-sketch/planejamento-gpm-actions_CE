// Verificacao read-only: lista os arquivos na pasta destino que se chamam
// "SOC.SOT - <mm.aaaa>.csv" (e todos os CSVs da pasta), pra detectar duplicatas.
// Uso: GOOGLE_CREDENTIALS=... node src/check-drive.js
const { google } = require("googleapis");
const { getAuthClient } = require("../lib/google");
const cfg = require("../config.json");

(async () => {
  const auth = await getAuthClient(["https://www.googleapis.com/auth/drive"]);
  const drive = google.drive({ version: "v3", auth });
  const folderId = cfg.destFolderId;
  const folder = await drive.files.get({ fileId: folderId, fields: "id,name,driveId", supportsAllDrives: true });
  const driveId = folder.data.driveId;
  console.log(`Pasta: ${folder.data.name} (${folderId}) | driveId=${driveId}`);

  const list = await drive.files.list({
    q: `'${folderId}' in parents and trashed = false and name contains 'SOC.SOT'`,
    fields: "files(id,name,modifiedTime,size)",
    pageSize: 100,
    supportsAllDrives: true,
    includeItemsFromAllDrives: true,
    corpora: "drive",
    driveId,
    orderBy: "name,modifiedTime",
  });
  const files = list.data.files || [];
  console.log(`\nArquivos 'SOC.SOT*' na pasta: ${files.length}`);
  for (const f of files) {
    console.log(`  - ${f.name} | id=${f.id} | mod=${f.modifiedTime} | ${f.size || "?"} bytes`);
  }

  // Agrupa por nome pra apontar duplicatas
  const porNome = {};
  for (const f of files) (porNome[f.name] ||= []).push(f);
  const dups = Object.entries(porNome).filter(([, arr]) => arr.length > 1);
  if (dups.length) {
    console.log("\n⚠️ DUPLICATAS encontradas:");
    for (const [nome, arr] of dups) console.log(`  "${nome}": ${arr.length} copias -> ids: ${arr.map((x) => x.id).join(", ")}`);
  } else {
    console.log("\n✓ Sem duplicatas (no maximo 1 por nome).");
  }
})();
