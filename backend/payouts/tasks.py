from celery import shared_task

from .services import due_processing_payout_ids, pending_payout_ids, process_payout_once


@shared_task
def process_payout(payout_id: str) -> dict:
    return process_payout_once(payout_id)


@shared_task
def enqueue_due_payouts(limit: int = 50) -> dict:
    payout_ids = pending_payout_ids(limit=limit) + due_processing_payout_ids(limit=limit)
    for payout_id in payout_ids:
        process_payout.delay(payout_id)
    return {"enqueued": len(payout_ids), "payout_ids": payout_ids}
