from web3 import Web3
from solcx import compile_source, install_solc

# instala versão do solc
install_solc("0.8.20")

# contrato Solidity inline
contract_source = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SimpleStorage {
    bytes32 public data;

    function saveHash(bytes32 _data) public {
        data = _data;
    }
}
"""

# compilar contrato
compiled = compile_source(
    contract_source,
    output_values=["abi", "bin"],
    solc_version="0.8.20"
)

contract_id, contract_interface = compiled.popitem()
abi = contract_interface["abi"]
bytecode = contract_interface["bin"]

# conectar no Anvil
w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
assert w3.is_connected(), "Erro ao conectar ao Anvil"

# chave padrão do Anvil (primeira conta)
PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
account = w3.eth.account.from_key(PRIVATE_KEY)
address = account.address

print(f"Deployando com: {address}")

# criar contrato
Contract = w3.eth.contract(abi=abi, bytecode=bytecode)

# nonce
nonce = w3.eth.get_transaction_count(address)

# tx de deploy
tx = Contract.constructor().build_transaction({
    "from": address,
    "nonce": nonce,
    "gas": 3000000,
    "gasPrice": w3.to_wei("1", "gwei"),
    "chainId": 31337
})

# assinar
signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)

# enviar
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

print(f"TX hash: {tx_hash.hex()}")

# esperar mineração
receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

contract_address = receipt.contractAddress

print(f"Contrato deployado em: {contract_address}")