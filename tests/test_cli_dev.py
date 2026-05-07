from __future__ import annotations

import socket
import subprocess

from typer.testing import CliRunner


def test_dev_command_starts_docker_backend_and_frontend(monkeypatch) -> None:
    import personify.cli as cli

    docker_calls: list[dict] = []
    popen_calls: list[dict] = []

    def fake_run(cmd, cwd=None, check=False, **_kwargs):
        docker_calls.append({"cmd": cmd, "cwd": cwd, "check": check})

    class FakeProc:
        def __init__(self, name: str) -> None:
            self.name = name
            self.terminated = False
            self.killed = False

        def poll(self):
            if self.name == "fastapi":
                return 0
            return None if not self.terminated else 0

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self) -> None:
            self.killed = True
            self.terminated = True

    def fake_popen(cmd, cwd=None, env=None):
        name = "fastapi" if "uvicorn" in cmd else "vite"
        popen_calls.append({"cmd": cmd, "cwd": cwd, "env": env, "name": name})
        return FakeProc(name)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "pnpm.cmd" if name == "pnpm" else None)
    monkeypatch.setattr(cli, "_is_port_open", lambda _host, _port: False)
    monkeypatch.setattr(cli, "_backend_health_ok", lambda _host, _port: False)

    result = CliRunner().invoke(cli.app, ["dev"])

    assert result.exit_code == 0, result.output
    assert docker_calls and docker_calls[0]["cmd"] == ["docker", "compose", "up", "-d"]
    assert len(popen_calls) == 2
    assert popen_calls[0]["name"] == "fastapi"
    assert popen_calls[0]["cmd"][:3] == [cli.sys.executable, "-m", "uvicorn"]
    assert "18765" in popen_calls[0]["cmd"]
    assert popen_calls[1]["name"] == "vite"
    assert popen_calls[1]["cmd"][:3] == ["pnpm.cmd", "exec", "vite"]
    assert "18766" in popen_calls[1]["cmd"]
    assert popen_calls[1]["env"]["PERSONIFY_DEV_API_ORIGIN"] == "http://localhost:18765"
    assert popen_calls[1]["env"]["PERSONIFY_DEV_FRONTEND_PORT"] == "18766"
    assert "MCP stays separate" in result.output


def test_dev_command_reuses_running_backend_and_starts_frontend(monkeypatch) -> None:
    import personify.cli as cli

    popen_calls: list[dict] = []

    def fake_popen(cmd, cwd=None, env=None):
        popen_calls.append({"cmd": cmd, "cwd": cwd, "env": env})

        class FakeProc:
            def poll(self):
                return 0

            def terminate(self) -> None:
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self) -> None:
                pass

        return FakeProc()

    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "pnpm.cmd" if name == "pnpm" else None)

    def fake_port_open(_host, port):
        return port == 18765

    monkeypatch.setattr(cli, "_is_port_open", fake_port_open)
    monkeypatch.setattr(cli, "_backend_health_ok", lambda _host, port: port == 18765)

    result = CliRunner().invoke(cli.app, ["dev"])

    assert result.exit_code == 0, result.output
    assert len(popen_calls) == 1
    assert popen_calls[0]["cmd"][:3] == ["pnpm.cmd", "exec", "vite"]
    assert "already running" in result.output


def test_dev_command_explains_when_docker_desktop_is_not_running(monkeypatch) -> None:
    import personify.cli as cli

    def fake_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["docker", "compose", "up", "-d"],
            stderr="open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = CliRunner().invoke(cli.app, ["dev"])

    assert result.exit_code == 1
    assert "Docker Desktop is not running" in result.output
    assert "npm start" in result.output
    assert "Traceback" not in result.output


def test_port_probe_detects_ipv6_localhost_listener() -> None:
    import personify.cli as cli

    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
        listener.bind(("::1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        assert cli._is_port_open("localhost", port)
