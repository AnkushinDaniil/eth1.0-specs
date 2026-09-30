"""Helpers for EIP-8272 recent root tests."""

from typing import Any, Dict, Sequence

from execution_testing import Bytecode, Fork, Frame, Op

from ..eip8141_frame_transactions.spec import Spec as FrameSpec
from .spec import RecentRoot, Spec, validation_data


def recent_root_frame(*roots: RecentRoot, **overrides: Any) -> Frame:
    """
    Return a recent root verifier frame checking `roots`.

    Keyword arguments override the corresponding frame fields, for
    variants that differ from the canonical frame in a single field.
    """
    kwargs: Dict[str, Any] = dict(
        mode=FrameSpec.MODE_VERIFY,
        flags=FrameSpec.APPROVE_NONE,
        target=Spec.RECENT_ROOT_ADDRESS,
        state_gas_limit=0,
        data=validation_data(*roots),
    )
    kwargs.update(overrides)
    return Frame(**kwargs)


_FAIL = 0xBA
_WRITE = 0xC0
_VALIDATE = 0x28


def _validation_prefix() -> Bytecode:
    """Return the code run once before the first tuple is checked."""
    return (
        Op.CALLVALUE
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.CALLDATASIZE
        + Op.PUSH1[Spec.WRITE_DATA_BYTES]
        + Op.EQ
        + Op.PUSH2[_WRITE]
        + Op.JUMPI
        + Op.CALLDATASIZE
        + Op.PUSH1[Spec.RECENT_ROOT_TUPLE_BYTES]
        + Op.CALLDATASIZE
        + Op.MOD
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.DUP1
        + Op.ISZERO
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.PUSH2[
            Spec.MAX_RECENT_ROOT_REFERENCES * Spec.RECENT_ROOT_TUPLE_BYTES
        ]
        + Op.DUP2
        + Op.GT
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.SLOTNUM
        + Op.PUSH1[0]
    )


def _validation_tuple(*, first: bool, key_warm: bool) -> Bytecode:
    """
    Return the code run to check one tuple that passes.

    Memory grows only while the first tuple is checked: to one word for
    the entry domain, then to the entry preimage the tuple is copied into.
    """
    entry_preimage_size = 32 + Spec.RECENT_ROOT_TUPLE_BYTES
    storage_preimage_size = 32 + 32 + 8
    return (
        Op.JUMPDEST
        + Op.PUSH1[0x20]
        + Op.DUP2
        + Op.ADD
        + Op.CALLDATALOAD
        + Op.PUSH1[0xC0]
        + Op.SHR
        + Op.DUP3
        + Op.DUP2
        + Op.LT
        + Op.ISZERO
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.DUP1
        + Op.DUP4
        + Op.SUB
        + Op.PUSH2[Spec.RECENT_ROOT_LENGTH]
        + Op.GT
        + Op.ISZERO
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.PUSH32[Spec.RECENT_ROOT_ENTRY_DOMAIN]
        + Op.PUSH1[0]
        + (
            Op.MSTORE.with_metadata(old_memory_size=0, new_memory_size=32)
            if first
            else Op.MSTORE
        )
        + Op.PUSH1[Spec.RECENT_ROOT_TUPLE_BYTES]
        + Op.DUP3
        + Op.PUSH1[0x20]
        + Op.CALLDATACOPY.with_metadata(
            data_size=Spec.RECENT_ROOT_TUPLE_BYTES,
            old_memory_size=32 if first else 0,
            new_memory_size=entry_preimage_size if first else 0,
        )
        + Op.PUSH1[entry_preimage_size]
        + Op.PUSH1[0]
        + Op.SHA3.with_metadata(data_size=entry_preimage_size)
        + Op.PUSH32[Spec.RECENT_ROOT_STORAGE_DOMAIN]
        + Op.PUSH1[0]
        + Op.MSTORE
        + Op.SWAP1
        + Op.PUSH2[Spec.RECENT_ROOT_LENGTH - 1]
        + Op.AND
        + Op.PUSH1[0xC0]
        + Op.SHL
        + Op.PUSH1[0x40]
        + Op.MSTORE
        + Op.PUSH1[storage_preimage_size]
        + Op.PUSH1[0]
        + Op.SHA3.with_metadata(data_size=storage_preimage_size)
        + Op.SLOAD.with_metadata(key_warm=key_warm)
        + Op.EQ
        + Op.ISZERO
        + Op.PUSH2[_FAIL]
        + Op.JUMPI
        + Op.PUSH1[Spec.RECENT_ROOT_TUPLE_BYTES]
        + Op.ADD
        + Op.DUP3
        + Op.DUP2
        + Op.LT
        + Op.PUSH2[_VALIDATE]
        + Op.JUMPI
    )


def validation_gas(fork: Fork, roots: Sequence[RecentRoot]) -> int:
    """
    Return the execution gas of a recent root verifier frame whose
    tuples `roots` all pass, with the contract and its storage cold at
    frame entry.

    The contract's path through its code is fixed by the number of
    tuples, so its cost is that of the path: the checks before the loop,
    one iteration per tuple, whose storage read is warm only for a tuple
    already checked, and the final `STOP`. The path is asserted to be the
    contract code it stands for.
    """
    prefix = _validation_prefix()
    loop = _validation_tuple(first=False, key_warm=False)
    code = Spec.RECENT_ROOT_CODE
    assert bytes(prefix) == code[:_VALIDATE]
    assert bytes(loop) + bytes(Op.STOP) == code[_VALIDATE:_FAIL]

    path = prefix
    seen = set()
    for index, root in enumerate(roots):
        path += _validation_tuple(
            first=index == 0, key_warm=root.storage_key in seen
        )
        seen.add(root.storage_key)
    path += Op.STOP
    return fork.frame_entry_gas_calculator()(
        target_warm=False
    ) + path.gas_cost(fork)
