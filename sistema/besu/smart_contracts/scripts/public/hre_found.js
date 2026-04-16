const path = require('path');
const fs = require('fs-extra');
var ethers = require('ethers');

const { besu } = require("../keys.js");
const host = besu.rpcnode.url;

const contractJsonPath = path.resolve(__dirname, '../../', 'contracts', 'HashStorage.json');
const contractJson = JSON.parse(fs.readFileSync(contractJsonPath));
const contractAbi = contractJson.abi;

async function main() {
    const provider = new ethers.JsonRpcProvider(host);
    const args = process.argv.slice(2);
    const fileHash = (args[0] || "").toLowerCase();
    const contractAddress = args[1];

    const readOnlyContract = new ethers.Contract(contractAddress, contractAbi, provider);
    const storedHash = await readOnlyContract.storedHash();

    if (storedHash && storedHash.toLowerCase() === fileHash) {
        console.log(fileHash);
        return fileHash;
    }

    return "";
}

if (require.main === module) {
    main()
        .then(() => process.exit(0))
        .catch((err) => {
            console.error("Unhandled error:", err);
            process.exit(1);
        });
}

module.exports = exports = main;
