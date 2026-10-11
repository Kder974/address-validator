"""Keccak-256 en Python pur — filet de secours **local** (jamais en production).

Pourquoi ce fichier existe (défaut d'environnement mesuré le 11/10/2026) : sur
cette machine Windows, une stratégie de contrôle d'application bloque les
modules natifs de `pycryptodome` :

    ctypes.CDLL(".../Crypto/Hash/_keccak.pyd")
    -> OSError: [WinError 4551] Une stratégie de contrôle d'application a
       bloqué ce fichier

`Crypto/Cipher/_raw_aes.pyd` se charge, `Crypto/Hash/_keccak.pyd` non. Effet
mesuré : `eth_account`, `eth_utils` et `web3` échouent au premier appel
(`Cannot load native module 'Crypto.Hash._keccak'`), donc les signatures
EIP-712 du protocole x402 ne peuvent être ni produites ni vérifiées localement.

Ce module installe un keccak Python pur dans `eth_hash.auto` **avant** que
`eth_account` ne l'importe. Il est utilisé par les tests et les oracles locaux ;
`app/` ne l'importe jamais. Sur Linux (donc sur le VPS et dans l'image Docker)
le backend natif fonctionne et ce filet n'est pas installé.

Vérification de correction, en deux temps :

1. vecteurs publics : `keccak256(b"")` et `keccak256(b"abc")` (assertions dans
   `_self_test()` et dans `tests/test_x402.py`) ;
2. vecteur **externe** décisif : la charge utile x402 publiée dans
   `specs/schemes/exact/scheme_exact_evm.md` (dépôt coinbase/x402) doit rendre
   l'adresse `from` qu'elle documente. Une permutation fausse rendrait une
   adresse quelconque : ce test ne peut pas passer par accident.
"""

from __future__ import annotations

MASK = (1 << 64) - 1

ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

RC = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)

#: Capacité de Keccak-256 : 1600 - 256 = 1344 bits, soit 136 octets de débit.
RATE = 136


def _rol(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & MASK
    return ((value << shift) | (value >> (64 - shift))) & MASK


def _keccak_f(state: list[list[int]]) -> list[list[int]]:
    """Permutation Keccak-f[1600] sur un état 5x5 de voies de 64 bits.

    Les quatre étapes suivent littéralement la spécification (`A[x][y]`, θ puis
    ρ, π, χ, ι). La variante compacte qui parcourt ρ et π en spirale a été
    essayée d'abord : elle était fausse, l'offset de rotation y est indexé sur la
    destination et non sur la source. Le désaccord a été mesuré contre le keccak
    natif (`keccak256(b"")` doit valoir `c5d24601…`), pas supposé.
    """
    for constant in RC:
        # θ : colonnes XORées, diffusées sur chaque voie.
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]

        # ρ puis π : B[y][(2x+3y) mod 5] = ROT(A[x][y], r[x][y]).
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(state[x][y], ROTATION[x][y])

        # χ : non-linéarité, voie par voie.
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y] & MASK)

        # ι : constante de tour sur la voie centrale.
        state[0][0] ^= constant
    return state


def keccak256(data: bytes) -> bytes:
    """Keccak-256 (remplissage pad10*1), tel que l'attend Ethereum.

    Le piège est le remplissage : `0x01` puis le **minimum** de zéros, et le bit
    final posé dans le dernier octet du bloc — qui vaut donc `0x81` quand les
    deux délimiteurs tombent dans le même octet. Ajouter un octet `0x80` de plus
    produirait un remplissage valide au sens « multiple du débit », mais un autre
    condensat : le premier essai de ce module a échoué exactement là.
    """
    state = [[0] * 5 for _ in range(5)]
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * ((-len(padded)) % RATE))
    padded[-1] |= 0x80

    for offset in range(0, len(padded), RATE):
        block = padded[offset : offset + RATE]
        for index in range(RATE // 8):
            lane = int.from_bytes(block[index * 8 : index * 8 + 8], "little")
            state[index % 5][index // 5] ^= lane
        _keccak_f(state)

    output = bytearray()
    for index in range(4):
        output += state[index % 5][index // 5].to_bytes(8, "little")
    return bytes(output)


def _self_test() -> bool:
    """Vecteurs publics : refuse de s'installer si la permutation est fausse."""
    return (
        keccak256(b"").hex()
        == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
        and keccak256(b"abc").hex()
        == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )


def native_keccak_available() -> bool:
    """Vrai si le backend natif (pycryptodome) fonctionne réellement."""
    try:
        from Crypto.Hash import keccak as native

        native.new(digest_bits=256).update(b"x").hexdigest()
        return True
    except Exception:  # noqa: BLE001 - tout échec vaut « indisponible »
        return False


def install_if_needed() -> bool:
    """Branche ce keccak dans `eth_hash.auto` si le natif est inutilisable.

    Renvoie True si le filet a été installé (ou l'était déjà).
    """
    if native_keccak_available():
        return False
    if not _self_test():
        raise RuntimeError("keccak Python pur invalide : vecteurs publics non respectes")

    import eth_hash.auto as auto

    auto.keccak = keccak256
    return True
