"""ARB Bot MCP server package (contracts MCP-SERVER-ENTRA #90, MCP-SHAREPOINT-OBO #91).

Package name is ``mcp_server`` (not ``mcp``) to avoid a name collision with
the third-party ``mcp`` Python SDK that this module imports from.
"""

from .server import build_starlette_app  # noqa: F401
