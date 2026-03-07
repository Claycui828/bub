






"""MCP client — connects to configured MCP servers and bridges their tools into ToolRegistry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp import types as mcp_types
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False


@dataclass
class McpServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # For streamable HTTP transport

    @property
    def transport(self) -> str:
        if self.url:
            return "streamable-http"
        return "stdio"


@dataclass
class McpToolInfo:
    """Metadata for a tool discovered from an MCP server."""

    server_name: str
    tool_name: str
    description: str
    input_schema: dict[str, Any]


class McpClientManager:
    """Manages connections to multiple MCP servers and exposes their tools.

    Stdio servers maintain a persistent subprocess + session.
    HTTP servers reconnect per call (stateless, no cross-task context issues).
    """

    def __init__(self, configs: list[McpServerConfig]) -> None:
        self._configs = configs
        self._stdio_sessions: dict[str, ClientSession] = {}
        self._http_configs: dict[str, McpServerConfig] = {}
        self._tools: dict[str, McpToolInfo] = {}  # keyed by "mcp__{server}__{tool}"
        self._cleanup_tasks: list[Any] = []  # stdio context managers

    @property
    def available(self) -> bool:
        return HAS_MCP

    async def connect_all(self) -> list[McpToolInfo]:
        """Connect to all configured MCP servers and discover tools.

        Returns list of discovered tools. Failures are logged but do not raise.
        """
        if not HAS_MCP:
            logger.warning("mcp.client.skip: mcp package not installed, run: uv add mcp")
            return []

        all_tools: list[McpToolInfo] = []
        for cfg in self._configs:
            try:
                tools = await self._connect_one(cfg)
                all_tools.extend(tools)
            except BaseException as exc:
                logger.error("mcp.client.connect.error server={} error={}", cfg.name, exc)
        return all_tools

    async def _connect_one(self, cfg: McpServerConfig) -> list[McpToolInfo]:
        """Connect to a single MCP server and list its tools."""
        logger.info("mcp.client.connect server={} transport={}", cfg.name, cfg.transport)

        if cfg.transport == "streamable-http":
            return await self._connect_http(cfg)
        return await self._connect_stdio(cfg)

    async def _connect_stdio(self, cfg: McpServerConfig) -> list[McpToolInfo]:
        """Connect via stdio transport (persistent subprocess)."""
        server_params = StdioServerParameters(
            command=cfg.command or "uvx",
            args=cfg.args,
            env=cfg.env or None,
        )
        cm = stdio_client(server_params)
        read_stream, write_stream = await cm.__aenter__()
        self._cleanup_tasks.append(cm)
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()
        self._stdio_sessions[cfg.name] = session
        return await self._discover_tools(cfg.name, session)

    async def _connect_http(self, cfg: McpServerConfig) -> list[McpToolInfo]:
        """Discover tools from an HTTP MCP server (stateless, reconnects per call)."""
        url = cfg.url
        if not url:
            return []
        tools = await self._http_session_call(url or "", self._list_tools_callback)
        self._http_configs[cfg.name] = cfg
        result: list[McpToolInfo] = []
        for tool in tools:
            qualified_name = f"mcp__{cfg.name}__{tool.name}"
            info = McpToolInfo(
                server_name=cfg.name,
                tool_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict) else {},
            )
            self._tools[qualified_name] = info
            result.append(info)
            logger.info("mcp.client.tool.discovered server={} tool={}", cfg.name, tool.name)
        return result

    @staticmethod
    async def _list_tools_callback(session: ClientSession) -> list[Any]:
        result = await session.list_tools()
        return list(result.tools)

    @staticmethod
    async def _http_session_call(url: str, callback: Any) -> Any:
        """Open a short-lived HTTP session, run callback, and close cleanly."""
        async with (
            streamable_http_client(url) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            return await callback(session)

    async def _discover_tools(self, server_name: str, session: ClientSession) -> list[McpToolInfo]:
        """List tools from a connected stdio session."""
        result = await session.list_tools()
        tools: list[McpToolInfo] = []
        for tool in result.tools:
            qualified_name = f"mcp__{server_name}__{tool.name}"
            info = McpToolInfo(
                server_name=server_name,
                tool_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict) else {},
            )
            self._tools[qualified_name] = info
            tools.append(info)
            logger.info("mcp.client.tool.discovered server={} tool={}", server_name, tool.name)
        return tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on a connected MCP server."""
        # Stdio: use persistent session
        session = self._stdio_sessions.get(server_name)
        if session is not None:
            return await self._execute_tool(session, tool_name, arguments)

        # HTTP: reconnect per call
        cfg = self._http_configs.get(server_name)
        if cfg is not None and cfg.url:
            return await self._call_http_tool(cfg.url, tool_name, arguments)

        return f"error: MCP server '{server_name}' not connected"

    async def _call_http_tool(self, url: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool via a short-lived HTTP session."""
        if not url:
            return "error: MCP server URL is empty"
        try:
            async def _callback(session: ClientSession) -> str:
                return await self._execute_tool(session, tool_name, arguments)
            result: Any = await self._http_session_call(url, _callback)
            return str(result) if result else "empty response"
        except Exception as exc:
            return f"mcp error: {exc!s}"

    @staticmethod
    async def _execute_tool(session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and format the result."""
        try:
            result = await session.call_tool(tool_name, arguments=arguments)
        except Exception as exc:
            return f"mcp error: {exc!s}"

        parts: list[str] = []
        for content in result.content:
            if isinstance(content, mcp_types.TextContent):
                parts.append(content.text)
            elif isinstance(content, mcp_types.ImageContent):
                parts.append(f"[image: {content.mimeType}]")
            elif isinstance(content, mcp_types.EmbeddedResource):
                parts.append(f"[resource: {content.resource.uri}]")
            else:
                parts.append(str(content))

        if result.isError:
            return f"mcp tool error: {' '.join(parts)}"
        return "\n".join(parts) if parts else "(no output)"

    def get_tool_info(self, qualified_name: str) -> McpToolInfo | None:
        return self._tools.get(qualified_name)

    def all_tools(self) -> list[tuple[str, McpToolInfo]]:
        """Return all discovered tools as (qualified_name, info) pairs."""
        return list(self._tools.items())

    async def close(self) -> None:
        """Disconnect all stdio sessions (HTTP sessions are stateless)."""
        for name, session in self._stdio_sessions.items():
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                logger.warning("mcp.client.session.close.error server={}", name)

        for cm in reversed(self._cleanup_tasks):
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                logger.warning("mcp.client.transport.close.error")

        self._stdio_sessions.clear()
        self._http_configs.clear()
        self._tools.clear()
        self._cleanup_tasks.clear()
        logger.info("mcp.client.closed")


def parse_mcp_configs(raw: dict[str, Any]) -> list[McpServerConfig]:
    """Parse MCP server configs from a dict.

    Expected format (top-level keys are server names):
    ```yaml
    filesystem:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    remote-server:
      url: "http://localhost:8000/mcp"
    ```
    """
    configs: list[McpServerConfig] = []
    if not isinstance(raw, dict):
        return configs
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        configs.append(
            McpServerConfig(
                name=name,
                command=value.get("command"),
                args=value.get("args", []),
                env=value.get("env", {}),
                url=value.get("url"),
            )
        )
    return configs


def load_mcp_configs(workspace: Path, home: Path) -> list[McpServerConfig]:
    """Load MCP server configs from mcp_servers.yaml.

    Search order (later files merge into earlier, last wins):
    1. Global: ~/.bub/mcp_servers.yaml
    2. Project: <workspace>/.bub/mcp_servers.yaml

    File format — top-level keys are server names:
    ```yaml
    filesystem:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    github:
      command: uvx
      args: ["mcp-server-github"]
      env:
        GITHUB_TOKEN: "ghp_..."
    remote:
      url: "http://localhost:8000/mcp"
    ```
    """
    import yaml

    merged: dict[str, Any] = {}
    candidates = [
        home / "mcp_servers.yaml",
        workspace / ".bub" / "mcp_servers.yaml",
    ]
    for path in candidates:
        if path.is_file():
            with open(path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                merged.update(data)
                logger.info("mcp.config.loaded path={} servers={}", path, list(data.keys()))

    return parse_mcp_configs(merged)