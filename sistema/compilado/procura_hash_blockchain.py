import hashlib
import os
import time
import subprocess
from pathlib import Path
import re

PASTA_DE_VIDEOS = "../trechos_nao_conformes"
EXTENSOES_VIDEO = ('.mp4', '.avi', '.mov', '.mkv')

HRE_EXISTS_JS = os.path.join("../besu/smart_contracts/scripts/public", "hre_found.js")

BASE_DIR = Path(__file__).resolve().parent
ARQUIVO_ENDERECO_PADRAO = BASE_DIR / "contract_address.txt"
PADRAO_ENDERECO = re.compile(r"^0x[0-9a-fA-F]{40}$")

# >>> escolha aqui o vídeo a consultar; se deixar vazio, verifica todos da pasta
NOME_DO_VIDEO = ""
# NOME_DO_VIDEO = ""

def buscar_arquivo_endereco():
    """
    Procura por qualquer .txt no diretório do script contendo APENAS um endereço Ethereum válido.
    Retorna (Path, endereço) se encontrar; caso contrário, (None, "").
    """
    for txt in BASE_DIR.glob("*.txt"):
        try:
            conteudo = txt.read_text(encoding="utf-8").strip()
            if PADRAO_ENDERECO.match(conteudo):
                print(f"Endereco encontrado no arquivo: {txt.name}")
                print(f"  {conteudo}")
                return txt, conteudo
        except Exception as e:
            print(f"Nao foi possivel ler '{txt.name}': {e}")
    return None, ""

def calcular_hash_video(caminho_do_video: str) -> str | None:
    sha256_hash = hashlib.sha256()
    try:
        with open(caminho_do_video, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return "0x" + sha256_hash.hexdigest()
    except IOError as e:
        print(f"Erro ao ler o arquivo {os.path.basename(caminho_do_video)}: {e}")
        return None

def check_hash_onchain(contract_address: str, file_hash: str) -> bool:
    """
    Verifica on-chain via Node se file_hash já está registrado no contrato.
    Mantém a convenção do seu hre_found.js:
      node hre_found.js <HASH> <CONTRACT_ADDRESS>
    Aceita três formatos de saída:
      - 'true' / 'false'
      - '0x...' (bytes32). Se igual ao file_hash, considera encontrado.
      - vazio -> não encontrado
    """
    try:
        result = subprocess.run(
            ['node', HRE_EXISTS_JS, file_hash, contract_address],  # hash primeiro, depois contrato
            capture_output=True, text=True, check=True
        )
        out = result.stdout.strip()
        low = out.lower()

        # Caso 1: booleano explícito
        if low in ("true", "false"):
            return low == "true"

        # Caso 2: retorna o próprio hash quando encontra
        if re.fullmatch(r"0x[0-9a-fA-F]{64}", out):
            return out.lower() == file_hash.lower()

        # Caso 3: vazio => não achou
        if out == "":
            return False

        # Qualquer outra coisa: trate como não encontrado, mas não quebre
        # (pode logar se quiser)
        # print(f"Saida inesperada do hre_found.js: '{out}'")
        return False

    except FileNotFoundError:
        print("hre_found.js nao encontrado; pulando verificacao on-chain.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"Erro ao executar hre_found.js: {e}; stderr: {e.stderr}")
        return False


if __name__ == "__main__":
    inicio = time.time()

    # Endereco do contrato via .txt
    _, contract_number = buscar_arquivo_endereco()
    if contract_number == "":
        print("Nenhum endereco valido encontrado em .txt. Encerrando.")
        exit(1)
    else:
        print(f"Usando contrato existente: {contract_number}")

    # Seleciona arquivos
    try:
        arquivos_na_pasta = os.listdir(PASTA_DE_VIDEOS)
    except FileNotFoundError:
        print(f"A pasta '{PASTA_DE_VIDEOS}' nao foi encontrada.")
        exit(1)

    if NOME_DO_VIDEO:
        candidatos = [NOME_DO_VIDEO]
    else:
        candidatos = [f for f in arquivos_na_pasta if f.lower().endswith(EXTENSOES_VIDEO)]

    if not candidatos:
        print("Nenhum arquivo de video encontrado para verificar.")
        exit(0)

    # Verifica cada arquivo solicitado (1 ou todos)
    for nome_arquivo in candidatos:
        caminho_completo = os.path.join(PASTA_DE_VIDEOS, nome_arquivo)
        if not os.path.exists(caminho_completo):
            print(f"\nArquivo nao encontrado: '{caminho_completo}'")
            continue

        print(f"\nArquivo analisado: '{nome_arquivo}'")
        file_hash = calcular_hash_video(caminho_completo)
        if not file_hash:
            print("Falha ao calcular hash; pulando arquivo.")
            continue

        print(f"Hash calculado: {file_hash}")
        existe = check_hash_onchain(contract_number, file_hash)

        if existe:
            print("Resultado: VIDEO REGISTRADO NA BLOCKCHAIN")
        else:
            print("Resultado: VIDEO NAO REGISTRADO NA BLOCKCHAIN")

    dur = time.time() - inicio
    print(f"\nScript finalizado em {dur:.2f}s")

