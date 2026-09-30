"""
Tests for the recent root verifier frame of
[EIP-8272: Recent Roots for Frame Transactions](https://eips.ethereum.org/EIPS/eip-8272).

A recent root verifier frame is a `VERIFY` frame targeting the recent root
contract with one to sixteen tuples as data. In a block it is an ordinary
EIP-8141 `VERIFY` frame: the contract runs as a static call, a revert or an
exceptional halt invalidates the transaction, and the frame is charged
ordinary gas. Its position and uniqueness only matter to the public mempool,
so every placement below is valid in a block.
"""

from typing import List

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    Environment,
    Fork,
    Frame,
    FrameReceipt,
    Op,
    StateTestFiller,
    Transaction,
    TransactionException,
    TransactionReceipt,
)

from ..eip8141_frame_transactions.helpers import (
    expiry_frame,
    sender_frame,
    verify_frame,
)
from ..eip8141_frame_transactions.spec import Spec as FrameSpec
from .helpers import recent_root_frame, validation_gas
from .spec import (
    RecentRoot,
    Spec,
    ref_spec_8272,
    source_id,
    write_data,
)

REFERENCE_SPEC_GIT_PATH = ref_spec_8272.git_path
REFERENCE_SPEC_VERSION = ref_spec_8272.version

pytestmark = pytest.mark.valid_from("Bogota")

CURRENT_SLOT = 10_000
"""Slot of the block executing the frame transaction."""

WRITE_FRAME_GAS = 500_000
"""Execution and state gas budget that covers one fresh recent root write."""

SALT = 0x5A17
ROOT = 0x2007

SLOT_EXECUTED = 0x01
"""Storage slot the sender frame's target writes when it executes."""

SOURCE = source_id(FrameSpec.ENTRY_POINT, SALT)
PREVIOUS = RecentRoot(SOURCE, CURRENT_SLOT - 1, ROOT)
DISTINCT = [
    RecentRoot(source_id(FrameSpec.ENTRY_POINT, salt), CURRENT_SLOT - 1, salt)
    for salt in range(1, Spec.MAX_RECENT_ROOT_REFERENCES + 1)
]


def record(pre: Alloc, *roots: RecentRoot) -> None:
    """Replace the recent root contract with one that records `roots`."""
    pre[Spec.RECENT_ROOT_ADDRESS] = Account(
        nonce=1,
        code=Spec.RECENT_ROOT_CODE,
        storage={root.storage_key: root.entry_hash for root in roots},
    )


def success_receipt(sender: object, frame_count: int) -> TransactionReceipt:
    """Return the receipt of a transaction whose frames all succeed."""
    return TransactionReceipt(
        payer=sender,
        frame_receipts=[
            FrameReceipt(status=FrameSpec.STATUS_SUCCESS)
            for _ in range(frame_count)
        ],
    )


@pytest.mark.parametrize(
    "tuple_root,error",
    [
        pytest.param(PREVIOUS, None, id="recorded"),
        pytest.param(
            RecentRoot(SOURCE, PREVIOUS.slot, ROOT + 1),
            TransactionException.TYPE_6_INVALID_FRAME_EXECUTION,
            id="not_recorded",
            marks=pytest.mark.exception_test,
        ),
    ],
)
@pytest.mark.pre_alloc_mutable
def test_verifier_frame(
    state_test: StateTestFiller,
    pre: Alloc,
    tuple_root: RecentRoot,
    error: TransactionException | None,
) -> None:
    """
    Execute a frame transaction whose first frame verifies a recent root.

    A recorded tuple lets the transaction execute; a tuple that is not
    recorded makes the contract revert, which invalidates the transaction.
    """
    sender = pre.fund_eoa()
    target = pre.deploy_contract(code=Op.SSTORE(SLOT_EXECUTED, 1) + Op.STOP)
    record(pre, PREVIOUS)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=[
                recent_root_frame(tuple_root),
                verify_frame(),
                sender_frame(target=target),
            ],
            error=error,
            expected_receipt=None if error else success_receipt(sender, 3),
        ),
        post={target: Account(storage={SLOT_EXECUTED: 0 if error else 1})},
    )


@pytest.mark.parametrize(
    "age,valid",
    [
        pytest.param(1, True, id="previous_slot"),
        pytest.param(Spec.RECENT_ROOT_USABLE_WINDOW, True, id="age_8191"),
        pytest.param(
            Spec.RECENT_ROOT_LENGTH,
            False,
            id="age_8192",
            marks=pytest.mark.exception_test,
        ),
        pytest.param(
            0, False, id="same_slot", marks=pytest.mark.exception_test
        ),
    ],
)
def test_write_then_verify(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    age: int,
    valid: bool,
) -> None:
    """
    Write a root in one block and verify it in a later one.

    The root becomes referenceable in the slot after its write and stays
    referenceable for `Spec.RECENT_ROOT_USABLE_WINDOW` slots. A root is
    not referenceable in the slot that writes it, even by a later
    transaction of the same block.
    """
    writer = pre.fund_eoa()
    sender = pre.fund_eoa()
    target = pre.deploy_contract(code=Op.SSTORE(SLOT_EXECUTED, 1) + Op.STOP)
    write_slot = 5
    written = RecentRoot(source_id(writer, SALT), write_slot, ROOT)

    write = Transaction(
        sender=writer,
        to=Spec.RECENT_ROOT_ADDRESS,
        data=write_data(SALT, ROOT),
    )
    verify = Transaction(
        sender=sender,
        frames=[
            recent_root_frame(written),
            verify_frame(),
            sender_frame(target=target),
        ],
        error=(
            None
            if valid
            else TransactionException.TYPE_6_INVALID_FRAME_EXECUTION
        ),
        expected_receipt=success_receipt(sender, 3) if valid else None,
    )
    if age == 0:
        blocks = [
            Block(
                slot_number=write_slot,
                txs=[write, verify],
                exception=verify.error,
            )
        ]
    else:
        blocks = [
            Block(slot_number=write_slot, txs=[write]),
            Block(
                slot_number=write_slot + age,
                txs=[verify],
                exception=verify.error,
            ),
        ]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            target: Account(storage={SLOT_EXECUTED: 1} if valid else {}),
        },
    )


@pytest.mark.parametrize(
    "roots",
    [
        pytest.param(DISTINCT[:1], id="one_tuple"),
        pytest.param(DISTINCT, id="sixteen_cold_tuples"),
        pytest.param([DISTINCT[0]] * 2, id="duplicate_tuples"),
    ],
)
@pytest.mark.parametrize(
    "gas_shortfall",
    [
        pytest.param(0, id="exact_gas"),
        pytest.param(1, id="one_gas_short", marks=pytest.mark.exception_test),
    ],
)
@pytest.mark.pre_alloc_mutable
def test_verifier_frame_gas(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
    roots: List[RecentRoot],
    gas_shortfall: int,
) -> None:
    """
    Charge the verifier frame ordinary gas: the cold access of the
    contract at frame entry and the contract's execution, with each
    storage key cold on its first read.

    With exactly that much execution gas the frame succeeds and reports
    it all as used; one gas less halts the frame, invalidating the
    transaction.
    """
    sender = pre.fund_eoa()
    target = pre.deploy_contract(code=Op.SSTORE(SLOT_EXECUTED, 1) + Op.STOP)
    record(pre, *roots)
    frame_gas = validation_gas(fork, roots)
    valid = gas_shortfall == 0

    expected_receipt = None
    if valid:
        expected_receipt = TransactionReceipt(
            payer=sender,
            frame_receipts=[
                FrameReceipt(
                    status=FrameSpec.STATUS_SUCCESS, gas_used=frame_gas
                ),
                FrameReceipt(status=FrameSpec.STATUS_SUCCESS),
                FrameReceipt(status=FrameSpec.STATUS_SUCCESS),
            ],
        )

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=[
                recent_root_frame(*roots, gas_limit=frame_gas - gas_shortfall),
                verify_frame(),
                sender_frame(target=target),
            ],
            error=(
                None
                if valid
                else TransactionException.TYPE_6_INVALID_FRAME_EXECUTION
            ),
            expected_receipt=expected_receipt,
        ),
        post={target: Account(storage={SLOT_EXECUTED: 1 if valid else 0})},
    )


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(
            recent_root_frame(PREVIOUS, flags=FrameSpec.APPROVE_PAYMENT),
            id="flags_1",
        ),
        pytest.param(
            recent_root_frame(PREVIOUS, state_gas_limit=100_000),
            id="nonzero_state_gas_limit",
        ),
    ],
)
@pytest.mark.pre_alloc_mutable
def test_non_canonical_frame_executes(
    state_test: StateTestFiller,
    pre: Alloc,
    frame: Frame,
) -> None:
    """
    Execute a `VERIFY` frame to the contract that differs from the
    canonical verifier frame in a field only the public mempool checks.
    """
    sender = pre.fund_eoa()
    target = pre.deploy_contract(code=Op.SSTORE(SLOT_EXECUTED, 1) + Op.STOP)
    record(pre, PREVIOUS)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=[frame, verify_frame(), sender_frame(target=target)],
            expected_receipt=success_receipt(sender, 3),
        ),
        post={target: Account(storage={SLOT_EXECUTED: 1})},
    )


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(recent_root_frame(PREVIOUS, value=1), id="value"),
        pytest.param(
            recent_root_frame(PREVIOUS, flags=FrameSpec.ATOMIC_BATCH_FLAG),
            id="atomic_batch_flag",
        ),
    ],
)
@pytest.mark.exception_test
@pytest.mark.pre_alloc_mutable
def test_statically_invalid_frame(
    state_test: StateTestFiller,
    pre: Alloc,
    frame: Frame,
) -> None:
    """
    Reject a verifier frame carrying value or the atomic batch flag, both
    statically invalid for a `VERIFY` frame.
    """
    sender = pre.fund_eoa()
    record(pre, PREVIOUS)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=[frame, verify_frame()],
            error=TransactionException.TYPE_6_INVALID_FRAME_FORMAT,
        ),
        post={sender: Account(nonce=0)},
    )


@pytest.mark.parametrize(
    "frames",
    [
        pytest.param(
            lambda target: [
                expiry_frame(),
                recent_root_frame(PREVIOUS),
                verify_frame(),
                sender_frame(target=target),
            ],
            id="after_expiry_verify",
        ),
        pytest.param(
            lambda target: [
                recent_root_frame(PREVIOUS),
                expiry_frame(),
                verify_frame(),
                sender_frame(target=target),
            ],
            id="before_expiry_verify",
        ),
        pytest.param(
            lambda target: [
                recent_root_frame(PREVIOUS),
                recent_root_frame(PREVIOUS),
                verify_frame(),
                sender_frame(target=target),
            ],
            id="twice",
        ),
        pytest.param(
            lambda target: [
                verify_frame(),
                recent_root_frame(PREVIOUS),
                sender_frame(target=target),
            ],
            id="after_account_validation",
        ),
        pytest.param(
            lambda target: [
                verify_frame(),
                sender_frame(target=target),
                recent_root_frame(PREVIOUS),
            ],
            id="last",
        ),
    ],
)
@pytest.mark.pre_alloc_mutable
def test_frame_placement(
    state_test: StateTestFiller,
    pre: Alloc,
    frames: object,
) -> None:
    """
    Execute verifier frames at positions the public mempool rejects.

    Placement and uniqueness are public mempool rules only; in a block
    every verifier frame executes as an ordinary `VERIFY` frame.
    """
    sender = pre.fund_eoa()
    target = pre.deploy_contract(code=Op.SSTORE(SLOT_EXECUTED, 1) + Op.STOP)
    record(pre, PREVIOUS)
    assert callable(frames)
    frame_list = frames(target)

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=frame_list,
            expected_receipt=success_receipt(sender, len(frame_list)),
        ),
        post={target: Account(storage={SLOT_EXECUTED: 1})},
    )


@pytest.mark.exception_test
def test_write_in_verifier_frame_invalidates(
    state_test: StateTestFiller,
    pre: Alloc,
) -> None:
    """
    Invalidate a transaction whose `VERIFY` frame carries a write.

    The frame runs the contract as a static call, so the write halts it
    and recent root storage stays empty.
    """
    sender = pre.fund_eoa()

    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=[
                # Budgets large enough for the write to succeed outside
                # a static context, so only the static rule halts it.
                recent_root_frame(
                    data=write_data(SALT, ROOT),
                    gas_limit=WRITE_FRAME_GAS,
                    state_gas_limit=WRITE_FRAME_GAS,
                ),
                verify_frame(),
            ],
            error=TransactionException.TYPE_6_INVALID_FRAME_EXECUTION,
        ),
        post={Spec.RECENT_ROOT_ADDRESS: Account(storage={})},
    )


@pytest.mark.pre_alloc_mutable
def test_introspect_verifier_frame(
    state_test: StateTestFiller,
    pre: Alloc,
    fork: Fork,
) -> None:
    """
    Read a completed verifier frame and its tuple from a later frame
    through the existing EIP-8141 introspection instructions.
    """
    sender = pre.fund_eoa()
    params = [
        (
            FrameSpec.FRAMEPARAM_TARGET,
            int.from_bytes(Spec.RECENT_ROOT_ADDRESS),
        ),
        (FrameSpec.FRAMEPARAM_MODE, FrameSpec.MODE_VERIFY),
        (FrameSpec.FRAMEPARAM_FLAGS, FrameSpec.APPROVE_NONE),
        (FrameSpec.FRAMEPARAM_STATE_GAS_LIMIT, 0),
        (FrameSpec.FRAMEPARAM_DATA_LENGTH, Spec.RECENT_ROOT_TUPLE_BYTES),
        (FrameSpec.FRAMEPARAM_STATUS, FrameSpec.STATUS_SUCCESS),
    ]
    code = Op.SSTORE(0, Op.FRAMEDATALOAD(0, 0)) + Op.SSTORE(
        1, Op.SHR(192, Op.FRAMEDATALOAD(32, 0))
    )
    code += Op.SSTORE(2, Op.FRAMEDATALOAD(40, 0))
    for index, (param, _) in enumerate(params):
        code += Op.SSTORE(3 + index, Op.FRAMEPARAM(0, param))
    probe = pre.deploy_contract(code=code + Op.STOP)
    record(pre, PREVIOUS)

    storage = {
        0: int.from_bytes(PREVIOUS.source),
        1: PREVIOUS.slot,
        2: PREVIOUS.root,
    } | {3 + index: value for index, (_, value) in enumerate(params)}
    state_test(
        env=Environment(slot_number=CURRENT_SLOT),
        pre=pre,
        tx=Transaction(
            sender=sender,
            frames=[
                recent_root_frame(PREVIOUS),
                verify_frame(),
                # Every slot the probe writes is fresh state.
                sender_frame(
                    target=probe,
                    state_gas_limit=len(storage)
                    * Op.SSTORE(key_warm=False, new_value=1).state_cost(fork),
                ),
            ],
            expected_receipt=success_receipt(sender, 3),
        ),
        post={probe: Account(storage=storage)},
    )
