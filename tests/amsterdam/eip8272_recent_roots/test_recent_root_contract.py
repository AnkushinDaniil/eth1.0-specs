"""
Tests for the recent root contract of
[EIP-8272: Recent Roots for Frame Transactions](https://eips.ethereum.org/EIPS/eip-8272).

The contract at `Spec.RECENT_ROOT_ADDRESS` selects an operation by calldata
length. Exactly two words write a root for the caller's root source under
the current slot; one to sixteen tuples validate recorded roots. These
tests call the contract directly, from transactions and contracts, to pin
both operations and every revert condition.
"""

from typing import Dict, List, Tuple

import pytest
from execution_testing import (
    Account,
    Address,
    Alloc,
    AuthorizationTuple,
    Block,
    BlockchainTestFiller,
    Bytecode,
    Bytes,
    Environment,
    Hash,
    Op,
    StateTestFiller,
    Transaction,
    TransactionReceipt,
)

from .spec import (
    RecentRoot,
    Spec,
    entry_hash,
    ref_spec_8272,
    source_id,
    storage_key,
    validation_data,
    write_data,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_8272.git_path
REFERENCE_SPEC_VERSION = ref_spec_8272.version

pytestmark = pytest.mark.valid_from("Bogota")

CURRENT_SLOT = 10_000
"""Slot of the block executing the call, old enough for every age tested."""

SALT = 0x5A17
ROOT = 0x2007
OTHER_ROOT = 0x2008

SLOT_SUCCESS = 0x00
"""Probe storage slot recording whether the call to the contract succeeded."""

SLOT_RETURN_SIZE = 0x01
"""Probe storage slot recording the size of the contract's return data."""

SENTINEL = 0xFF
"""Value both probe slots hold before the call, so each write is visible."""


def call_probe(pre: Alloc, call_opcode: Op, *, value: int = 0) -> Address:
    """
    Deploy a contract that calls the recent root contract with its own
    calldata and records the call's success and return data size.
    """
    if call_opcode in (Op.CALL, Op.CALLCODE):
        call = call_opcode(
            Op.GAS, Spec.RECENT_ROOT_ADDRESS, value, 0, Op.CALLDATASIZE, 0, 0
        )
    else:
        assert value == 0
        call = call_opcode(
            Op.GAS, Spec.RECENT_ROOT_ADDRESS, 0, Op.CALLDATASIZE, 0, 0
        )
    return pre.deploy_contract(
        code=Op.CALLDATACOPY(0, 0, Op.CALLDATASIZE)
        + Op.SSTORE(SLOT_SUCCESS, call)
        + Op.SSTORE(SLOT_RETURN_SIZE, Op.RETURNDATASIZE)
        + Op.STOP,
        storage={SLOT_SUCCESS: SENTINEL, SLOT_RETURN_SIZE: SENTINEL},
        balance=value,
    )


def probe_result(*, success: bool) -> Dict[int, int]:
    """Return the probe storage after a call that did or did not succeed."""
    return {SLOT_SUCCESS: 1 if success else 0, SLOT_RETURN_SIZE: 0}


def recorded(*roots: RecentRoot) -> Dict[Hash, Hash]:
    """Return the recent root storage recording `roots`."""
    return {root.storage_key: root.entry_hash for root in roots}


def writes_code(writes: List[Tuple[int, int]]) -> Bytecode:
    """Return code that writes each `(salt, root)` in order."""
    code = Bytecode()
    for salt, root in writes:
        code += (
            Op.MSTORE(0, salt)
            + Op.MSTORE(32, root)
            + Op.POP(Op.CALL(Op.GAS, Spec.RECENT_ROOT_ADDRESS, 0, 0, 64, 0, 0))
        )
    return code + Op.STOP


@pytest.mark.parametrize(
    "slot",
    [
        pytest.param(1, id="slot_1"),
        pytest.param(Spec.RECENT_ROOT_LENGTH - 1, id="last_index"),
        pytest.param(Spec.RECENT_ROOT_LENGTH, id="index_wraps_to_zero"),
        pytest.param(3 * Spec.RECENT_ROOT_LENGTH + 5, id="third_wrap"),
        pytest.param(2**64 - 1, id="max_slot"),
    ],
)
@pytest.mark.parametrize(
    "from_contract", [False, True], ids=["eoa", "contract"]
)
def test_write(
    state_test: StateTestFiller,
    pre: Alloc,
    slot: int,
    from_contract: bool,
) -> None:
    """
    Write a root under the caller's root source at the current slot.

    The entry commits to the full slot while the storage key uses the slot
    modulo `Spec.RECENT_ROOT_LENGTH`. The write returns no data and emits
    no log.
    """
    sender = pre.fund_eoa()
    if from_contract:
        caller = call_probe(pre, Op.CALL)
        to = caller
        post: Dict[Address, Account] = {
            caller: Account(storage=probe_result(success=True))
        }
    else:
        caller = sender
        to = Spec.RECENT_ROOT_ADDRESS
        post = {}

    source = source_id(caller, SALT)
    post[Spec.RECENT_ROOT_ADDRESS] = Account(
        nonce=1,
        balance=0,
        code=Spec.RECENT_ROOT_CODE,
        storage={storage_key(source, slot): entry_hash(source, slot, ROOT)},
    )

    state_test(
        env=Environment(slot_number=slot),
        pre=pre,
        tx=Transaction(
            sender=sender,
            to=to,
            data=write_data(SALT, ROOT),
            expected_receipt=TransactionReceipt(logs=[]),
        ),
        post=post,
    )


@pytest.mark.parametrize(
    "writes",
    [
        pytest.param([(SALT, ROOT), (SALT, OTHER_ROOT)], id="same_salt"),
        pytest.param([(SALT, ROOT), (SALT + 1, OTHER_ROOT)], id="other_salt"),
    ],
)
def test_writes_in_one_slot(
    state_test: StateTestFiller,
    pre: Alloc,
    writes: List[Tuple[int, int]],
) -> None:
    """
    Write twice from one source address in one slot.

    Writes under the same salt target one storage key, so only the last
    root remains recorded; writes under different salts are different
    root sources and both remain.
    """
    writer = pre.deploy_contract(code=writes_code(writes))
    storage: Dict[Hash, Hash] = {}
    for salt, root in writes:
        source = source_id(writer, salt)
        storage[storage_key(source, CURRENT_SLOT)] = entry_hash(
            source, CURRENT_SLOT, root
        )
    assert len(storage) == len({salt for salt, _ in writes})

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(sender=pre.fund_eoa(), to=writer),
        post={Spec.RECENT_ROOT_ADDRESS: Account(storage=storage)},
    )


def test_write_overwrites_expired_entry(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    Overwrite an entry `Spec.RECENT_ROOT_LENGTH` slots old.

    A write uses the ring buffer index of the current slot, which the
    entry written exactly one ring length earlier also uses.
    """
    sender = pre.fund_eoa()
    first_slot = 7
    second_slot = first_slot + Spec.RECENT_ROOT_LENGTH
    source = source_id(sender, SALT)
    assert storage_key(source, first_slot) == storage_key(source, second_slot)

    blocks = [
        Block(
            slot_number=slot,
            txs=[
                Transaction(
                    sender=sender,
                    to=Spec.RECENT_ROOT_ADDRESS,
                    data=write_data(SALT, root),
                )
            ],
        )
        for slot, root in [(first_slot, ROOT), (second_slot, OTHER_ROOT)]
    ]
    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            Spec.RECENT_ROOT_ADDRESS: Account(
                storage={
                    storage_key(source, second_slot): entry_hash(
                        source, second_slot, OTHER_ROOT
                    )
                }
            )
        },
    )


@pytest.mark.parametrize(
    "operation",
    [pytest.param("write"), pytest.param("validation")],
)
@pytest.mark.pre_alloc_mutable
def test_call_with_value_reverts(
    state_test: StateTestFiller,
    pre: Alloc,
    operation: str,
) -> None:
    """Revert a call carrying value, whatever the operation."""
    probe = call_probe(pre, Op.CALL, value=1)
    stored = RecentRoot(source_id(probe, SALT), CURRENT_SLOT - 1, ROOT)
    pre[Spec.RECENT_ROOT_ADDRESS] = Account(
        nonce=1, code=Spec.RECENT_ROOT_CODE, storage=recorded(stored)
    )
    if operation == "write":
        data = write_data(SALT, OTHER_ROOT)
    elif operation == "validation":
        data = validation_data(stored)
    else:
        raise ValueError(operation)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(sender=pre.fund_eoa(), to=probe, data=data),
        post={
            probe: Account(balance=1, storage=probe_result(success=False)),
            Spec.RECENT_ROOT_ADDRESS: Account(
                balance=0, storage=recorded(stored)
            ),
        },
    )


def test_write_in_static_context_fails(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """Fail a write under `STATICCALL`, leaving storage unchanged."""
    probe = call_probe(pre, Op.STATICCALL)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=pre.fund_eoa(), to=probe, data=write_data(SALT, ROOT)
        ),
        post={
            probe: Account(storage=probe_result(success=False)),
            Spec.RECENT_ROOT_ADDRESS: Account(storage={}),
        },
    )


@pytest.mark.parametrize("call_opcode", [Op.DELEGATECALL, Op.CALLCODE])
def test_write_in_caller_storage_context(
    state_test: StateTestFiller,
    pre: Alloc,
    call_opcode: Op,
) -> None:
    """
    Write into the invoking account's storage under `DELEGATECALL` and
    `CALLCODE`, never into recent root storage.
    """
    sender = pre.fund_eoa()
    probe = call_probe(pre, call_opcode)
    # The code sees the probe's caller as `msg.sender` under DELEGATECALL
    # and the probe itself under CALLCODE.
    source_address = sender if call_opcode == Op.DELEGATECALL else probe
    source = source_id(source_address, SALT)
    storage = probe_result(success=True) | {
        storage_key(source, CURRENT_SLOT): entry_hash(
            source, CURRENT_SLOT, ROOT
        )
    }

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(sender=sender, to=probe, data=write_data(SALT, ROOT)),
        post={
            probe: Account(storage=storage),
            Spec.RECENT_ROOT_ADDRESS: Account(storage={}),
        },
    )


def test_write_through_delegated_account(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """
    Write into the delegating account's storage when an EIP-7702 account
    delegates to the recent root contract.
    """
    sender = pre.fund_eoa()
    authority = pre.fund_eoa(amount=0)
    source = source_id(sender, SALT)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            to=authority,
            data=write_data(SALT, ROOT),
            authorization_list=[
                AuthorizationTuple(
                    address=Spec.RECENT_ROOT_ADDRESS, nonce=0, signer=authority
                )
            ],
        ),
        post={
            authority: Account(
                storage={
                    storage_key(source, CURRENT_SLOT): entry_hash(
                        source, CURRENT_SLOT, ROOT
                    )
                }
            ),
            Spec.RECENT_ROOT_ADDRESS: Account(storage={}),
        },
    )


SOURCE = source_id(Address(0x5005), SALT)
PREVIOUS = RecentRoot(SOURCE, CURRENT_SLOT - 1, ROOT)
OLDEST = RecentRoot(
    SOURCE, CURRENT_SLOT - Spec.RECENT_ROOT_USABLE_WINDOW, ROOT
)
EXPIRED = RecentRoot(SOURCE, CURRENT_SLOT - Spec.RECENT_ROOT_LENGTH, ROOT)
CURRENT = RecentRoot(SOURCE, CURRENT_SLOT, ROOT)
FUTURE = RecentRoot(SOURCE, CURRENT_SLOT + 1, ROOT)
DISTINCT = [
    RecentRoot(source_id(Address(0x5005), salt), CURRENT_SLOT - salt, salt)
    for salt in range(1, Spec.MAX_RECENT_ROOT_REFERENCES + 1)
]


@pytest.mark.parametrize(
    "stored,data,success",
    [
        pytest.param([PREVIOUS], validation_data(PREVIOUS), True, id="one"),
        pytest.param(
            DISTINCT, validation_data(*DISTINCT), True, id="sixteen_distinct"
        ),
        pytest.param(
            [PREVIOUS],
            validation_data(PREVIOUS, PREVIOUS),
            True,
            id="duplicate",
        ),
        pytest.param([OLDEST], validation_data(OLDEST), True, id="age_8191"),
        pytest.param(
            [EXPIRED], validation_data(EXPIRED), False, id="age_8192"
        ),
        pytest.param([CURRENT], validation_data(CURRENT), False, id="current"),
        pytest.param([FUTURE], validation_data(FUTURE), False, id="future"),
        pytest.param(
            [PREVIOUS],
            validation_data(RecentRoot(SOURCE, PREVIOUS.slot, OTHER_ROOT)),
            False,
            id="wrong_root",
        ),
        pytest.param(
            [PREVIOUS],
            validation_data(
                RecentRoot(
                    source_id(Address(0x5005), SALT + 1), PREVIOUS.slot, ROOT
                )
            ),
            False,
            id="wrong_source",
        ),
        pytest.param(
            [PREVIOUS],
            validation_data(RecentRoot(SOURCE, PREVIOUS.slot - 1, ROOT)),
            False,
            id="wrong_slot",
        ),
        pytest.param(
            DISTINCT[:-1],
            validation_data(*DISTINCT),
            False,
            id="last_of_sixteen_missing",
        ),
        pytest.param([PREVIOUS], Bytes(b""), False, id="empty"),
        pytest.param(
            [PREVIOUS],
            validation_data(PREVIOUS)[:-1],
            False,
            id="length_71",
        ),
        pytest.param(
            [PREVIOUS],
            validation_data(PREVIOUS) + b"\x00",
            False,
            id="length_73",
        ),
        pytest.param(
            [PREVIOUS],
            validation_data(
                *[PREVIOUS] * (Spec.MAX_RECENT_ROOT_REFERENCES + 1)
            ),
            False,
            id="seventeen",
        ),
        pytest.param(
            [PREVIOUS],
            write_data(SALT, ROOT)[:-1],
            False,
            id="length_63",
        ),
        pytest.param(
            [PREVIOUS],
            write_data(SALT, ROOT) + b"\x00",
            False,
            id="length_65",
        ),
    ],
)
@pytest.mark.pre_alloc_mutable
def test_validation(
    state_test: StateTestFiller,
    pre: Alloc,
    stored: List[RecentRoot],
    data: Bytes,
    success: bool,
) -> None:
    """
    Validate recent root tuples against recorded entries.

    Each failing case records the entry its tuple would otherwise match
    and breaks exactly one rule: the tuple's age, its fields, or the
    calldata length. A validation returns no data and changes no state.
    """
    probe = call_probe(pre, Op.CALL)
    pre[Spec.RECENT_ROOT_ADDRESS] = Account(
        nonce=1, code=Spec.RECENT_ROOT_CODE, storage=recorded(*stored)
    )

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(sender=pre.fund_eoa(), to=probe, data=data),
        post={
            probe: Account(storage=probe_result(success=success)),
            Spec.RECENT_ROOT_ADDRESS: Account(
                nonce=1, storage=recorded(*stored)
            ),
        },
    )


@pytest.mark.pre_alloc_mutable
def test_reference_vector(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """Validate the EIP's reference vector at its current slot."""
    source = Hash(
        "0xb9382d35273c75a50631a3e84d3c75ec9266e2b18c35a627e16cdbf26a18ca85"
    )
    entry = Hash(
        "0x0a0d1254c851be5a133b4c9a9e300f5602fc0f43dbe65aa6a66930d4ca0a51b8"
    )
    key = Hash(
        "0x5f027aa1cbe2df279bf6518edd4b44ea5409fd800189ec35224e10ab05e574c3"
    )
    data = Bytes(
        "0xb9382d35273c75a50631a3e84d3c75ec9266e2b18c35a627e16cdbf26a18ca85"
        "0000000000000001"
        "0000000000000000000000000000000000000000000000000000000000000002"
    )
    vector = RecentRoot(source_id(Address(1), 0), 1, 2)
    assert (vector.source, vector.entry_hash, vector.storage_key) == (
        source,
        entry,
        key,
    )
    assert validation_data(vector) == data

    probe = call_probe(pre, Op.CALL)
    pre[Spec.RECENT_ROOT_ADDRESS] = Account(
        nonce=1, code=Spec.RECENT_ROOT_CODE, storage={key: entry}
    )

    state_test(
        env=Environment(slot_number=2),
        pre=pre,
        tx=Transaction(sender=pre.fund_eoa(), to=probe, data=data),
        post={probe: Account(storage=probe_result(success=True))},
    )
