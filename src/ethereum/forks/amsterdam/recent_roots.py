"""
Recent roots let a frame transaction verify, in a canonical
[`VERIFY`][v] frame, that a root written by a root source in a recent slot
is still recorded ([EIP-8272]).

Root sources write roots to, and frames verify them against, the recent root
contract at [`RECENT_ROOT_ADDRESS`], whose runtime code is
[`RECENT_ROOT_CODE`]. Both operations are ordinary EVM execution of that code:
a recent root verifier frame is a `VERIFY` frame like any other, so a failed
check invalidates the transaction and the frame is charged ordinary gas.
Block execution therefore only needs the contract to be in place, which
[`install_recent_root_contract`] ensures when the fork activates.

[EIP-8272]: https://eips.ethereum.org/EIPS/eip-8272
[v]: ref:ethereum.forks.amsterdam.transactions.frame_transaction.FrameMode.VERIFY
[`RECENT_ROOT_ADDRESS`]: ref:ethereum.forks.amsterdam.recent_roots.RECENT_ROOT_ADDRESS
[`RECENT_ROOT_CODE`]: ref:ethereum.forks.amsterdam.recent_roots.RECENT_ROOT_CODE
[`install_recent_root_contract`]: ref:ethereum.forks.amsterdam.recent_roots.install_recent_root_contract
"""  # noqa: E501

from typing import Final

from ethereum_types.bytes import Bytes
from ethereum_types.numeric import Uint

from ethereum.exceptions import InvalidBlock
from ethereum.state import EMPTY_ACCOUNT, EMPTY_CODE_HASH, Account, Address
from ethereum.state_mpt import (
    State,
    account_has_storage,
    set_account,
    store_code,
)

RECENT_ROOT_ADDRESS: Final[Address] = Address(
    bytes.fromhex("0000000000000000000000000000000000008272")
)
"""
Address of the recent root contract.
"""

RECENT_ROOT_CODE: Final[Bytes] = Bytes(
    bytes.fromhex(
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
)
"""
Runtime code of the recent root contract.

A call with a nonzero value reverts. A call with exactly two words of
calldata, a salt and a root, is a write: it records the root for the
caller's root source under the current slot. A call whose calldata is one to
sixteen tuples of a source identifier, a slot and a root is a validation: it
succeeds, without side effects, only if every tuple names a recent slot and
matches the recorded entry. Any other calldata reverts.

[EIP-8272] leaves this value to be determined; the code here is the
`recent_root` runtime of `ethereum/sys-asm`.

[EIP-8272]: https://eips.ethereum.org/EIPS/eip-8272
"""


def install_recent_root_contract(state: State) -> None:
    """
    Install [`RECENT_ROOT_CODE`] at [`RECENT_ROOT_ADDRESS`] against the
    parent state of the first block in which recent roots are active.

    The account may already exist, for example because it received ether,
    but it must have neither code nor storage, or the block is invalid. Its
    balance is preserved and its nonce is raised to at least one.

    [`RECENT_ROOT_ADDRESS`]: ref:ethereum.forks.amsterdam.recent_roots.RECENT_ROOT_ADDRESS
    [`RECENT_ROOT_CODE`]: ref:ethereum.forks.amsterdam.recent_roots.RECENT_ROOT_CODE
    """  # noqa: E501
    account = state.get_account_optional(RECENT_ROOT_ADDRESS)
    if account is None:
        account = EMPTY_ACCOUNT

    if account.code_hash != EMPTY_CODE_HASH or account_has_storage(
        state, RECENT_ROOT_ADDRESS
    ):
        raise InvalidBlock("recent root address has code or storage")

    set_account(
        state,
        RECENT_ROOT_ADDRESS,
        Account(
            nonce=max(account.nonce, Uint(1)),
            balance=account.balance,
            code_hash=store_code(state, RECENT_ROOT_CODE),
        ),
    )
