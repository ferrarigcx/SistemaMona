// hre_exists.js (exemplo esquemático)
const hre = require("hardhat");

async function main() {
  const [address, fileHash] = process.argv.slice(2);
  if (!address || !fileHash) {
    console.error("usage: node hre_exists.js <contractAddress> <hash>");
    process.exit(1);
  }

  const Contract = await hre.ethers.getContractAt("NonConformityLedger", address);
  const exists = await Contract.existsHash(fileHash); // sua função view no contrato
  console.log(exists ? "true" : "false");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
