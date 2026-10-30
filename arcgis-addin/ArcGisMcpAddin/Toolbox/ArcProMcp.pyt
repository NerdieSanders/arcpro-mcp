# -*- coding: utf-8 -*-
"""Python toolbox used by the ArcPro MCP add-in to run execute_code jobs.

The add-in writes this file to %LOCALAPPDATA%\\ArcProMcp\\toolbox and runs
ExecuteCode through Geoprocessing.ExecuteToolAsync. Each run gets a fresh
namespace, so variables never leak between calls. Cancellation works through a
sentinel file: a watcher thread sees it and raises KeyboardInterrupt in the
running code at its next Python bytecode. A single long arcpy call is
interrupted only after it returns.
"""

import builtins
import contextlib
import ctypes
import io
import json
import os
import threading
import time
import traceback

import arcpy

MAX_CAPTURED_CHARS = 200_000


class _Tee(io.TextIOBase):
    """Writes to a live file (read for progress) and keeps a capped copy."""

    def __init__(self, path):
        self._file = open(path, "w", encoding="utf-8", buffering=1)
        self._parts = []
        self._size = 0
        self.truncated = False

    def writable(self):
        return True

    def write(self, text):
        self._file.write(text)
        if self._size < MAX_CAPTURED_CHARS:
            room = MAX_CAPTURED_CHARS - self._size
            self._parts.append(text[:room])
            self._size += min(len(text), room)
            if len(text) > room:
                self.truncated = True
        else:
            self.truncated = True
        return len(text)

    def flush(self):
        self._file.flush()

    def getvalue(self):
        return "".join(self._parts)

    def close(self):
        if not self.closed:
            super().close()  # calls flush() while the file is still open
            self._file.close()


def run_code(code_path, result_path, cancel_path, stdout_path):
    with open(code_path, encoding="utf-8") as handle:
        code = handle.read()

    namespace = {
        "__name__": "__main__",
        "__file__": code_path,
        "__builtins__": builtins,
        "arcpy": arcpy,
    }
    result = {
        "ok": False,
        "cancelled": False,
        "error": None,
        "traceback": None,
        "stdout": "",
        "stdout_truncated": False,
        "stdout_path": stdout_path,
        "elapsed_s": 0.0,
    }

    tee = _Tee(stdout_path)
    lock = threading.Lock()
    state = {"running": True}
    target = threading.get_ident()
    stop = threading.Event()

    def watch():
        while not stop.wait(0.5):
            if os.path.exists(cancel_path):
                with lock:
                    if state["running"]:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(
                            ctypes.c_ulong(target), ctypes.py_object(KeyboardInterrupt)
                        )
                return

    watcher = threading.Thread(target=watch, name="execute_code-cancel", daemon=True)
    started = time.perf_counter()
    try:
        try:
            watcher.start()
            with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                exec(compile(code, "<execute_code>", "exec"), namespace)
            result["ok"] = True
        except KeyboardInterrupt:
            result["cancelled"] = True
            result["error"] = "Cancelled."
        except BaseException as exc:  # SystemExit included: report, don't exit Pro
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["traceback"] = traceback.format_exc()
        finally:
            with lock:
                state["running"] = False
    except KeyboardInterrupt:
        # A cancel that landed just as the code finished.
        result["cancelled"] = True

    stop.set()
    result["elapsed_s"] = round(time.perf_counter() - started, 3)
    result["stdout"] = tee.getvalue()
    result["stdout_truncated"] = tee.truncated
    tee.close()

    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, default=str)


class Toolbox:
    def __init__(self):
        self.label = "ArcPro MCP"
        self.alias = "arcpromcp"
        self.tools = [ExecuteCode]


class ExecuteCode:
    def __init__(self):
        self.label = "Execute Code"
        self.description = "Runs a Python file in a fresh namespace for the ArcPro MCP add-in."
        self.canRunInBackground = True

    def getParameterInfo(self):
        names = ["code_path", "result_path", "cancel_path", "stdout_path"]
        return [
            arcpy.Parameter(
                displayName=name,
                name=name,
                datatype="GPString",
                parameterType="Required",
                direction="Input",
            )
            for name in names
        ]

    def execute(self, parameters, messages):
        run_code(*[parameter.valueAsText for parameter in parameters])
