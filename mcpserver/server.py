"""stdio transport ＋ tool 註冊。**這是整個套件裡唯一可以 import 官方 SDK 的檔案。**

那條規則買到兩件事（ADR-032 決策一／二）：沒裝 `[mcp]` 的核心安裝照樣 import 得動
其餘五個模組、跑得完它們的測試（CI 的 `test-core-without-rag-extra` job 就是實測環境）；
日後若要換掉 SDK，動的是一個檔案。契約測試守著它。

**為什麼用低階 `mcp.server.lowlevel.Server` 而不是高階 `MCPServer`**：高階版的
``@server.tool()`` 從函式簽章推導 inputSchema，那會讓 schema 有兩份來源；低階版的
``on_list_tools`` 可以把 `tools.TOOLS` 的 schema 原樣送出去，維持單一定義。
（**不要寫 `FastMCP`**——那是 1.x 的名字，`mcp.server.fastmcp` 在 2.x 已經不存在。
同一次改版也把 result model 的欄位從 camelCase 換成 snake_case：這裡的
``types.Tool(input_schema=...)`` 就是那個改名的落點，而 wire format 仍是 `inputSchema`。）
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from mcpserver import availability, tools

SERVER_NAME = "omnicontext"


def _server_version() -> str:
    try:
        from core import __version__

        return str(__version__)
    except Exception:
        return "0"


def build_server() -> Any:
    """組出低階 Server。只有這裡碰 SDK。"""
    import mcp.types as types
    from mcp.server.lowlevel import Server

    async def on_list_tools(_ctx: Any, _params: Any) -> Any:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool["name"],
                    description=tool["description"],
                    input_schema=tool["inputSchema"],
                )
                for tool in tools.TOOLS
            ]
        )

    async def on_call_tool(_ctx: Any, params: Any) -> Any:
        try:
            payload = tools.call_tool(params.name, dict(params.arguments or {}))
        except tools.ToolError as exc:
            # 訊息一律取自封閉字串表，永遠不含 str(exc)（ADR-032 D4：
            # 錯誤訊息走的是白名單投影管不到的另一條路）。
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=tools.ERRORS[exc.code])],
                structured_content={"result": "error", "error_code": exc.code},
                is_error=True,
            )
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=payload,
        )

    return Server(
        name=SERVER_NAME,
        version=_server_version(),
        instructions=(
            "OmniContext 的唯讀脈絡層。每一筆結果都帶 source_ref（<table>:<id>）指回 SQLite row。"
            "這個 server 不會寫入任何東西，也不會替你重算專案狀態。"
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def _serve() -> None:
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_stdio() -> int:
    """啟動 stdio server。兩道閘門都過了才會走到這裡。"""
    import anyio

    missing = availability.missing_sdk_packages()
    if missing:
        raise availability.McpExtraNotInstalled(missing)
    if not availability.mcp_enabled():
        raise RuntimeError(tools.ERRORS["mcp_disabled"])
    anyio.run(_serve)
    return 0
