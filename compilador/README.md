# Compilador Consulta Serviços GPM CE

[![Consulta Serviços GPM CE](https://github.com/lucasbeloni-sketch/compilador_consulta_servicos_GPM_CE/actions/workflows/run_consulta_servicos_GPM_CE.yml/badge.svg)](https://github.com/lucasbeloni-sketch/compilador_consulta_servicos_GPM_CE/actions/workflows/run_consulta_servicos_GPM_CE.yml)

Compila CSVs de serviços a partir de uma pasta no Google Drive, normaliza os
dados (datas em pt-BR por arquivo, valores numéricos), gera `BANCO.csv` e
atualiza uma planilha do Google Sheets.

## O que faz

1. Lê todos os `.csv` de uma pasta do Drive.
2. Concatena, remove duplicados e seleciona as colunas relevantes por posição.
3. Infere o formato de data (DMY/MDY) por arquivo de origem e normaliza.
4. Ordena por data, grava `BANCO.csv` (separador `;`, decimal `,`, UTF-8 BOM).
5. Faz upload do `BANCO.csv` de volta ao Drive e atualiza o Sheet.

## Execução

Roda via GitHub Actions (`.github/workflows/run_consulta_servicos_GPM_CE.yml`):

- **Agendado:** a cada 3 horas (`cron: "0 */3 * * *"`, UTC → bate nos horários
  cheios de Brasília: 00, 03, 06, 09, 12, 15, 18, 21).
- **Manual:** botão *Run workflow* na aba Actions.

Um único run por vez (`concurrency`) para não gravar no Sheet/Drive em paralelo.

## Configuração

Requer o secret de repositório **`GOOGLE_CREDENTIALS_B64`**: o JSON da service
account do Google Cloud, codificado em base64 (ASCII).

```bash
gh secret set GOOGLE_CREDENTIALS_B64 \
  --repo <owner>/<repo> \
  --body "$(base64 -w0 service_account.json)"
```

A service account precisa de acesso à pasta do Drive e à planilha, com os
escopos `drive` e `spreadsheets`.

## IDs configurados (topo do script)

| Constante | Uso |
|-----------|-----|
| `NEW_FOLDER_ID` | pasta de entrada (CSVs de origem) |
| `FOLDER_ID` | pasta de saída do `BANCO.csv` |
| `SPREADSHEET_ID` / `SHEET_NAME` | planilha/aba de destino |
| `KEEP_COL_POS_1BASED` | posições das colunas mantidas (1-based) |

## Rodar local

```bash
pip install -r requirements.txt
export GOOGLE_CREDENTIALS_B64="$(base64 -w0 service_account.json)"
python compilador_consulta_servicos_GPM_CE.py
```
