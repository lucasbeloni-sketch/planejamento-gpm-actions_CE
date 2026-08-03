# Plan_Principal (CE) — Etapas 2 e 3

Continuacao headless do pipeline `consulta-servico-gpm-actions_CE`. Rodam depois
do `baixar` + `compilador` (Etapa 1), no mesmo workflow (`.github/workflows/baixar.yml`).

| Etapa | Script | O que faz |
|-------|--------|-----------|
| 2 | `atualizar_plan_principal_CE.py` | Para cada unidade em `BD_Planilhas`, reaplica as formulas da `Plan_Principal`, aguarda o calculo, **congela** (cola valores) e carimba `G3`. Depois roda o `preencherChuva` (BF/BG via Open-Meteo). Traducao fiel do Apps Script `atualizarPlan_Principal` + `preencherChuva`. |
| 3 | `compilador_planilha_principal_CE.py` | Consolida os CSVs de `FOLDER_ID` + `Plan_Principal!B5:CH` das unidades, normaliza, remove duplicados, ordena por data e grava `COMPILADO.csv` em `DEST_FOLDER_ID`. Carimba timestamp em `BD_Config_CE!C2`. |

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

### Etapa 2 — `atualizar_plan_principal_CE.py`

| Variavel | Default | Descricao |
|----------|---------|-----------|
| `LISTA_PLANILHAS_SPREADSHEET_ID` | `1CuHVv…` | Planilha de controle (aba `BD_Planilhas`). |
| `ABA_LISTA_PLANILHAS` | `BD_Planilhas` | Aba com a lista de unidades. |
| `TIMEZONE` | `America/Fortaleza` | Fuso do carimbo `G3`. |
| `CALC_WAIT_SECONDS` | `15` | Espera para o Sheets recalcular antes de congelar. |
| `VERIFICAR_PROPAGACAO` | `true` | Espera o IMPORTRANGE do `BD_Serv_GPM` propagar (auto-pula se A1 nao for IMPORTRANGE). |
| `PROPAGACAO_TIMEOUT_SECONDS` | `300` | Timeout da propagacao por unidade. |
| `RODAR_CHUVA` | `true` | Roda o `preencherChuva` (BF/BG) depois do atualizar. |

### Etapa 3 — `compilador_planilha_principal_CE.py`

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
| `FORMAT_NUMBER_COLS` | `` (vazio) | Colunas convertidas para decimal-virgula. **Calibrar** se o COMPILADO precisar. |

> As colunas de formatacao sao 0-based relativas a coluna **A da saida** (= coluna
> **B** da origem, pois o range comeca em B). Por seguranca so a data (A) vem
> configurada; defina `FORMAT_NUMBER_COLS` se colunas especificas precisarem sair
> com virgula decimal.

## Rodar local

```powershell
cd plan_principal
pip install -r requirements.txt
$env:GOOGLE_CREDENTIALS = Get-Content ..\credentials.json -Raw
python atualizar_plan_principal_CE.py     # Etapa 2
python compilador_planilha_principal_CE.py # Etapa 3
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
