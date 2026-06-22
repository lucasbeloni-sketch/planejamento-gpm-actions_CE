const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const AdmZip = require("adm-zip");
const { extrairCsv } = require("../src/gpm");

const tmp = (n) => path.join(os.tmpdir(), `csvtest-${process.pid}-${n}`);

test("extrairCsv: zip com .csv -> extrai bytes crus e conta linhas", () => {
  const csv = Buffer.from("﻿a;b\n1;2\n3;4\n", "utf8");
  const zip = new AdmZip();
  zip.addFile("consulta_servicos_x.csv", csv);
  const p = tmp("z.zip");
  zip.writeZip(p);
  const r = extrairCsv(p);
  assert.strictEqual(r.origem, "zip");
  assert.strictEqual(r.linhas, 2);
  assert.ok(r.buffer.equals(csv), "preserva bytes (BOM incluso)");
  fs.unlinkSync(p);
});

test("extrairCsv: csv direto (sem zip)", () => {
  const p = tmp("d.csv");
  fs.writeFileSync(p, Buffer.from("﻿a;b\n1;2\n", "utf8"));
  const r = extrairCsv(p);
  assert.strictEqual(r.origem, "csv-direto");
  assert.strictEqual(r.linhas, 1);
  fs.unlinkSync(p);
});

test("extrairCsv: xlsx (zip com xl/) lanca erro claro", () => {
  const zip = new AdmZip();
  zip.addFile("xl/workbook.xml", Buffer.from("<x/>"));
  const p = tmp("x.xlsx");
  zip.writeZip(p);
  assert.throws(() => extrairCsv(p), /XLSX/);
  fs.unlinkSync(p);
});

test("extrairCsv: HTML de erro lanca", () => {
  const p = tmp("h.html");
  fs.writeFileSync(p, "<!doctype html><html><body>erro</body></html>");
  assert.throws(() => extrairCsv(p), /HTML/);
  fs.unlinkSync(p);
});
