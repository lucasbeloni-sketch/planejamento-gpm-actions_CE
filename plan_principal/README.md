# Plan_Principal (CE) — Etapas 3 e 4

Parte final do pipeline `planejamento-gpm-actions_CE`. Rodam depois do `baixar`
(Etapa 1) + `compilador` (Etapa 2), no mesmo workflow
([`../.github/workflows/pipeline.yml`](../.github/workflows/pipeline.yml)).

> Numeracao: estas etapas eram chamadas de "2 e 3" quando o pipeline tinha 3
> jobs. Hoje o pipeline tem 4 e elas sao a **3** e a **4** — os nomes dos scripts
> nao mudaram.

| Etapa | Script | O que faz |
|-------|--------|-----------|
| 3 | `atualizar_plan_principal_CE.py` | Para cada unidade em `BD_Planilhas`, reaplica as formulas da `Plan_Principal`, aguarda o calculo, **congela** (cola valores) e carimba `G3`. Depois roda o `preencherChuva` (BF/BG via Open-Meteo). Traducao fiel do Apps Script `atualizarPlan_Principal` + `preencherChuva`. |
| 4 | `compilador_planilha_principal_CE.py` | Consolida os CSVs de `FOLDER_ID` + `Plan_Principal!B5:CH` das unidades, normaliza, remove duplicados, ordena por data e grava `COMPILADO.csv` em `DEST_FOLDER_ID`. Carimba timestamp em `BD_Config_CE!C2`. |

`common.py` centraliza credenciais + retry (compartilhado pelas duas etapas).

## Credenciais

Usa o mesmo secret do repo: **`GOOGLE_CREDENTIALS`** (JSON cru da service account).
Aceita tambem `GOOGLE_CREDENTIALS_B64` (base64) e arquivo local `service_account.json`.
A service account precisa de **Editor** nas planilhas-unidade, na planilha de
controle (`BD_Planilhas` / `BD_Config_CE`) e nas pastas do Drive.

## Fonte das unidades (1 lugar so)

Os IDs das planilhas-unidade saem da **coluna C da aba `BD_Planilhas`** da planilha
de controle (`LISTA_PLANILHAS_SPREADSHEET_ID`). As duas etapas leem dali; nao ha
lista fixa de IDs. Layout esperado: `B`=Unidade (nome), `C`=ID, `D`=valor-BE (nome
que vai na coluna `BO` da Plan_Principal). Cabecalho e linhas sem ID valido sao ignorados.

## Configuracao (env vars — todas com default CE)

### Etapa 3 — `atualizar_plan_principal_CE.py`

| Variavel | Default | Descricao |
|----------|---------|-----------|
| `LISTA_PLANILHAS_SPREADSHEET_ID` | `1CuHVv…` | Planilha de controle (aba `BD_Planilhas`). |
| `ABA_LISTA_PLANILHAS` | `BD_Planilhas` | Aba com a lista de unidades. |
| `TIMEZONE` | `America/Fortaleza` | Fuso do carimbo `G3`. |
| `CALC_WAIT_SECONDS` | `15` | Espera para o Sheets recalcular antes de congelar. |
| `VERIFICAR_PROPAGACAO` | `true` | Espera o IMPORTRANGE do `BD_Serv_GPM` propagar (auto-pula se A1 nao for IMPORTRANGE). |
| `PROPAGACAO_TIMEOUT_SECONDS` | `300` | Timeout da propagacao por unidade. |
| `RODAR_CHUVA` | `true` | Roda o `preencherChuva` (BF/BG) depois do atualizar. |

### Etapa 4 — `compilador_planilha_principal_CE.py`

| Variavel | Default | Descricao |
|----------|---------|-----------|
| `FOLDER_ID` | `1fdfj…` | Pasta `BACKUP_Planejamento`: CSVs de planejamento por unidade dos meses fechados (ex.: `JZN.csv`). **Nao** e a pasta do BANCO/Consulta_Servico. Leitura nao-recursiva (a subpasta `COMPLETO` nao e lida). |
| `DEST_FOLDER_ID` | `11_6Wb…` | Subpasta `COMPLETO` (destino do `COMPILADO.csv`). |
| `DEST_CSV_NAME` | `COMPILADO.csv` | Nome do arquivo consolidado. |
| `SOURCE_SHEET_NAME` | `Plan_Principal` | Aba lida nas unidades. |
| `SOURCE_RANGE_A1` | `B5:CH` | Intervalo lido. |
| `SOURCE_SPREADSHEET_IDS` | (auto do `BD_Planilhas`) | Lista fixa por virgula; se setada, tem prioridade. |
| `TIMESTAMP_SPREADSHEET_ID` | `1-_lTK…` | Planilha do timestamp. |
| `TIMESTAMP_SHEET_NAME` / `TIMESTAMP_CELL` | `BD_Config_CE` / `C2` | Destino do timestamp. |
| `FORMAT_DATE_COLS` | `A` | Colunas (da saida) tratadas como data (extrai `dd/mm/aaaa`). |
| `FORMAT_NUMBER_COLS` | `AU,AV,AX,AZ` | Colunas convertidas para decimal-virgula (calibrado em 05/08/2026). |

> As colunas de formatacao sao 0-based relativas a coluna **A da saida** (= coluna
> **B** da origem, pois o range comeca em B).
>
> **Calibracao do `FORMAT_NUMBER_COLS` (05/08/2026):** medido no `COMPILADO.csv`
> real (215 linhas x 85 colunas). As unicas colunas que saiam com ponto decimal
> eram `AX` = "REALIZADO PLANEJADO (R$)" e `AZ` = "PRODUCAO GPM (R$)"
> (`6453.3` -> `6453,3`). `AU` = "PLANEJADO R$" e `AV` = "META R$" entram por
> prevencao: hoje sao inteiros (passam intactos), mas se vierem com centavos ja
> saem com virgula. As colunas de `%` (`AW`, `AY`, `BA`, `BO`) chegam como texto
> `"78%"` e nao sao tocadas — um dia que aparecer `78.5%` vai precisar de
> tratamento proprio no `format_number_value`. As de tempo vem `HH:MM:SS` do
> Sheets (sem transformacao). Nao copiar as letras do pipeline BA
> (`AK,AL,AN,AP,BP`): o layout do `Plan_Principal` CE e outro.

## Rodar local

```powershell
cd plan_principal
pip install -r requirements.txt
$env:GOOGLE_CREDENTIALS = Get-Content ..\credentials.json -Raw
python atualizar_plan_principal_CE.py      # Etapa 3
python compilador_planilha_principal_CE.py # Etapa 4
```

## Notas

- **gspread nao tem `flush()`**: as etapas escrevem as formulas, esperam
  `CALC_WAIT_SECONDS` e leem de volta para congelar.
- **Ordem chuva x atualizar**: o `preencherChuva` roda **depois** do atualizar
  (igual ao processo manual). Como o `AZ` (que le `BF`) ja foi congelado, ele usa
  o `BF` da rodada anterior; a chuva atualiza `BF/BG` para a proxima.
- **Formulas fieis ao Apps Script CE**: chave de lookup na `Carteira_Planejador`
  e a coluna `K`; abas de apoio `BD_Config`, `BD_Metas`, `BD_Serv_GPM`. A coluna
  `BO` recebe o valor-BE da unidade (nao mais o `"JUAZEIRO DO NORTE"` fixo).
