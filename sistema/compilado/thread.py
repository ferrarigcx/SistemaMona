import time
import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import statistics
from solcx import compile_source, install_solc

# ===== CONFIG =====
BASE_DIR = Path(__file__).resolve().parent.parent
NODE_SCRIPT = BASE_DIR / "besu" / "smart_contracts" / "scripts" / "public" / "hre_hash2.js"
CONTRACT_ADDRESS = "0xc5a5C42992dECbae36851359345FE25997F5C42d"
TIMEOUT = 120

# Ajuste os cenarios como quiser
CENARIOS = [
    (10, 1),
    (10, 2),
    (20, 5),
    (20, 10),
    (40, 20),
]


def gerar_hash_unico(i: int) -> str:
    payload = f"tx-{i}-{time.time_ns()}".encode()
    return "0x" + hashlib.sha256(payload).hexdigest()


def calcular_p95(valores):
    if not valores:
        return 0.0
    valores = sorted(valores)
    idx = int(0.95 * (len(valores) - 1))
    return valores[idx]


def enviar_transacao(i: int):
    inicio = time.perf_counter()
    hash_hex = gerar_hash_unico(i)

    try:
        proc = subprocess.run(
            ["node", str(NODE_SCRIPT), hash_hex, CONTRACT_ADDRESS],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False
        )

        fim = time.perf_counter()

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        sucesso = (
            proc.returncode == 0
            and "RECEIPT_STATUS: 1" in stdout
            and "SUCCESS" in stdout
        )

        return {
            "id": i,
            "ok": sucesso,
            "latencia": fim - inicio,
            "hash": hash_hex,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    except subprocess.TimeoutExpired:
        fim = time.perf_counter()
        return {
            "id": i,
            "ok": False,
            "latencia": fim - inicio,
            "hash": hash_hex,
            "returncode": -1,
            "stdout": "",
            "stderr": "TimeoutExpired",
        }

    except Exception as e:
        fim = time.perf_counter()
        return {
            "id": i,
            "ok": False,
            "latencia": fim - inicio,
            "hash": hash_hex,
            "returncode": -2,
            "stdout": "",
            "stderr": str(e),
        }


def rodar_benchmark(total_tx: int, threads: int):
    resultados = []
    inicio_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(enviar_transacao, i) for i in range(total_tx)]

        for future in as_completed(futures):
            resultados.append(future.result())

    fim_total = time.perf_counter()
    tempo_total = fim_total - inicio_total

    sucessos = [r for r in resultados if r["ok"]]
    falhas = [r for r in resultados if not r["ok"]]
    latencias = [r["latencia"] for r in resultados]

    resumo = {
        "threads": threads,
        "total_tx": total_tx,
        "sucessos": len(sucessos),
        "falhas": len(falhas),
        "tempo_total": tempo_total,
        "tps": (len(sucessos) / tempo_total) if tempo_total > 0 else 0.0,
        "lat_media": statistics.mean(latencias) if latencias else 0.0,
        "lat_mediana": statistics.median(latencias) if latencias else 0.0,
        "lat_min": min(latencias) if latencias else 0.0,
        "lat_max": max(latencias) if latencias else 0.0,
        "p95": calcular_p95(latencias),
    }

    return resumo, resultados


def imprimir_resumo(resumo):
    print(f"Threads: {resumo['threads']}")
    print(f"Total TX: {resumo['total_tx']}")
    print(f"Sucessos: {resumo['sucessos']}")
    print(f"Falhas: {resumo['falhas']}")
    print(f"Tempo total: {resumo['tempo_total']:.4f}s")
    print(f"TPS: {resumo['tps']:.4f}")
    print(f"Lat media: {resumo['lat_media']:.4f}s")
    print(f"Lat mediana: {resumo['lat_mediana']:.4f}s")
    print(f"Lat min: {resumo['lat_min']:.4f}s")
    print(f"Lat max: {resumo['lat_max']:.4f}s")
    print(f"P95: {resumo['p95']:.4f}s")
    print("-" * 40)


def imprimir_amostra_erros(resultados, limite=3):
    erros = [r for r in resultados if not r["ok"]]
    if not erros:
        return

    print("Amostra de erros:")
    for r in erros[:limite]:
        print(f"TX {r['id']} | returncode={r['returncode']}")
        if r["stderr"]:
            print("STDERR:", r["stderr"])
        if r["stdout"]:
            print("STDOUT:", r["stdout"])
        print("-" * 20)

def compile_contract(source_code: str, solc_version="0.8.20"):
    print("compile contract")   
    # Instalar versão do compilador (se não tiver)
    install_solc(solc_version)

    compiled_sol = compile_source(
        source_code,
        output_values=["abi", "bin"],
        solc_version=solc_version
    )

    # Pega o contrato (último do dict)
    contract_id, contract_interface = compiled_sol.popitem()

    abi = contract_interface['abi']
    bytecode = contract_interface['bin']

    return abi, bytecode

def deploy_contract():
    print("deploy contract")    


def main():
    print("===== BENCHMARK POR THREAD =====\n")
    print(f"BASE_DIR: {BASE_DIR}")
    print(f"NODE_SCRIPT: {NODE_SCRIPT}")
    print(f"Script existe? {NODE_SCRIPT.exists()}")
    print(f"CONTRACT_ADDRESS: {CONTRACT_ADDRESS}\n")
    abi, bytecode = compile_contract("contracts/hash.sol")

    print("ABI:", abi)
    print("Bytecode:", bytecode)        

'''    
    if not NODE_SCRIPT.exists():
        print("ERRO: hre_hash.js nao encontrado.")
        return

    for total_tx, threads in CENARIOS:
        resumo, resultados = rodar_benchmark(total_tx, threads)
        imprimir_resumo(resumo)
        imprimir_amostra_erros(resultados)
'''
   


if __name__ == "__main__":
    main()
