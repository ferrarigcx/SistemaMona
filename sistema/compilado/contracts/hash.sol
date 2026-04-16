// SPDX-License-Identifier: MIT
pragma solidity ^0.8.10;

contract HashStorage {

    //var 
    bytes32 public storedHash;

    //tipo Console.log da minha var
    //var video, indexada;
    event video(
        bytes32 indexed storedHash
    );

    event issue(
        string indexed _issue_i
    )

    function saveHash(bytes32 _hash, string _issue_i) public {
        storedHash = _hash;
        emit video(_hash);
        emit issue(_issue_i)
    }
}

mapping(bytes32 => bool) public hashExists;
function register(bytes32 h) external { require(!hashExists[h], "DUPLICATE"); hashExists[h] = true; /* ... */ }
function existsHash(bytes32 h) external view returns (bool) { return hashExists[h]; }
