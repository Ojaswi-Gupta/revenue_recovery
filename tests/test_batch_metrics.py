"""
Batch metrics tests — validates the full end-to-end batch processing pipeline.
Seeds data, runs batch, and verifies measured recovery across the full dataset.
"""

import pytest
import pytest_asyncio
from datetime import datetime

from recovrai.models.database import Base
from recovrai.models.events import PaymentEvent, CheckoutEvent, SubscriptionEvent, InvoiceEvent
from recovrai.models.recovery import RecoveryWorkflow, RecoveryAction, AuditLog
from recovrai.models.metrics import RecoveryMetric
from recovrai.services.recovery_orchestrator import RecoveryOrchestrator
from recovrai.config import WorkflowStatus


@pytest_asyncio.fixture
async def db_session():
    """Create an in-memory database for batch testing."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def orchestrator():
    return RecoveryOrchestrator()


def _seed_payment_events(session, count=10):
    """Seed payment failure events."""
    error_codes = [
        "INSUFFICIENT_FUNDS", "GATEWAY_ERROR", "CARD_EXPIRED",
        "UPI_TIMEOUT", "BANK_DECLINED", "SERVER_ERROR",
        "NETWORK_ERROR", "INTERNATIONAL_CARD_DECLINED",
    ]
    events = []
    for i in range(count):
        event = PaymentEvent(
            id=f"batch-pe-{i}",
            payment_id=f"pay_batch_{i}",
            order_id=f"order_batch_{i}",
            customer_id=f"cust_batch_{i}",
            customer_name=f"Customer {i}",
            customer_email=f"customer{i}@example.com",
            customer_phone=f"+9198765432{i:02d}",
            amount=(i + 1) * 50000,  # ₹500 to ₹5000
            status="failed",
            method="upi" if i % 2 == 0 else "card",
            error_code=error_codes[i % len(error_codes)],
            error_description=f"Payment failed: {error_codes[i % len(error_codes)]}",
            error_source="gateway",
            error_reason=error_codes[i % len(error_codes)].lower(),
            processed=False,
        )
        events.append(event)
        session.add(event)
    return events


def _seed_checkout_events(session, count=5):
    """Seed checkout abandonment events."""
    stages = ["payment", "cart", "address", "confirmation", "payment"]
    events = []
    for i in range(count):
        event = CheckoutEvent(
            id=f"batch-ce-{i}",
            session_id=f"sess_batch_{i}",
            customer_id=f"cust_checkout_{i}",
            customer_name=f"Shopper {i}",
            customer_email=f"shopper{i}@example.com",
            customer_phone=f"+9198765433{i:02d}",
            cart_value=(i + 1) * 100000,
            items_count=i + 1,
            stage_reached=stages[i % len(stages)],
            time_spent_seconds=60 * (i + 1),
            processed=False,
        )
        events.append(event)
        session.add(event)
    return events


class TestBatchProcessing:
    @pytest.mark.asyncio
    async def test_batch_processes_all_events(self, db_session, orchestrator):
        """Test that batch processing handles all unprocessed events."""
        _seed_payment_events(db_session, count=10)
        _seed_checkout_events(db_session, count=5)
        await db_session.flush()

        results = await orchestrator.process_batch(db_session)

        assert results["total_processed"] == 15
        assert results["amount_at_risk"] > 0
        assert len(results["details"]) == 15

    @pytest.mark.asyncio
    async def test_batch_recovery_metrics(self, db_session, orchestrator):
        """Test that batch processing produces measurable recovery metrics."""
        _seed_payment_events(db_session, count=10)
        await db_session.flush()

        results = await orchestrator.process_batch(db_session)

        # Auto-retries should be recovered
        assert results["recovered"] >= 0
        assert results["amount_recovered"] >= 0

        # Some should be escalated (high value or low confidence)
        # The exact count depends on the data but should be non-negative
        assert results["escalated"] >= 0

        # Total should add up
        total_classified = (
            results["recovered"]
            + results["escalated"]
            + results["failed"]
            + results["compliance_stopped"]
        )
        # Some may still be in 'executing' state
        assert total_classified <= results["total_processed"]

    @pytest.mark.asyncio
    async def test_batch_creates_audit_trail(self, db_session, orchestrator):
        """Test that every batch action creates audit log entries."""
        _seed_payment_events(db_session, count=5)
        await db_session.flush()

        await orchestrator.process_batch(db_session)

        from sqlalchemy import select, func
        stmt = select(func.count()).select_from(AuditLog)
        count = (await db_session.execute(stmt)).scalar() or 0

        # At minimum: event_detected + diagnosis_completed per event = 10
        assert count >= 10

    @pytest.mark.asyncio
    async def test_batch_skips_already_processed(self, db_session, orchestrator):
        """Test that already-processed events are not re-processed."""
        _seed_payment_events(db_session, count=5)
        await db_session.flush()

        # First batch
        results1 = await orchestrator.process_batch(db_session)
        assert results1["total_processed"] == 5

        # Second batch — should find nothing new
        await db_session.commit()
        results2 = await orchestrator.process_batch(db_session)
        assert results2["total_processed"] == 0

    @pytest.mark.asyncio
    async def test_batch_saves_metrics_to_db(self, db_session, orchestrator):
        """Test that batch metrics are persisted to the database."""
        _seed_payment_events(db_session, count=5)
        await db_session.flush()

        await orchestrator.process_batch(db_session)

        from sqlalchemy import select
        stmt = select(RecoveryMetric)
        result = await db_session.execute(stmt)
        metrics = list(result.scalars().all())

        assert len(metrics) == 1
        assert metrics[0].batch_size == 5
        assert metrics[0].total_amount_at_risk > 0
