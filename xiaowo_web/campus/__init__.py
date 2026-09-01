"""Public campus services facade."""

from xiaowo_web.campus.service import CampusService
from xiaowo_web.campus.tool_store import CampusToolError, CampusToolStore

__all__ = ["CampusService", "CampusToolError", "CampusToolStore"]
