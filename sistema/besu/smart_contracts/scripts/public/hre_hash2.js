const path = require('path');
const fs = require('fs-extra');
var ethers = require('ethers');

// RPCNODE details
const { tessera, besu } = require("../keys.js");
const host = besu.rpcnode.url;
const accountPrivateKey = besu.rpcnode.accountPrivateKey;

// ABI
const contractJsonPath = path.resolve(__dirname, '../../', 'contracts', 'HashStorage.json');
const contractJson = JSON.parse(fs.readFileSync(contractJsonPath));
const contractAbi = contractJson.abi;

// ===== FUNCAO PRINCIPAL =====
async function set(provider, wallet, deployedContractAbi, deployedContractAddress, value) {

  // 1. VALIDAR ENDERECO
  if (!ethers.isAddress(deployedContractAddress)) {
    console.error("ERRO: Endereco invalido");
    process.exit(1);
  }

  // 2. VERIFICAR SE EXISTE CONTRATO NO ENDERECO
  const code = await provider.getCode(deployedContractAddress);
  if (!code || code === "0x") {
    console.error("ERRO: Nenhum contrato encontrado nesse endereco");
    process.exit(1);
  }

  const contract = new ethers.Contract(deployedContractAddress, deployedContractAbi, provider);
  const contractWithSigner = contract.connect(wallet);

  try {
    // 3. ENVIAR TRANSACAO
    const tx = await contractWithSigner.saveHash(value);

    console.log("TX_HASH:", tx.hash);

    // 4. ESPERAR CONFIRMACAO REAL
    const receipt = await tx.wait();

    console.log("RECEIPT_STATUS:", receipt.status);
    console.log("BLOCK_NUMBER:", receipt.blockNumber);

    // 5. VALIDAR SUCESSO REAL
    if (receipt.status !== 1) {
      console.error("ERRO: Transacao falhou na execucao");
      process.exit(1);
    }

    console.log("SUCCESS");
    process.exit(0);

  } catch (err) {
    console.error("ERRO_CONTRATO:", err.message || err);
    process.exit(1);
  }
}

// ===== MAIN =====
async function main() {

  const provider = new ethers.JsonRpcProvider(host);
  const wallet = new ethers.Wallet(accountPrivateKey, provider);

  const args = process.argv.slice(2);

  if (args.length < 2) {
    console.error("Uso: node hre_hash.js <hash> <contractAddress>");
    process.exit(1);
  }

  const hashValue = args[0];
  const contractAddress = args[1];

  await set(provider, wallet, contractAbi, contractAddress, hashValue);
}

// ===== EXECUCAO =====
if (require.main === module) {
  main()
    .then(() => process.exit(0))
    .catch((err) => {
      console.error("Unhandled error:", err);
      process.exit(1);
    });
}

module.exports = exports = main;
