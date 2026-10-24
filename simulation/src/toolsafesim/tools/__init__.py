"""The 20 ToolSafeSim tools. Importing this package registers them."""
from . import database, files, productivity, web  # noqa: F401
from .base import REGISTRY, ToolResult, ToolSpec, preview_tool, run_tool, tool_schemas

EXPECTED_TOOLS = [
    "search_emails", "read_email", "search_calendar", "create_event", "draft_email", "send_email",
    "query_records", "get_record", "update_record", "delete_record",
    "list_files", "read_file", "search_code", "run_tests", "write_file", "git_commit",
    "web_search", "fetch_page", "change_permissions", "upload_file",
]
assert sorted(REGISTRY) == sorted(EXPECTED_TOOLS), set(REGISTRY) ^ set(EXPECTED_TOOLS)

__all__ = ["REGISTRY", "ToolResult", "ToolSpec", "run_tool", "preview_tool", "tool_schemas", "EXPECTED_TOOLS"]
