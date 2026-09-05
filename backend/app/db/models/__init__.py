"""Import every model module so `Base.metadata` is fully populated.

Alembic's `env.py` imports `Base` from here (indirectly via
`app.db.models.base`) -- this module's only job is to make sure every
table is registered on that metadata before autogenerate runs.
"""

from app.db.models.agent_session import AgentSession, Handoff
from app.db.models.audit import AuditEvent
from app.db.models.base import Base
from app.db.models.buyer import Buyer
from app.db.models.cart import Cart, CartItem
from app.db.models.catalog import Product, ProductRelationship
from app.db.models.growth import AgentRecommendation, Campaign, CampaignEvent
from app.db.models.merchant import Merchant
from app.db.models.order import Order, OrderItem
from app.db.models.payment import Payment
from app.db.models.policy import TransactionPolicy
from app.db.models.support import Refund, Return

__all__ = [
    "AgentRecommendation",
    "AgentSession",
    "AuditEvent",
    "Base",
    "Buyer",
    "Campaign",
    "CampaignEvent",
    "Cart",
    "CartItem",
    "Handoff",
    "Merchant",
    "Order",
    "OrderItem",
    "Payment",
    "Product",
    "ProductRelationship",
    "Refund",
    "Return",
    "TransactionPolicy",
]
