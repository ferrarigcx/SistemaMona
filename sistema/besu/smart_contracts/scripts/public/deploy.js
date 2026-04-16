const path = require('path');
const fs = require('fs-extra');
var ethers = require('ethers');

// RPCNODE details
const { tessera, besu } = require("../keys.js");
const host = besu.rpcnode.url;
const accountPrivateKey = besu.rpcnode.accountPrivateKey;

// abi and bytecode generated from simplestorage.sol:
// > solcjs --bin --abi simplestorage.sol
const contractJsonPath = path.resolve(__dirname, '../../', 'contracts', 'HashStorage.json');
const contractJson = JSON.parse(fs.readFileSync(contractJsonPath));
const contractAbi = contractJson.abi;
const contractBytecode = contractJson.evm.bytecode.object


async function createContract(wallet, contractAbi, contractByteCode) {
    const factory = new ethers.ContractFactory(contractAbi, contractByteCode, wallet);
    const contract = await factory.deploy();
    // The contract is NOT deployed yet; we must wait until it is mined
    const deployed = await contract.waitForDeployment();
    //The contract is deployed now
    return contract
};

async function main() {
    const provider = new ethers.JsonRpcProvider(host);
    const wallet = new ethers.Wallet(accountPrivateKey, provider);
    const args = process.argv.slice(2);
    var contract = await  createContract(wallet, contractAbi, contractBytecode);
    contractAddress = await contract.getAddress();
    console.log(contractAddress);

}

main();

/*

if (require.main === module) {
    main();
}

module.exports = exports = main*/