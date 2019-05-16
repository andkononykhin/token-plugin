import json

import pytest

from common.serializers.serialization import state_roots_serializer
from plenum.common.constants import NYM, PROOF_NODES, ROOT_HASH, STATE_PROOF
from plenum.common.exceptions import (RequestNackedException,
                                      RequestRejectedException)
from plenum.common.txn_util import get_seq_no
from sovtoken.constants import OUTPUTS, XFER_PUBLIC, ADDRESS, SEQNO, AMOUNT, MINT_PUBLIC, PAYMENT_ADDRESS
from sovtoken.test.helper import decode_proof
from sovtokenfees.constants import FEES, FEE
from sovtokenfees.static_fee_req_handler import StaticFeesReqHandler
from state.db.persistent_db import PersistentDB
from state.trie.pruning_trie import Trie, rlp_encode
from storage.kv_in_memory import KeyValueStorageInMemory


def test_get_fees_when_no_fees_set(helpers):
    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == {}
    helpers.node.assert_set_fees_in_memory({})


def test_set_fees_invalid_numeric(helpers):
    """
    Test set fees with invalid numeric amount.
    """
    def _test_invalid_fees(amount):
        fees = {
            NYM: amount,
            XFER_PUBLIC: 5
        }

        with pytest.raises(RequestNackedException):
            helpers.inner.general.do_set_fees(fees)

        ledger_fees = helpers.general.do_get_fees()[FEES]
        assert ledger_fees == {}
        helpers.node.assert_set_fees_in_memory({})

    _test_invalid_fees(-1)
    _test_invalid_fees(5.5)
    _test_invalid_fees("3")
    _test_invalid_fees(None)


def test_fees_can_be_zero(helpers):
    """
    Fees can be set to zero.
    """
    fees = {NYM: 1}
    helpers.general.do_set_fees(fees)

    with pytest.raises(RequestRejectedException):
        result = helpers.general.do_nym()

    fees = {NYM: 0}
    helpers.general.do_set_fees(fees)

    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert fees == ledger_fees
    helpers.node.assert_set_fees_in_memory(fees)

    helpers.general.do_nym()


def test_non_trustee_set_fees(helpers):
    """
    Only trustees can change the sovtokenfees
    """
    fees = {
        NYM: 1,
        XFER_PUBLIC: 2
    }
    fees_request = helpers.request.set_fees(fees)
    fees_request.signatures = None
    fees_request._identifier = helpers.wallet._stewards[0]
    fees_request = helpers.wallet.sign_request_stewards(json.dumps(fees_request.as_dict))
    with pytest.raises(RequestRejectedException):
        helpers.sdk.sdk_send_and_check([fees_request])
    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == {}


def test_set_fees_not_enough_trustees(helpers):
    """
    Setting fees requires at least three trustees
    """
    fees = {
        NYM: 1,
        XFER_PUBLIC: 2
    }
    fees_request = helpers.request.set_fees(fees)
    for idr in dict(fees_request.signatures).keys():
        if idr != fees_request.identifier:
            fees_request.signatures.pop(idr)
            break
    assert len(fees_request.signatures) == 2

    with pytest.raises(RequestRejectedException):
        helpers.sdk.send_and_check_request_objects([fees_request])

    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == {}


def test_set_fees_with_stewards(helpers):
    """
    Setting fees fails with stewards.
    """
    fees = {NYM: 1}
    fees_request = helpers.request.set_fees(fees)
    fees_request.signatures.popitem()
    assert len(fees_request.signatures) == 2

    fees_request = helpers.wallet.sign_request_stewards(
        json.dumps(fees_request.as_dict),
        number_signers=1
    )
    assert len(json.loads(fees_request)["signatures"]) == 3

    with pytest.raises(RequestRejectedException):
        helpers.sdk.sdk_send_and_check([fees_request])

    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == {}
    helpers.node.assert_set_fees_in_memory({})


def test_set_fees_update(helpers):

    fees_1 = {NYM: 1, XFER_PUBLIC: 2}
    helpers.general.do_set_fees(fees_1)

    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == fees_1

    # Update XFER_PUBLIC fees

    fees_2 = {XFER_PUBLIC: 42}
    helpers.general.do_set_fees(fees_2)

    # Check, that XFER_PUBLIC fee was updated
    ledger_fee = helpers.general.do_get_fee(XFER_PUBLIC)[FEE]
    assert ledger_fee == fees_2.get(XFER_PUBLIC)

    # Check, that NYM fee was not affected
    ledger_fee = helpers.general.do_get_fee(NYM)[FEE]
    assert ledger_fee == fees_1.get(NYM)

    # Check, that all fees was updated and not rewritten
    ledger_fees = helpers.general.do_get_fees()[FEES]
    result_fees = fees_1
    result_fees.update(fees_2)
    assert result_fees == ledger_fees



def test_trustee_set_valid_fees(helpers, fees_set, fees):
    """
    Set a valid sovtokenfees
    """
    helpers.node.assert_set_fees_in_memory(fees)


def test_get_fees(helpers, fees_set, fees):
    """
    Get the sovtokenfees from the ledger
    """
    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == fees


def test_get_fee(helpers):
    """
    Get the sovtokenfee from the ledger by alias
    """
    alias = NYM
    fee = 5
    helpers.general.do_set_fees({alias: fee})
    resp = helpers.general.do_get_fee(alias)
    assert resp.get(STATE_PROOF, False)
    assert fee == resp[FEE]


def test_get_fee_with_unknown_alias(helpers, fees):
    """
    Get the sovtokenfee from the ledger by unknown alias
    """
    alias = "test_alias"
    helpers.general.do_set_fees({NYM: 5})
    resp = helpers.general.do_get_fee(alias)
    assert resp[FEE] is None
    assert resp[STATE_PROOF]


def test_change_fees(helpers, fees_set, fees):
    """
    Change the sovtokenfees on the ledger and check that sovtokenfees has
    changed.
    """
    updated_fees = {**fees, NYM: 10}
    helpers.general.do_set_fees(updated_fees)
    ledger_fees = helpers.general.do_get_fees()[FEES]
    assert ledger_fees == updated_fees
    assert ledger_fees != fees
    helpers.node.assert_set_fees_in_memory(updated_fees)


def test_get_fees_with_proof(helpers, fees_set, fees):
    """
    Get the sovtokenfees from the ledger
    """
    result = helpers.general.do_get_fees()
    fees = result[FEES]
    state_proof = result[STATE_PROOF]
    assert state_proof
    proof_nodes = decode_proof(result[STATE_PROOF][PROOF_NODES])
    client_trie = Trie(PersistentDB(KeyValueStorageInMemory()))
    fees = rlp_encode([StaticFeesReqHandler.state_serializer.serialize(fees)])
    assert client_trie.verify_spv_proof(
        state_roots_serializer.deserialize(result[STATE_PROOF][ROOT_HASH]),
        StaticFeesReqHandler.build_path_for_set_fees(), fees, proof_nodes)


def test_mint_after_set_fees(helpers, fees_set):
    # Try another minting after setting fees
    address = helpers.wallet.create_address()
    outputs = [{ADDRESS: address, AMOUNT: 60}]
    mint_req = helpers.general.do_mint(outputs)
    utxos = helpers.general.get_utxo_addresses([address])[0]
    assert utxos[0][PAYMENT_ADDRESS] == address
    assert utxos[0][AMOUNT] == 60
