"""
Etapa 2 (CE) - Atualiza a aba Plan_Principal de cada unidade e preenche a chuva.

Traducao fiel do Apps Script `atualizarPlan_Principal` + `preencherChuva` da
operacao CE, rodando headless via gspread. Para cada planilha listada na aba
`BD_Planilhas` da planilha de controle:

  1. (opcional) espera a propagacao do IMPORTRANGE no BD_Serv_GPM;
  2. reaplica as formulas da Plan_Principal, aguarda o calculo e congela os
     valores (colar valores), carimbando G3;
  3. roda o preencherChuva (BP %CHUVA / BQ previsao via Open-Meteo).

Diferencas para o Apps Script:
- o nome da unidade em BO (hardcoded "JUAZEIRO DO NORTE" no manual) vem da
  coluna D (valor-BE) da BD_Planilhas, entao serve qualquer unidade;
- gspread nao tem SpreadsheetApp.flush(): trocado por espera (CALC_WAIT_SECONDS)
  + leitura de volta ao congelar.

NOTA sobre a ordem chuva-vs-atualizar: igual ao processo manual, o preencherChuva
roda DEPOIS do atualizar (o atualizar limpa BO:BQ e a chuva repreenche BP/BQ).
ATENCAO: BF/BG NAO sao colunas de chuva - o AZ compara BF com K. Escrever a
chuva em BF (bug corrigido) zerava o AZ e apagava o BP/BQ da rodada.
"""

import os
import re
import json
import time
import math
import logging
import traceback
import datetime as dt
from zoneinfo import ZoneInfo

import gspread
import requests
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import rowcol_to_a1, a1_to_rowcol

from common import load_service_account_credentials


# =========================================================
# CONFIG
# =========================================================
LISTA_PLANILHAS_SPREADSHEET_ID = os.getenv(
    "LISTA_PLANILHAS_SPREADSHEET_ID",
    "1CuHVvASsIbWQwIByKnJoe1dviqv5I_6BeCiw1yYNxLU",
)
ABA_LISTA_PLANILHAS = os.getenv("ABA_LISTA_PLANILHAS", "BD_Planilhas")

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "America/Fortaleza"))

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "5000"))
CALC_WAIT_SECONDS = int(os.getenv("CALC_WAIT_SECONDS", "15"))

# Espera de propagacao do IMPORTRANGE (BD_Serv_GPM <- master). Adaptativa:
# se A1 do BD_Serv_GPM nao tiver IMPORTRANGE, e pulada automaticamente.
VERIFICAR_PROPAGACAO = os.getenv("VERIFICAR_PROPAGACAO", "true").strip().lower() in {"1", "true", "yes"}
PROPAGACAO_MODO = os.getenv("PROPAGACAO_MODO", "linhas").strip().lower()
ABA_SERV_GPM = os.getenv("ABA_SERV_GPM", "BD_Serv_GPM")
PROPAGACAO_TIMEOUT_SECONDS = int(os.getenv("PROPAGACAO_TIMEOUT_SECONDS", "300"))
PROPAGACAO_POLL_INTERVAL = int(os.getenv("PROPAGACAO_POLL_INTERVAL", "10"))

# preencherChuva
RODAR_CHUVA = os.getenv("RODAR_CHUVA", "true").strip().lower() in {"1", "true", "yes"}

ABA_PLAN = "Plan_Principal"
ABA_CART = "Carteira_Planejador"

PLAN_LINHA_DADOS = 6
PLAN_COL_DATA = 2    # B
PLAN_COL_TRAB = 11   # K (TRABALHO - chave da Carteira_Planejador!E)
PLAN_COL_CHUVA = 68  # BP (% CHUVA)
PLAN_COL_PREV = 69   # BQ (PREV. DESCRICAO)

CART_LINHA_DADOS = 6
CART_COL_TRAB = 5    # E
CART_COL_COORD1 = 52  # AZ
CART_COL_COORD2 = 53  # BA

UTM_ZONA = 24
UTM_SUL = True
LIMITE_PREVISAO_DIAS = 15
CHUVA_MM = 1.0
CLIMO_ANOS = 12
CLIMO_JANELA_DIAS = 3
CHUVA_TIMEZONE = os.getenv("CHUVA_TIMEZONE", "America/Fortaleza")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# =========================================================
# AUTENTICACAO
# =========================================================
def get_gspread_client() -> gspread.Client:
    return gspread.authorize(load_service_account_credentials(SCOPES))


# =========================================================
# HELPERS GENERICOS
# =========================================================
def executar_com_retry(func, tentativas: int = 5, espera_inicial: float = 2.0):
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            return func()
        except APIError as erro:
            ultimo_erro = erro
            if tentativa == tentativas:
                raise
            espera = espera_inicial * tentativa
            logging.warning(f"Erro Google API. Tentativa {tentativa}/{tentativas}. Nova tentativa em {espera:.0f}s.")
            time.sleep(espera)
    raise ultimo_erro


def abrir_aba(spreadsheet: gspread.Spreadsheet, nome_aba: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(nome_aba)
    except WorksheetNotFound as erro:
        raise RuntimeError(f"A aba '{nome_aba}' nao foi encontrada na planilha '{spreadsheet.title}'.") from erro


def is_blank(valor) -> bool:
    return valor is None or valor == ""


def as_text(valor) -> str:
    return "" if valor is None else str(valor)


def num_para_letra(n: int) -> str:
    """1 -> A, 27 -> AA."""
    letras = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(ord("A") + resto) + letras
    return letras


def pad_row(row: list, n_cols: int) -> list:
    row = list(row or [])
    if len(row) < n_cols:
        row += [""] * (n_cols - len(row))
    return row[:n_cols]


def dimensoes_range(range_a1: str):
    """Retorna (linha_inicial, coluna_inicial, qtd_linhas, qtd_colunas)."""
    if ":" not in range_a1:
        row, col = a1_to_rowcol(range_a1)
        return row, col, 1, 1
    inicio, fim = range_a1.split(":")
    row_ini, col_ini = a1_to_rowcol(inicio)
    row_fim, col_fim = a1_to_rowcol(fim)
    return row_ini, col_ini, row_fim - row_ini + 1, col_fim - col_ini + 1


def ler_range(worksheet, range_a1, value_render="UNFORMATTED_VALUE", date_render="SERIAL_NUMBER"):
    return executar_com_retry(
        lambda: worksheet.get(range_a1, value_render_option=value_render, date_time_render_option=date_render)
    )


def limpar_intervalos(worksheet, ranges) -> None:
    if ranges:
        executar_com_retry(lambda: worksheet.batch_clear(list(ranges)))


def escrever_celula(worksheet, a1, valor, raw: bool = True) -> None:
    opt = "RAW" if raw else "USER_ENTERED"
    executar_com_retry(lambda: worksheet.update(range_name=a1, values=[[valor]], value_input_option=opt))


def escrever_formulas_coluna(worksheet, start_row, col_1based, formulas, chunk_size=CHUNK_SIZE):
    """Escreve uma coluna de formulas (lista de strings) em blocos."""
    if not formulas:
        return
    for offset in range(0, len(formulas), chunk_size):
        bloco = formulas[offset:offset + chunk_size]
        row_ini = start_row + offset
        row_fim = row_ini + len(bloco) - 1
        range_a1 = f"{rowcol_to_a1(row_ini, col_1based)}:{rowcol_to_a1(row_fim, col_1based)}"
        valores = [[f] for f in bloco]
        executar_com_retry(
            lambda ra=range_a1, v=valores: worksheet.update(range_name=ra, values=v, value_input_option="USER_ENTERED")
        )


def escrever_matriz(worksheet, start_row, start_col, values, raw=True, chunk_size=CHUNK_SIZE):
    if not values:
        return
    opt = "RAW" if raw else "USER_ENTERED"
    total_cols = max(len(row) for row in values)
    for offset in range(0, len(values), chunk_size):
        bloco = values[offset:offset + chunk_size]
        row_ini = start_row + offset
        row_fim = row_ini + len(bloco) - 1
        col_fim = start_col + total_cols - 1
        range_a1 = f"{rowcol_to_a1(row_ini, start_col)}:{rowcol_to_a1(row_fim, col_fim)}"
        bloco_pad = [pad_row(row, total_cols) for row in bloco]
        executar_com_retry(
            lambda ra=range_a1, v=bloco_pad: worksheet.update(range_name=ra, values=v, value_input_option=opt)
        )


def congelar_intervalo(worksheet, range_a1: str) -> None:
    """Le os resultados calculados das formulas e cola como valores (RAW)."""
    row_ini, col_ini, qtd_linhas, qtd_colunas = dimensoes_range(range_a1)
    valores = ler_range(worksheet, range_a1)
    matriz = [pad_row(valores[i] if i < len(valores) else [], qtd_colunas) for i in range(qtd_linhas)]
    escrever_matriz(worksheet, row_ini, col_ini, matriz, raw=True)


def ultima_linha_preenchida(worksheet, range_a1="A:CD") -> int:
    """Equivalente ao getLastRow() do Apps Script no intervalo principal."""
    valores = ler_range(worksheet, range_a1)
    for idx in range(len(valores) - 1, -1, -1):
        if any(not is_blank(c) for c in valores[idx]):
            return idx + 1
    return 0


def remover_filtro_basico(spreadsheet, worksheet) -> None:
    try:
        req = {"requests": [{"clearBasicFilter": {"sheetId": worksheet.id}}]}
        executar_com_retry(lambda: spreadsheet.batch_update(req))
    except Exception as erro:
        logging.warning(f"Nao foi possivel remover filtro (ou nao havia): {erro}")


def escape_formula_text(texto: str) -> str:
    return as_text(texto).replace('"', '""')


# =========================================================
# BD_PLANILHAS (lista de unidades)
# =========================================================
def buscar_planilhas(ss_lista: gspread.Spreadsheet):
    """
    Le a aba BD_Planilhas: B=Unidade(nome), C=ID, D=valor-BE (nome que vai no BO).
    Ignora o cabecalho e linhas cujo ID nao parece um ID de planilha valido.
    Remove IDs vazios e duplicados.
    """
    aba = abrir_aba(ss_lista, ABA_LISTA_PLANILHAS)
    valores = ler_range(aba, "B1:D", value_render="FORMATTED_VALUE", date_render="FORMATTED_STRING")

    planilhas, vistos = [], set()
    for row in valores:
        nome = as_text(row[0]).strip() if len(row) > 0 else ""
        id_planilha = as_text(row[1]).strip() if len(row) > 1 else ""
        valor_be = as_text(row[2]).strip() if len(row) > 2 else ""

        # Pula cabecalho ("ID") e qualquer coisa que nao seja ID de planilha do Sheets.
        if not re.fullmatch(r"[A-Za-z0-9_-]{30,}", id_planilha):
            continue
        if id_planilha in vistos:
            continue

        planilhas.append({"nome": nome or "Sem nome informado", "id": id_planilha, "valor_be": valor_be})
        vistos.add(id_planilha)

    return planilhas


# =========================================================
# FORMULAS - PLAN_PRINCIPAL (CE)
# =========================================================
def _xlookup_cart(row: int, col_retorno: str) -> str:
    return f'=XLOOKUP(K{row};Carteira_Planejador!$E:$E;Carteira_Planejador!${col_retorno}:${col_retorno};"")'


def formula_av(row: int) -> str:
    """Preco: soma AB..AU aplicando fator (I-/R-/normal) por BD_Config W/X/Y linhas 5..24."""
    partes = []
    for i, col_idx in enumerate(range(28, 48)):  # AB..AU
        c = num_para_letra(col_idx)
        cfg = 5 + i  # linha da BD_Config
        partes.append(
            f'IFERROR(IF(LEFT({c}{row};2)="I-";VALUE(MID({c}{row};3;99))*BD_Config!$W${cfg};'
            f'IF(LEFT({c}{row};2)="R-";VALUE(MID({c}{row};3;99))*BD_Config!$X${cfg};'
            f'VALUE({c}{row})*BD_Config!$Y${cfg}));0)'
        )
    soma = "+".join(partes)
    return f'=IF(B{row}="";"";IF(CA{row}<>"";CA{row};({soma})))'


def formula_aw(row: int) -> str:
    a = row - 1
    return (
        f'=IF(B{row}="";"";IF(COUNTIFS($B$1:B{a};B{row};$H$1:H{a};H{row})=0;'
        f'XLOOKUP(H{row}&B{row};BD_Metas!$A:$A;BD_Metas!$D:$D;0);0))'
    )


def formula_ay(row: int) -> str:
    return (
        f'=IF(B{row}="";"";IF(AV{row}=0;0;'
        f'SUMIFS(BD_Serv_GPM!$G:$G;BD_Serv_GPM!$H:$H;K{row};BD_Serv_GPM!$D:$D;H{row};BD_Serv_GPM!$F:$F;B{row})'
        f'+SUMIFS(BD_Serv_GPM!$G:$G;BD_Serv_GPM!$J:$J;K{row};BD_Serv_GPM!$D:$D;H{row};BD_Serv_GPM!$F:$F;B{row})))'
    )


def formula_ba(row: int) -> str:
    return f'=IF(B{row}="";"";SUMIFS(BD_Serv_GPM!$G:$G;BD_Serv_GPM!$D:$D;H{row};BD_Serv_GPM!$F:$F;B{row}))'


def formula_bc(row: int) -> str:
    return (
        f'=IF(B{row}="";"";IF(XLOOKUP(K{row}&B{row}*1&H{row};BD_Serv_GPM!$I:$I;BD_Serv_GPM!$E:$E;"-")="-";'
        f'XLOOKUP(K{row}&B{row}*1&H{row};BD_Serv_GPM!$K:$K;BD_Serv_GPM!$E:$E;"-");'
        f'XLOOKUP(K{row}&B{row}*1&H{row};BD_Serv_GPM!$I:$I;BD_Serv_GPM!$E:$E;"-")))'
    )


def formula_bo(row: int, valor_be: str) -> str:
    return f'=IF(B{row}<>"";"{escape_formula_text(valor_be)}";"")'


def formula_ax(row: int) -> str:
    return f'=IF(B{row}="";"";IFERROR(AV{row}/AW{row};0))'


def formula_az(row: int) -> str:
    return (
        f'=IF(B{row}="";"";IF(AY{row}<=0;"";IF(AND(BF{row}<>"";BF{row}<>K{row});0;'
        f'IF(AV{row}>0;IFERROR(AY{row}/AV{row};0);1))))'
    )


def formula_bb(row: int) -> str:
    return f'=IF(B{row}="";"";IFERROR(BA{row}/AW{row};0))'


def formula_cd(row: int) -> str:
    return f'=XLOOKUP($K{row};Carteira_Planejador!$E:$E;Carteira_Planejador!$AT:$AT;"")'


# =========================================================
# PROPAGACAO DO IMPORTRANGE (adaptativa)
# =========================================================
_IMPORTRANGE_RE = re.compile(
    r'IMPORTRANGE\s*\(\s*["\']([^"\']+)["\']\s*[;,]\s*["\']([^"\']+)["\']', re.IGNORECASE
)


def _sig(worksheet, range_a1):
    valores = ler_range(worksheet, range_a1)
    linhas = [[as_text(c) for c in (r or [])] for r in valores]
    while linhas and all(c == "" for c in linhas[-1]):
        linhas.pop()
    return len(linhas)


def aguardar_propagacao_importrange(client, ss_dest, nome_planilha) -> None:
    if not VERIFICAR_PROPAGACAO:
        return
    try:
        aba_serv = ss_dest.worksheet(ABA_SERV_GPM)
    except WorksheetNotFound:
        logging.warning(f"Aba '{ABA_SERV_GPM}' nao encontrada em '{nome_planilha}'. Pulando propagacao.")
        return

    formula = executar_com_retry(lambda: aba_serv.acell("A1", value_render_option="FORMULA").value)
    match = _IMPORTRANGE_RE.search(formula or "")
    if not match:
        logging.info(f"'{ABA_SERV_GPM}' de '{nome_planilha}' sem IMPORTRANGE em A1. Pulando propagacao.")
        return

    origem_id, origem_range = match.group(1), match.group(2)
    origem_tab, celulas = (origem_range.split("!", 1) if "!" in origem_range else (ABA_SERV_GPM, origem_range))
    logging.info(f"Verificando propagacao IMPORTRANGE: origem {origem_id} / {origem_range}")

    ss_origem = executar_com_retry(lambda: client.open_by_key(origem_id))
    origem_aba = abrir_aba(ss_origem, origem_tab)

    inicio = time.monotonic()
    tentativa = 0
    while True:
        tentativa += 1
        n_origem = _sig(origem_aba, celulas)
        n_unidade = _sig(aba_serv, celulas)
        if n_origem == n_unidade:
            logging.info(f"Propagacao confirmada (tentativa {tentativa}): {n_unidade} linhas.")
            return
        if time.monotonic() - inicio >= PROPAGACAO_TIMEOUT_SECONDS:
            raise RuntimeError(
                f"Timeout ({PROPAGACAO_TIMEOUT_SECONDS}s) aguardando IMPORTRANGE em '{nome_planilha}'. "
                f"origem={n_origem} unidade={n_unidade}"
            )
        logging.info(f"Ainda propagando (tent. {tentativa}): origem={n_origem} unidade={n_unidade}. Nova checagem em {PROPAGACAO_POLL_INTERVAL}s.")
        time.sleep(PROPAGACAO_POLL_INTERVAL)


# =========================================================
# ATUALIZAR PLAN_PRINCIPAL (equivalente ao atualizarPlan_Principal)
# =========================================================
def atualizar_plan_principal(ss_dest: gspread.Spreadsheet, valor_be: str) -> None:
    aba = abrir_aba(ss_dest, ABA_PLAN)
    logging.info("Atualizando aba Plan_Principal...")

    remover_filtro_basico(ss_dest, aba)
    escrever_celula(aba, "G3", "Em Atualizacao")

    last = ultima_linha_preenchida(aba, "A:CD")
    logging.info(f"Ultima linha preenchida da Plan_Principal: {last}")
    if last < PLAN_LINHA_DADOS:
        _carimbar_g3(aba)
        logging.info("Nenhuma linha para atualizar a partir da linha 6.")
        return

    # Limpezas iniciais (mesmos ranges do Apps Script)
    limpar_intervalos(aba, ["E6:E", "I6:I", "M6:P", "T6:U", "X6:Y", "AV6:BC", "BO6:BQ", "CB6:CD"])

    linhas = list(range(PLAN_LINHA_DADOS, last + 1))

    # -------- Formulas iniciais --------
    logging.info("Aplicando formulas iniciais (E, M:P, T:U, X:Y, AV, AW, AY, BA, BC, BO)...")
    escrever_formulas_coluna(aba, 6, 5, [_xlookup_cart(r, "AG") for r in linhas])   # E
    escrever_formulas_coluna(aba, 6, 13, [_xlookup_cart(r, "BL") for r in linhas])  # M
    escrever_formulas_coluna(aba, 6, 14, [_xlookup_cart(r, "L") for r in linhas])   # N
    escrever_formulas_coluna(aba, 6, 15, [_xlookup_cart(r, "J") for r in linhas])   # O
    escrever_formulas_coluna(aba, 6, 16, [_xlookup_cart(r, "AU") for r in linhas])  # P
    escrever_formulas_coluna(aba, 6, 20, [_xlookup_cart(r, "I") for r in linhas])   # T
    escrever_formulas_coluna(aba, 6, 21, [_xlookup_cart(r, "BO") for r in linhas])  # U
    escrever_formulas_coluna(aba, 6, 24, [_xlookup_cart(r, "BJ") for r in linhas])  # X
    escrever_formulas_coluna(aba, 6, 25, [_xlookup_cart(r, "BK") for r in linhas])  # Y
    escrever_formulas_coluna(aba, 6, 48, [formula_av(r) for r in linhas])           # AV
    escrever_formulas_coluna(aba, 6, 49, [formula_aw(r) for r in linhas])           # AW
    escrever_formulas_coluna(aba, 6, 51, [formula_ay(r) for r in linhas])           # AY
    escrever_formulas_coluna(aba, 6, 53, [formula_ba(r) for r in linhas])           # BA
    escrever_formulas_coluna(aba, 6, 55, [formula_bc(r) for r in linhas])           # BC
    escrever_formulas_coluna(aba, 6, 67, [formula_bo(r, valor_be) for r in linhas]) # BO

    logging.info(f"Aguardando calculo inicial por {CALC_WAIT_SECONDS}s...")
    time.sleep(CALC_WAIT_SECONDS)

    # -------- Formulas derivadas --------
    logging.info("Aplicando formulas derivadas (AX, AZ, BB, CD)...")
    escrever_formulas_coluna(aba, 6, 50, [formula_ax(r) for r in linhas])  # AX
    escrever_formulas_coluna(aba, 6, 52, [formula_az(r) for r in linhas])  # AZ
    escrever_formulas_coluna(aba, 6, 54, [formula_bb(r) for r in linhas])  # BB
    escrever_formulas_coluna(aba, 6, 82, [formula_cd(r) for r in linhas])  # CD

    logging.info(f"Aguardando calculo das derivadas por {CALC_WAIT_SECONDS}s...")
    time.sleep(CALC_WAIT_SECONDS)

    # -------- Congelar valores (colar valores) --------
    logging.info("Congelando valores...")
    for rng in [f"E6:E{last}", f"I6:I{last}", f"M6:P{last}", f"X6:Y{last}",
                f"BO6:BO{last}", f"AV6:BC{last}", f"T6:U{last}", f"CB6:CD{last}"]:
        congelar_intervalo(aba, rng)

    _carimbar_g3(aba)
    logging.info("Plan_Principal atualizada com sucesso.")


def _carimbar_g3(worksheet) -> None:
    data_hora = dt.datetime.now(TIMEZONE).strftime("%d/%m/%Y %H:%M:%S")
    escrever_celula(worksheet, "G3", data_hora, raw=False)
    try:
        executar_com_retry(
            lambda: worksheet.format("G3", {"numberFormat": {"type": "DATE_TIME", "pattern": "dd/MM/yyyy HH:mm:ss"}})
        )
    except Exception as erro:
        logging.warning(f"Nao foi possivel formatar G3: {erro}")


# =========================================================
# PREENCHER CHUVA (BP/BQ) - Open-Meteo
# =========================================================
def normaliza_trab(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        return str(int(round(v)))
    return re.sub(r"\.0$", "", str(v).strip())


def iso_data(d: dt.date) -> str:
    return d.strftime("%Y-%m-%d")


def dia_do_ano(d: dt.date) -> int:
    return d.timetuple().tm_yday


def _parse_data_plan(valor) -> dt.date | None:
    """Extrai a data (dd/mm/aaaa) da coluna B, tolerando sufixos como ' - terca-feira'."""
    if valor is None or valor == "":
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(valor))
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def utm_para_latlon(easting, northing, zona=UTM_ZONA, sul=UTM_SUL):
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = easting - 500000.0
    y = (northing - 10000000.0) if sul else northing
    lon0 = math.radians(zona * 6 - 183)
    M = y / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s1, c1, t1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    N1 = a / math.sqrt(1 - e2 * s1 ** 2)
    T1 = t1 ** 2
    C1 = ep2 * c1 ** 2
    R1 = a * (1 - e2) / (1 - e2 * s1 ** 2) ** 1.5
    D = x / (N1 * k0)
    lat = phi1 - (N1 * t1 / R1) * (D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = lon0 + (D - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120) / c1
    return {"lat": math.degrees(lat), "lon": math.degrees(lon)}


def resolve_coord(v1, v2):
    if not isinstance(v1, (int, float)) or not isinstance(v2, (int, float)):
        return None
    if abs(v1) <= 1.5 and abs(v2) <= 1.5:
        return None
    if abs(v1) > 180 or abs(v2) > 180:
        if not (v1 > 100000 and v2 > 1000000):
            return None
        return utm_para_latlon(v1, v2)
    if abs(v1) > 90:
        return None
    return {"lat": v1, "lon": v2}


def buscar_json(url: str):
    for tent in range(1, 4):
        resp = requests.get(url, timeout=60)
        code = resp.status_code
        if code == 429 or code >= 500:
            time.sleep(1.5 * tent)
            continue
        try:
            j = resp.json()
        except Exception:
            raise RuntimeError(f"resposta invalida (HTTP {code})")
        if code != 200 or j.get("error"):
            raise RuntimeError(j.get("reason") or f"HTTP {code}")
        return j
    raise RuntimeError("API indisponivel (limite de requisicoes?)")


def descricao_wmo(code) -> str:
    m = {
        0: "Ceu limpo", 1: "Predominio de sol", 2: "Parcialmente nublado", 3: "Nublado",
        45: "Nevoa", 48: "Nevoa com geada", 51: "Garoa fraca", 53: "Garoa moderada", 55: "Garoa forte",
        61: "Chuva fraca", 63: "Chuva moderada", 65: "Chuva forte", 66: "Chuva congelante fraca",
        67: "Chuva congelante forte", 71: "Neve fraca", 73: "Neve moderada", 75: "Neve forte",
        80: "Pancadas fracas", 81: "Pancadas moderadas", 82: "Pancadas fortes",
        95: "Trovoada", 96: "Trovoada com granizo", 99: "Trovoada com granizo forte",
    }
    return m.get(code, f"Condicao {code}")


def faixa_clima(p) -> str:
    if p < 10:
        return "Tempo seco - chuva improvavel"
    if p < 30:
        return "Predominio de sol - chuva pouco provavel"
    if p < 60:
        return "Possibilidade de pancadas isoladas"
    return "Chuva provavel"


def previsao(lat, lon, data_alvo, cache):
    chave = f"f_{lat:.3f}_{lon:.3f}"
    j = cache.get(chave)
    if not j:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&daily=precipitation_probability_max,weather_code&forecast_days=16"
               f"&timezone={requests.utils.quote(CHUVA_TIMEZONE)}")
        j = buscar_json(url)
        cache[chave] = j
    tempos = j["daily"]["time"]
    alvo = iso_data(data_alvo)
    idx = tempos.index(alvo) if alvo in tempos else -1
    prob = j["daily"]["precipitation_probability_max"][idx] if idx >= 0 else None
    if idx < 0 or prob is None:
        return climatologia(lat, lon, data_alvo, cache)
    return {"prob": prob, "desc": descricao_wmo(j["daily"]["weather_code"][idx]) + " (previsao)"}


def climatologia(lat, lon, data_alvo, cache):
    chave = f"c_{lat:.3f}_{lon:.3f}"
    j = cache.get(chave)
    if not j:
        ano_fim = dt.date.today().year - 1
        ano_ini = ano_fim - CLIMO_ANOS + 1
        url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
               f"&start_date={ano_ini}-01-01&end_date={ano_fim}-12-31"
               f"&daily=precipitation_sum&timezone={requests.utils.quote(CHUVA_TIMEZONE)}")
        j = buscar_json(url)
        cache[chave] = j
    tempos = j["daily"]["time"]
    chuvas = j["daily"]["precipitation_sum"]
    alvo_doy = dia_do_ano(data_alvo)
    total = com_chuva = 0
    for k in range(len(tempos)):
        d = dt.date.fromisoformat(tempos[k])
        dist = abs(dia_do_ano(d) - alvo_doy)
        dist = min(dist, 365 - dist)
        if dist <= CLIMO_JANELA_DIAS:
            mm = chuvas[k]
            if mm is not None:
                total += 1
                if mm >= CHUVA_MM:
                    com_chuva += 1
    if total == 0:
        return {"prob": "", "desc": "sem historico"}
    prob = round(100 * com_chuva / total)
    return {"prob": prob, "desc": f"{faixa_clima(prob)} (climatologia, {CLIMO_ANOS} anos)"}


def _ler_coordenadas(cart):
    ncols = max(CART_COL_TRAB, CART_COL_COORD1, CART_COL_COORD2)
    fim = num_para_letra(ncols)
    valores = ler_range(cart, f"A{CART_LINHA_DADOS}:{fim}")
    mapa = {}
    for row in valores:
        trab = normaliza_trab(row[CART_COL_TRAB - 1] if len(row) >= CART_COL_TRAB else "")
        if not trab:
            continue
        v1 = row[CART_COL_COORD1 - 1] if len(row) >= CART_COL_COORD1 else None
        v2 = row[CART_COL_COORD2 - 1] if len(row) >= CART_COL_COORD2 else None
        ll = resolve_coord(v1, v2)
        if ll:
            mapa[trab] = ll
    return mapa


def preencher_chuva(ss_dest: gspread.Spreadsheet) -> None:
    """Preenche BP (% CHUVA) e BQ (PREV. DESCRICAO) da Plan_Principal (Open-Meteo)."""
    plan = abrir_aba(ss_dest, ABA_PLAN)
    cart = abrir_aba(ss_dest, ABA_CART)

    coords = _ler_coordenadas(cart)
    last = ultima_linha_preenchida(plan, "A:CD")
    if last < PLAN_LINHA_DADOS:
        return

    n = last - PLAN_LINHA_DADOS + 1
    fim = num_para_letra(PLAN_COL_PREV)
    # B (data) e K (trabalho) precisam de valor legivel -> FORMATTED
    dados = ler_range(plan, f"A{PLAN_LINHA_DADOS}:{fim}{last}",
                      value_render="FORMATTED_VALUE", date_render="FORMATTED_STRING")

    cache = {}
    hoje = dt.datetime.now(ZoneInfo(CHUVA_TIMEZONE)).date()
    saida_chuva, saida_prev = [], []

    for i in range(n):
        row = dados[i] if i < len(dados) else []
        data = _parse_data_plan(row[PLAN_COL_DATA - 1] if len(row) >= PLAN_COL_DATA else "")
        trab = normaliza_trab(row[PLAN_COL_TRAB - 1] if len(row) >= PLAN_COL_TRAB else "")

        if not data or not trab:
            saida_chuva.append([""]); saida_prev.append([""]); continue
        c = coords.get(trab)
        if not c:
            saida_chuva.append([""]); saida_prev.append(["sem coordenada na carteira"]); continue

        dias = (data - hoje).days
        try:
            if 0 <= dias <= LIMITE_PREVISAO_DIAS:
                res = previsao(c["lat"], c["lon"], data, cache)
            else:
                res = climatologia(c["lat"], c["lon"], data, cache)
        except Exception as e:
            res = {"prob": "", "desc": f"erro: {e}"}
        prob = res["prob"]
        saida_chuva.append([""] if prob == "" or prob is None else [prob / 100])
        saida_prev.append([res["desc"]])

    executar_com_retry(
        lambda: plan.update(range_name=f"BP{PLAN_LINHA_DADOS}:BP{last}", values=saida_chuva, value_input_option="USER_ENTERED")
    )
    executar_com_retry(
        lambda: plan.update(range_name=f"BQ{PLAN_LINHA_DADOS}:BQ{last}", values=saida_prev, value_input_option="USER_ENTERED")
    )
    try:
        executar_com_retry(lambda: plan.format(f"BP{PLAN_LINHA_DADOS}:BP{last}", {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}))
    except Exception as erro:
        logging.warning(f"Nao foi possivel formatar BP: {erro}")
    logging.info("Probabilidade de chuva atualizada (BP/BQ).")


# =========================================================
# EXECUCAO POR UNIDADE
# =========================================================
def executar_para_planilha(client, item, indice, total) -> None:
    nome, sid, valor_be = item["nome"], item["id"], item["valor_be"]
    logging.info("=" * 80)
    logging.info(f"Unidade {indice}/{total}: {nome} | {sid} | BO='{valor_be}'")
    logging.info("=" * 80)

    ss_dest = executar_com_retry(lambda: client.open_by_key(sid))

    aguardar_propagacao_importrange(client, ss_dest, nome)
    atualizar_plan_principal(ss_dest, valor_be)

    if RODAR_CHUVA:
        try:
            preencher_chuva(ss_dest)
        except Exception as e:
            # Chuva e best-effort: nao invalida um atualizar bem-sucedido.
            logging.warning(f"preencherChuva falhou em '{nome}': {e}")


# =========================================================
# MAIN
# =========================================================
def main() -> None:
    inicio = dt.datetime.now(TIMEZONE)
    logging.info(f"Inicio Etapa 2 (Plan_Principal CE): {inicio.strftime('%d/%m/%Y %H:%M:%S')}")

    client = get_gspread_client()
    ss_lista = executar_com_retry(lambda: client.open_by_key(LISTA_PLANILHAS_SPREADSHEET_ID))
    planilhas = buscar_planilhas(ss_lista)

    if not planilhas:
        raise RuntimeError(
            f"Nenhum ID valido em {ABA_LISTA_PLANILHAS} da planilha {LISTA_PLANILHAS_SPREADSHEET_ID}."
        )
    logging.info(f"Unidades encontradas: {len(planilhas)}")

    sucessos, erros = [], []
    for indice, item in enumerate(planilhas, start=1):
        try:
            executar_para_planilha(client, item, indice, len(planilhas))
            sucessos.append(item)
        except Exception as erro:
            logging.error(f"Falha na unidade {item['nome']} ({item['id']}): {erro}")
            logging.error(traceback.format_exc())
            erros.append({**item, "erro": str(erro)})

    fim = dt.datetime.now(TIMEZONE)
    logging.info("=" * 80)
    logging.info(f"RESUMO ETAPA 2 | inicio {inicio:%d/%m/%Y %H:%M:%S} | fim {fim:%d/%m/%Y %H:%M:%S} "
                 f"| duracao {(fim - inicio).total_seconds():.1f}s")
    logging.info(f"Sucesso: {len(sucessos)} | Erro: {len(erros)}")
    for it in erros:
        logging.info(f"- ERRO {it['nome']} | {it['id']} | {it['erro']}")

    if erros:
        raise RuntimeError(f"Etapa 2 finalizada com erro em {len(erros)} unidade(s).")
    logging.info("Etapa 2 concluida com sucesso.")


if __name__ == "__main__":
    main()
