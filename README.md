# Pipeline Planejamento CE (GitHub Actions)

Pipeline headless e autonomo do planejamento **CE**: puxa os dados de servico do
GPM, compila o banco, reaplica/congela as `Plan_Principal` das unidades e gera o
`COMPILADO.csv`. Quatro etapas sequenciais no mesmo workflow
([`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml)).

| # | Job | Stack | O que faz |
|---|-----|-------|-----------|
| 1 | `baixar` | Node + Playwright | Baixa o relatorio **Consulta Servicos** do GPM CE (`https://sirtecce.gpm.srv.br/`) e sobe o CSV — ja renomeado `PREFIXO - mm.aaaa.csv` — na pasta `Consulta_Servico` do Drive, sobrescrevendo o arquivo do mes. |
| 2 | `compilador` | Python + pandas | Le os CSVs dessa pasta, normaliza, gera o `BANCO.csv` consolidado e atualiza a aba `BD_ConsultaServ`. |
| 3 | `plan_principal` | Python + gspread | Para cada unidade da aba `BD_Planilhas`: reaplica as formulas da `Plan_Principal`, espera o calculo, **congela** (cola valores) e roda o `preencherChuva` (BF/BG via Open-Meteo). |
| 4 | `compilar_planilha` | Python | Consolida os CSVs do `BACKUP_Planejamento` + `Plan_Principal!B5:CH` das unidades no `COMPILADO.csv` (subpasta `COMPLETO`). |

Cada etapa depende da anterior (`needs`). O job `notify` abre/comenta uma issue
(label `pipeline-CE-falha`) dizendo **qual** etapa quebrou.

Detalhes de cada modulo: [`compilador/README.md`](compilador/README.md) e
[`plan_principal/README.md`](plan_principal/README.md).

> **Historico do nome**: o repo nasceu como `consulta-servico-gpm-actions_CE`,
> versao headless da Skill `baixar-consulta-servico-gpm` — so a Etapa 1. Com as
> Etapas 2-4 o escopo virou o pipeline inteiro, dai o nome atual.

## ⚠️ Estado atual (05/08/2026)

**Etapa 1 (`baixar`) falha desde 01/08 — por busca vazia, nao por DOM quebrado.**

Erro: `Pesquisa: toolbar de export (#form_principal) nao apareceu em 45s apos Pesquisar.`

Diagnostico confirmado pelo artefato `debug` do run: o GPM respondeu a pesquisa
com o alerta `alerta_aviso = 'Nenhum registro encontrado'` e **sem** o
`#form_principal` — o GPM nao renderiza a toolbar de export quando nao ha linhas.
Login, data, contrato e Pesquisar funcionaram; o contrato `SOC.SOT` e que nao tem
servico lancado de 01/08 pra ca.

O historico dos runs bate com o vira-mes: **27–31/07 = 46 runs, 100% success;
01/08 em diante = 100% failure**. Nada foi alterado no codigo nesse intervalo.

- **Nao e caso de recalibrar seletores.** A Etapa 1 volta sozinha quando o
  primeiro servico de agosto for lancado no GPM.
- Se ficar vazio por muito tempo, confira se o contrato
  (`config.json -> contratos`) nao venceu. Teste que separa os dois casos: rodar
  local com data de um mes que tinha dado — se exportar, era so mes vazio.
- Consequencia hoje: `compilador` fica `skipped` e o CSV do mes nao e atualizado.
- **Etapas 3 e 4 seguem passando** — elas rodam mesmo com o `compilador` skipped
  (por design do `somente_plan`), so nao rodam se ele **falhar**. O
  `COMPILADO.csv` e as `Plan_Principal` continuam atualizando com o banco que ja
  estava no Drive.

> Limitacao conhecida: hoje "mes sem servico" e "export quebrado" dao o mesmo erro
> e a mesma issue de falha a cada 2h. Ver [Limitacoes](#limitacoes-conhecidas).

## O que mudou em relacao a Skill original

| Skill (Claude Desktop) | Aqui (Actions) |
|---|---|
| Login **manual** no Chrome | Login automatico via `GPM_USER`/`GPM_PASS` (secrets) |
| **Claude in Chrome** clicando na UI | **Playwright headless** replicando os cliques |
| Grava no **Drive Desktop** (G:) via Write | Sobe pela **Drive API** (service account) na mesma pasta |
| Dança de BOM + Write verbatim + md5 | Sobe os **bytes crus** extraidos do `.zip` (BOM intacto) |
| Compilacao/planejamento **manual** (Apps Script + IMPORTRANGE) | Etapas 2-4 no mesmo run, em Python |

A pasta-destino da Etapa 1 e a mesma da Skill: `1lZ8AvXtviCYH9tXE-oG-GwNaUiGjZN0Z`.

## Estrutura

```
config.json                      Etapa 1: contratos, pasta-destino, timezone, minLinhasDados, seletores
src/baixar.js                    orquestrador (login -> por contrato: retry + guard + enviar)
src/gpm.js                       Playwright: login, navegacao, filtro de data/contrato, export, extracao do zip
src/drive.js                     upload/update na pasta do Drive (service account) + auto-dedup
src/util.js                      funcoes puras (mes/ano vigente, contagem de linhas) — testadas
lib/google.js                    auth da service account + withRetry (herdado do precificacao-actions)
test/                            testes unitarios (node --test): funcoes puras + extrairCsv
tools/                           helpers de calibracao/ops: inspect, diag-contrato, debug-export, check-drive
compilador/                      Etapa 2 (Python): CSVs do Drive -> BANCO.csv + aba BD_ConsultaServ
plan_principal/                  Etapas 3 e 4 (Python): Plan_Principal + chuva, e COMPILADO.csv
  atualizar_plan_principal_CE.py       Etapa 3
  compilador_planilha_principal_CE.py  Etapa 4
  common.py                            credenciais + retry compartilhados
.github/workflows/pipeline.yml   as 4 etapas + cron + botao manual + notificacao de falha
```

Guards da Etapa 1: `minLinhasDados` no `config.json` impede sobrescrever o arquivo
do mes com um CSV so-cabecalho (glitch do GPM); `drive.js` faz auto-dedup (manda
copias extras do mesmo nome pra lixeira); `baixar.js` retenta cada contrato 2x
(o GPM e flaky).

## Secrets (Settings -> Secrets and variables -> Actions)

| Secret | Usado por | Observacao |
|---|---|---|
| `GOOGLE_CREDENTIALS` | **todas as 4 etapas** | JSON **inteiro** da key da service account. |
| `GPM_USER` | Etapa 1 | Usuario do GPM CE. |
| `GPM_PASS` | Etapa 1 | Senha do GPM CE. |

A service account precisa de **Editor** em: pasta `Consulta_Servico` do Shared
Drive, pasta `BACKUP_Planejamento` (+ subpasta `COMPLETO`), planilha
`BD_ConsultaServ`, planilha de controle (`BD_Planilhas` / `BD_Config_CE`) e todas
as planilhas-unidade.

> As etapas Python tambem aceitam `GOOGLE_CREDENTIALS_B64` (base64) como fallback,
> mas nao e necessario: com o `GOOGLE_CREDENTIALS` (JSON cru) as quatro funcionam.

## Rodar / operar

### Botao manual (Actions -> Run workflow)

Input `somente_plan`: marque pra **pular as Etapas 1 e 2** e rodar so a 3 e a 4
(util quando o GPM esta fora do ar mas voce quer reprocessar o planejamento).

### Etapa 1 local (teste / calibracao)

```powershell
cd C:\Users\sirte\Documents\GitHub\consulta-servico-gpm-actions_CE
npm install
npx playwright install chromium
$env:GOOGLE_CREDENTIALS = Get-Content credentials.json -Raw
$env:GPM_USER = "..."; $env:GPM_PASS = "..."

# 1) Calibrar seletores contra o site real (abre o browser):
$env:HEADED = "1"; npm run inspect     # gera ./debug/*.html e *.png

# 2) Teste sem mexer no Drive (baixa e extrai, NAO envia):
$env:DRY_RUN = "1"; $env:HEADED = "1"; npm start

# 3) Rodada real:
Remove-Item Env:DRY_RUN, Env:HEADED -ErrorAction SilentlyContinue; npm start
```

Outros scripts: `npm test` (unitarios), `npm run diag` (diagnostico do dropdown de
contrato), `npm run check` (checagem read-only de duplicatas na pasta do Drive).

`credentials.json` esta no `.gitignore` — nunca commitar.

### Etapas 2-4 local

```powershell
pip install -r compilador/requirements.txt
python compilador/compilador_consulta_servicos_GPM_CE.py    # Etapa 2

pip install -r plan_principal/requirements.txt
python plan_principal/atualizar_plan_principal_CE.py        # Etapa 3
python plan_principal/compilador_planilha_principal_CE.py   # Etapa 4
```

## Calibracao dos seletores

Os seletores da tela ConsultaServicos sao heuristica no `src/gpm.js`, com override
por `config.json -> selectors` (o override tem prioridade). Os de login estao
confirmados; os de dentro da tela (data, contrato, Pesquisar, export) estao `null`
e dependem da heuristica — que **funciona** (46 runs verdes em 27–31/07). So
recalibre se o dump mostrar DOM diferente, **nao** por causa de busca vazia.

1. Baixe os artefatos `debug` do run que falhou (ou rode `HEADED=1 npm run inspect`
   local e logue na janela). Veja `./debug/*.html` e `*.png`. Se o HTML tiver
   `alerta_aviso = 'Nenhum registro encontrado'`, o problema **nao** e seletor.
2. Pra cada passo (campo de data, dropdown de contrato, botao Pesquisar, icone
   Excel/CSV, toolbar `#form_principal`), confirme o seletor real e cole em
   `config.json -> selectors`.
3. Rode `DRY_RUN=1 HEADED=1 npm start` e ajuste ate baixar o `.zip` certo.

Em qualquer falha o codigo grava screenshot + HTML em `./debug`, e o workflow sobe
esses arquivos como artefato (`retention-days: 7`).

## Contratos

Edite `config.json -> contratos`. Cada item: `{ "dropdown": "<texto exato no GPM>",
"prefixo": "<prefixo do arquivo>" }`. Nome final: `PREFIXO - mm.aaaa.csv` (mes/ano
vigentes). O loop processa um contrato por vez, com retry individual.

## Cron

`0 */2 * * *` = de 2 em 2 horas (minuto 0 UTC), em `.github/workflows/pipeline.yml`.
A Data Servico Inicio e sempre o dia 1 do mes, entao o arquivo do mes vai sendo
sobrescrito a cada rodada ate virar o mes. `concurrency: planejamento-gpm-CE`
impede dois runs (cron + manual) escrevendo no Drive/Sheets ao mesmo tempo.

## Limitacoes conhecidas

- **Mes sem servico = falha**: se a busca nao retorna linhas, o GPM nao renderiza
  o `#form_principal` e o `pesquisar()` (`src/gpm.js`) trata isso como erro fatal
  — mesma mensagem e mesma issue que um export realmente quebrado, a cada 2h. E o
  que acontece todo inicio de mes, ate o primeiro servico ser lancado.
- **DOM do GPM muda**: quebra a Etapa 1 e exige recalibrar os seletores.
- **Captcha / 2FA no login**: se o GPM passar a exigir, o login automatico nao
  passa. Ideal seria um usuario de servico sem 2FA.
- **`gspread` nao tem `flush()`**: a Etapa 3 escreve as formulas, espera
  `CALC_WAIT_SECONDS` e le de volta pra congelar — nao ha como forcar o recalculo.
- **Etapas 3 e 4 rodam mesmo com a Etapa 1/2 skipped**: por design (permite o
  `somente_plan`), mas significa que elas podem consolidar dados velhos sem
  reclamar. O sinal de que o banco esta parado e a issue de falha da Etapa 1.
