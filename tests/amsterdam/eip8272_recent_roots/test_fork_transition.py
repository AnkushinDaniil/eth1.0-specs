"""
Tests for the EIP-8272 fork transition.

The first block in which recent roots are active installs
`Spec.RECENT_ROOT_CODE` at `Spec.RECENT_ROOT_ADDRESS` against its parent
state, before any transaction. An account already there keeps its balance
and has its nonce raised to at least one; an account that already holds code
or storage makes that block invalid. Later blocks do not repeat the install.

As with the EIP-8141 transition tests, the transition tool runs every block
of these fixtures with the fork's rules, the pre-fork blocks included, so
the pre-fork blocks contain only transfers and an `EXTCODESIZE` probe. The
install itself is applied by the filler, which cannot detect an invalid
install: the invalid activation cases skip the transition tool's exception
check and rely on clients rejecting the block.
"""

from typing import Dict

import pytest
from execution_testing import (
    Account,
    Alloc,
    Block,
    BlockchainTestFiller,
    BlockException,
    Op,
    Transaction,
)

from ..eip8141_frame_transactions.helpers import sender_frame, verify_frame
from .helpers import recent_root_frame
from .spec import RecentRoot, Spec, ref_spec_8272, source_id, write_data

REFERENCE_SPEC_GIT_PATH = ref_spec_8272.git_path
REFERENCE_SPEC_VERSION = ref_spec_8272.version

pytestmark = pytest.mark.valid_at_transition_to("Bogota")

FORK_TIMESTAMP = 15_000
"""Timestamp at which the transition fork activates EIP-8272."""

SALT = 0x5A17
ROOT = 0x2007
OTHER_ROOT = 0x2008

SLOT_EXECUTED = 0x01
"""Storage slot the sender frame's target writes when it executes."""


@pytest.mark.pre_alloc_mutable
@pytest.mark.parametrize(
    "pre_fork_nonce,pre_fork_balance",
    [
        pytest.param(None, None, id="absent_before_fork"),
        pytest.param(0, 1, id="balance_before_fork"),
        pytest.param(7, 1, id="nonce_and_balance_before_fork"),
    ],
)
@pytest.mark.parametrize(
    "transfer_before_fork",
    [
        pytest.param(False, id="no_transfer"),
        pytest.param(True, id="transfer_before_fork"),
    ],
)
def test_recent_root_contract_installed_at_fork_transition(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    pre_fork_nonce: int | None,
    pre_fork_balance: int | None,
    transfer_before_fork: bool,
) -> None:
    """
    Install the recent root contract at the fork block.

    A probe records `EXTCODESIZE` of the contract address keyed by block
    number: no code before the fork block, the code from it on. The
    account keeps its balance, including one received by a pre-fork
    transfer, and ends with a nonce of at least one and no storage.
    """
    sender = pre.fund_eoa()
    probe = pre.deploy_contract(
        Op.SSTORE(Op.NUMBER, Op.EXTCODESIZE(Spec.RECENT_ROOT_ADDRESS))
        + Op.STOP
    )
    if pre_fork_nonce is not None and pre_fork_balance is not None:
        pre[Spec.RECENT_ROOT_ADDRESS] = Account(
            nonce=pre_fork_nonce, balance=pre_fork_balance
        )

    blocks = [
        Block(
            timestamp=FORK_TIMESTAMP - 1,
            txs=[
                Transaction(sender=sender, to=probe),
                Transaction(
                    sender=sender,
                    to=Spec.RECENT_ROOT_ADDRESS,
                    value=1 if transfer_before_fork else 0,
                ),
            ],
        ),
        Block(
            timestamp=FORK_TIMESTAMP,
            txs=[Transaction(sender=sender, to=probe)],
        ),
        Block(
            timestamp=FORK_TIMESTAMP + 1,
            txs=[Transaction(sender=sender, to=probe)],
        ),
    ]

    code_size = len(Spec.RECENT_ROOT_CODE)
    post = {
        probe: Account(storage={1: 0, 2: code_size, 3: code_size}),
        Spec.RECENT_ROOT_ADDRESS: Account(
            nonce=max(pre_fork_nonce or 0, 1),
            balance=(pre_fork_balance or 0)
            + (1 if transfer_before_fork else 0),
            code=Spec.RECENT_ROOT_CODE,
            storage={},
        ),
    }

    blockchain_test(pre=pre, blocks=blocks, post=post)


def test_write_and_verify_after_activation(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
) -> None:
    """
    Write roots from the fork block on and verify them in later blocks.

    Blocks after the fork block run no second initialization: the roots
    written so far stay recorded and the account's nonce is unchanged.
    """
    writer = pre.fund_eoa()
    sender = pre.fund_eoa()
    target = pre.deploy_contract(
        Op.SSTORE(SLOT_EXECUTED, Op.ADD(1, Op.SLOAD(SLOT_EXECUTED))) + Op.STOP
    )
    source = source_id(writer, SALT)
    fork_slot = 20
    first = RecentRoot(source, fork_slot, ROOT)
    second = RecentRoot(source, fork_slot + 1, OTHER_ROOT)

    def write(root: int) -> Transaction:
        return Transaction(
            sender=writer,
            to=Spec.RECENT_ROOT_ADDRESS,
            data=write_data(SALT, root),
        )

    def verify(root: RecentRoot) -> Transaction:
        return Transaction(
            sender=sender,
            frames=[
                recent_root_frame(root),
                verify_frame(),
                sender_frame(target=target),
            ],
        )

    blocks = [
        Block(timestamp=FORK_TIMESTAMP - 1, slot_number=fork_slot - 1),
        Block(
            timestamp=FORK_TIMESTAMP,
            slot_number=fork_slot,
            txs=[write(ROOT)],
        ),
        Block(
            timestamp=FORK_TIMESTAMP + 1,
            slot_number=fork_slot + 1,
            txs=[verify(first), write(OTHER_ROOT)],
        ),
        Block(
            timestamp=FORK_TIMESTAMP + 2,
            slot_number=fork_slot + 2,
            txs=[verify(first), verify(second)],
        ),
    ]

    storage = {root.storage_key: root.entry_hash for root in (first, second)}
    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            target: Account(storage={SLOT_EXECUTED: 3}),
            Spec.RECENT_ROOT_ADDRESS: Account(
                nonce=1, code=Spec.RECENT_ROOT_CODE, storage=storage
            ),
        },
    )


@pytest.mark.exception_test
@pytest.mark.pre_alloc_mutable
@pytest.mark.parametrize(
    "code,storage",
    [
        pytest.param(Op.STOP, {}, id="code"),
        pytest.param(Spec.RECENT_ROOT_CODE, {}, id="recent_root_code"),
        pytest.param(b"", {1: 1}, id="storage"),
        pytest.param(Op.STOP, {1: 1}, id="code_and_storage"),
    ],
)
def test_activation_over_code_or_storage_is_invalid(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    code: bytes,
    storage: Dict[int, int],
) -> None:
    """
    Reject the fork block when the recent root address already holds code
    or storage, even when that code is `Spec.RECENT_ROOT_CODE`.
    """
    pre[Spec.RECENT_ROOT_ADDRESS] = Account(
        nonce=1, balance=1, code=code, storage=storage
    )
    blocks = [
        Block(timestamp=FORK_TIMESTAMP - 1),
        Block(
            timestamp=FORK_TIMESTAMP,
            exception=BlockException.SYSTEM_CONTRACT_ADDRESS_NOT_EMPTY,
            # The filler, not the transition tool, performs the install.
            skip_exception_verification=True,
        ),
    ]

    blockchain_test(
        pre=pre,
        blocks=blocks,
        post={
            Spec.RECENT_ROOT_ADDRESS: Account(
                nonce=1, balance=1, code=code, storage=storage
            )
        },
    )
