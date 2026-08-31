"""
Tests for Recovery Orchestrator — end-to-end workflow validation.
"""

import pytest
import pytest_asyncio
from datetime import datetime

from recovrai.models.database import Base, get_engine, get_session_factory
from recovrai.models.events import PaymentEvent, CheckoutEvent, SubscriptionEvent, InvoiceEvent
from recovrai.models.recovery import RecoveryWorkflow, AuditLog
from recovrai.services.recovery_orchestrator import RecoveryOrchestrator
from recovrai.config import WorkflowStatus


@pytest_asyncio.fixture
async def db_session():
    """Create an in-memory database for testing."""
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


def _make_payment_event(**kwargs) -> PaymentEvent:
    defaults = {
        "id": "test-pe-1",
        "payment_id": "pay_test123",
        "order_id": "order_test123",
        "customer_id": "cust_test1",
        "customer_name": "Aarav Sharma",
        "customer_email": "aarav@example.com",
        "customer_phone": "+919876543210",
        "amount": 250000,
        "status": "failed",
        "method": "upi",
        "error_code": "INSUFFICIENT_FUNDS",
        "error_description": "Payment failed due to insufficient funds",
        "error_source": "bank",
        "error_reason": "insufficient_funds",
        "processed": False,
    }
    defaults.update(kwargs)
    return PaymentEvent(**defaults)


def _make_checkout_event(**kwargs) -> CheckoutEvent:
    defaults = {
        "id": "test-ce-1",
        "session_id": "sess_test123",
        "customer_id": "cust_test2",
        "customer_name": "Priya Patel",
        "customer_email": "priya@example.com",
        "customer_phone": "+919876543211",
        "cart_value": 450000,
        "items_count": 3,
        "items_description": "3x Premium T-shirts",
        "stage_reached": "payment",
        "time_spent_seconds": 180,
        "processed": False,
    }
    defaults.update(kwargs)
    return CheckoutEvent(**defaults)


class TestEventIngestion:
    @pytest.mark.asyncio
    async def test_ingest_payment_event(self, db_session, orchestrator):
        event = _make_payment_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)

        assert workflow is not None
        assert workflow.status == WorkflowStatus.DETECTED.value
        assert workflow.amount_at_risk == 250000
        assert workflow.customer_name == "Aarav Sharma"
        assert workflow.event_type == "payment_failed"
        assert event.processed is True

    @pytest.mark.asyncio
    async def test_ingest_checkout_event(self, db_session, orchestrator):
        event = _make_checkout_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)

        assert workflow.event_type == "checkout_abandoned"
        assert workflow.amount_at_risk == 450000

    @pytest.mark.asyncio
    async def test_audit_log_created_on_ingest(self, db_session, orchestrator):
        event = _make_payment_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)

        from sqlalchemy import select
        stmt = select(AuditLog).where(AuditLog.workflow_id == workflow.id)
        result = await db_session.execute(stmt)
        logs = list(result.scalars().all())

        assert len(logs) >= 1
        assert logs[0].action == "event_detected"
        assert logs[0].actor == "system"


class TestDiagnosis:
    @pytest.mark.asyncio
    async def test_diagnose_payment_failure(self, db_session, orchestrator):
        event = _make_payment_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)
        workflow = await orchestrator.diagnose_workflow(db_session, workflow, event)

        assert workflow.status == WorkflowStatus.INTERVENTION_PLANNED.value
        assert workflow.root_cause is not None
        assert workflow.confidence == 1.0
        assert workflow.recommended_action == "send_payment_link"


class TestExecution:
    @pytest.mark.asyncio
    async def test_execute_intervention(self, db_session, orchestrator):
        event = _make_payment_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)
        workflow = await orchestrator.diagnose_workflow(db_session, workflow, event)
        workflow = await orchestrator.execute_intervention(db_session, workflow)

        assert workflow.contact_attempts >= 1
        assert workflow.current_channel is not None
        assert workflow.last_contact_at is not None


class TestOptOut:
    @pytest.mark.asyncio
    async def test_customer_opt_out_stops_workflows(self, db_session, orchestrator):
        event = _make_payment_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)
        count = await orchestrator.handle_customer_opt_out(
            db_session, "+919876543210"
        )

        assert count == 1

        from sqlalchemy import select
        stmt = select(RecoveryWorkflow).where(RecoveryWorkflow.id == workflow.id)
        result = await db_session.execute(stmt)
        updated_wf = result.scalar_one()
        assert updated_wf.status == WorkflowStatus.STOPPED_COMPLIANCE.value


class TestSimulateRecovery:
    @pytest.mark.asyncio
    async def test_simulate_payment_received(self, db_session, orchestrator):
        event = _make_payment_event()
        db_session.add(event)
        await db_session.flush()

        workflow = await orchestrator.ingest_event(db_session, event)
        recovered = await orchestrator.simulate_payment_received(
            db_session, workflow.id
        )

        assert recovered is not None
        assert recovered.status == WorkflowStatus.RECOVERED.value
        assert recovered.amount_recovered == 250000
        assert recovered.resolved_at is not None
