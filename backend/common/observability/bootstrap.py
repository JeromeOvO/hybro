"""Application-shell logging bootstrap.

Import this module before runtime modules that may emit warnings or logs.
"""

from common.config.settings import settings
from common.observability.logging import configure_logging

configure_logging(settings)

__all__ = ["settings"]
