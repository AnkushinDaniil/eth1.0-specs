"""
EIP-8272: Recent Roots for Frame Transactions.

Frame transactions can declare verified recent roots.

https://eips.ethereum.org/EIPS/eip-8272
"""

from typing import Mapping

from ....base_fork import BaseFork

RECENT_ROOT_ADDRESS = 0x0000000000000000000000000000000000008272
# EIP-8272 leaves RECENT_ROOT_CODE to be determined; this is the
# `recent_root` runtime of ethereum/sys-asm.
RECENT_ROOT_BYTECODE = bytes.fromhex(
    "346100ba57366040146100c05736604836066100ba5780156100ba576104808111"
    "6100ba574b60005b602081013560c01c828110156100ba5780830361200011156100"
    "ba577f8f42481679c8e6fefa040974b3c905e0ce3f2e464ba93acdb074a4118161"
    "7efc60005260488260203760686000207fbdc897da2177d260ff5f4be5d4b2aad4"
    "3f89c3347a305b584fa5a2546d053daa60005290611fff1660c01b604052604860"
    "00205414156100ba5760480182811061002857005b60006000fd5b336000526020"
    "60006020376034600c20807f8f42481679c8e6fefa040974b3c905e0ce3f2e464b"
    "a93acdb074a41181617efc6040524b606852606052602060206088376068604020"
    "817fbdc897da2177d260ff5f4be5d4b2aad43f89c3347a305b584fa5a2546d053d"
    "aa60a852611fff4b1660d05260c852604860a8205500"
)


class EIP8272(BaseFork):
    """EIP-8272 class."""

    @classmethod
    def pre_allocation(cls) -> Mapping:
        """Pre-allocate the recent root contract as activation leaves it."""
        return {
            RECENT_ROOT_ADDRESS: {
                "nonce": 1,
                "code": RECENT_ROOT_BYTECODE,
            }
        } | super(EIP8272, cls).pre_allocation()  # type: ignore

    @classmethod
    def activation_code_installs(cls) -> Mapping:
        """Install the recent root contract when the fork activates."""
        return {
            RECENT_ROOT_ADDRESS: RECENT_ROOT_BYTECODE,
        } | super(EIP8272, cls).activation_code_installs()  # type: ignore

    @classmethod
    def activation_minimum_nonces(cls) -> Mapping:
        """Raise the recent root contract's nonce to at least one."""
        return {
            RECENT_ROOT_ADDRESS: 1,
        } | super(EIP8272, cls).activation_minimum_nonces()  # type: ignore
