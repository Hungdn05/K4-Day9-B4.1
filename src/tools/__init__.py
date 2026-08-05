"""Domain toolboxes. Each specialist agent is handed exactly one of these."""

from src.tools.customer_tools import CUSTOMER_TOOLS
from src.tools.delivery_tools import DELIVERY_TOOLS
from src.tools.order_tools import ORDER_TOOLS
from src.tools.payment_tools import PAYMENT_TOOLS
from src.tools.policy_tools import POLICY_TOOLS
from src.tools.registry import Tool, ToolBox, ToolError

TOOLBOXES = {
    box.domain: box
    for box in (CUSTOMER_TOOLS, ORDER_TOOLS, PAYMENT_TOOLS, DELIVERY_TOOLS, POLICY_TOOLS)
}

__all__ = [
    "CUSTOMER_TOOLS",
    "ORDER_TOOLS",
    "PAYMENT_TOOLS",
    "DELIVERY_TOOLS",
    "POLICY_TOOLS",
    "TOOLBOXES",
    "Tool",
    "ToolBox",
    "ToolError",
]
