// Detecao de "pesquisa vazia" vs "export quebrado".
//
// A armadilha: "Nenhum registro encontrado" aparece nas strings de i18n da tela
// (`MSG_NO_RECORDS`, lang do DataTables) em TODA pesquisa, com ou sem resultado.
// Um regex no innerHTML casaria sempre e faria o pipeline engolir em silencio um
// export realmente quebrado. Por isso lemos so o VALOR de `alerta_aviso`.
//
// Os trechos abaixo sao recortes do dump real de debug/pesquisa-vazia.html
// (run 31012448923, 05/08/2026).

const test = require("node:test");
const assert = require("node:assert");
const { extrairAvisoDaTela } = require("../src/gpm");

// Strings de i18n: presentes em qualquer tela, com ou sem resultado.
const I18N = `
  <script>
    let lang_msg = {
      'MSG_PARAM_INV': 'Parâmetros Inválidos',
      'MSG_NO_RECORDS': 'Nenhum registro encontrado',
      'PDF_ERROR': 'Não foi possível gerar o PDF'
    }
    let dt_lang = { emptyTable: 'Nenhum registro encontrado', zeroRecords: 'Nenhum registro encontrado' };
  </script>
`;

test("pesquisa vazia: le o aviso que o GPM setou", () => {
  const html = `${I18N}
    <script>
      // Sweet Alert
      let alerta_icone = 'warning';
      let alerta_aviso = 'Nenhum registro encontrado';
      let alerta_aviso_2 = '';
    </script>
    <tbody><tr><td class="text-center" colspan="100%">Nenhum registro encontrado</td></tr></tbody>`;
  assert.strictEqual(extrairAvisoDaTela(html), "Nenhum registro encontrado");
});

test("pesquisa COM resultado: aviso vazio, apesar do i18n citar o texto", () => {
  const html = `${I18N}
    <script>
      let alerta_icone = 'warning';
      let alerta_aviso = '';
    </script>
    <form id="form_principal"></form>`;
  // "" e falsy -> quem chama NAO trata como vazio. Este e o caso que impede o
  // falso-positivo que mascararia export quebrado.
  assert.strictEqual(extrairAvisoDaTela(html), "");
});

test("tela sem a variavel: devolve null (fail-safe -> trata como falha)", () => {
  assert.strictEqual(extrairAvisoDaTela(I18N), null);
  assert.strictEqual(extrairAvisoDaTela(""), null);
  assert.strictEqual(extrairAvisoDaTela(null), null);
});

test("aceita aspas duplas e apostrofo escapado no valor", () => {
  assert.strictEqual(extrairAvisoDaTela(`let alerta_aviso = "Nenhum registro encontrado";`), "Nenhum registro encontrado");
  assert.strictEqual(extrairAvisoDaTela(`let alerta_aviso = 'N\\'ao ha dados';`), "N\\'ao ha dados");
});

test("nao casa nome de variavel parecido", () => {
  assert.strictEqual(extrairAvisoDaTela(`let outro_alerta_aviso = 'Nenhum registro encontrado';`), null);
});

test("outro aviso do GPM nao e confundido com pesquisa vazia", () => {
  const html = `${I18N}<script>let alerta_aviso = 'Parâmetros Inválidos';</script>`;
  const aviso = extrairAvisoDaTela(html);
  assert.strictEqual(aviso, "Parâmetros Inválidos");
  assert.ok(!/nenhum registro encontrado/i.test(aviso), "nao deve ser tratado como vazio");
});
