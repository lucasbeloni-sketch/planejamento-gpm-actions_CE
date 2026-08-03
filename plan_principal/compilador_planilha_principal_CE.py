"""
Etapa 3 (CE) - Consolida CSVs do Drive + Plan_Principal das unidades no COMPILADO.csv.

Fluxo:
  1. Le todos os .csv de FOLDER_ID, mescla, normaliza numeros.
  2. Le Plan_Principal!B5:CH de cada planilha-unidade (a lista de IDs vem
     automaticamente da coluna C da aba BD_Planilhas da planilha de controle).
  3. Normaliza, remove duplicados, ordena pela coluna A (data) e grava
     COMPILADO.csv em DEST_FOLDER_ID (preservando a linha 1 se ja existir).
  4. Carimba o timestamp em BD_Config_CE!C2.

IDs/nomes tem defaults CE e podem ser sobrescritos por variaveis de ambiente.
"""

import os
import io
import csv
import sys
import re
import logging
from datetime import datetime
from typing import List, Dict, Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from common import load_service_account_credentials, execute_with_retries

# =========================
# CONFIGURACOES
# =========================
FOLDER_ID = os.getenv("FOLDER_ID", "16I_LgXXXt064zuyWY24_pOxZhy7WQcOf")
DEST_FOLDER_ID = os.getenv("DEST_FOLDER_ID", "11_6WbEe5m5EByGud9EPrim3scXujvLOq")
DEST_CSV_NAME = os.getenv("DEST_CSV_NAME", "COMPILADO.csv")

# Fonte dos IDs das unidades: coluna C da aba BD_Planilhas da planilha de controle.
# (mesma fonte da Etapa 2). Pode ser sobrescrito por SOURCE_SPREADSHEET_IDS (lista
# separada por virgula), que tem prioridade se definido.
LISTA_PLANILHAS_SPREADSHEET_ID = os.getenv(
    "LISTA_PLANILHAS_SPREADSHEET_ID",
    "1CuHVvASsIbWQwIByKnJoe1dviqv5I_6BeCiw1yYNxLU",
)
ABA_LISTA_PLANILHAS = os.getenv("ABA_LISTA_PLANILHAS", "BD_Planilhas")
SOURCE_SPREADSHEET_IDS_ENV = os.getenv("SOURCE_SPREADSHEET_IDS", "").strip()

SOURCE_SHEET_NAME = os.getenv("SOURCE_SHEET_NAME", "Plan_Principal")
SOURCE_RANGE_A1 = os.getenv("SOURCE_RANGE_A1", "B5:CH")

# Destino do timestamp de execucao
TIMESTAMP_SPREADSHEET_ID = os.getenv("TIMESTAMP_SPREADSHEET_ID", "1-_lTKT4wSDlJtTXkF1tLHstV9h-S3Yq_2cE8jOIC3kI")
TIMESTAMP_SHEET_NAME = os.getenv("TIMESTAMP_SHEET_NAME", "BD_Config_CE")
TIMESTAMP_CELL = os.getenv("TIMESTAMP_CELL", "C2")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# =========================
# CSV - AUMENTA LIMITE
# =========================
def configure_csv_field_limit():
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return csv.field_size_limit()
        except OverflowError:
            limit //= 10

configure_csv_field_limit()

# =========================
# UTILITARIOS
# =========================
def cell_has_value(cell: Any) -> bool:
    return cell is not None and str(cell).strip() != ""

def row_has_any_value(row: List[Any]) -> bool:
    return any(cell_has_value(cell) for cell in row)

def remove_fully_blank_rows(values: List[List[Any]]) -> List[List[Any]]:
    return [row for row in values if row_has_any_value(row)]

def filter_rows_where_first_column_has_value(values: List[List[Any]]) -> List[List[Any]]:
    return [row for row in values if cell_has_value(row[0] if row else "")]

def column_letter_to_number(letter: str) -> int:
    result = 0
    for char in letter.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result

def get_range_width(a1_range: str) -> int:
    match = re.match(r"([A-Z]+)\d*:([A-Z]+)", a1_range.upper())
    if not match:
        raise ValueError(f"Nao foi possivel calcular a largura do range: {a1_range}")
    return column_letter_to_number(match.group(2)) - column_letter_to_number(match.group(1)) + 1

def pad_rows_to_width(values: List[List[Any]], width: int) -> List[List[Any]]:
    return [(list(row) + [""] * (width - len(row)))[:width] for row in values]

# =========================
# INDICES DE FORMATACAO (0-based, relativos a coluna A da saida = coluna B da origem)
# Configuraveis por env var (letras separadas por virgula). Default: so a data (A).
# Ajuste FORMAT_NUMBER_COLS / FORMAT_DURATION_COLS conforme o COMPILADO CE precisar.
# =========================
def _parse_cols(env_name: str, default: str) -> List[int]:
    raw = os.getenv(env_name, default)
    return [column_letter_to_number(c.strip()) - 1 for c in raw.split(",") if c.strip()]

FORMAT_DATE_COLUMNS = _parse_cols("FORMAT_DATE_COLS", "A")
FORMAT_NUMBER_COLUMNS = _parse_cols("FORMAT_NUMBER_COLS", "")
FORMAT_DURATION_COLUMNS = _parse_cols("FORMAT_DURATION_COLS", "")

# =========================
# NORMALIZACAO NUMERICA
# =========================
def is_grouped_thousands(value: str, sep: str) -> bool:
    parts = value.split(sep)
    if len(parts) <= 1 or not all(part.isdigit() for part in parts):
        return False
    if not (1 <= len(parts[0]) <= 3):
        return False
    return all(len(part) == 3 for part in parts[1:])

def normalize_numeric_string(value: Any):
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    original = value
    s = value.strip().replace(" ", " ")
    if s == "":
        return ""
    if s.startswith("'"):
        s = s[1:].strip()
    is_percent = s.endswith("%")
    if is_percent:
        s = s[:-1].strip()
    s = s.replace("R$", "").replace("$", "").strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.startswith("-"):
        negative, s = True, s[1:].strip()
    s = s.replace(" ", "")
    if not re.fullmatch(r"[\d\.,]+", s):
        return original
    if re.fullmatch(r"\d+", s) and len(s) > 1 and s.startswith("0") and not is_percent:
        return original
    if "." in s and "," in s:
        last_dot, last_comma = s.rfind("."), s.rfind(",")
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if is_grouped_thousands(s, ","):
            s = s.replace(",", "")
        elif s.count(",") == 1:
            left, right = s.split(",")
            if right.isdigit() and 1 <= len(right) <= 6:
                s = left + "." + right
            else:
                return original
        else:
            return original
    elif "." in s:
        if is_grouped_thousands(s, "."):
            s = s.replace(".", "")
        elif s.count(".") == 1:
            left, right = s.split(".")
            if not (right.isdigit() and 1 <= len(right) <= 6):
                return original
        else:
            return original
    try:
        number = float(s) if "." in s else int(s)
        if negative:
            number = -number
        if is_percent:
            return f"{number}%"
        return number
    except ValueError:
        return original

# =========================
# AUTENTICACAO
# =========================
def get_services():
    creds = load_service_account_credentials(SCOPES)
    return build("drive", "v3", credentials=creds), build("sheets", "v4", credentials=creds)

# =========================
# FONTE DOS IDS (BD_Planilhas)
# =========================
def get_source_spreadsheet_ids(sheets_service) -> List[str]:
    """
    IDs das unidades. Se SOURCE_SPREADSHEET_IDS estiver setado, usa (lista por
    virgula). Senao, le a coluna C da aba BD_Planilhas da planilha de controle,
    validando o formato e removendo cabecalho/duplicados.
    """
    if SOURCE_SPREADSHEET_IDS_ENV:
        return [s.strip() for s in SOURCE_SPREADSHEET_IDS_ENV.split(",") if s.strip()]

    resp = execute_with_retries(
        sheets_service.spreadsheets().values().get(
            spreadsheetId=LISTA_PLANILHAS_SPREADSHEET_ID,
            range=f"{ABA_LISTA_PLANILHAS}!C1:C",
            valueRenderOption="FORMATTED_VALUE",
        ),
        description=f"leitura de {ABA_LISTA_PLANILHAS}!C (IDs das unidades)",
    )
    ids, vistos = [], set()
    for row in resp.get("values", []):
        sid = (row[0].strip() if row and row[0] else "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{30,}", sid) or sid in vistos:
            continue
        ids.append(sid)
        vistos.add(sid)
    return ids

# =========================
# GOOGLE DRIVE - LEITURA
# =========================
def list_csv_files_in_folder(drive_service, folder_id: str) -> List[Dict[str, str]]:
    files, page_token = [], None
    query = f"'{folder_id}' in parents and trashed = false and name contains '.csv'"
    while True:
        response = execute_with_retries(
            drive_service.files().list(
                q=query, fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token, pageSize=1000,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ),
            description="listagem de CSVs no Drive",
        )
        files.extend(f for f in response.get("files", []) if f["name"].lower().endswith(".csv"))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    files.sort(key=lambda x: x["name"].lower())
    return files

def download_csv_content(drive_service, file_id: str) -> str:
    request = drive_service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk(num_retries=2)
    raw = buffer.getvalue()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")

# =========================
# GOOGLE DRIVE - UPLOAD
# =========================
def find_existing_file_in_folder(drive_service, folder_id: str, filename: str):
    response = execute_with_retries(
        drive_service.files().list(
            q=f"'{folder_id}' in parents and trashed = false and name = '{filename}'",
            fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True,
        ),
        description=f"busca de arquivo existente '{filename}'",
    )
    files = response.get("files", [])
    return files[0]["id"] if files else None

def get_first_csv_row_raw(existing_content: str) -> str:
    reader = csv.reader(io.StringIO(existing_content, newline=""), delimiter=";")
    try:
        first_row = next(reader)
    except StopIteration:
        return ""
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", lineterminator="\n").writerow(first_row)
    return buf.getvalue().rstrip("\n")

def upload_csv_to_drive(drive_service, folder_id: str, filename: str, rows: List[List[Any]]):
    existing_id = find_existing_file_in_folder(drive_service, folder_id, filename)
    first_line = ""
    if existing_id:
        logging.info(f"Arquivo '{filename}' encontrado. Preservando linha 1...")
        first_line = get_first_csv_row_raw(download_csv_content(drive_service, existing_id))
    buffer = io.StringIO()
    if first_line:
        buffer.write(first_line + "\n")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    for row in rows:
        writer.writerow([str(cell) if cell is not None else "" for cell in row])
    csv_bytes = buffer.getvalue().encode("utf-8-sig")
    media = MediaIoBaseUpload(io.BytesIO(csv_bytes), mimetype="text/csv", resumable=True)
    if existing_id:
        logging.info(f"Substituindo conteudo de '{filename}' a partir da linha 2...")
        execute_with_retries(
            drive_service.files().update(fileId=existing_id, media_body=media, supportsAllDrives=True),
            description=f"atualizacao do arquivo '{filename}'",
        )
    else:
        logging.info(f"Criando novo arquivo '{filename}' na pasta {folder_id}...")
        execute_with_retries(
            drive_service.files().create(
                body={"name": filename, "parents": [folder_id], "mimeType": "text/csv"},
                media_body=media, fields="id", supportsAllDrives=True,
            ),
            description=f"criacao do arquivo '{filename}'",
        )
    logging.info(f"Arquivo '{filename}' salvo com sucesso.")

# =========================
# CSV / CONSOLIDACAO
# =========================
def detect_csv_dialect(csv_text: str):
    try:
        return csv.Sniffer().sniff(csv_text[:10000], delimiters=",;\t|")
    except Exception:
        class SimpleDialect(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return SimpleDialect

def parse_csv_text(csv_text: str) -> List[List[str]]:
    dialect = detect_csv_dialect(csv_text)
    reader = csv.reader(io.StringIO(csv_text, newline=""), dialect=dialect)
    return [[str(cell) for cell in row] for row in reader if row_has_any_value(row)]

def normalize_header(header: List[str]) -> List[str]:
    return [str(col).strip().lower() for col in header]

def merge_csvs(file_contents: List[str]) -> List[List[str]]:
    merged_rows: List[List[str]] = []
    first_header = None
    for content in file_contents:
        rows = parse_csv_text(content)
        if not rows:
            continue
        header = normalize_header(rows[0])
        data_rows = rows[1:] if len(rows) > 1 else []
        if first_header is None:
            first_header = header
            merged_rows.append(rows[0])
            merged_rows.extend(data_rows)
        elif header == first_header:
            merged_rows.extend(data_rows)
        else:
            merged_rows.extend(rows)
    if not merged_rows:
        return []
    max_cols = max(len(row) for row in merged_rows)
    return [row + [""] * (max_cols - len(row)) for row in merged_rows]

# =========================
# GOOGLE SHEETS - LEITURA
# =========================
def get_sheet_range_values(sheets_service, spreadsheet_id, sheet_name, range_a1):
    resp = execute_with_retries(
        sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"{sheet_name}!{range_a1}",
            valueRenderOption="FORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING",
            majorDimension="ROWS",
        ),
        description=f"leitura de {sheet_name}!{range_a1}",
    )
    return resp.get("values", [])

def collect_source_sheets_data(sheets_service, spreadsheet_ids, sheet_name, range_a1):
    all_rows = []
    expected_width = get_range_width(range_a1)
    for spreadsheet_id in spreadsheet_ids:
        try:
            logging.info(f"Lendo {sheet_name}!{range_a1} da planilha {spreadsheet_id}...")
            rows = get_sheet_range_values(sheets_service, spreadsheet_id, sheet_name, range_a1)
            if not rows:
                logging.info(f" - Nenhum dado encontrado em {spreadsheet_id}")
                continue
            rows = pad_rows_to_width(rows, expected_width)
            rows = remove_fully_blank_rows(rows)
            rows = filter_rows_where_first_column_has_value(rows)
            logging.info(f" - {len(rows)} linha(s) aproveitada(s)")
            all_rows.extend(rows)
        except Exception as e:
            logging.warning(f"Erro ao ler {spreadsheet_id}: {e}")
    return all_rows

# =========================
# GOOGLE SHEETS - ESCRITA DE TIMESTAMP
# =========================
def write_execution_timestamp(sheets_service):
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    target_range = f"{TIMESTAMP_SHEET_NAME}!{TIMESTAMP_CELL}"
    logging.info(f"Gravando timestamp '{timestamp}' em {target_range}...")
    execute_with_retries(
        sheets_service.spreadsheets().values().update(
            spreadsheetId=TIMESTAMP_SPREADSHEET_ID, range=target_range,
            valueInputOption="USER_ENTERED", body={"values": [[timestamp]]},
        ),
        description=f"gravacao do timestamp em {target_range}",
    )
    logging.info("Timestamp gravado com sucesso.")

# =========================
# NORMALIZACAO E FORMATACAO
# =========================
def normalize_rows(values, skip_first_row=False):
    result = []
    for i, row in enumerate(values):
        if skip_first_row and i == 0:
            result.append(row)
        else:
            result.append([normalize_numeric_string(cell) for cell in row])
    return result

def format_date_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = re.match(r"(\d{2}/\d{2}/\d{4})", value.strip())
    return match.group(1) if match else value

def format_number_value(value: Any) -> Any:
    if isinstance(value, float):
        return str(value).replace(".", ",")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+\.\d+", value.strip()):
        return value.strip().replace(".", ",")
    return value

def apply_column_formats(rows):
    result = []
    for row_idx, row in enumerate(rows):
        if row_idx == 0:
            result.append(row)
            continue
        new_row = list(row)
        for col_idx in FORMAT_DATE_COLUMNS:
            if col_idx < len(new_row):
                new_row[col_idx] = format_date_value(new_row[col_idx])
        for col_idx in FORMAT_NUMBER_COLUMNS:
            if col_idx < len(new_row):
                new_row[col_idx] = format_number_value(new_row[col_idx])
        # Colunas de duracao ficam como "HH:MM:SS" do Sheets (sem transformacao).
        result.append(new_row)
    return result

def remove_duplicate_rows(rows):
    seen, result = set(), []
    for row in rows:
        key = tuple(str(cell) for cell in row)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result

# =========================
# MAIN
# =========================
def main():
    drive_service, sheets_service = get_services()
    all_rows: List[List[Any]] = []

    # 1) CONSOLIDA CSVs
    logging.info("Listando arquivos CSV na pasta...")
    files = list_csv_files_in_folder(drive_service, FOLDER_ID)
    if files:
        logging.info(f"{len(files)} arquivo(s) CSV encontrado(s).")
        csv_contents = [download_csv_content(drive_service, f["id"]) for f in files]
        merged = merge_csvs(csv_contents)
        if merged:
            merged = remove_fully_blank_rows(merged)
            merged = normalize_rows(merged, skip_first_row=True)
            logging.info(f"Linhas dos CSVs apos limpeza: {len(merged)}")
            all_rows.extend(merged)
    else:
        logging.info("Nenhum arquivo CSV encontrado na pasta.")

    # 2) LE PLAN_PRINCIPAL!B5:CH DAS UNIDADES (IDs vindos do BD_Planilhas)
    source_ids = get_source_spreadsheet_ids(sheets_service)
    logging.info(f"Unidades a compilar: {len(source_ids)}")
    source_rows = collect_source_sheets_data(sheets_service, source_ids, SOURCE_SHEET_NAME, SOURCE_RANGE_A1)
    if source_rows:
        source_rows = normalize_rows(source_rows, skip_first_row=False)
        source_rows = remove_fully_blank_rows(source_rows)
        logging.info(f"Linhas das unidades apos limpeza: {len(source_rows)}")
        all_rows.extend(source_rows)
    else:
        logging.info("Nenhum dado encontrado nas planilhas de origem.")

    # 3) SALVA COMPILADO.csv
    if not all_rows:
        logging.info("Nenhum dado para salvar. Encerrando.")
        return
    all_rows = apply_column_formats(all_rows)
    before = len(all_rows)
    all_rows = remove_duplicate_rows(all_rows)
    logging.info(f"Linhas removidas por duplicidade: {before - len(all_rows)}")

    def sort_key(row):
        val = str(row[0]).strip() if row else ""
        try:
            return (0, datetime.strptime(val, "%d/%m/%Y"), "")
        except ValueError:
            return (1, datetime.min, val.lower())
    all_rows = sorted(all_rows, key=sort_key)

    logging.info(f"Total de linhas a salvar: {len(all_rows)}")
    upload_csv_to_drive(drive_service, DEST_FOLDER_ID, DEST_CSV_NAME, all_rows)

    # 4) TIMESTAMP
    try:
        write_execution_timestamp(sheets_service)
    except Exception as e:
        logging.warning(f"Falha ao gravar timestamp em {TIMESTAMP_SHEET_NAME}!{TIMESTAMP_CELL}: {e}")

    logging.info("Processo concluido com sucesso.")

if __name__ == "__main__":
    main()
