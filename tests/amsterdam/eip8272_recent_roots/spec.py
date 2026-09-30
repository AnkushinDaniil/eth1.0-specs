"""Defines EIP-8272 specification constants and functions."""

from dataclasses import dataclass

from execution_testing import Address, Bytes, Hash, keccak256


@dataclass(frozen=True)
class ReferenceSpec:
    """Defines the reference spec version and git path."""

    git_path: str
    version: str


ref_spec_8272 = ReferenceSpec(
    "EIPS/eip-8272.md", "274ce98c57ec285862f4d2ff48cd9f545c385f12"
)


@dataclass(frozen=True)
class Spec:
    """
    Parameters from the EIP-8272 specification as defined at
    https://eips.ethereum.org/EIPS/eip-8272.
    """

    RECENT_ROOT_ADDRESS = Address(0x8272)
    # The EIP leaves RECENT_ROOT_CODE to be determined; this is the
    # `recent_root` runtime of ethereum/sys-asm.
    RECENT_ROOT_CODE = bytes.fromhex(
        "346100ba57366040146100c05736604836066100ba5780156100ba576104808111"
        "6100ba574b60005b602081013560c01c828110156100ba57808303612000111561"
        "00ba577f8f42481679c8e6fefa040974b3c905e0ce3f2e464ba93acdb074a41181"
        "617efc60005260488260203760686000207fbdc897da2177d260ff5f4be5d4b2aa"
        "d43f89c3347a305b584fa5a2546d053daa60005290611fff1660c01b6040526048"
        "6000205414156100ba5760480182811061002857005b60006000fd5b3360005260"
        "2060006020376034600c20807f8f42481679c8e6fefa040974b3c905e0ce3f2e46"
        "4ba93acdb074a41181617efc6040524b6068526060526020602060883760686040"
        "20817fbdc897da2177d260ff5f4be5d4b2aad43f89c3347a305b584fa5a2546d05"
        "3daa60a852611fff4b1660d05260c852604860a8205500"
    )
    RECENT_ROOT_LENGTH = 8192
    RECENT_ROOT_USABLE_WINDOW = 8191
    MAX_RECENT_ROOT_REFERENCES = 16
    RECENT_ROOT_TUPLE_BYTES = 72
    RECENT_ROOT_ENTRY_DOMAIN = keccak256(b"RECENT_ROOT_ENTRY")
    RECENT_ROOT_STORAGE_DOMAIN = keccak256(b"RECENT_ROOT_STORAGE")
    WRITE_DATA_BYTES = 64


def uint64_be(value: int) -> bytes:
    """Encode `value` as an eight-byte big-endian unsigned integer."""
    return value.to_bytes(8, "big")


def source_id(source_address: Address, salt: int) -> Hash:
    """Return the identifier of the root source `(source_address, salt)`."""
    return keccak256(bytes(source_address) + salt.to_bytes(32, "big"))


def entry_hash(source: Hash, slot: int, root: int) -> Hash:
    """Return the entry committed for `root` from `source` at `slot`."""
    return keccak256(
        Spec.RECENT_ROOT_ENTRY_DOMAIN
        + source
        + uint64_be(slot)
        + root.to_bytes(32, "big")
    )


def storage_key(source: Hash, slot: int) -> Hash:
    """Return the recent root storage key `source` uses for `slot`."""
    return keccak256(
        Spec.RECENT_ROOT_STORAGE_DOMAIN
        + source
        + uint64_be(slot % Spec.RECENT_ROOT_LENGTH)
    )


def write_data(salt: int, root: int) -> Bytes:
    """Return the calldata of a write of `root` under `salt`."""
    return Bytes(salt.to_bytes(32, "big") + root.to_bytes(32, "big"))


@dataclass(frozen=True)
class RecentRoot:
    """A `(source_id, slot, root)` tuple of the validation operation."""

    source: Hash
    slot: int
    root: int

    @property
    def encoded(self) -> bytes:
        """Return the tuple's 72-byte encoding."""
        return (
            self.source + uint64_be(self.slot) + self.root.to_bytes(32, "big")
        )

    @property
    def storage_key(self) -> Hash:
        """Return the storage key the tuple is checked against."""
        return storage_key(self.source, self.slot)

    @property
    def entry_hash(self) -> Hash:
        """Return the entry the tuple matches."""
        return entry_hash(self.source, self.slot, self.root)


def validation_data(*roots: RecentRoot) -> Bytes:
    """Return the calldata validating `roots`."""
    return Bytes(b"".join(root.encoded for root in roots))
