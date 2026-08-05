# Etapa 2 — Compilador Consulta Serviços GPM CE

Compila os CSVs de serviços da pasta `Consulta_Servico` do Google Drive (a que a
Etapa 1 alimenta), normaliza os dados (datas em pt-BR por arquivo, valores
numéricos), gera o `BANCO.csv` e atualiza a aba `BD_ConsultaServ`.

Roda como o job `compilador` do workflow do repo
([`../.github/workflows/pipeline.yml`](../.github/workflows/pipeline.yml)), logo
após o job `baixar` — não tem cron nem workflow próprio. Antes vivia num repo
separado (`compilador_consulta_servicos_GPM_CE`), hoje é só este diretório.

## O que faz

1. Lê todos os `.csv` da pasta de entrada do Drive.
2. Concatena, remove duplicados e seleciona as colunas relevantes por posição.
3. Infere o formato de data (DMY/MDY) por arquivo de origem e normaliza.
4. Ordena por data, grava `BANCO.csv` (separador `;`, decimal `,`, UTF-8 BOM).
5. Faz upload do `BANCO.csv` de volta ao Drive e atualiza o Sheet
   (limpa `BD_ConsultaServ!A3:G` e reescreve; carimba a hora em `B1`).

## Credenciais

Usa o **mesmo secret do repo**: `GOOGLE_CREDENTIALS` (JSON cru da service
account). Aceita `GOOGLE_CREDENTIALS_B64` (base64) como fallback. A SA precisa de
acesso às duas pastas do Drive e à planilha, com os escopos `drive` e
`spreadsheets`.

## IDs configurados (topo do script)

| Constante | Valor | Uso |
|-----------|-------|-----|
| `NEW_FOLDER_ID` | `1lZ8Av…` | pasta de entrada (`Consulta_Servico`, saída da Etapa 1) |
| `FOLDER_ID` | `16I_Lg…` | pasta de saída do `BANCO.csv` |
| `SPREADSHEET_ID` / `SHEET_NAME` | `1YtcYE…` / `BD_ConsultaServ` | planilha/aba de destino |
| `KEEP_COL_POS_1BASED` | `47, 6, 27, 50, 52, 68, 70` | posições das colunas mantidas (1-based) |

## Rodar local

```powershell
pip install -r requirements.txt
$env:GOOGLE_CREDENTIALS = Get-Content ..\credentials.json -Raw
python compilador_consulta_servicos_GPM_CE.py
```
