"""Local synthetic load acceptance for SSE connections and bounded chat runs."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.web.helpers import make_settings
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.main import create_app


class GatedRunner:
    def __init__(self) -> None:
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    async def run(self, _request: QaRunRequest) -> AnswerBundle:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
            return AnswerBundle(markdown="合成负载回答已完成。", terminal_reason="completed")
        finally:
            with self._lock:
                self.active -= 1

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self.active, self.max_active

    def close(self) -> None:
        self.release.set()


def _server_socket() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2048)
    return listener, int(listener.getsockname()[1])


def _wait_for_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("负载验收服务器启动失败")
        if time.monotonic() >= deadline:
            raise TimeoutError("负载验收服务器启动超时")
        time.sleep(0.02)


async def _wait_for_runner(runner: GatedRunner, expected: int) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while runner.snapshot()[0] != expected:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"并发执行槽未达到 {expected}: active={runner.snapshot()[0]}",
            )
        await asyncio.sleep(0.02)


async def _exercise(base_url: str, runner: GatedRunner, connections: int) -> dict[str, int]:
    limits = httpx.Limits(
        max_connections=connections + 20,
        max_keepalive_connections=connections + 20,
    )
    timeout = httpx.Timeout(20.0, connect=10.0, pool=10.0)
    origin = base_url
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as control:
        session_response = await control.get("/api/v1/auth/session")
        session_response.raise_for_status()
        csrf = session_response.json()["csrf_token"]
        mutation_headers = {"Origin": origin, "X-CSRF-Token": csrf}

        runs: list[dict] = []
        for index in range(connections):
            response = await control.post(
                "/api/v1/chat/runs",
                json={"question": f"公开合成负载问题 {index + 1}", "mode": "local"},
                headers=mutation_headers,
            )
            if response.status_code != 200:
                raise AssertionError(f"第 {index + 1} 个 run 创建失败: {response.text}")
            runs.append(response.json())

        await _wait_for_runner(runner, 30)

        connected = 0
        connected_lock = asyncio.Lock()
        all_connected = asyncio.Event()
        release_streams = asyncio.Event()

        async with httpx.AsyncClient(
            base_url=base_url,
            cookies=control.cookies,
            limits=limits,
            timeout=None,
        ) as streams:
            async def hold_stream(run: dict) -> None:
                nonlocal connected
                async with streams.stream("GET", run["events_url"]) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise AssertionError(f"SSE 连接失败: {response.status_code} {body!r}")
                    async with connected_lock:
                        connected += 1
                        if connected == connections:
                            all_connected.set()
                    await release_streams.wait()
                    terminal_seen = False
                    async for line in response.aiter_lines():
                        if line == "event: answer.completed":
                            terminal_seen = True
                    if not terminal_seen:
                        raise AssertionError("SSE 未以 answer.completed 完整结束")

            stream_tasks = [asyncio.create_task(hold_stream(run)) for run in runs]
            try:
                await asyncio.wait_for(all_connected.wait(), timeout=15)
                active, peak = runner.snapshot()
                if connected != connections:
                    raise AssertionError(f"SSE 在线连接不足: {connected}/{connections}")
                if active != 30 or peak != 30:
                    raise AssertionError(f"回答并发边界错误: active={active}, peak={peak}")

                busy = await control.post(
                    "/api/v1/chat/runs",
                    json={"question": "超过有界队列的合成问题", "mode": "local"},
                    headers=mutation_headers,
                )
                busy_payload = busy.json()
                if busy.status_code != 503 or busy_payload.get("error", {}).get("code") != "RUN_BUSY":
                    raise AssertionError(f"超载未被稳定拒绝: {busy.status_code} {busy.text}")

                runner.release.set()
                release_streams.set()
                await asyncio.wait_for(asyncio.gather(*stream_tasks), timeout=20)
                return {
                    "sse_connections": connected,
                    "peak_answers": peak,
                    "busy_status": busy.status_code,
                    "completed_streams": len(stream_tasks),
                }
            finally:
                runner.release.set()
                release_streams.set()
                for task in stream_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*stream_tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connections", type=int, default=100)
    args = parser.parse_args()
    if args.connections < 31 or args.connections > 200:
        raise SystemExit("--connections 必须在 31 到 200 之间")

    temp_root = os.environ.get("XIAOWO_TEST_TMP")
    temp_parent = Path(temp_root).resolve() if temp_root else None
    if temp_parent is not None:
        temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        dir=temp_parent,
        prefix="web-load-",
        ignore_cleanup_errors=True,
    ) as directory:
        data_path = Path(directory)
        listener, port = _server_socket()
        settings = make_settings(
            data_path,
            public_origin=f"http://127.0.0.1:{port}",
            extra={
                "XIAOWO_MAX_CONCURRENT_RUNS": "30",
                "XIAOWO_MAX_QUEUED_RUNS": str(args.connections - 30),
            },
        )
        settings = replace(settings, run_timeout_seconds=60.0)
        runner = GatedRunner()
        app = create_app(settings, runner=runner)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False),
        )
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            name="xiaowo-load-server",
            daemon=True,
        )
        thread.start()
        try:
            _wait_for_server(server, thread)
            result = asyncio.run(_exercise(f"http://127.0.0.1:{port}", runner, args.connections))
        finally:
            runner.release.set()
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
        if thread.is_alive():
            raise RuntimeError("负载验收服务器未正常退出")

    print(
        "Web load acceptance passed: "
        f"SSE={result['sse_connections']}, "
        f"peak_answers={result['peak_answers']}, "
        f"busy_http={result['busy_status']}, "
        f"completed={result['completed_streams']}",
    )


if __name__ == "__main__":
    main()
