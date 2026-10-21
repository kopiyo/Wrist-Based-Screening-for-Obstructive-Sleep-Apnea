"""Consent-bound alert rule and delivery primitives.

The package deliberately has no dependency on the API's ORM or provider SDKs.
The host application supplies storage and delivery adapters implementing the
protocols in :mod:`apps.api.alerts.ports`.
"""

from .delivery import AlertDispatcher, build_notification
from .engine import AlertRuleEngine, AlertRuleError, RuleUpdate
from .models import Alert, AlertRule, DeliveryChannel, Night, RuleKind
from .ports import AlertRepository, NotificationProvider
from .time_rules import in_quiet_hours, next_quiet_hours_end

__all__ = [
    "Alert",
    "AlertDispatcher",
    "AlertRepository",
    "AlertRule",
    "AlertRuleEngine",
    "AlertRuleError",
    "DeliveryChannel",
    "Night",
    "NotificationProvider",
    "RuleKind",
    "RuleUpdate",
    "build_notification",
    "in_quiet_hours",
    "next_quiet_hours_end",
]
