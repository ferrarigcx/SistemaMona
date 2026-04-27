import time
import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import statistics
import os
from web3 import Web3
from solcx import compile_source, install_solc
import threading
from web3.middleware import ExtraDataToPOAMiddleware
from requests.adapters import HTTPAdapter
from requests import Session

session = Session()
adapter = HTTPAdapter(pool_connections=1000, pool_maxsize=1000)
session.mount("http://", adapter)

# ===== CONFIG =====

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545", session=session))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
nonce_lock = threading.Lock()

funding_private_key = "0xae6ae8e5ccbfb04590405997ee2d52d2b330726137b875053c36d94e974d162f"
funding_account = w3.eth.account.from_key(funding_private_key)
funding_nonce_lock = threading.Lock()
funding_nonce = w3.eth.get_transaction_count(funding_account.address)



def compile_contract(source: str, solc_version="0.8.20"):
    
    install_solc(solc_version)

    # Se for caminho de arquivo
    if os.path.exists(source):
        with open(source, "r") as f:
            source_code = f.read()
    else:
        source_code = source

    compiled_sol = compile_source(
        source_code,
        output_values=["abi", "bin"],
        solc_version=solc_version
    )

    _, contract_interface = compiled_sol.popitem()

    return contract_interface['abi'], contract_interface['bin']

def deploy_contract(abi, bytecode):
    global funding_private_key
    #0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
    assert w3.is_connected()

    account = w3.eth.account.from_key(funding_private_key)
    address = account.address

    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    nonce = w3.eth.get_transaction_count(address)

    constructor = Contract.constructor()

    # 🔥 estimate com try (importante)
   
    gas_estimate = 3000000  # fallback

    # 🔥 usar o MESMO constructor
    transaction = constructor.build_transaction({
        'from': address,
        'nonce': nonce,
        'gas': int(gas_estimate * 1.3),
        'gasPrice': 0,  #1
        'chainId': 1337
    })

    signed_txn = w3.eth.account.sign_transaction(transaction, funding_private_key)

    tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

    return receipt.contractAddress

def rodar_benchmark(total_accounts: int, contract_address):
    resultados = []

    global w3

    # cria total contas
    accounts = [w3.eth.account.create() for _ in range(total_accounts)]

    # funding
    txs = fund_account(accounts, 0.01)

    for tx in txs:
        w3.eth.wait_for_transaction_receipt(tx)

    inicio_total = time.perf_counter()


    #max 12 -> 120
    with ThreadPoolExecutor(max_workers=total_accounts) as executor:
        futures = [
            executor.submit(
                enviar_transacao,
                i,
                contract_address,
                accounts[i].key
            )
            for i in range(total_accounts)
        ]

        for future in as_completed(futures):
            resultados.append(future.result())

    fim_total = time.perf_counter()

    resumo = calcular_resumo(
        resultados,
        total_accounts,
        total_accounts,
        inicio_total,
        fim_total
    )

    return resumo, resultados

 
def fund_account(accounts, value_eth):
    
    sender_address = w3.eth.account.from_key(funding_private_key).address

    # 2. Fetch the exact nonce the network expects right now
    nonce = w3.eth.get_transaction_count(sender_address,  'pending')
       
    tx_hashes = []

    # 🔥 estimate com try (importante)
 
    gas_estimate = 3000000  # fallback

 
    for i, acc in enumerate(accounts):
        tx = {
            "to": acc.address,
            "value": w3.to_wei(value_eth, "ether"),
            "gas": int(gas_estimate * 1.3),               # Fixed for ETH transfers
            "gasPrice": 0,      # Correct Wei per gas
            "nonce": nonce + i,    # Sequential, gap-safe
            "chainId": 1337         # Dynamic, not hardcoded
        }
        
        signed = w3.eth.account.sign_transaction(tx, funding_private_key)
        tx_hashes.append(w3.eth.send_raw_transaction(signed.raw_transaction))
        
        time.sleep(1)
        
    return tx_hashes

def criar_conta():
    return w3.eth.account.create()

BASE_DIR = Path(__file__).resolve().parent.parent
NODE_SCRIPT = BASE_DIR / "besu" / "smart_contracts" / "scripts" / "public" / "hre_hash2.js"
CONTRACT_ADDRESS = ""

abi, bytecode = compile_contract("contracts/hash.sol")

TIMEOUT = 120



# Ajuste os cenarios como quiser
CENARIOS = [
    (10)
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


def send_single_transaction(
    w3,
    contract_address,
    abi,
    private_key,
    gasPrice=1,
):
    start = time.perf_counter()

    account = w3.eth.account.from_key(private_key)
    address = account.address

    contract = w3.eth.contract(address=contract_address, abi=abi)

    nonce = w3.eth.get_transaction_count(address, "pending")

    func = contract.functions.saveHash(
        Web3.keccak(text=f"tx-{time.time_ns()}")
    )

    gas_estimate = func.estimate_gas({'from': address})

    tx = func.build_transaction({
        'from': address,
        'nonce': nonce,
        'gas': int(gas_estimate * 1.2),
        'gasPrice': gasPrice,
        "chainId": w3.eth.chain_id
    })

    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

    end = time.perf_counter()

    return tx_hash, (end - start)



def enviar_transacao(i: int, contract_address, private_key):
    inicio = time.perf_counter()

    try:
        global abi
        global w3

        tx_hash, tempo = send_single_transaction(
            w3,
            contract_address,
            abi,
            private_key
        )

        fim = time.perf_counter()

        return {
            "id": i,
            "ok": True,
            "txs": 1,
            "tx_hash": tx_hash.hex(),
            "latencia_tx": tempo,
            "latencia_thread": fim - inicio,
        }

    except Exception as e:
        fim = time.perf_counter()

        return {
            "id": i,
            "ok": False, "id": i,
            "erro": str(e),
            "latencia_thread": fim - inicio
        }
    
def calcular_resumo(resultados, total_tx, threads, inicio_total, fim_total):
    tempo_total = fim_total - inicio_total

    sucessos = [r for r in resultados if r.get("ok")]
    falhas = [r for r in resultados if not r.get("ok")]

    # 🔥 agora cada sucesso = 1 tx
    total_tx_executadas = len(sucessos)

    # 🔥 latência da thread (tempo total da execução)
    latencias_thread = [
        r["latencia_thread"] for r in resultados if "latencia_thread" in r
    ]

    # 🔥 latência real da tx (melhor métrica agora)
    latencias_tx = [
        r["latencia_tx"] for r in sucessos if "latencia_tx" in r
    ]

    def p95(lista):
        if not lista:
            return 0.0
        lista_ordenada = sorted(lista)
        idx = int(0.95 * len(lista_ordenada)) - 1
        return lista_ordenada[max(idx, 0)]

    resumo = {
        "threads": threads,

        # 📊 volume
        "total_tx_config": total_tx,
        "total_tx_executadas": total_tx_executadas,

        # ✔ status
        "sucessos": len(sucessos),
        "falhas": len(falhas),

        # ⏱ tempo total
        "tempo_total": tempo_total,

        # 🚀 TPS REAL (envio)
        "tps_envio": (
            total_tx_executadas / tempo_total if tempo_total > 0 else 0.0
        ),

        # 📉 latência da thread
        "lat_thread_media": statistics.mean(latencias_thread) if latencias_thread else 0.0,
        "lat_thread_p95": p95(latencias_thread),

        # 📉 latência da transação (mais importante agora)
        "lat_tx_media": statistics.mean(latencias_tx) if latencias_tx else 0.0,
        "lat_tx_mediana": statistics.median(latencias_tx) if latencias_tx else 0.0,
        "lat_tx_min": min(latencias_tx) if latencias_tx else 0.0,
        "lat_tx_max": max(latencias_tx) if latencias_tx else 0.0,
        "lat_tx_p95": p95(latencias_tx),
    }

    return resumo




def imprimir_resumo(resumo):
    print(f"Threads: {resumo['threads']}")

    # 📊 volume
    print(f"Total TX (config): {resumo['total_tx_config']}")
    print(f"Total TX (executadas): {resumo['total_tx_executadas']}")

    # ✔ status
    print(f"Sucessos: {resumo['sucessos']}")
    print(f"Falhas: {resumo['falhas']}")

    # ⏱ tempo
    print(f"Tempo total: {resumo['tempo_total']:.4f}s")

    # 🚀 TPS
    print(f"TPS (envio): {resumo['tps_envio']:.4f}")

    # 📉 latência thread
    print(f"Latência thread média: {resumo['lat_thread_media']:.4f}s")
    print(f"Latência thread P95: {resumo['lat_thread_p95']:.4f}s")

    # 📉 latência real da tx
    print(f"Latência TX média: {resumo['lat_tx_media']:.4f}s")
    print(f"Latência TX mediana: {resumo['lat_tx_mediana']:.4f}s")
    print(f"Latência TX min: {resumo['lat_tx_min']:.4f}s")
    print(f"Latência TX max: {resumo['lat_tx_max']:.4f}s")
    print(f"P95 TX: {resumo['lat_tx_p95']:.4f}s")

    print("-" * 40)


def imprimir_amostra_erros(resultados, limite=3):
    erros = [r for r in resultados if not r.get("ok")]

    print(f"\n❌ Total de erros: {len(erros)}")

    if not erros:
        print("Nenhum erro encontrado ✅")
        return

    print(f"Mostrando até {limite} erros:\n")

    for r in erros[:limite]:
        print(f"TX ID: {r.get('id')}")
        print(f"Erro: {r.get('erro')}")

        # 🔍 extras úteis (se existirem)
        if "latencia_thread" in r:
            print(f"Latência thread: {r.get('latencia_thread'):.4f}s")

        if "tx_hash" in r:
            print(f"TX Hash: {r.get('tx_hash')}")

        print("-" * 40)


   


def main():
    print("===== BENCHMARK POR THREAD =====\n")
    global abi
    add = ""
    add = deploy_contract(abi, bytecode)
    #rodar_benchmark(1, 1, add)
    
    for total_accounts in CENARIOS:
        resumo,resultados = rodar_benchmark(total_accounts, add)
        imprimir_resumo(resumo)
        imprimir_amostra_erros(resultados)
    
   


if __name__ == "__main__":
    main()
