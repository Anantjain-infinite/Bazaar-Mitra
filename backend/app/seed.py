"""Seed the database with realistic, coherent multi-merchant demo data.

    uv run python -m app.seed

Idempotent: safe to run repeatedly. Every insert is a get-or-create keyed
on a natural unique field (merchant business_name, product
merchant_id+sku, buyer phone, order public_order_id, ...), so re-running
after adding a new seed_* function only adds what's missing.

Deliberately NOT idempotent-via-"wipe and recreate" — that would nuke any
data created through the live API during a demo/dev session, which is
exactly the kind of silent destructive behavior this project's safety
model argues against everywhere else.

Design note on the demo dataset: three of the five merchants stock a
"Wireless Mouse" at three different price/stock points (₹799/23 in
stock, ₹899/7 in stock, ₹699/out of stock) — this isn't arbitrary, it's
exactly the "Merchant A/B/C" comparison in the spec's AI Buyer demo
scenario (search "wireless mouse under ₹1000" -> compare three
merchants -> best available option -> cross-sell a mouse pad -> ₹998
total against a ₹1,000 transaction limit). Gupta Electronics' default
transaction_policies row is deliberately set to a ₹1,000 limit for the
same reason.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Buyer,
    Merchant,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductRelationship,
    TransactionPolicy,
)
from app.db.models.enums import OrderStatus, PaymentStatus
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("bazaar_mitra.seed")
logging.basicConfig(level=logging.INFO)

_RNG = random.Random(42)  # deterministic-looking "randomness" across re-runs


# --- Merchants ---------------------------------------------------------

MERCHANTS = [
    {
        "business_name": "Sharma General Store",
        "owner_name": "Ramesh Sharma",
        "phone": "9876500001",
        "email": "ramesh@sharmageneralstore.example",
        "address": "14 Chandni Chowk Road",
        "city": "Delhi",
        "state": "Delhi",
        "preferred_language": "hi",
    },
    {
        "business_name": "Gupta Electronics",
        "owner_name": "Sanjay Gupta",
        "phone": "9876500002",
        "email": "sanjay@guptaelectronics.example",
        "address": "22 MG Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "preferred_language": "en",
    },
    {
        "business_name": "Patel Stationery",
        "owner_name": "Bhavesh Patel",
        "phone": "9876500003",
        "email": "bhavesh@patelstationery.example",
        "address": "7 Linking Road",
        "city": "Mumbai",
        "state": "Maharashtra",
        "preferred_language": "hi",
    },
    {
        "business_name": "Kumar Daily Needs",
        "owner_name": "Suresh Kumar",
        "phone": "9876500004",
        "email": "suresh@kumardailyneeds.example",
        "address": "45 FC Road",
        "city": "Pune",
        "state": "Maharashtra",
        "preferred_language": "hi",
    },
    {
        "business_name": "Verma Mobile & Accessories",
        "owner_name": "Deepak Verma",
        "phone": "9876500005",
        "email": "deepak@vermamobile.example",
        "address": "3 Golghar Market",
        "city": "Gorakhpur",
        "state": "Uttar Pradesh",
        "preferred_language": "hi",
    },
]

# --- Products, keyed by merchant business_name -------------------------
# Each entry: (sku, name, description, category, price, stock, metadata)

PRODUCTS: dict[str, list[tuple]] = {
    "Sharma General Store": [
        (
            "SGS-ATTA5",
            "Atta (Wheat Flour) 5kg",
            "Whole wheat flour, 5kg pack",
            "groceries",
            210,
            80,
            {},
        ),
        ("SGS-RICE1", "Basmati Rice 1kg", "Aged basmati rice", "groceries", 95, 40, {}),
        (
            "SGS-OIL1L",
            "Mustard Oil 1L",
            "Cold-pressed mustard oil",
            "groceries",
            165,
            25,
            {},
        ),
        ("SGS-SUGAR1", "Sugar 1kg", "Refined white sugar", "groceries", 48, 60, {}),
        ("SGS-DAL1", "Toor Dal 1kg", "Split pigeon peas", "groceries", 140, 35, {}),
    ],
    "Gupta Electronics": [
        (
            "GEL-WMOUSE",
            "Wireless Mouse",
            "2.4GHz wireless optical mouse, 3 buttons",
            "electronics",
            799,
            23,
            {"wireless": True, "color": "black"},
        ),
        (
            "GEL-MPAD",
            "Mouse Pad",
            "Anti-slip rubber-base mouse pad",
            "electronics",
            199,
            50,
            {},
        ),
        (
            "GEL-WDMOUSE",
            "Wired Mouse",
            "USB wired optical mouse",
            "electronics",
            199,
            40,
            {"wireless": False},
        ),
        (
            "GEL-KBD",
            "USB Keyboard",
            "Full-size USB wired keyboard",
            "electronics",
            549,
            18,
            {},
        ),
        (
            "GEL-HDMI",
            "HDMI Cable 1.5m",
            "High-speed HDMI cable",
            "electronics",
            149,
            60,
            {},
        ),
        (
            "GEL-BTSPK",
            "Bluetooth Speaker",
            "Portable Bluetooth speaker, 10W",
            "electronics",
            899,
            15,
            {"wireless": True},
        ),
        (
            "GEL-WMOUSE-PRO",
            "Wireless Mouse (Ergonomic, Rechargeable)",
            "Rechargeable ergonomic wireless mouse",
            "electronics",
            1299,
            10,
            {"wireless": True, "rechargeable": True},
        ),
    ],
    "Patel Stationery": [
        (
            "PST-NB200",
            "Notebook 200 Pages",
            "Ruled notebook, 200 pages",
            "stationery",
            35,
            100,
            {},
        ),
        (
            "PST-PEN5",
            "Ballpoint Pen (Pack of 5)",
            "Smooth-writing ballpoint pens",
            "stationery",
            25,
            70,
            {},
        ),
        (
            "PST-A4REAM",
            "A4 Paper Ream",
            "500 sheets, 75 GSM",
            "stationery",
            320,
            45,
            {},
        ),
        (
            "PST-WMOUSE",
            "Wireless Mouse",
            "2.4GHz wireless optical mouse",
            "electronics",
            899,
            7,
            {"wireless": True},
        ),
        ("PST-MPAD", "Mouse Pad", "Standard mouse pad", "electronics", 179, 20, {}),
    ],
    "Kumar Daily Needs": [
        (
            "KDN-TEA500",
            "Tea Powder 500g",
            "Strong CTC tea powder",
            "groceries",
            210,
            30,
            {},
        ),
        (
            "KDN-MILKP",
            "Milk Powder 400g",
            "Full-cream milk powder",
            "groceries",
            180,
            25,
            {},
        ),
        (
            "KDN-BISC",
            "Biscuits (Pack)",
            "Glucose biscuits, family pack",
            "groceries",
            30,
            90,
            {},
        ),
        (
            "KDN-DET1",
            "Detergent Powder 1kg",
            "Stain-removal detergent powder",
            "groceries",
            110,
            40,
            {},
        ),
    ],
    "Verma Mobile & Accessories": [
        (
            "VMA-WMOUSE",
            "Wireless Mouse",
            "2.4GHz wireless optical mouse",
            "electronics",
            699,
            0,
            {"wireless": True},
        ),
        (
            "VMA-CASE",
            "Mobile Phone Case (Universal)",
            "Shockproof silicone case",
            "mobile accessories",
            149,
            55,
            {},
        ),
        (
            "VMA-SCRPROT",
            "Screen Protector",
            "Tempered glass screen protector",
            "mobile accessories",
            99,
            80,
            {},
        ),
        (
            "VMA-PWRBK",
            "Power Bank 10000mAh",
            "Fast-charging portable power bank",
            "mobile accessories",
            899,
            20,
            {},
        ),
        (
            "VMA-CABLE",
            "USB-C Cable 1m",
            "Braided USB-C charging cable",
            "mobile accessories",
            149,
            65,
            {},
        ),
    ],
}

# --- Product relationships, keyed by merchant, as (from_sku, type, to_sku, priority) ---

RELATIONSHIPS: dict[str, list[tuple]] = {
    "Gupta Electronics": [
        ("GEL-WMOUSE", "CROSS_SELL", "GEL-MPAD", 10),
        ("GEL-WMOUSE", "UPSELL", "GEL-WMOUSE-PRO", 5),
        ("GEL-WDMOUSE", "ALTERNATIVE", "GEL-WMOUSE", 5),
        ("GEL-KBD", "CROSS_SELL", "GEL-WMOUSE", 3),
    ],
    "Patel Stationery": [
        ("PST-WMOUSE", "CROSS_SELL", "PST-MPAD", 10),
    ],
}

# --- Per-merchant transaction policy overrides (defaults applied if absent) ---
# Gupta Electronics uses a tight ₹1,000 limit on purpose — see module docstring.

POLICY_OVERRIDES: dict[str, dict] = {
    "Gupta Electronics": {"max_transaction_amount": 1000, "max_daily_amount": 3000},
}
_DEFAULT_POLICY = {"max_transaction_amount": 5000, "max_daily_amount": 10000}

BUYERS = [
    {
        "name": "Anita Verma",
        "phone": "9123456701",
        "email": "anita.verma@example.com",
        "preferred_language": "hi",
        "is_ai_agent": False,
    },
    {
        "name": "Rohit Kumar",
        "phone": "9123456702",
        "email": "rohit.kumar@example.com",
        "preferred_language": "en",
        "is_ai_agent": False,
    },
    {
        "name": "Bazaar Mitra AI Buyer",
        "phone": None,
        "email": None,
        "preferred_language": "en",
        "is_ai_agent": True,
    },
]


async def _get_or_create_merchant(db: AsyncSession, data: dict) -> Merchant:
    result = await db.execute(
        select(Merchant).where(Merchant.business_name == data["business_name"])
    )
    merchant = result.scalar_one_or_none()
    if merchant:
        return merchant
    merchant = Merchant(currency="INR", active=True, **data)
    db.add(merchant)
    await db.flush()
    logger.info("Created merchant: %s", merchant.business_name)
    return merchant


async def _get_or_create_product(
    db: AsyncSession, merchant: Merchant, row: tuple
) -> Product:
    sku, name, description, category, price, stock, metadata = row
    result = await db.execute(
        select(Product).where(Product.merchant_id == merchant.id, Product.sku == sku)
    )
    product = result.scalar_one_or_none()
    if product:
        return product
    product = Product(
        merchant_id=merchant.id,
        sku=sku,
        name=name,
        description=description,
        category=category,
        price=Decimal(str(price)),
        currency="INR",
        stock_quantity=stock,
        active=True,
        metadata_=metadata,
    )
    db.add(product)
    await db.flush()
    return product


async def _get_or_create_relationship(
    db: AsyncSession,
    merchant: Merchant,
    products_by_sku: dict[str, Product],
    row: tuple,
) -> None:
    from_sku, rel_type, to_sku, priority = row
    product = products_by_sku[from_sku]
    related = products_by_sku[to_sku]
    result = await db.execute(
        select(ProductRelationship).where(
            ProductRelationship.product_id == product.id,
            ProductRelationship.related_product_id == related.id,
            ProductRelationship.relationship_type == rel_type,
        )
    )
    if result.scalar_one_or_none():
        return
    db.add(
        ProductRelationship(
            merchant_id=merchant.id,
            product_id=product.id,
            related_product_id=related.id,
            relationship_type=rel_type,
            priority=priority,
        )
    )


async def _get_or_create_policy(db: AsyncSession, merchant: Merchant) -> None:
    result = await db.execute(
        select(TransactionPolicy).where(
            TransactionPolicy.merchant_id == merchant.id,
            TransactionPolicy.buyer_id.is_(None),
        )
    )
    if result.scalar_one_or_none():
        return
    limits = POLICY_OVERRIDES.get(merchant.business_name, _DEFAULT_POLICY)
    db.add(
        TransactionPolicy(
            merchant_id=merchant.id,
            buyer_id=None,
            max_transaction_amount=Decimal(str(limits["max_transaction_amount"])),
            max_daily_amount=Decimal(str(limits["max_daily_amount"])),
            currency="INR",
            confirmation_required=True,
            enabled=True,
        )
    )


async def _get_or_create_buyer(db: AsyncSession, data: dict) -> Buyer:
    if data.get("phone"):
        result = await db.execute(select(Buyer).where(Buyer.phone == data["phone"]))
    else:
        result = await db.execute(
            select(Buyer).where(Buyer.name == data["name"], Buyer.is_ai_agent.is_(True))
        )
    buyer = result.scalar_one_or_none()
    if buyer:
        return buyer
    buyer = Buyer(consent_flags={"marketing": True, "recorded_calls": True}, **data)
    db.add(buyer)
    await db.flush()
    logger.info("Created buyer: %s (ai_agent=%s)", buyer.name, buyer.is_ai_agent)
    return buyer


async def _seed_historical_order(
    db: AsyncSession,
    *,
    public_order_id: str,
    merchant: Merchant,
    buyer: Buyer,
    line_items: list[tuple[Product, int]],
    days_ago: int,
    with_failed_first_attempt: bool = False,
) -> None:
    result = await db.execute(
        select(Order).where(Order.public_order_id == public_order_id)
    )
    if result.scalar_one_or_none():
        return

    subtotal = sum(Decimal(str(p.price)) * qty for p, qty in line_items)
    placed_at = datetime.now(UTC) - timedelta(days=days_ago)

    order = Order(
        public_order_id=public_order_id,
        merchant_id=merchant.id,
        buyer_id=buyer.id,
        status=OrderStatus.PAID,
        payment_status=PaymentStatus.CAPTURED,
        currency="INR",
        subtotal=subtotal,
        discount=Decimal("0"),
        shipping_amount=Decimal("0"),
        total=subtotal,
        confirmation_required=True,
        confirmation_received=True,
        confirmed_at=placed_at,
        created_at=placed_at,
        updated_at=placed_at,
    )
    db.add(order)
    await db.flush()

    for product, qty in line_items:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                sku=product.sku,
                name_snapshot=product.name,
                quantity=qty,
                quoted_unit_price=product.price,
                final_unit_price=product.price,
                total=Decimal(str(product.price)) * qty,
            )
        )

    attempt = 1
    if with_failed_first_attempt:
        db.add(
            Payment(
                order_id=order.id,
                attempt_number=attempt,
                razorpay_order_id=f"order_seed_{uuid.uuid4().hex[:12]}",
                amount=order.total,
                currency="INR",
                status=PaymentStatus.FAILED,
                failure_code="BAD_REQUEST_ERROR",
                failure_reason="Payment declined by issuing bank (demo data)",
                raw_response_metadata={"seed_demo": True},
                created_at=placed_at,
                updated_at=placed_at,
            )
        )
        attempt += 1

    db.add(
        Payment(
            order_id=order.id,
            attempt_number=attempt,
            razorpay_order_id=f"order_seed_{uuid.uuid4().hex[:12]}",
            razorpay_payment_id=f"pay_seed_{uuid.uuid4().hex[:12]}",
            razorpay_signature="seed_demo_signature_not_real",
            amount=order.total,
            currency="INR",
            status=PaymentStatus.CAPTURED,
            raw_response_metadata={
                "seed_demo": True,
                "method": _RNG.choice(["upi", "card", "netbanking"]),
            },
            verified_at=placed_at,
            created_at=placed_at,
            updated_at=placed_at,
        )
    )


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        merchants_by_name: dict[str, Merchant] = {}
        for m in MERCHANTS:
            merchants_by_name[m["business_name"]] = await _get_or_create_merchant(db, m)
        await db.flush()

        products_by_merchant: dict[str, dict[str, Product]] = {}
        for merchant_name, rows in PRODUCTS.items():
            merchant = merchants_by_name[merchant_name]
            products_by_merchant[merchant_name] = {}
            for row in rows:
                product = await _get_or_create_product(db, merchant, row)
                products_by_merchant[merchant_name][row[0]] = product
        await db.flush()

        for merchant_name, rows in RELATIONSHIPS.items():
            merchant = merchants_by_name[merchant_name]
            for row in rows:
                await _get_or_create_relationship(
                    db, merchant, products_by_merchant[merchant_name], row
                )

        for merchant in merchants_by_name.values():
            await _get_or_create_policy(db, merchant)

        buyers = [await _get_or_create_buyer(db, b) for b in BUYERS]
        anita, rohit, _ai_buyer = buyers
        await db.flush()

        # A handful of historical, already-completed orders so the
        # dashboard (Phase 9) has something real to show immediately.
        gel = products_by_merchant["Gupta Electronics"]
        sgs = products_by_merchant["Sharma General Store"]
        vma = products_by_merchant["Verma Mobile & Accessories"]

        await _seed_historical_order(
            db,
            public_order_id="ORD-DEMO0001",
            merchant=merchants_by_name["Gupta Electronics"],
            buyer=rohit,
            line_items=[(gel["GEL-WMOUSE"], 1), (gel["GEL-MPAD"], 1)],
            days_ago=6,
            with_failed_first_attempt=True,  # mirrors the spec's canonical demo (fail, then retry+succeed)
        )
        await _seed_historical_order(
            db,
            public_order_id="ORD-DEMO0002",
            merchant=merchants_by_name["Gupta Electronics"],
            buyer=anita,
            line_items=[(gel["GEL-KBD"], 1), (gel["GEL-WDMOUSE"], 1)],
            days_ago=3,
        )
        await _seed_historical_order(
            db,
            public_order_id="ORD-DEMO0003",
            merchant=merchants_by_name["Sharma General Store"],
            buyer=anita,
            line_items=[
                (sgs["SGS-ATTA5"], 1),
                (sgs["SGS-RICE1"], 2),
                (sgs["SGS-OIL1L"], 1),
            ],
            days_ago=10,
        )
        await _seed_historical_order(
            db,
            public_order_id="ORD-DEMO0004",
            merchant=merchants_by_name["Verma Mobile & Accessories"],
            buyer=rohit,
            line_items=[(vma["VMA-CASE"], 1), (vma["VMA-SCRPROT"], 1)],
            days_ago=2,
        )

        await db.commit()
        logger.info("Seed complete.")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
