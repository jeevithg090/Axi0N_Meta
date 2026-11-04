"""The supermarket use case (docs/usecases/supermarket.md).

Queue, hazard and shelf checks, standing watches, staff alerts with photos, advice from Ask
answers with pre-registered checks, restock tasks closed by photo, and the owner's brief.
"""

from rulecam.retail.build import build_retail
from rulecam.retail.checks import RetailError
from rulecam.retail.models import Finding, StaffTask, Watch
from rulecam.retail.service import RetailService

__all__ = ["Finding", "RetailError", "RetailService", "StaffTask", "Watch", "build_retail"]
