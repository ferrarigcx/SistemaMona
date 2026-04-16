import os
import re
import hashlib
import subprocess
import gradio as gr
from pathlib import Path

# ===== Configurações =====
PASTA_DE_VIDEOS = "../trechos_nao_conformes"
EXTENSOES_VIDEO = ('.mp4', '.avi', '.mov', '.mkv')
HRE_EXISTS_JS = os.path.join("../besu/smart_contracts/scripts/public", "hre_found.js")

BASE_DIR = Path(__file__).resolve().parent
ARQUIVO_ENDERECO_PADRAO = BASE_DIR / "contract_address.txt"
PADRAO_ENDERECO = re.compile(r"^0x[0-9a-fA-F]{40}$")
HEX64_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# ===== Utilitários =====
def buscar_arquivo_endereco():
    for txt in BASE_DIR.glob("*.txt"):
        try:
            conteudo = txt.read_text(encoding="utf-8").strip()
            if PADRAO_ENDERECO.match(conteudo):
                return conteudo
        except Exception:
            pass
    return ""

def calcular_hash_streaming(file_path: str) -> str:
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return "0x" + sha.hexdigest()

def check_hash_onchain(contract_address: str, file_hash: str) -> tuple[bool, str]:
    """
    Executa hre_found.js com a assinatura:
        node hre_found.js <HASH> <CONTRACT_ADDRESS>

    Interpreta as saídas:
        - 'true' / 'false'
        - '0x...' (bytes32) → se igual ao hash calculado, considera encontrado
        - vazio → não encontrado
    """
    try:
        res = subprocess.run(
            ['node', HRE_EXISTS_JS, file_hash, contract_address],
            capture_output=True, text=True, check=True
        )
        out = res.stdout.strip()
        low = out.lower()

        if low in ("true", "false"):
            return (low == "true", f"hre_found.js retornou: {out}")

        if HEX64_RE.fullmatch(out):
            achou = (out.lower() == file_hash.lower())
            return (achou, f"hre_found.js retornou o hash: {out}")

        if out == "":
            return (False, "hre_found.js retornou vazio (não encontrado)")

        return (False, f"hre_found.js retornou saída inesperada: {out}")

    except FileNotFoundError:
        return (False, "Node ou hre_found.js não encontrado. Verifique o caminho e o Node.js no PATH.")
    except subprocess.CalledProcessError as e:
        return (False, f"Erro ao executar hre_found.js: {e}\nSTDERR:\n{e.stderr}")

def listar_videos_da_pasta() -> list[str]:
    try:
        arquivos = os.listdir(PASTA_DE_VIDEOS)
        return sorted([f for f in arquivos if f.lower().endswith(EXTENSOES_VIDEO)])
    except FileNotFoundError:
        return []

# ===== Pipeline da interface =====
def verificar(video_upload, video_da_pasta, endereco_manual):
    addr_txt = buscar_arquivo_endereco()
    contract_address = (endereco_manual or "").strip() or addr_txt

    header = f"Contrato: {contract_address or '(não encontrado)'}"
    if not contract_address or not PADRAO_ENDERECO.match(contract_address):
        return header, "Endereço do contrato ausente ou inválido. Informe no campo ou crie contract_address.txt.", ""

    temp_path = None
    if video_upload is not None:
        temp_path = getattr(video_upload, "name", None)
    elif video_da_pasta:
        candidate = Path(PASTA_DE_VIDEOS) / video_da_pasta
        if candidate.exists():
            temp_path = str(candidate)

    if not temp_path or not os.path.exists(temp_path):
        return header, "Envie um vídeo (upload) ou selecione um arquivo da pasta.", ""

    try:
        file_hash = calcular_hash_streaming(temp_path)
    except Exception as e:
        return header, f"Erro ao calcular hash: {e}", ""

    existe, detalhe = check_hash_onchain(contract_address, file_hash)
    if existe:
        resultado = f"VIDEO REGISTRADO NA BLOCKCHAIN\n\nHash calculado:\n{file_hash}\n\n{detalhe}"
    else:
        resultado = f"VIDEO NAO REGISTRADO NA BLOCKCHAIN\n\nHash calculado:\n{file_hash}\n\n{detalhe}"

    return header, resultado, file_hash

# ===== Interface Gradio =====
with gr.Blocks(title="Verificar vídeo na blockchain") as demo:
    gr.Markdown("## Verificar vídeo na blockchain (consulta de hash)")
    gr.Markdown(
        "- Envie um vídeo ou selecione um arquivo existente na pasta configurada.\n"
        "- Informe o endereço do contrato (ou deixe que o programa leia de contract_address.txt).\n"
        "- O sistema calculará o SHA-256, executará hre_found.js e mostrará o resultado."
    )

    with gr.Row():
        video_upload = gr.File(label="Upload de vídeo", file_types=[".mp4", ".avi", ".mov", ".mkv"])
        video_da_pasta = gr.Dropdown(
            choices=listar_videos_da_pasta(),
            label=f"Ou selecione da pasta: {PASTA_DE_VIDEOS}",
            value=None
        )

    endereco_manual = gr.Textbox(
        label="Endereço do contrato (opcional — se vazio, usa contract_address.txt)",
        placeholder="0x..."
    )

    btn = gr.Button("Verificar")
    contrato_info = gr.Markdown()
    resultado = gr.Textbox(label="Resultado", lines=10)
    hash_calc = gr.Textbox(label="Hash calculado (SHA-256)", lines=2)

    btn.click(
        fn=verificar,
        inputs=[video_upload, video_da_pasta, endereco_manual],
        outputs=[contrato_info, resultado, hash_calc]
    )

    addr_init = buscar_arquivo_endereco()
    if addr_init:
        contrato_info.value = f"Contrato: {addr_init}"

if __name__ == "__main__":
    demo.launch()
