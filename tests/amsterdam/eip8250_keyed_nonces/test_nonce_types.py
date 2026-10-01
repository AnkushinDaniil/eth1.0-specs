"""
Nonce type tests for
[EIP-8250: Keyed Nonces for Frame Transactions](https://eips.ethereum.org/EIPS/eip-8250).

The most significant byte of a nonce key is its type. A general key
advances like an account nonce. A binary key is single use: it admits
only `nonce_seq == 0` while unused and ends at one. The type is part of
the key, so the same low 31 bytes under each type select different
slots.
"""

from typing import Dict, List

import pytest
from execution_testing import (
    Alloc,
    StateTestFiller,
    Transaction,
    TransactionException,
)

from ..eip8141_frame_transactions.helpers import verify_frame
from .helpers import (
    BINARY_KEY,
    KEY_A,
    KEY_B,
    keyed_storage,
    nonce_manager,
    set_keyed_nonces,
    typed_key,
)
from .spec import Spec, ref_spec_8250

REFERENCE_SPEC_GIT_PATH = ref_spec_8250.git_path
REFERENCE_SPEC_VERSION = ref_spec_8250.version

pytestmark = pytest.mark.valid_from("Bogota")

GENERAL_TWIN = typed_key(Spec.NONCE_TYPE_GENERAL, KEY_A)
"""The general key over the same low 31 bytes as `BINARY_KEY`."""


@pytest.mark.pre_alloc_mutable
@pytest.mark.parametrize(
    "stored,nonce_keys",
    [
        pytest.param({}, [BINARY_KEY], id="unused_binary_key"),
        pytest.param({}, [KEY_B, BINARY_KEY], id="with_unused_general_key"),
        pytest.param({GENERAL_TWIN: 4}, [BINARY_KEY], id="general_twin_used"),
    ],
)
def test_binary_key_consumed(
    state_test: StateTestFiller,
    pre: Alloc,
    stored: Dict[int, int],
    nonce_keys: List[int],
) -> None:
    """
    Consume an unused binary key at `nonce_seq == 0`, leaving its slot
    at one.

    `general_twin_used` shows that a used general key over the same low
    31 bytes does not spend the binary key.
    """
    sender = pre.fund_eoa()
    if stored:
        set_keyed_nonces(pre, [(sender, stored)])

    tx = Transaction(
        sender=sender,
        nonce=0,
        nonce_keys=nonce_keys,
        frames=[
            verify_frame(
                state_gas_limit=len(nonce_keys)
                * Spec.KEYED_NONCE_FIRST_USE_STATE_GAS
            )
        ],
    )

    state_test(
        pre=pre,
        tx=tx,
        post={
            Spec.NONCE_MANAGER: nonce_manager(
                keyed_storage(sender, stored | dict.fromkeys(nonce_keys, 1))
            ),
        },
    )


@pytest.mark.exception_test
@pytest.mark.pre_alloc_mutable
@pytest.mark.parametrize(
    "stored,nonce_keys,nonce_seq,error",
    [
        pytest.param(
            {BINARY_KEY: 1},
            [BINARY_KEY],
            0,
            TransactionException.NONCE_MISMATCH_TOO_LOW,
            id="spent",
        ),
        pytest.param(
            {BINARY_KEY: 1},
            [BINARY_KEY],
            1,
            TransactionException.NONCE_MISMATCH_TOO_HIGH,
            id="spent_at_its_stored_seq",
        ),
        pytest.param(
            {},
            [BINARY_KEY],
            1,
            TransactionException.NONCE_MISMATCH_TOO_HIGH,
            id="unused_nonzero_seq",
        ),
        pytest.param(
            {KEY_A: 3},
            [KEY_A, BINARY_KEY],
            3,
            TransactionException.NONCE_MISMATCH_TOO_HIGH,
            id="with_used_general_key",
        ),
    ],
)
def test_binary_key_rejected(
    state_test: StateTestFiller,
    pre: Alloc,
    stored: Dict[int, int],
    nonce_keys: List[int],
    nonce_seq: int,
    error: TransactionException,
) -> None:
    """
    Reject a binary key that is spent or selected at a non-zero
    `nonce_seq`.

    `spent_at_its_stored_seq` holds one and selects one, which a general
    key would accept. `with_used_general_key` can never be valid: the
    shared `nonce_seq` must match the used general key and be zero for
    the binary key.
    """
    sender = pre.fund_eoa()
    set_keyed_nonces(pre, [(sender, stored)])

    tx = Transaction(
        sender=sender,
        nonce=nonce_seq,
        nonce_keys=nonce_keys,
        frames=[verify_frame()],
        error=error,
    )

    state_test(
        pre=pre,
        tx=tx,
        post={
            Spec.NONCE_MANAGER: nonce_manager(keyed_storage(sender, stored)),
        },
    )
