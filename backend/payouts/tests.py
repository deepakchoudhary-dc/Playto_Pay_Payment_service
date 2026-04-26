import threading
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection
from django.db.utils import IntegrityError
from django.test import TransactionTestCase, skipUnlessDBFeature

from . import tasks
from .models import BankAccount, InvalidPayoutTransition, LedgerEntry, Merchant, Payout
from .services import (
    IDEMPOTENCY_UNIQUE_CONSTRAINT,
    _is_idempotency_key_collision,
    complete_payout,
    create_payout_with_idempotency,
    ledger_balances_for_merchant,
    process_payout_once,
    reconcile_materialized_balances,
)


class PayoutEngineTests(TransactionTestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(name="Test Merchant", email="merchant@example.com")
        self.bank_account = BankAccount.objects.create(
            merchant=self.merchant,
            beneficiary_name="Test Merchant",
            bank_name="HDFC Bank",
            masked_account_number="XXXXXX1234",
            ifsc="HDFC0000001",
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount_paise=10_000,
            direction=LedgerEntry.Direction.CREDIT,
            bucket=LedgerEntry.Bucket.AVAILABLE,
            reason=LedgerEntry.Reason.CUSTOMER_PAYMENT,
            description="Seed credit",
        )
        reconcile_materialized_balances(self.merchant.id)

    def _request_payout(self, amount_paise: int, key=None):
        key = key or uuid4()
        payload = {"amount_paise": amount_paise, "bank_account_id": str(self.bank_account.id)}
        return create_payout_with_idempotency(
            merchant_id=self.merchant.id,
            amount_paise=amount_paise,
            bank_account_id=self.bank_account.id,
            idempotency_key=key,
            request_payload=payload,
        )

    def test_payout_request_holds_funds_and_idempotent_replay(self):
        key = uuid4()
        body, status_code, replayed = self._request_payout(6_000, key=key)
        replay_body, replay_status, replayed_again = self._request_payout(6_000, key=key)

        self.assertEqual(status_code, 201)
        self.assertFalse(replayed)
        self.assertTrue(replayed_again)
        self.assertEqual(replay_status, 201)
        self.assertEqual(replay_body, body)
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 4_000,
                "held_balance_paise": 6_000,
                "total_balance_paise": 10_000,
            },
        )

    def test_invalid_amount_returns_validation_response_not_false_idempotency_collision(self):
        key = uuid4()
        body, status_code, replayed = self._request_payout(-50, key=key)
        replay_body, replay_status, replayed_again = self._request_payout(-50, key=key)

        self.assertEqual(status_code, 400)
        self.assertFalse(replayed)
        self.assertEqual(body, {"detail": "amount_paise must be greater than 0."})
        self.assertEqual(replay_status, 400)
        self.assertTrue(replayed_again)
        self.assertEqual(replay_body, body)
        self.assertEqual(Payout.objects.count(), 0)

    def test_non_idempotency_integrity_error_is_not_replayed_as_idempotency_collision(self):
        with patch(
            "payouts.services._create_payout_after_idempotency_record",
            side_effect=IntegrityError("payout_amount_positive"),
        ):
            with self.assertRaisesMessage(IntegrityError, "payout_amount_positive"):
                self._request_payout(6_000)

    def test_idempotency_collision_uses_structured_sqlstate_and_constraint(self):
        exc = self._integrity_error_from(sqlstate="23505", constraint_name=IDEMPOTENCY_UNIQUE_CONSTRAINT)

        self.assertTrue(_is_idempotency_key_collision(exc))

    def test_idempotency_collision_rejects_message_only_matches(self):
        exc = IntegrityError(f"duplicate key violates {IDEMPOTENCY_UNIQUE_CONSTRAINT}")

        self.assertFalse(_is_idempotency_key_collision(exc))

    def test_idempotency_collision_rejects_other_unique_constraints(self):
        exc = self._integrity_error_from(sqlstate="23505", constraint_name="some_other_unique_constraint")

        self.assertFalse(_is_idempotency_key_collision(exc))

    def test_payout_request_uses_materialized_balance_without_ledger_aggregate(self):
        with patch(
            "payouts.services.ledger_balances_for_merchant",
            side_effect=AssertionError("hot path should not aggregate ledger balances"),
        ):
            body, status_code, _ = self._request_payout(6_000)

        self.assertEqual(status_code, 201)
        self.assertEqual(body["amount_paise"], 6_000)

    def test_payout_request_self_heals_stale_low_materialized_balance(self):
        Merchant.objects.filter(pk=self.merchant.pk).update(available_balance_paise=5_000)

        body, status_code, replayed = self._request_payout(8_000)
        self.merchant.refresh_from_db()

        self.assertEqual(status_code, 201)
        self.assertFalse(replayed)
        self.assertEqual(body["amount_paise"], 8_000)
        self.assertEqual(self.merchant.available_balance_paise, 2_000)
        self.assertEqual(self.merchant.held_balance_paise, 8_000)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 2_000,
                "held_balance_paise": 8_000,
                "total_balance_paise": 10_000,
            },
        )

    def test_payout_request_reconciles_before_insufficient_balance_response(self):
        Merchant.objects.filter(pk=self.merchant.pk).update(available_balance_paise=5_000)

        body, status_code, replayed = self._request_payout(12_000)
        self.merchant.refresh_from_db()

        self.assertEqual(status_code, 400)
        self.assertFalse(replayed)
        self.assertEqual(body["detail"], "Insufficient available balance.")
        self.assertEqual(body["available_balance_paise"], 10_000)
        self.assertEqual(self.merchant.available_balance_paise, 10_000)
        self.assertEqual(self.merchant.held_balance_paise, 0)
        self.assertEqual(Payout.objects.count(), 0)

    def test_payout_request_does_not_overdraw_stale_high_materialized_balance(self):
        Merchant.objects.filter(pk=self.merchant.pk).update(available_balance_paise=20_000)

        body, status_code, replayed = self._request_payout(15_000)
        self.merchant.refresh_from_db()

        self.assertEqual(status_code, 400)
        self.assertFalse(replayed)
        self.assertEqual(body["detail"], "Insufficient available balance.")
        self.assertEqual(body["available_balance_paise"], 10_000)
        self.assertEqual(Payout.objects.count(), 0)
        self.assertEqual(self.merchant.available_balance_paise, 10_000)
        self.assertEqual(self.merchant.held_balance_paise, 0)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 10_000,
                "held_balance_paise": 0,
                "total_balance_paise": 10_000,
            },
        )

    def test_payout_request_skips_aggregate_for_known_current_insufficient_balance(self):
        _, status_code, _ = self._request_payout(8_000)
        self.assertEqual(status_code, 201)

        with patch(
            "payouts.services.ledger_balances_for_merchant",
            side_effect=AssertionError("known-current insufficient balance should not aggregate"),
        ):
            body, status_code, replayed = self._request_payout(8_000)

        self.assertEqual(status_code, 400)
        self.assertFalse(replayed)
        self.assertEqual(body["available_balance_paise"], 2_000)

    def test_completed_payout_settles_held_funds(self):
        body, _, _ = self._request_payout(6_000)
        result = process_payout_once(body["id"], forced_outcome="success")

        self.assertEqual(result["status"], Payout.Status.COMPLETED)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 4_000,
                "held_balance_paise": 0,
                "total_balance_paise": 4_000,
            },
        )

    def test_completed_payout_recovers_from_materialized_held_balance_drift(self):
        body, _, _ = self._request_payout(6_000)
        Merchant.objects.filter(pk=self.merchant.pk).update(held_balance_paise=5_999)

        result = process_payout_once(body["id"], forced_outcome="success")
        self.merchant.refresh_from_db()

        self.assertEqual(result["status"], Payout.Status.COMPLETED)
        self.assertEqual(self.merchant.available_balance_paise, 4_000)
        self.assertEqual(self.merchant.held_balance_paise, 0)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 4_000,
                "held_balance_paise": 0,
                "total_balance_paise": 4_000,
            },
        )

    def test_failed_payout_releases_held_funds(self):
        body, _, _ = self._request_payout(6_000)
        result = process_payout_once(body["id"], forced_outcome="failure")

        self.assertEqual(result["status"], Payout.Status.FAILED)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 10_000,
                "held_balance_paise": 0,
                "total_balance_paise": 10_000,
            },
        )

    def test_failed_payout_recovers_from_materialized_held_balance_drift(self):
        body, _, _ = self._request_payout(6_000)
        Merchant.objects.filter(pk=self.merchant.pk).update(held_balance_paise=5_999)

        result = process_payout_once(body["id"], forced_outcome="failure")
        self.merchant.refresh_from_db()

        self.assertEqual(result["status"], Payout.Status.FAILED)
        self.assertEqual(self.merchant.available_balance_paise, 10_000)
        self.assertEqual(self.merchant.held_balance_paise, 0)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 10_000,
                "held_balance_paise": 0,
                "total_balance_paise": 10_000,
            },
        )

    def test_failed_to_completed_transition_is_blocked(self):
        body, _, _ = self._request_payout(6_000)
        process_payout_once(body["id"], forced_outcome="failure")

        with self.assertRaises(InvalidPayoutTransition):
            complete_payout(body["id"])

    def test_payout_task_does_not_autoretry_all_exceptions(self):
        self.assertEqual(getattr(tasks.process_payout, "autoretry_for", ()), ())

    def _integrity_error_from(self, *, sqlstate: str, constraint_name: str) -> IntegrityError:
        class FakeDiag:
            def __init__(self, name):
                self.constraint_name = name

        class FakeDatabaseError(Exception):
            def __init__(self, code, name):
                super().__init__("database error")
                self.sqlstate = code
                self.diag = FakeDiag(name)

        try:
            raise FakeDatabaseError(sqlstate, constraint_name)
        except FakeDatabaseError as cause:
            try:
                raise IntegrityError("wrapped database error") from cause
            except IntegrityError as exc:
                return exc

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_payouts_do_not_overdraw(self):
        if connection.vendor != "postgresql":
            self.skipTest("This race test requires PostgreSQL row-lock behavior.")

        barrier = threading.Barrier(2)
        lock = threading.Lock()
        results = []

        def worker():
            close_old_connections()
            barrier.wait()
            try:
                _, status_code, _ = self._request_payout(6_000, key=uuid4())
                with lock:
                    results.append(status_code)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), [201, 400])
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(
            ledger_balances_for_merchant(self.merchant.id),
            {
                "available_balance_paise": 4_000,
                "held_balance_paise": 6_000,
                "total_balance_paise": 10_000,
            },
        )
