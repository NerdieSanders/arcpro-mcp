"""Tool results must reach the client as plain text, not structuredContent.

With structured output on, FastMCP wraps a `-> str` result as
{"result": "<string>"}, and clients that render it show escaped \\n and \\".
"""

import importlib
import os
import pathlib
import sys
import types
import unittest

import anyio

PYTHON_SERVER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_SERVER))

# pywin32 exists only on Windows. These tests never touch the pipe.
for _name in ("pywintypes", "win32file", "win32pipe"):
    try:
        importlib.import_module(_name)
    except ImportError:
        sys.modules[_name] = types.ModuleType(_name)

from mcp.shared.memory import (
    create_connected_server_and_client_session as connect,
)


def load_server(toolset):
    os.environ["ARCPRO_MCP_TOOLSET"] = toolset
    sys.modules.pop("arcgis_mcp.server", None)
    return importlib.import_module("arcgis_mcp.server")


class ToolOutputTest(unittest.TestCase):
    def test_no_tool_declares_structured_output(self):
        for toolset in ("core", "full"):
            server = load_server(toolset)

            async def main(server=server):
                async with connect(server.mcp._mcp_server) as session:
                    return (await session.list_tools()).tools

            tools = anyio.run(main)
            self.assertGreater(len(tools), 0)
            with_schema = [t.name for t in tools if t.outputSchema]
            self.assertEqual([], with_schema, toolset)

    def test_result_is_plain_text_with_real_newlines(self):
        server = load_server("core")
        reply = {"success": True, "data": {"path": "C:\\Users\\x", "msg": "a\nb"}}

        async def fake_cancel(job_id=None):
            return reply

        original = server.jobs.cancel
        server.jobs.cancel = fake_cancel
        try:

            async def main():
                async with connect(server.mcp._mcp_server) as session:
                    return await session.call_tool("cancel_job", {})

            result = anyio.run(main)
        finally:
            server.jobs.cancel = original

        self.assertIsNone(result.structuredContent)
        text = result.content[0].text
        self.assertEqual(server._dump(reply["data"]), text)
        self.assertIn("\n", text)


if __name__ == "__main__":
    unittest.main()
