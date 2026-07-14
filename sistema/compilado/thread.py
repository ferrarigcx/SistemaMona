import csv
import time
import hashlib
import subprocess
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
import statistics
import os


from solcx import compile_source, install_solc
import threading


from concurrent.futures import TimeoutError
from collections import Counter

import asyncio
from web3 import Web3
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers import AsyncHTTPProvider
from web3.exceptions import TimeExhausted
from requests.adapters import HTTPAdapter
from requests import Session
from collections import defaultdict


from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError,
    CancelledError
)


#session = Session()
#adapter = HTTPAdapter(pool_connections=1000, pool_maxsize=1000)
#session.mount("http://", adapter)

# ===== CONFIG =====
# 1. Usamos AsyncWeb3 e AsyncHTTPProvider (não precisa mais da 'session' antiga)
w3 = AsyncWeb3(AsyncHTTPProvider("http://127.0.0.1:8545", request_kwargs={"timeout": 30}))

# 2. Injetamos o middleware específico para cadeias PoA/QBFT (como o Besu)
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

MAX_CONCURRENT = 2000


#w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545", session=session))
#w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
#nonce_lock = threading.Lock()

CHAIN_ID = 1337
funding_private_key = "0xae6ae8e5ccbfb04590405997ee2d52d2b330726137b875053c36d94e974d162f"
funding_account = w3.eth.account.from_key(funding_private_key)
funding_nonce_lock = threading.Lock()
funding_nonce =  w3.eth.get_transaction_count(funding_account.address)


abi = None
bytecode = None
contract_address = ""



#compila contrato
def compile_contract(source: str, solc_version="0.8.20"):
    install_solc(solc_version)
    if os.path.exists(source):
        with open(source, "r") as f:
            source_code = f.read()
    else:
        source_code = source

    compiled_sol = compile_source(source_code, output_values=["abi", "bin"], solc_version=solc_version)
    _, contract_interface = compiled_sol.popitem()
    return contract_interface['abi'], contract_interface['bin']

#deploy contrato com carteira uma carteira criada pela rede besu (funding_private_key) que já tem ETH; 
# ===== DEPLOY ASSÍNCRONO =====
async def deploy_contract():
    global contract_address
    account = w3.eth.account.from_key(funding_private_key)
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    
    tx = await Contract.constructor().build_transaction({
        'from': account.address,
        'nonce': await w3.eth.get_transaction_count(account.address),
        'gas': 3000000,
        'gasPrice': 0,
        'chainId': CHAIN_ID
    })
    
    signed = account.sign_transaction(tx)
    tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    contract_address = receipt.contractAddress
    print(f"✅ Contrato deployado em: {contract_address}")



async def enviar_transacao_async(account, private_key, duration_seconds, semaphore):
    address = account.address
    contract = w3.eth.contract(address=contract_address, abi=abi)
    nonce = await w3.eth.get_transaction_count(address, "pending")
    
    success_count = 0
    errors = defaultdict(int)
    tx_hashes = []  # ⚡ NOVO: Armazena hashes com timestamps
    start_time = time.perf_counter()

    retry_count = 0
    max_retries = 3

    while (time.perf_counter() - start_time) < duration_seconds:
        async with semaphore:
            try:
                tx = await contract.functions.saveHash(
                    AsyncWeb3.keccak(text=f"{time.time_ns()}")
                ).build_transaction({
                    "from": address,
                    "nonce": nonce,
                    "gas": 30000,
                    "gasPrice": 0,
                    "chainId": CHAIN_ID
                })
                
                signed = account.sign_transaction(tx)
                inicio_envio = time.perf_counter()
                tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
                nonce += 1
                success_count += 1
                
                # ⚡ NOVO: Armazena hash e timestamp de envio
                tx_hashes.append({
                    "hash": tx_hash,
                    "enviado_em": inicio_envio
                })
                
            except Exception as e:
                error_msg = str(e).lower()
                if "nonce" in error_msg:
                    # Resincroniza o nonce com o nó
                    nonce = await w3.eth.get_transaction_count(address, "pending")
                    retry_count += 1
                    errors["nonce_ressincronizado"] += 1
                    
                    # Se já tentou muitas vezes, pula esta iteração
                    if retry_count > max_retries:
                        retry_count = 0
                        await asyncio.sleep(0.1)  # Pequena pausa
                else:
                    error_type = type(e).__name__
                    error_key = f"{error_type}: {error_msg[:80]}"
                    errors[error_key] += 1

    return {
        "address": address,
        "success_count": success_count,
        "errors": dict(errors),
        "tx_hashes": tx_hashes  # ⚡ NOVO: Retorna hashes
    }
    
async def verificar_confirmacoes(resultados):
    """Verifica se as transações foram confirmadas e calcula latência"""
    print("\n🔍 Verificando confirmações das transações...")
    
    todas_txs = []
    for resultado in resultados:
        for tx_info in resultado["tx_hashes"]:
            todas_txs.append({
                "hash": tx_info["hash"],
                "enviado_em": tx_info["enviado_em"],
                "address": resultado["address"]
            })
    
    print(f"Total de transações para verificar: {len(todas_txs)}")
    
    confirmadas = []
    nao_confirmadas = []
    
    # Verifica cada transação
    for i, tx_info in enumerate(todas_txs):
        try:
            receipt = await w3.eth.wait_for_transaction_receipt(
                tx_info["hash"],
                timeout=30,
                poll_latency=0.5
            )
            confirmado_em = time.perf_counter()
            latencia = confirmado_em - tx_info["enviado_em"]
            
            confirmadas.append({
                "hash": tx_info["hash"].hex(),
                "address": tx_info["address"],
                "block_number": receipt.blockNumber,
                "gas_used": receipt.gasUsed,
                "status": receipt.status,
                "latencia_confirmacao": latencia
            })
            
            # Progresso
            if (i + 1) % 100 == 0:
                print(f"  Verificadas: {i + 1}/{len(todas_txs)}")
                
        except Exception as e:
            nao_confirmadas.append({
                "hash": tx_info["hash"].hex(),
                "address": tx_info["address"],
                "erro": str(e)[:100]
            })
    
    return confirmadas, nao_confirmadas    

async def rodar_benchmark(total_accounts: int, duration_seconds: int = 10):
    print(f"\n🚀 Iniciando benchmark com {total_accounts} contas por {duration_seconds}s...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    while await w3.eth.syncing or (await w3.eth.block_number) == 0:
        await asyncio.sleep(1)
        
    accounts = [w3.eth.account.create() for _ in range(total_accounts)]
    await fund_accounts(accounts)
    
    inicio_total = time.perf_counter()
    
    tasks = [
        enviar_transacao_async(accounts[i], accounts[i].key, duration_seconds, semaphore)
        for i in range(total_accounts)
    ]
    
    resultados = await asyncio.gather(*tasks)
    fim_envio = time.perf_counter()
    
    tempo_envio = fim_envio - inicio_total
    total_tx = sum(r["success_count"] for r in resultados)
    tps_envio = total_tx / tempo_envio if tempo_envio > 0 else 0

    print("\n" + "="*50)
    print("📊 RESUMO DO ENVIO")
    print("="*50)
    print(f"Contas (Threads lógicas) : {total_accounts}")
    print(f"Tempo de envio           : {tempo_envio:.2f} segundos")
    print(f"Total de TX enviadas     : {total_tx}")
    print(f"TPS de Envio             : {tps_envio:.2f} tx/s")
    print("="*50)
    
    # ⚡ NOVO: Verificar confirmações
    confirmadas, nao_confirmadas = await verificar_confirmacoes(resultados)
    fim_total = time.perf_counter()
    
    # ⚡ NOVO: Imprimir relatório completo
    imprimir_relatorio_completo(
        resultados, confirmadas, nao_confirmadas, 
        tempo_envio, total_tx, tps_envio, fim_total - inicio_total
    )

def imprimir_relatorio_completo(resultados, confirmadas, nao_confirmadas, tempo_envio, total_tx, tps_envio, tempo_total):
    """Imprime relatório completo com métricas de confirmação"""
    
    print("\n" + "="*70)
    print("📊 RELATÓRIO COMPLETO DO BENCHMARK")
    print("="*70)
    
    # Métricas de Envio
    print("\n🚀 MÉTRICAS DE ENVIO")
    print("-" * 70)
    print(f"Tempo de envio           : {tempo_envio:.2f}s")
    print(f"Total de TX enviadas     : {total_tx}")
    print(f"TPS de Envio             : {tps_envio:.2f} tx/s")
    
    # Métricas de Confirmação
    print("\n✅ MÉTRICAS DE CONFIRMAÇÃO")
    print("-" * 70)
    print(f"Total de TX confirmadas  : {len(confirmadas)}")
    print(f"Total de TX não confirm. : {len(nao_confirmadas)}")
    
    taxa_sucesso = (len(confirmadas) / total_tx * 100) if total_tx > 0 else 0
    print(f"Taxa de sucesso          : {taxa_sucesso:.2f}%")
    
    # Latência de Confirmação
    if confirmadas:
        latencias = [tx["latencia_confirmacao"] for tx in confirmadas]
        latencias.sort()
        
        latencia_media = sum(latencias) / len(latencias)
        latencia_mediana = latencias[len(latencias) // 2]
        latencia_min = min(latencias)
        latencia_max = max(latencias)
        latencia_p95 = latencias[int(len(latencias) * 0.95)]
        
        print(f"\n⏱️  LATÊNCIA DE CONFIRMAÇÃO")
        print(f"Média                    : {latencia_media:.3f}s")
        print(f"Mediana                  : {latencia_mediana:.3f}s")
        print(f"Mínima                   : {latencia_min:.3f}s")
        print(f"Máxima                   : {latencia_max:.3f}s")
        print(f"P95                      : {latencia_p95:.3f}s")
        
        # Gas usado
        gas_usado = [tx["gas_used"] for tx in confirmadas]
        gas_medio = sum(gas_usado) / len(gas_usado)
        gas_max = max(gas_usado)
        
        print(f"\n⛽ GAS UTILIZADO")
        print(f"Médio                    : {gas_medio:.0f}")
        print(f"Máximo                   : {gas_max}")
    
    # Tempo total
    print(f"\n⏰ TEMPO TOTAL DO TESTE    : {tempo_total:.2f}s")
    
    # Erros por carteira
    imprimir_erros_por_carteira(resultados)
    
    # Transações não confirmadas
    if nao_confirmadas:
        print("\n" + "="*70)
        print("⚠️  TRANSAÇÕES NÃO CONFIRMADAS")
        print("="*70)
        print(f"Total: {len(nao_confirmadas)}\n")
        
        # Mostra primeiras 10
        for i, tx in enumerate(nao_confirmadas[:10]):
            print(f"{i+1}. Hash: {tx['hash'][:20]}...")
            print(f"   Carteira: {tx['address']}")
            print(f"   Erro: {tx['erro']}")
            print()
        
        if len(nao_confirmadas) > 10:
            print(f"... e mais {len(nao_confirmadas) - 10} transações")
    
    print("="*70)    

def imprimir_erros_por_carteira(resultados):
    """Imprime os erros agrupados por carteira"""
    print("\n" + "="*70)
    print("🔍 ERROS POR CARTEIRA")
    print("="*70)
    
    carteiras_com_erro = [r for r in resultados if r["errors"]]
    
    if not carteiras_com_erro:
        print("✅ Nenhuma carteira apresentou erros!")
        return
    
    print(f"\nTotal de carteiras com erros: {len(carteiras_com_erro)}\n")
    
    # Agrupa erros por tipo globalmente
    erros_globais = defaultdict(int)
    
    for resultado in carteiras_com_erro:
        address = resultado["address"]
        errors = resultado["errors"]
        total_erros = sum(errors.values())
        
        print(f"📍 Carteira: {address}")
        print(f"   Total de erros: {total_erros}")
        
        for error_key, count in errors.items():
            print(f"   • {error_key}: {count}x")
            erros_globais[error_key] += count
        
        print()
    
    # Resumo global de erros
    print("="*70)
    print("📊 RESUMO GLOBAL DE ERROS")
    print("="*70)
    total_erros_global = sum(erros_globais.values())
    print(f"Total de erros em todas as carteiras: {total_erros_global}\n")
    
    for error_key, count in sorted(erros_globais.items(), key=lambda x: x[1], reverse=True):
        percent = (count / total_erros_global * 100) if total_erros_global > 0 else 0
        print(f"• {error_key}")
        print(f"  Ocorrências: {count} ({percent:.1f}%)")
        print()

 
# ===== FUNDING ASSÍNCRONO =====
async def fund_accounts(accounts, value_eth=0.01):
    print("Funding contas...")
    
    # ⚡ CORREÇÃO: Fazer funding em lotes de 50 transações
    LOTE_SIZE = 100
    total_accounts = len(accounts)
    
    for i in range(0, total_accounts, LOTE_SIZE):
        lote = accounts[i:i + LOTE_SIZE]
        nonce = await w3.eth.get_transaction_count(funding_account.address, 'pending')
        
        print(f"  Enviando lote {i//LOTE_SIZE + 1}/{(total_accounts + LOTE_SIZE - 1)//LOTE_SIZE} ({len(lote)} contas)...")
        
        txs = []
        for j, acc in enumerate(lote):
            tx = {
                "to": acc.address,
                "value": w3.to_wei(value_eth, "ether"),
                "gas": 21000,
                "gasPrice": 0,
                "nonce": nonce + j,
                "chainId": CHAIN_ID
            }
            signed = funding_account.sign_transaction(tx)
            txs.append(await w3.eth.send_raw_transaction(signed.raw_transaction))
        
        # ⚡ Espera o lote ser confirmado antes de enviar o próximo
        await asyncio.gather(*[w3.eth.wait_for_transaction_receipt(tx, timeout=60) for tx in txs])
    
    print("✅ Funding concluído.")

def criar_conta():
    return w3.eth.account.create()

BASE_DIR = Path(__file__).resolve().parent.parent
NODE_SCRIPT = BASE_DIR / "besu" / "smart_contracts" / "scripts" / "public" / "hre_hash2.js"
CONTRACT_ADDRESS = ""

abi, bytecode = compile_contract("contracts/hash.sol")

TIMEOUT = 120



# Ajuste os cenarios como quiser
CENARIOS = [
    (1024, 10),    # Atual: 1024 contas, 10s (~125 TPS)
    (2048, 15),    # Dobro de contas
    (4096, 15),    # Quádruplo
    (8192, 15),    # 8x mais contas
   # (1024, 300),   # Mesmo contas, 5 minutos (sustentação)
   # (1024, 600),   # 10 minutos (stress prolongado)
]
#, 2, 4, 9, 16, 25, 36, 49, 64, 81, 100, 121, 144, 169,
#196, 225, 256, 289, 324,
#    361, 400, 441, 484, 529,
#    576, 625, 676, 729, 784,
#    841, 900, 961
def gerar_hash_unico(i: int) -> str:
    payload = f"tx-{i}-{time.time_ns()}".encode()
    return "0x" + hashlib.sha256(payload).hexdigest()


def main():
    print("===== BENCHMARK POR THREAD =====\n")
    global abi
    add = ""
    #add = deploy_contract(abi, bytecode)
    #rodar_benchmark(1, 1, add)
    erros = Counter()
    asyncio.run(deploy_contract())
    for total_accounts, duration in CENARIOS:
        print(f"\n{'='*70}")
        print(f"🔥 CENÁRIO: {total_accounts} contas por {duration}s")
        print(f"{'='*70}")
        asyncio.run(rodar_benchmark(total_accounts=total_accounts, duration_seconds=duration))

if __name__ == "__main__":
    main()
