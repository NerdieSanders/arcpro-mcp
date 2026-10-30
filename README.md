# arcpro-mcp - MCP connector for ArcGIS Pro

`arcpro-mcp` connects ArcGIS Pro 3.7 to MCP clients such as Claude Code. It is a fork of [marcovgonzalezv/arcgis-mcp](https://github.com/marcovgonzalezv/arcgis-mcp) (MIT). An ArcGIS Pro Add-In written in C# runs inside ArcGIS Pro, and a Python FastMCP server exposes it as MCP tools through a local Windows Named Pipe.

By default the server registers 6 core tools. Setting `ARCPRO_MCP_TOOLSET=full` also registers the upstream project's single-purpose tools, for 170 MCP tools in total. The Add-In has 75 Add-In commands. There are 2 MCP resources and 2 prompt templates.

## What this fork changes

- **Small default toolset.** `status`, `execute_code`, `run_gp_tool`, `get_layer_fields`, `save_project`, and `cancel_job`. Every registered tool's description costs context in each session, and `execute_code` covers what the single-purpose tools do.
- **`execute_code`** runs Python inside Pro through a bundled Python toolbox, so `arcpy.mp.ArcGISProject("CURRENT")` is the open project. Each run gets a fresh namespace, and printed output is returned.
- **Long jobs don't time out.** `execute_code` and `run_gp_tool` send an MCP progress notification every 5 seconds with the elapsed time, percent done, and the last printed line or geoprocessing message. The response read has no time limit.
- **Real cancellation.** `cancel_job`, or the client cancelling the request, stops the job in Pro. Geoprocessing tools get a `CancellationToken`. Python code is interrupted between statements through a sentinel file; a single long arcpy call stops only after it returns.
- **No double runs.** The pipe client retries only when the request never reached Pro.
- **Status during a job.** The pipe accepts several connections at once, so `status` and `cancel_job` answer while a job runs. One job runs at a time; a second one is refused with the running job's id instead of queuing silently.

### Register with Claude Code

Register the server at user scope with a long per-server timeout. Don't add it to the Claude desktop app's config: calls through the desktop app are capped at about 60 seconds.

```bash
claude mcp add-json arcpro_mcp '{"type":"stdio","command":"C:/path/to/venv/Scripts/arcgis-mcp-server.exe","timeout":7200000}' -s user
```

Use forward slashes in the path.

---

## Overview

- **ArcGIS Pro Add-In**: a C# add-in loaded by ArcGIS Pro. It invokes the ArcGIS Pro SDK inside the ArcGIS Pro process and exposes commands through `\\.\pipe\ArcGisMcpBridge`.
- **Python MCP server**: a FastMCP server that registers the public tools, resources, and prompts. It communicates with the Add-In through the local Named Pipe client.
- **IPC layer**: local transport over a Named Pipe with length-prefixed JSON messages, structured responses, timeouts, retries, and explicit error metadata.
- **Validation suite**: Python tests, linting, formatting, Add-In compilation, Git whitespace checks, and generated artifact scanning.

The server exposes operations for projects, maps, layers, selections, symbology, layouts, geodatabases, portal metadata, feature services, and geoprocessing.

---

## Requirements

- Windows.
- ArcGIS Pro 3.7.
- ArcGIS Pro SDK for .NET with the local ArcGIS Pro SDK assemblies installed.
- .NET 10 SDK.
- Python 3.10 or higher.
- Python packages from `python-server/requirements.txt`.
- Development packages from `python-server/requirements-dev.txt` for testing and release validation.

---

## Repository structure

```text
arcgis-mcp/
+- arcgis-addin/
|  +- ArcGisMcpAddin.sln
|  +- ArcGisMcpAddin/
|     +- Config.daml
|     +- Module1.cs
|     +- PipeServer.cs
|     +- CommandHandler.cs
|     +- Commands/
|     +- Images/
+- python-server/
|  +- pyproject.toml
|  +- arcgis_mcp/
|  |  +- __init__.py
|  |  +- __main__.py
|  |  +- server.py
|  |  +- pipe_client.py
|  |  +- tools/
|  |  +- resources/
|  |  +- prompts/
|  +- test_connection.py
|  +- tests/
|  +- requirements.txt
|  +- requirements-dev.txt
+- packaging/
|  +- build_release.ps1
+- docs/
|  +- setup-guide.md
|  +- installation-guide.md
|  +- installation-guide.es.md
|  +- release-checklist.md
+- scripts/
|  +- validate_release.ps1
+- setup.ps1
+- install_addin.ps1
+- README.md
+- CONTRIBUTING.md
+- SECURITY.md
```

---

## ArcGIS Pro Add-In

The Add-In starts the local pipe server when ArcGIS Pro loads the module. Commands execute inside ArcGIS Pro and can access the active project, active map, layers, layouts, geodatabases, portal state, and geoprocessing APIs.

Key Add-In files:

- `arcgis-addin/ArcGisMcpAddin/Module1.cs`: starts and stops the pipe server.
- `arcgis-addin/ArcGisMcpAddin/PipeServer.cs`: manages local connections and message framing.
- `arcgis-addin/ArcGisMcpAddin/CommandHandler.cs`: routes JSON commands to the implementation modules.
- `arcgis-addin/ArcGisMcpAddin/Commands/`: contains command groups for project, map, data, layouts, symbology, editing, geodatabase, portal, and geoprocessing.
- `arcgis-addin/ArcGisMcpAddin/Commands/CoreCommands.cs`: reports the Add-In version, MCP version, command names, and tool capabilities.

Internal Add-In commands include `health_check`, `get_capabilities`, `list_maps`, `list_layers`, `save_project`, `publish_web_layer`, `stage_service_definition`, `create_map_series`, and `ping`. The `ping` command is internal IPC infrastructure and is not exposed as a public MCP tool.

---

## Python MCP server

The Python server registers the public MCP interface and delegates operations to the Add-In whenever a call to the ArcGIS Pro SDK is required inside the ArcGIS Pro process.

Key server files:

- `python-server/arcgis_mcp/server.py`: FastMCP entry point and public registration of tools, resources, and prompts.
- `python-server/arcgis_mcp/pipe_client.py`: Windows Named Pipe client.
- `python-server/arcgis_mcp/tools/`: MCP tool wrappers grouped by functional area.
- `python-server/arcgis_mcp/resources/`: MCP resources with ArcPy and Add-In SDK references.
- `python-server/arcgis_mcp/prompts/`: MCP templates for ArcPy and Add-In development.
- `python-server/tests/test_operational_contracts.py`: contract tests for tool count, Add-In command coverage, release gates, public documentation, and repository hygiene.

Public MCP tools include `spatial_join`, `query_layer`, `export_all_layouts`, `stage_service_definition`, `run_gp_tool`, `search_arcgis_docs`, `query_feature_service`, `publish_web_layer`, `create_basic_layout`, `export_active_map`, `apply_graduated_symbology`, `save_layer_file`, and `describe_dataset`.

---

## MCP configuration

Register the Python server in an MCP-compatible client.

**Option A — installed entry point (recommended for end users):**

After `pip install arcgis-mcp-server` (or running `setup.ps1`), use the generated console script:

```json
{
  "mcpServers": {
    "arcgis-mcp": {
      "command": "C:/path/to/Scripts/arcgis-mcp-server.exe"
    }
  }
}
```

**Option B — from source (developers):**

```json
{
  "mcpServers": {
    "arcgis-mcp": {
      "command": "python.exe",
      "args": ["-m", "arcgis_mcp"]
    }
  }
}
```

Use the absolute path of the preferred Python executable if `python.exe` is not available on `PATH`.

---

## Installation

### End users (release distribution)

1. Download `ArcGisMcpAddin.esriAddinX`, the `arcgis_mcp_server-*.whl` and `setup.ps1` from the latest release.
2. Place the three files in the same folder and run:

```powershell
.\setup.ps1
```

`setup.ps1` creates a dedicated virtual environment, installs the wheel, registers the Add-In in ArcGIS Pro, and prints the MCP client configuration. No terminal expertise or build toolchain required.

### Developers (build from source)

Run PowerShell from the repository root:

```powershell
cd C:\path\to\arcgis-mcp
.\install_addin.ps1
```

Then install the Python server as an editable package:

```powershell
cd C:\path\to\arcgis-mcp\python-server
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

After installing the Add-In, close and reopen ArcGIS Pro to load the current DLL.

For a detailed, step-by-step walkthrough see `docs/installation-guide.md` (English) or `docs/installation-guide.es.md` (Spanish).

### Building a release

Maintainers produce the distributable artifacts with:

```powershell
.\packaging\build_release.ps1
```

This compiles the `.esriAddinX`, builds the wheel, and writes SHA256 checksums into `dist\`.

---

## Running with ArcGIS Pro

1. Open ArcGIS Pro 3.7.
2. Open or create a project.
3. Confirm that `ArcGIS Pro MCP Server Bridge` appears in the Add-In Manager.
4. Open a map view.
5. Confirm that the `ArcGIS MCP` tab is available on the ribbon.
6. Run the connection test:

```powershell
cd C:\path\to\arcgis-mcp\python-server
python test_connection.py
```

Expected result:

```text
SUCCESS: Connected to ArcGIS Pro MCP Bridge.
```

---

## Validation

Run the full release validation from the repository root:

```powershell
.\scripts\validate_release.ps1
```

The release script validates:

- Python linting with Ruff.
- Python formatting with Ruff.
- Pytest and unittest suites.
- Python bytecode compilation.
- Add-In formatting.
- Add-In compilation.
- Git whitespace checks.
- Generated artifact cleanup and scanning.

The GitHub Actions workflow runs the Python-side checks that can run without a local ArcGIS Pro SDK installation.

---

## Security model

The Add-In accepts commands through a local Named Pipe and executes them inside the active ArcGIS Pro session. Run ArcGIS Pro and the MCP server under the same trusted Windows user context. Connect only trusted MCP clients, because the tools can inspect, edit, export, publish, and geoprocess GIS data available to the active ArcGIS Pro project.

See `SECURITY.md` for operational guidelines and vulnerability reporting.

---

## License

This project is distributed under the MIT License. See `LICENSE` for the full text.

---

## Release checklist

Before publishing:

1. Run `.\scripts\validate_release.ps1`.
2. Confirm that `README.md`, `docs/setup-guide.md`, and `docs/release-checklist.md` match the current tool contract.
3. Confirm that there are no generated artifacts.
4. Confirm that no local personal paths or private project data are included.
5. Confirm that `LICENSE` contains the MIT License.
6. Configure the Git author metadata.
7. Configure the GitHub remote.

The detailed checklist is in `docs/release-checklist.md`.
