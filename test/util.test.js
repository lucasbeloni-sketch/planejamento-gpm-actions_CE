const { test } = require("node:test");
const assert = require("node:assert");
const { anoMesVigente, mesAnoVigente, contarLinhasDados } = require("../src/util");

test("anoMesVigente: junho/2026 em America/Sao_Paulo", () => {
  const now = new Date("2026-06-15T12:00:00Z");
  assert.deepStrictEqual(anoMesVigente("America/Sao_Paulo", now), { ano: 2026, mes: 6 });
});

test("mesAnoVigente: formata mm.aaaa com zero a esquerda", () => {
  assert.strictEqual(mesAnoVigente("America/Sao_Paulo", new Date("2026-01-09T12:00:00Z")), "01.2026");
});

test("contarLinhasDados: ignora cabecalho, BOM e linhas vazias", () => {
  assert.strictEqual(contarLinhasDados(Buffer.from("﻿a;b;c\n1;2;3\n4;5;6\n", "utf8")), 2);
  assert.strictEqual(contarLinhasDados(Buffer.from("﻿a;b\n", "utf8")), 0); // so cabecalho
  assert.strictEqual(contarLinhasDados(Buffer.from("a;b\n1;2\n\n\n", "utf8")), 1);
});
