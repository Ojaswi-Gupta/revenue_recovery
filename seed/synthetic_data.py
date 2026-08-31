import asyncio
import random
import uuid
from datetime import datetime, timedelta

from recovrai.models.database import get_db_session, init_db, Base, get_engine
from recovrai.models.events import PaymentEvent, CheckoutEvent, SubscriptionEvent, InvoiceEvent
from recovrai.models.recovery import RecoveryWorkflow, RecoveryAction, AuditLog

# Seed for reproducibility
random.seed(42)

# Helper data
INDIAN_FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Neha", "Arjun", "Kavya",
    "Raj", "Sanya", "Rahul", "Pooja", "Aditya", "Sneha", "Karan", "Riya",
    "Amit", "Divya", "Siddharth", "Megha"
]

INDIAN_LAST_NAMES = [
    "Sharma", "Patel", "Gupta", "Singh", "Mehta", "Verma", "Reddy", "Iyer",
    "Malhotra", "Kapoor", "Jain", "Nair", "Rao", "Das", "Chaudhary",
    "Bose", "Menon", "Mukherjee", "Yadav", "Kumar"
]

INDIAN_COMPANIES = [
    "Infosys Technologies", "TCS Digital", "Wipro Solutions", "Reliance Retail",
    "Flipkart Wholesale", "Zomato Media", "Swiggy Foods", "Paytm Payments",
    "Ola Cabs", "MakeMyTrip Travels", "Nykaa Cosmetics", "Freshworks Inc",
    "Zoho Corp", "PolicyBazaar", "Dream11"
]

ERROR_CODES = [
    "BAD_REQUEST_ERROR", "GATEWAY_ERROR", "SERVER_ERROR", "INSUFFICIENT_FUNDS",
    "CARD_EXPIRED", "INTERNATIONAL_CARD_DECLINED", "BANK_DECLINED", "UPI_TIMEOUT",
    "NETWORK_ERROR"
]

METHODS = ["card", "upi", "netbanking", "wallet"]
BANKS = ["HDFC", "SBI", "ICICI", "Axis", "Kotak", "Yes Bank"]
DEVICE_TYPES = ["mobile", "desktop", "tablet"]
STAGES = ["cart", "address", "payment", "confirmation"]
PLAN_NAMES = ["Pro Monthly", "Enterprise Annual", "Starter Plan", "Basic Tier", "Premium Subscription"]

def generate_customer() -> tuple[str, str, str, str]:
    """Generates a realistic Indian customer profile."""
    first_name = random.choice(INDIAN_FIRST_NAMES)
    last_name = random.choice(INDIAN_LAST_NAMES)
    name = f"{first_name} {last_name}"
    email = f"{first_name.lower()}.{last_name.lower()}{random.randint(1,99)}@example.com"
    phone = f"+91{random.randint(9000000000, 9999999999)}"
    customer_id = f"cust_{uuid.uuid4().hex[:12]}"
    return customer_id, name, email, phone

def generate_payment_events(count: int = 40) -> list[PaymentEvent]:
    """Generates synthetic failed payment events."""
    events = []
    for _ in range(count):
        cust_id, name, email, phone = generate_customer()
        method = random.choice(METHODS)
        error = random.choice(ERROR_CODES)
        
        events.append(
            PaymentEvent(
                id=str(uuid.uuid4()),
                payment_id=f"pay_{uuid.uuid4().hex[:12]}",
                order_id=f"order_{uuid.uuid4().hex[:12]}",
                customer_id=cust_id,
                customer_name=name,
                customer_email=email,
                customer_phone=phone,
                amount=random.randint(150, 85000) * 100,  # paise
                status="failed",
                method=method,
                bank=random.choice(BANKS) if method in ["netbanking", "card"] else None,
                error_code=error,
                error_description=f"Transaction failed due to {error.lower().replace('_', ' ')}",
                error_source="bank" if error in ["BANK_DECLINED", "INSUFFICIENT_FUNDS"] else "gateway",
                error_reason=error.lower(),
                international=error == "INTERNATIONAL_CARD_DECLINED"
            )
        )
    return events

def generate_checkout_events(count: int = 25) -> list[CheckoutEvent]:
    """Generates synthetic checkout abandonment events."""
    events = []
    # Weighted choices for stages
    stage_choices = ["payment"] * 60 + ["cart"] * 25 + ["address"] * 15
    
    for _ in range(count):
        cust_id, name, email, phone = generate_customer()
        items_count = random.randint(1, 10)
        
        events.append(
            CheckoutEvent(
                id=str(uuid.uuid4()),
                session_id=f"sess_{uuid.uuid4().hex[:12]}",
                customer_id=cust_id,
                customer_name=name,
                customer_email=email,
                customer_phone=phone,
                cart_value=random.randint(500, 50000) * 100,
                items_count=items_count,
                items_description=f"{items_count} items in cart",
                stage_reached=random.choice(stage_choices),
                time_spent_seconds=random.randint(30, 900),
                device_type=random.choice(DEVICE_TYPES)
            )
        )
    return events

def generate_subscription_events(count: int = 20) -> list[SubscriptionEvent]:
    """Generates synthetic subscription failure events."""
    events = []
    for _ in range(count):
        cust_id, name, email, phone = generate_customer()
        
        events.append(
            SubscriptionEvent(
                id=str(uuid.uuid4()),
                subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
                plan_id=f"plan_{uuid.uuid4().hex[:12]}",
                plan_name=random.choice(PLAN_NAMES),
                customer_id=cust_id,
                customer_name=name,
                customer_email=email,
                customer_phone=phone,
                amount=random.randint(99, 9999) * 100,
                status=random.choice(["halted", "pending"]),
                failure_count=random.randint(1, 5),
                last_failure_reason=random.choice(ERROR_CODES)
            )
        )
    return events

def generate_invoice_events(count: int = 20) -> list[InvoiceEvent]:
    """Generates synthetic overdue invoice events."""
    events = []
    base_date = datetime.now()
    
    for _ in range(count):
        cust_id, name, email, phone = generate_customer()
        total_amount = random.randint(5000, 500000) * 100
        days_overdue = random.randint(1, 90)
        
        events.append(
            InvoiceEvent(
                id=str(uuid.uuid4()),
                invoice_id=f"inv_{uuid.uuid4().hex[:12]}",
                invoice_number=f"INV-2024-{random.randint(1000, 9999)}",
                customer_id=cust_id,
                customer_name=name,
                customer_email=email,
                customer_phone=phone,
                company_name=random.choice(INDIAN_COMPANIES),
                amount=total_amount,
                status="overdue",
                amount_paid=0 if random.random() > 0.3 else int(total_amount * random.uniform(0.1, 0.5)),
                due_date=base_date - timedelta(days=days_overdue),
                days_overdue=days_overdue,
                reminder_count=random.randint(0, 3)
            )
        )
    return events

async def seed_database() -> None:
    """Creates tables and seeds synthetic events into the database."""
    print("Initializing database...")
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    print("Generating synthetic data...")
    payment_events = generate_payment_events(40)
    checkout_events = generate_checkout_events(25)
    subscription_events = generate_subscription_events(20)
    invoice_events = generate_invoice_events(20)
    
    total = len(payment_events) + len(checkout_events) + len(subscription_events) + len(invoice_events)
    
    print("Inserting data into database...")
    async with get_db_session() as session:
        session.add_all(payment_events)
        session.add_all(checkout_events)
        session.add_all(subscription_events)
        session.add_all(invoice_events)
    
    print(f"Database seeded successfully with {total} records.")
    print(f"  - Payment events: {len(payment_events)}")
    print(f"  - Checkout events: {len(checkout_events)}")
    print(f"  - Subscription events: {len(subscription_events)}")
    print(f"  - Invoice events: {len(invoice_events)}")


if __name__ == "__main__":
    asyncio.run(seed_database())
