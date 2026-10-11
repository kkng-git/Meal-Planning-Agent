#!/usr/bin/env python3
"""Run rubric-aligned integration checks against the meal-planning agent.

By default, the script starts a temporary local Uvicorn server with an isolated
filesystem MCP directory. With ``--base-url``, it instead tests an existing
Cloud Run deployment and uses Cloud Logging to verify model-selected tools.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
REQUEST_TIMEOUT_SECONDS = 180
STARTUP_TIMEOUT_SECONDS = 60


@dataclass
class TestResult:
    name: str
    passed: bool
    seconds: float
    detail: str


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_status(method: str, url: str, payload: dict) -> int:
    """Return the HTTP status, including expected validation errors."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def chat(base_url: str, query: str, session_id: str) -> str:
    payload = request_json(
        "POST",
        f"{base_url}/chat",
        {"query": query, "session_id": session_id},
    )
    response = str(payload.get("response", "")).strip()
    assert response, "API returned an empty response"
    failure_markers = (
        "agent or tool connection failed",
        "completed without producing a final response",
    )
    assert not any(marker in response.lower() for marker in failure_markers), response
    return response


def read_log_since(log_path: Path, offset: int) -> str:
    with log_path.open("r", encoding="utf-8", errors="replace") as log_file:
        log_file.seek(offset)
        return log_file.read()


def called_tools(log_text: str) -> list[str]:
    return re.findall(r"'name': '([^']+)'", log_text)


def wait_for_server(base_url: str, process: subprocess.Popen, log_path: Path) -> dict:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(
                f"Server exited during startup with code {process.returncode}.\n{log_text}"
            )
        try:
            return request_json("GET", f"{base_url}/health", timeout=2)
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.5)
    raise TimeoutError(f"Server did not become healthy: {last_error}")


def wait_for_remote_server(base_url: str) -> dict:
    """Wait through a possible Cloud Run cold start and return its health data."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return request_json("GET", f"{base_url}/health", timeout=10)
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(1)
    raise TimeoutError(f"Remote service did not become healthy: {last_error}")


def utc_log_marker() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_cloud_logs_since(
    marker: str,
    project: str,
    service: str,
    region: str,
) -> str:
    """Return Cloud Run application logs after a marker, waiting for ingestion."""
    log_filter = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service}" '
        f'AND resource.labels.location="{region}" '
        f'AND timestamp>="{marker}"'
    )
    deadline = time.monotonic() + 45
    last_output = ""
    while time.monotonic() < deadline:
        completed = subprocess.run(
            [
                "gcloud",
                "logging",
                "read",
                log_filter,
                f"--project={project}",
                "--order=asc",
                "--limit=200",
                "--format=value(textPayload)",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Could not read Cloud Run logs: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        last_output = completed.stdout
        if re.search(r'"POST /chat HTTP/1\.1" 200 OK', last_output):
            return last_output
        time.sleep(2)
    raise TimeoutError(
        "Cloud Run request completed, but its access log was not available within "
        f"45 seconds. Latest log output: {last_output[-500:]}"
    )


def run_case(name: str, test: Callable[[], str]) -> TestResult:
    started = time.monotonic()
    try:
        detail = test()
        return TestResult(name, True, time.monotonic() - started, detail)
    except Exception as error:  # Continue so the report includes every case.
        return TestResult(name, False, time.monotonic() - started, str(error))


def response_preview(response: str, limit: int = 140) -> str:
    one_line = " ".join(response.split())
    return one_line if len(one_line) <= limit else f"{one_line[:limit]}..."


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        help="Test an existing deployment instead of starting local Uvicorn.",
    )
    parser.add_argument(
        "--gcp-project",
        default=os.getenv("GCP_PROJECT_ID"),
        help="Google Cloud project used to verify remote tool calls in logs.",
    )
    parser.add_argument(
        "--cloud-run-service",
        default=os.getenv("CLOUD_RUN_SERVICE", "meal-planning-agent"),
    )
    parser.add_argument(
        "--gcp-region",
        default=os.getenv("GCP_REGION", "us-west1"),
    )
    arguments = parser.parse_args()

    remote_mode = arguments.base_url is not None
    if remote_mode and not arguments.gcp_project:
        parser.error("--gcp-project or GCP_PROJECT_ID is required with --base-url")

    port = available_port() if not remote_mode else None
    base_url = (
        arguments.base_url.rstrip("/")
        if remote_mode
        else f"http://127.0.0.1:{port}"
    )
    run_token = f"{int(time.time())}-{os.getpid()}"
    results: list[TestResult] = []

    with tempfile.TemporaryDirectory(prefix="meal-agent-live-test-") as temp_dir:
        temp_root = Path(temp_dir)
        data_dir = temp_root / "data"
        data_dir.mkdir()
        log_path = temp_root / "server.log"

        environment = os.environ.copy()
        environment["RECIPE_DATA_DIR"] = str(data_dir)
        environment["PYTHONUNBUFFERED"] = "1"

        process: subprocess.Popen | None = None
        if not remote_mode:
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

        try:
            health = (
                wait_for_remote_server(base_url)
                if remote_mode
                else wait_for_server(base_url, process, log_path)
            )

            def mark_logs() -> str | int:
                return utc_log_marker() if remote_mode else log_path.stat().st_size

            def logs_since(marker: str | int) -> str:
                if remote_mode:
                    return read_cloud_logs_since(
                        str(marker),
                        str(arguments.gcp_project),
                        arguments.cloud_run_service,
                        arguments.gcp_region,
                    )
                return read_log_since(log_path, int(marker))

            def session(name: str) -> str:
                return f"{name}-{run_token}"

            def test_mcp_discovery() -> str:
                expected_bound = {
                    "filesystem_read_text_file",
                    "filesystem_write_file",
                    "filesystem_list_directory",
                    "tavily_tavily_search",
                    "fetch_fetch",
                }
                bound = set(health.get("tools", []))
                discovered = set(health.get("discovered_tools", []))
                assert health.get("status") == "ok", health
                assert health.get("unavailable_servers") == {}, health
                assert health.get("missing_tools") == [], health
                assert expected_bound == bound, (
                    f"bound tools differ: expected={sorted(expected_bound)}, "
                    f"actual={sorted(bound)}"
                )
                assert any(name.startswith("filesystem_") for name in discovered)
                assert any(name.startswith("tavily_") for name in discovered)
                assert "fetch_fetch" in discovered
                return (
                    f"all 3 MCP servers discovered; {len(bound)} focused tools bound"
                )

            results.append(run_case("MCP discovery and bindings", test_mcp_discovery))

            def test_http_contract() -> str:
                missing_session = request_status(
                    "POST",
                    f"{base_url}/chat",
                    {"query": "Hello"},
                )
                empty_query = request_status(
                    "POST",
                    f"{base_url}/chat",
                    {"query": "", "session_id": "contract-test"},
                )
                assert missing_session == 422, missing_session
                assert empty_query == 422, empty_query
                return "POST /chat enforces query and session_id; invalid bodies return 422"

            results.append(run_case("HTTP contract validation", test_http_contract))

            def test_no_tool_reasoning() -> str:
                marker = mark_logs()
                response = chat(
                    base_url,
                    "What is a good ratio of water to white rice on the stove? "
                    "Answer from general knowledge and do not use tools.",
                    session("live-no-tool"),
                )
                tools = called_tools(logs_since(marker))
                assert not tools, f"expected no tools, but called {tools}"
                return f"no tools selected; response: {response_preview(response)}"

            results.append(run_case("Reasoning: answer without tools", test_no_tool_reasoning))

            def test_web_tool_reasoning() -> str:
                marker = mark_logs()
                response = chat(
                    base_url,
                    "Find one quick vegetarian Thai dinner recipe, inspect its recipe "
                    "page, and give me the source. Do not save anything.",
                    session("live-web-tools"),
                )
                tools = called_tools(logs_since(marker))
                assert "tavily_tavily_search" in tools, tools
                assert "fetch_fetch" in tools, tools
                assert "filesystem_write_file" not in tools, tools
                return f"selected {tools}; response: {response_preview(response)}"

            results.append(run_case("Reasoning: search then fetch", test_web_tool_reasoning))

            def test_three_tool_chain() -> str:
                saved_name = f"thursday_pad_thai_{run_token}.md"
                marker = mark_logs()
                response = chat(
                    base_url,
                    "Find one vegetarian Pad Thai recipe from an individual recipe "
                    "page and inspect that page. Then save a concise verification "
                    f"note to {saved_name} containing the title, source URL, and "
                    "only details verified from the page. If the bounded page excerpt "
                    "is incomplete, still save the verified note rather than retrying "
                    "the page repeatedly.",
                    session("live-three-tool-chain"),
                )
                tools = called_tools(logs_since(marker))
                expected = {
                    "tavily_tavily_search",
                    "fetch_fetch",
                    "filesystem_write_file",
                }
                assert expected.issubset(tools), tools
                if remote_mode:
                    saved_text = chat(
                        base_url,
                        f"Read {saved_name} and return its recipe title and first two "
                        "ingredients. Do not search the web.",
                        session("live-three-tool-readback"),
                    )
                    assert len(saved_text.strip()) >= 30, (
                        "saved recipe readback is unexpectedly short"
                    )
                else:
                    saved_path = data_dir / saved_name
                    assert saved_path.is_file(), f"expected saved file at {saved_path}"
                    saved_text = saved_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    assert len(saved_text.strip()) >= 100, (
                        "saved recipe is unexpectedly short"
                    )
                return (
                    f"chained {tools}; saved and verified {saved_name}; response: "
                    f"{response_preview(response)}"
                )

            results.append(
                run_case("Reasoning: search, fetch, save", test_three_tool_chain)
            )

            preference_name = f"live_test_preference_{run_token}.md"

            def test_filesystem_write() -> str:
                marker = mark_logs()
                response = chat(
                    base_url,
                    f"Save this exact preference note as {preference_name}: "
                    "I prefer vegetarian meals and must avoid peanuts. "
                    "Do not search the web. Confirm after saving.",
                    session("live-filesystem"),
                )
                tools = called_tools(logs_since(marker))
                assert "filesystem_write_file" in tools, tools
                assert "tavily_tavily_search" not in tools, tools
                if remote_mode:
                    detail = preference_name
                else:
                    written_path = data_dir / preference_name
                    assert written_path.is_file(), (
                        "filesystem tool reported success but wrote no file"
                    )
                    saved_text = written_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).lower()
                    assert "vegetarian" in saved_text and "peanut" in saved_text
                    detail = written_path.name
                return (
                    f"selected {tools}; wrote {detail}; response: "
                    f"{response_preview(response)}"
                )

            results.append(run_case("MCP filesystem write", test_filesystem_write))

            def test_filesystem_read() -> str:
                marker = mark_logs()
                response = chat(
                    base_url,
                    f"Read {preference_name} and tell me the dietary preference "
                    "and restricted ingredient it contains. Do not search the web.",
                    session("live-filesystem-read"),
                )
                tools = called_tools(logs_since(marker))
                assert "filesystem_read_text_file" in tools, tools
                assert "tavily_tavily_search" not in tools, tools
                lowered = response.lower()
                assert "vegetarian" in lowered and "peanut" in lowered, response
                return f"selected {tools}; response: {response_preview(response)}"

            results.append(run_case("MCP filesystem read", test_filesystem_read))

            def test_tool_execution_error() -> str:
                marker = mark_logs()
                missing_name = f"definitely_missing_recipe_{run_token}.md"
                response = chat(
                    base_url,
                    f"Read the file {missing_name} and tell me "
                    "what it contains. Do not use web search.",
                    session("live-missing-file"),
                )
                tools = called_tools(logs_since(marker))
                assert "filesystem_read_text_file" in tools, tools
                return (
                    f"missing-file error returned a valid API response after {tools}: "
                    f"{response_preview(response)}"
                )

            results.append(run_case("Tool execution error recovery", test_tool_execution_error))

            def test_conversation_memory() -> str:
                session_id = session("live-memory")
                first = chat(
                    base_url,
                    "For this conversation, remember that my favorite cuisine is "
                    "Korean and that I avoid mushrooms. Just acknowledge this; do not "
                    "use tools.",
                    session_id,
                )
                marker = mark_logs()
                second = chat(
                    base_url,
                    "What cuisine did I say I like, and what ingredient do I avoid? "
                    "Answer briefly without tools.",
                    session_id,
                )
                tools = called_tools(logs_since(marker))
                assert not tools, f"memory recall unexpectedly called tools: {tools}"
                lowered = second.lower()
                assert "korean" in lowered, second
                assert "mushroom" in lowered, second
                return (
                    f"turn 1: {response_preview(first, 80)}; "
                    f"turn 2 recalled both facts: {response_preview(second)}"
                )

            results.append(run_case("Conversational memory", test_conversation_memory))

            def test_session_isolation() -> str:
                secret = "cardamom-482"
                session_a = session("live-session-a")
                session_b = session("live-session-b")
                first = chat(
                    base_url,
                    f"Remember the private ingredient code {secret} in this session. "
                    "Do not use tools.",
                    session_a,
                )
                other_session = chat(
                    base_url,
                    "What private ingredient code did I give you? If I did not give "
                    "you one in this conversation, say you do not know. Do not use tools.",
                    session_b,
                )
                same_session = chat(
                    base_url,
                    "What private ingredient code did I give you? Do not use tools.",
                    session_a,
                )
                assert secret not in other_session.lower(), other_session
                assert secret in same_session.lower(), same_session
                return (
                    f"session B did not receive session A state; session A recalled "
                    f"{secret}. Initial response: {response_preview(first, 70)}"
                )

            results.append(run_case("Memory session isolation", test_session_isolation))

            def test_malformed_tool_payload() -> str:
                from agent import _compact_tavily_result

                malformed = "not-json:" + ("x" * 3000)
                compacted = _compact_tavily_result(malformed)
                assert compacted == malformed[:1800]
                return "malformed Tavily content is handled and capped at 1,800 characters"

            results.append(run_case("Malformed tool payload handling", test_malformed_tool_payload))

            def test_connection_failure_resilience() -> str:
                fault_port = available_port()
                fault_base_url = f"http://127.0.0.1:{fault_port}"
                fault_log_path = temp_root / "transport-failure-server.log"
                fault_environment = environment.copy()
                # npx is installed outside these system paths on the development
                # machine. Hiding it simulates the filesystem MCP transport being
                # unavailable while Python and the remote Tavily endpoint remain usable.
                fault_environment["PATH"] = "/usr/bin:/bin"

                with fault_log_path.open("w", encoding="utf-8") as fault_log:
                    fault_process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(fault_port),
                        ],
                        cwd=PROJECT_ROOT,
                        env=fault_environment,
                        stdout=fault_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )

                try:
                    try:
                        fault_health = wait_for_server(
                            fault_base_url,
                            fault_process,
                            fault_log_path,
                        )
                    except Exception as error:
                        raise AssertionError(
                            "service did not remain available when the filesystem "
                            "MCP transport was unavailable"
                        ) from error
                    assert fault_health.get("status") == "degraded", fault_health
                    fault_tools = set(fault_health.get("tools", []))
                    assert {
                        "tavily_tavily_search",
                        "fetch_fetch",
                    }.issubset(fault_tools), fault_health
                    assert not any(
                        name.startswith("filesystem_")
                        for name in fault_tools
                    ), fault_health
                    assert "filesystem" in fault_health.get(
                        "unavailable_servers", {}
                    ), fault_health
                    missing_tools = set(
                        fault_health.get("missing_tools", [])
                    )
                    assert {
                        "filesystem_read_text_file",
                        "filesystem_write_file",
                        "filesystem_list_directory",
                    }.issubset(missing_tools), fault_health

                    response = chat(
                        fault_base_url,
                        "What is a simple way to season roasted vegetables? "
                        "Answer from general knowledge without using tools.",
                        "live-degraded-startup",
                    )
                    return (
                        "service remained available with Tavily and Fetch; "
                        f"no-tool chat succeeded: {response_preview(response)}"
                    )
                finally:
                    if fault_process.poll() is None:
                        os.killpg(fault_process.pid, signal.SIGTERM)
                        try:
                            fault_process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(fault_process.pid, signal.SIGKILL)
                            fault_process.wait(timeout=5)

            if not remote_mode:
                results.append(
                    run_case(
                        "MCP connection failure resilience",
                        test_connection_failure_resilience,
                    )
                )

        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

        target = f"remote deployment {base_url}" if remote_mode else "local server"
        print(f"\nLive meal-planning agent test results ({target})")
        print("=" * 78)
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"{status:4}  {result.name:<34} {result.seconds:6.1f}s")
            print(f"      {result.detail}")
        passed = sum(result.passed for result in results)
        print("-" * 78)
        print(f"{passed}/{len(results)} tests passed")
        if remote_mode:
            print(
                "Local-only transport failure injection was not run against the "
                "deployed service."
            )

        return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
