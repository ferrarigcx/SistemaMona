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

# ===== CONFIG =====

w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
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
        'gasPrice': 1,  
        'chainId': 1337
    })

    signed_txn = w3.eth.account.sign_transaction(transaction, funding_private_key)

    tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)


    return receipt.contractAddress

def rodar_benchmark(total_tx: int, threads: int, contract_address):
    resultados = []

    global w3


    accounts = [w3.eth.account.create() for _ in range(total_tx)]
    txs = []
    txs  = fund_account(accounts, 0.01)

    for tx in txs:
        w3.eth.wait_for_transaction_receipt(tx)
    
    inicio_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [
            executor.submit(enviar_transacao, i, contract_address, accounts[i].key, total_tx)
            for i in range(total_tx)
        ]

        for future in as_completed(futures):
            resultados.append(future.result())
    fim_total = time.perf_counter()
    resumo = calcular_resumo(
        resultados,
        total_tx,
        threads,
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
            "gas": 21000,               # Fixed for ETH transfers
            "gasPrice": gas_estimate,      # Correct Wei per gas
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
    (10, 1),
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


def send_contract_transaction(
    w3,
    contract_address,
    abi,
    private_key,
    tx_total,
    gas=200000,
    gasPrice=1,
    chain_id=None
):
    start = time.perf_counter()
   
    if chain_id is None:
        chain_id = w3.eth.chain_id

    account = w3.eth.account.from_key(private_key)
    address = account.address

    contract = w3.eth.contract(address=contract_address, abi=abi)
 
    nonce = w3.eth.get_transaction_count(address, "pending")

   
    tx_hashes = []
    for i in range(tx_total):
        func = contract.functions.saveHash(
            Web3.keccak(text=f"tx-{i}")
        )

        # 🔥 estimate gas (evita erro)
        gas_estimate = func.estimate_gas({'from': address})


        tx = func.build_transaction({
            'from': address,
            'nonce': nonce + i,
            'gas': int(gas_estimate * 1.2),
            'gasPrice': gasPrice,
            "chainId": w3.eth.chain_id
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hashes.append(tx_hash)

    end = time.perf_counter()

    tps = tx_total / (end - start)

    return tx_hashes, tps



def enviar_transacao(i: int, contract_address, account, total_tx):
    inicio = time.perf_counter()
    try:
        global abi
        global w3
      
        # enviar tx
        tx_hashes, tps = send_contract_transaction(
            w3,
            contract_address,
            abi,
            account,
            total_tx)

        fim = time.perf_counter()
       
        return {
            "id": i,
            "ok": True,
            "txs": len(tx_hashes),
            "tps": tps,
            "latencia": fim - inicio
            }
        

    except Exception as e:
        fim = time.perf_counter()

        return {
            "id": i,
            "ok": False,
            "erro": str(e),
            "latencia": fim - inicio
        }
    
def calcular_resumo(resultados, total_tx, threads, inicio_total, fim_total):
    tempo_total = fim_total - inicio_total

    sucessos = [r for r in resultados if r.get("ok")]
    falhas = [r for r in resultados if not r.get("ok")]

    latencias = [r["latencia"] for r in resultados if "latencia" in r]

    def p95(lista):
        if not lista:
            return 0.0
        lista_ordenada = sorted(lista)
        idx = int(0.95 * len(lista_ordenada)) - 1
        return lista_ordenada[max(idx, 0)]

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
        "p95": p95(latencias),
    }

    return resumo   




def imprimir_resumo(resumo):
    print(f"Threads: {resumo['threads']}")
    print(f"Total TX: {resumo['total_tx']}")
    print(f"Sucessos: {resumo['sucessos']}")
    print(f"Falhas: {resumo['falhas']}")
    print(f"Tempo total: {resumo['tempo_total']:.4f}s")
    print(f"TXS: {resumo['txs']:.4f}")
    print(f"TPS: {resumo['tps']:.4f}")
    print(f"Lat media: {resumo['lat_media']:.4f}s")
    print(f"Lat mediana: {resumo['lat_mediana']:.4f}s")
    print(f"Lat min: {resumo['lat_min']:.4f}s")
    print(f"Lat max: {resumo['lat_max']:.4f}s")
    print(f"P95: {resumo['p95']:.4f}s")
    print("-" * 40)


def imprimir_amostra_erros(resultados, limite=3):
    erros = [r for r in resultados if not r["ok"]]

    for r in erros[:limite]:
        print(f"TX {r['id']} | erro: {r.get('erro')}")


   


def main():
    print("===== BENCHMARK POR THREAD =====\n")
    global abi
    add = ""
    add = deploy_contract(abi, bytecode)
    #rodar_benchmark(1, 1, add)
    
    for total_tx, threads in CENARIOS:
        resumo,resultados =rodar_benchmark(total_tx, threads, add)
        imprimir_resumo(resumo)
        imprimir_amostra_erros(resultados)
    
   


if __name__ == "__main__":
    main()
