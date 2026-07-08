import csv
import time
import hashlib
import subprocess
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
import statistics
import os
from web3 import Web3
from solcx import compile_source, install_solc
import threading
from web3.middleware import ExtraDataToPOAMiddleware
from requests.adapters import HTTPAdapter
from requests import Session

from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError,
    CancelledError
)


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




#compila contrato
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

#deploy contrato com carteira uma carteira criada pela rede besu (funding_private_key) que já tem ETH; 
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

def enviar_transacao(
    i: int,
    contract_address,
    private_key,
    stop_event,
):

    inicio_thread = time.perf_counter()

    account = w3.eth.account.from_key(private_key)
    address = account.address

    resultados = []

    contract = w3.eth.contract(
        address=contract_address,
        abi=abi
    )

    nonce = w3.eth.get_transaction_count(
        address,
        "pending"
    )

    while not stop_event.is_set():

        try:

            func = contract.functions.saveHash(Web3.keccak(text=f"{time.time_ns()}") )
            gas_estimate = func.estimate_gas({ 'from': address })
            tx = func.build_transaction({ 'from': address, 'nonce': nonce, 'gas': int(gas_estimate * 1.2), 'gasPrice': 0, 'chainId': w3.eth.chain_id })
            signed = w3.eth.account.sign_transaction( tx, private_key )
            inicio_envio = time.perf_counter()
            tx_hash = w3.eth.send_raw_transaction( signed.raw_transaction )
            fim_envio = time.perf_counter()
            receipt = w3.eth.wait_for_transaction_receipt(
                 tx_hash,
                timeout=5
            )
            fim_confirmacao = time.perf_counter()
            
            if receipt.status == 1: nonce += 1

            # envia a transação

            resultados.append({
                "ok": True,
                "tx_hash": tx_hash.hex(),
                "confirmada": receipt.status == 1,
                "block_number": receipt.blockNumber,
                "gas_used": receipt.gasUsed,
                "latencia_rpc": fim_envio - inicio_envio,
                "latencia_confirmacao": fim_confirmacao - fim_envio,
                "latencia_tx": fim_confirmacao - inicio_envio,
                "latencia_thread": fim_confirmacao - inicio_thread
            })

          

        except Exception as e:

                resultados.append({
                "ok": False,
                "erro": str(e)
                })

                time.sleep(0.1)

                continue

    return {
        "thread_id": i,
        "address": address,
        "transacoes": resultados
    }

# função principal do benchmark, roda o cenário completo e retorna o resumo e os resultados detalhados;
def rodar_benchmark(total_accounts: int, contract_address):

    resultados = []

    stop_event = threading.Event()

    # Cria as contas
    accounts = [
        w3.eth.account.create()
        for _ in range(total_accounts)
    ]

    # Faz o funding
    txs = fund_account(accounts, 0.01)

    for tx in txs:
        w3.eth.wait_for_transaction_receipt(tx)

    inicio_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=total_accounts) as executor:

        futures = [
            executor.submit(
                enviar_transacao,
                i,
                contract_address,
                accounts[i].key,
                stop_event
            )
            for i in range(total_accounts)
        ]

        # Executa por 5 segundos
        time.sleep(20)

        print("Parando benchmark...")

        # Sinaliza para as threads encerrarem
        stop_event.set()

        # Aguarda todas as threads terminarem
        for future in futures:

            try:

                carteira = future.result()

                resultados.append(carteira)

            except Exception as e:

                resultados.append({
                    "thread_id": -1,
                    "address": "",
                    "transacoes": [
                        {
                            "ok": False,
                            "erro": str(e)
                        }
                    ]
                })

    fim_total = time.perf_counter()

    resumo = calcular_resumo(
        resultados,
        total_accounts,
        len(resultados),
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
    5
]

#, 2, 4, 9, 16, 25, 36, 49, 64, 81, 100, 121, 144, 169,
#196, 225, 256, 289, 324,
#    361, 400, 441, 484, 529,
#    576, 625, 676, 729, 784,
#    841, 900, 961
def gerar_hash_unico(i: int) -> str:
    payload = f"tx-{i}-{time.time_ns()}".encode()
    return "0x" + hashlib.sha256(payload).hexdigest()


def calcular_p95(valores):
    if not valores:
        return 0.0
    valores = sorted(valores)
    idx = int(0.95 * (len(valores) - 1))
    return valores[idx]

def imprimir_carteiras(resultados):

    print("\n========== CARTEIRAS ==========\n")

    for carteira in resultados:

        txs = carteira["transacoes"]

        sucessos = sum(
            1
            for tx in txs
            if tx.get("ok")
        )

        falhas = len(txs) - sucessos

        print(f"Thread........: {carteira['thread_id']}")
        print(f"Carteira......: {carteira['address']}")
        print(f"Total TX......: {len(txs)}")
        print(f"Sucessos......: {sucessos}")
        print(f"Falhas........: {falhas}")

        if sucessos:

            media = statistics.mean([
                tx["latencia_tx"]
                for tx in txs
                if tx.get("ok")
            ])

            print(f"Latência média: {media:.4f}s")

        print("-" * 60)
    
def calcular_resumo(resultados, total_tx, threads, inicio_total, fim_total):

    tempo_total = fim_total - inicio_total

    todas_tx = []

    for carteira in resultados:
        todas_tx.extend(carteira["transacoes"])

    sucessos = [
        tx
        for tx in todas_tx
        if tx.get("ok")
    ]

    falhas = [
        tx
        for tx in todas_tx
        if not tx.get("ok")
    ]

    total_tx_executadas = len(sucessos)

    latencias_thread = [
        tx["latencia_thread"]
        for tx in sucessos
        if "latencia_thread" in tx
    ]

    latencias_tx = [
        tx["latencia_tx"]
        for tx in sucessos
        if "latencia_tx" in tx
    ]

    latencias_confirmacao = [
        tx["latencia_confirmacao"]
        for tx in sucessos
        if "latencia_confirmacao" in tx
    ]

    gas_used = [
        tx["gas_used"]
        for tx in sucessos
        if "gas_used" in tx
    ]

    confirmadas = sum(
        1
        for tx in sucessos
        if tx.get("confirmada")
    )
  
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

        # ✔ status blockchain
        "confirmadas": confirmadas,

        # ⏱ tempo total
        "tempo_total": tempo_total,
        # quantidade de transações SUBMETIDAS por segundo
        # 🚀 TPS REAL (envio)

       
        "tps_envio": (
            total_tx_executadas / tempo_total if tempo_total > 0 else 0.0
        ),


        # 📉 latência da thread
        "lat_thread_media": statistics.mean(latencias_thread) if latencias_thread else 0.0,
        "lat_thread_p95": p95(latencias_thread),

        # 📉 latência da transação (mais importante agora)
        "lat_envio_media": statistics.mean(latencias_tx) if latencias_tx else 0.0,
        "lat_envio_p95": p95(latencias_tx),
        # 🚀 envio RPC
        
        # ⛓ confirmação blockchain
        "lat_confirmacao_media": (
            statistics.mean(latencias_confirmacao)
            if latencias_confirmacao else 0.0
        ),

        "lat_confirmacao_mediana": (
            statistics.median(latencias_confirmacao)
            if latencias_confirmacao else 0.0
        ),

        "lat_confirmacao_min": (
            min(latencias_confirmacao)
            if latencias_confirmacao else 0.0
        ),

        "lat_confirmacao_max": (
            max(latencias_confirmacao)
            if latencias_confirmacao else 0.0
        ),

        "lat_confirmacao_p95": p95(
            latencias_confirmacao
        ),

        # ⛽ gas
        "gas_medio": (
            statistics.mean(gas_used)
            if gas_used else 0.0
        ),

        "gas_max": (
            max(gas_used)
            if gas_used else 0.0
        ),
    }

    return resumo




def imprimir_resumo(resumo):

    print("\n========== RESUMO BENCHMARK ==========")

    # 🧵 concorrência
    print(f"Threads: {resumo['threads']}")

    # 📊 volume
    print(f"Total TX (config): {resumo['total_tx_config']}")
    print(f"Total TX (executadas): {resumo['total_tx_executadas']}")

    # ✔ envio
    print(f"Sucessos envio: {resumo['sucessos']}")
    print(f"Falhas envio: {resumo['falhas']}")
    print(f"TX confirmadas: {resumo['confirmadas']}")

    # ⏱ tempos
    print(f"Tempo envio: {resumo['tempo_total']:.4f}s")
  
    # 🚀 TPS
    print(f"TPS envio: {resumo['tps_envio']:.4f}")
 
    # 📉 latência thread
    print(f"Latência thread média: {resumo['lat_thread_media']:.4f}s")
    print(f"Latência thread P95: {resumo['lat_thread_p95']:.4f}s")

  # 🚀 latência envio RPC
    print(
        f"Latência envio média: "
        f"{resumo['lat_envio_media']:.4f}s"
    )

    print(
        f"Latência envio P95: "
        f"{resumo['lat_envio_p95']:.4f}s"
    )

    # ⛓ latência confirmação blockchain
    print(
        f"Latência confirmação média: "
        f"{resumo['lat_confirmacao_media']:.4f}s"
    )

    print(
        f"Latência confirmação mediana: "
        f"{resumo['lat_confirmacao_mediana']:.4f}s"
    )

    print(
        f"Latência confirmação min: "
        f"{resumo['lat_confirmacao_min']:.4f}s"
    )

    print(
        f"Latência confirmação max: "
        f"{resumo['lat_confirmacao_max']:.4f}s"
    )

    print(
        f"Latência confirmação P95: "
        f"{resumo['lat_confirmacao_p95']:.4f}s"
    )

    # ⛽ gas
    print(f"Gas médio: {resumo['gas_medio']:.2f}")
    print(f"Gas máximo: {resumo['gas_max']}")

    print("======================================\n")




   


def main():
    print("===== BENCHMARK POR THREAD =====\n")
    global abi
    add = ""
    add = deploy_contract(abi, bytecode)
    #rodar_benchmark(1, 1, add)
    
    for total_accounts in CENARIOS:
        resumo,resultados = rodar_benchmark(total_accounts, add)
        imprimir_resumo(resumo)
        #imprimir_amostra_erros(resultados)
        imprimir_carteiras(resultados)
    
   


if __name__ == "__main__":
    main()
