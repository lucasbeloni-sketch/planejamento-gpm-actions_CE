# Consulta Servico GPM — pipeline automatico (GitHub Actions)

Pipeline de 2 etapas, sequenciais, no mesmo workflow:

1. **baixar** (Node/Playwright) — baixa o relatorio **Consulta Servicos** do GPM
   CE (`https://sirtecce.gpm.srv.br/`) e envia o CSV — ja renomeado — para a pasta
   `Consulta_Servico` no Google Drive, sobrescrevendo o arquivo do mes.
2. **compilador** (Python/pandas) — roda **so depois** do baixar (`needs: baixar`).
   Le os CSVs dessa mesma pasta do Drive e compila pra planilha
   (`BD_ConsultaServ`), subindo tambem o `BANCO.csv` consolidado. Codigo em
   `compilador/`.

Versao headless e autonoma da Skill `baixar-consulta-servico-gpm`.

## O que mudou em relacao a Skill

| Skill (Claude Desktop) | Aqui (Actions) |
|---|---|
| Login **manual** no Chrome | Login automatico via `GPM_USER`/`GPM_PASS` (secrets) |
| **Claude in Chrome** clicando na UI | **Playwright headless** replicando os cliques |
| Grava no **Drive Desktop** (G:) via ferramenta Write | Sobe pela **Drive API** (service account) na mesma pasta |
| Dança de BOM + Write verbatim + md5 | Sobe os **bytes crus** extraidos do `.zip` (BOM intacto) |

A pasta-destino e a mesma da Skill: `1lZ8AvXtviCYH9tXE-oG-GwNaUiGjZN0Z`.

## Estrutura

```
config.json              contratos, pasta-destino, timezone, minLinhasDados, seletores
src/baixar.js            orquestrador (login -> por contrato: retry + guard + enviar)
src/gpm.js               Playwright: login, navegacao, filtro, export, extracao do zip
src/drive.js             upload/update na pasta do Drive (service account) + auto-dedup
src/util.js              funcoes puras (mes/ano, contagem de linhas) — testadas
lib/google.js            auth da service account + withRetry (do precificacao-actions)
test/                    testes unitarios (node --test) das funcoes puras + extrairCsv
tools/                   helpers (inspect, diag-contrato, check-drive) — calibracao/ops
compilador/              etapa 2: script Python que compila os CSVs do Drive pro Sheet
  compilador_consulta_servicos_GPM_CE.py
  requirements.txt
  README.md
.github/workflows/baixar.yml   pipeline: baixar -> compilador + cron diario + botao manual + notificacao
```

Guards: `minLinhasDados` no config impede sobrescrever o arquivo do mes com um CSV
so-cabecalho (glitch do GPM). `drive.js` faz auto-dedup (manda copias extras do
mesmo nome pra lixeira). `baixar.js` retenta cada contrato 2x (GPM e flaky).

## Secrets (GitHub -> Settings -> Secrets and variables -> Actions)

- `GOOGLE_CREDENTIALS` — JSON **inteiro** da key da service account. **Usado pelas
  duas etapas** (baixar e compilador). A SA precisa de acesso **Editor** na pasta
  `Consulta_Servico` do Shared Drive **e** na planilha `BD_ConsultaServ`.
- `GPM_USER` — usuario do GPM CE.
- `GPM_PASS` — senha do GPM CE.

> O compilador tambem aceita `GOOGLE_CREDENTIALS_B64` (base64) como fallback, mas
> nao e necessario: com `GOOGLE_CREDENTIALS` (JSON cru) as duas etapas funcionam.

## Rodar local (teste / calibracao)

```powershell
cd C:\Users\sirte\Documents\GitHub\consulta-servico-gpm-actions
npm install
npx playwright install chromium
$env:GOOGLE_CREDENTIALS = Get-Content credentials.json -Raw
$env:GPM_USER = "..."; $env:GPM_PASS = "..."

# 1) Calibrar seletores contra o site real (abre o browser; logue na janela):
$env:HEADED = "1"; npm run inspect    # gera ./debug/*.html e *.png

# 2) Teste sem mexer no Drive (baixa e extrai, NAO envia):
$env:DRY_RUN = "1"; $env:HEADED = "1"; npm start

# 3) Rodada real:
Remove-Item Env:DRY_RUN, Env:HEADED -ErrorAction SilentlyContinue; npm start
```

`credentials.json` esta no `.gitignore` — nunca commitar.

## ⚠️ Calibracao dos seletores (faca isto antes do primeiro run de verdade)

Os seletores em `src/gpm.js` sao a melhor aproximacao a partir da descricao da
Skill — **nao foram validados contra o DOM real** (o site e interno/autenticado).
Antes de confiar no run automatico:

1. Rode `HEADED=1 npm run inspect`, logue na janela e deixe abrir a Consulta
   Servicos. Veja `./debug/*.html`.
2. Pra cada passo (login, campo de data, dropdown de contrato, botao Pesquisar,
   icone Excel/CSV), confirme o seletor real e cole em `config.json -> selectors`
   (eles tem prioridade sobre a heuristica do codigo).
3. Rode com `DRY_RUN=1 HEADED=1 npm start` e ajuste ate baixar o `.zip` certo.

Em qualquer falha, o codigo grava screenshot + HTML em `./debug` (e o workflow
sobe esses artefatos). Use-os pra ajustar os seletores.

## Contratos

Edite `config.json -> contratos`. Cada item: `{ "dropdown": "<texto exato no GPM>",
"prefixo": "<prefixo do arquivo>" }`. Nome final: `PREFIXO - mm.aaaa.csv` (mes/ano
vigentes). O loop processa um contrato por vez.

## Cron

`0 * * * *` = de hora em hora (minuto 0 UTC). Ajuste em `.github/workflows/baixar.yml`.
A Data Servico Inicio e sempre o dia 1 do mes, entao o arquivo do mes vai sendo
sobrescrito a cada hora ate virar o mes. O `compilador` roda logo apos o `baixar`
no mesmo run (nao tem cron proprio).

## Limitacoes conhecidas

- **Captcha / 2FA no login**: se o GPM exigir, o login automatico nao passa.
  Verifique com o time do GPM se da pra ter um usuario de servico sem 2FA.
- **DOM muda**: se a UI do GPM mudar, recalibre os seletores (passo acima).
