"""
Utilitarios compartilhados pelos scripts do pipeline Plan_Principal (CE).

Centraliza:
- configuracao de logging;
- carregamento das credenciais da service account (secret cru, base64 ou arquivo);
- execucao de requisicoes do google-api-python-client com retry/backoff.

Credenciais (ordem de prioridade):
1) GOOGLE_CREDENTIALS      -> JSON cru da service account (secret ja usado no _CE);
2) GOOGLE_CREDENTIALS_B64  -> mesmo JSON, porem em base64;
3) arquivo local           -> service_account.json (ou GOOGLE_APPLICATION_CREDENTIALS).

O retry baseado em callable usado pelo atualizar_plan_principal (gspread) fica
naquele script; aqui ficam os helpers do googleapiclient (Drive/Sheets v4).
"""

import os
import sys
import json
import time
import base64
import socket
import logging

from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError


def setup_logging(level=logging.INFO) -> None:
    """Configura logging para stdout. Idempotente (nao duplica handlers)."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.WARNING)


setup_logging()

LOCAL_CREDENTIALS_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")

API_TIMEOUT_SECONDS = int(os.getenv("API_TIMEOUT_SECONDS", "300"))
API_MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", "5"))

socket.setdefaulttimeout(API_TIMEOUT_SECONDS)


def load_service_account_credentials(scopes) -> Credentials:
    """
    Carrega credenciais da service account de tres formas (nessa ordem):
    1) GOOGLE_CREDENTIALS (JSON cru), 2) GOOGLE_CREDENTIALS_B64 (base64),
    3) arquivo local service_account.json / GOOGLE_APPLICATION_CREDENTIALS.
    """
    raw = os.getenv("GOOGLE_CREDENTIALS", "").strip()
    if raw:
        logging.info("Usando credenciais da variavel GOOGLE_CREDENTIALS (JSON cru)...")
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=scopes)

    credentials_b64 = os.getenv("GOOGLE_CREDENTIALS_B64", "").strip()
    if credentials_b64:
        logging.info("Usando credenciais da variavel GOOGLE_CREDENTIALS_B64 (base64)...")
        info = json.loads(base64.b64decode(credentials_b64).decode("utf-8"))
        return Credentials.from_service_account_info(info, scopes=scopes)

    if os.path.exists(LOCAL_CREDENTIALS_FILE):
        logging.info(f"Usando credenciais do arquivo local: {LOCAL_CREDENTIALS_FILE}")
        return Credentials.from_service_account_file(LOCAL_CREDENTIALS_FILE, scopes=scopes)

    raise FileNotFoundError(
        "Credenciais nao encontradas. Defina GOOGLE_CREDENTIALS (JSON cru), "
        f"GOOGLE_CREDENTIALS_B64 (base64) ou adicione {LOCAL_CREDENTIALS_FILE}."
    )


def execute_with_retries(request, description: str = "requisicao"):
    """
    Executa uma requisicao do google-api-python-client com retry e backoff
    exponencial para erros transitorios (429/5xx) e timeouts de rede.
    """
    last_error = None
    for attempt in range(API_MAX_RETRIES):
        try:
            return request.execute(num_retries=2)
        except HttpError as e:
            last_error = e
            status = getattr(e.resp, "status", None)
            retryable = status in {429, 500, 502, 503, 504}
            if not retryable or attempt == API_MAX_RETRIES - 1:
                raise
            wait_seconds = 2 ** attempt
            logging.warning(f"Falha HTTP em {description} (status={status}). Tentando novamente em {wait_seconds}s...")
            time.sleep(wait_seconds)
        except (TimeoutError, socket.timeout, OSError) as e:
            last_error = e
            if attempt == API_MAX_RETRIES - 1:
                raise
            wait_seconds = 2 ** attempt
            logging.warning(f"Timeout/erro de rede em {description}. Tentando novamente em {wait_seconds}s...")
            time.sleep(wait_seconds)
    raise last_error
