"""SLO rules and alert routing checked with promtool and amtool (ADR 0044).

Runs the pinned Prometheus and Alertmanager images from compose.yml, so the checks use the same
versions as the deployment. Skipped without Docker.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "compose.yml").read_text(encoding="utf-8")


def _image(name: str) -> str:
    found = re.search(rf"image: (prom/{name}:\S+@sha256:[0-9a-f]+)", COMPOSE)
    assert found, f"compose.yml must pin prom/{name} by digest"
    return found.group(1)


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="module", autouse=True)
def docker() -> None:
    if not _docker_ready():
        pytest.skip("Docker is not available")


def _run(
    image: str, tool: str, *args: str, mount: Path, target: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", "-v", f"{mount}:{target}:ro", "--entrypoint", tool, image, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def test_the_prometheus_config_and_rules_are_valid() -> None:
    done = _run(
        _image("prometheus"), "promtool", "check", "config", "/etc/prometheus/prometheus.yml",
        mount=ROOT / "deploy" / "prometheus", target="/etc/prometheus",
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_alert_rules_pass_their_unit_tests() -> None:
    done = _run(
        _image("prometheus"), "promtool", "test", "rules", "/etc/prometheus/tests/alerts_test.yml",
        mount=ROOT / "deploy" / "prometheus", target="/etc/prometheus",
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("receiver", ["none", "webhook"])
def test_the_alertmanager_config_is_valid_for_both_receivers(receiver: str, tmp_path: Path) -> None:
    template = (ROOT / "deploy" / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8")
    (tmp_path / "config.yml").write_text(template.replace("RECEIVER", receiver), encoding="utf-8")
    # The image runs as nobody, which must be able to read the mounted file.
    tmp_path.chmod(0o755)
    (tmp_path / "config.yml").chmod(0o644)

    done = _run(
        _image("alertmanager"), "amtool", "check-config", "/etc/am/config.yml",
        mount=tmp_path, target="/etc/am",
    )  # fmt: skip

    assert done.returncode == 0, done.stdout + done.stderr


def test_worker_down_inhibits_the_queue_alerts_it_causes(tmp_path: Path) -> None:
    import json
    import time
    import urllib.request

    template = (ROOT / "deploy" / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8")
    (tmp_path / "config.yml").write_text(template.replace("RECEIVER", "none"), encoding="utf-8")
    tmp_path.chmod(0o755)
    (tmp_path / "config.yml").chmod(0o644)
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", "127.0.0.1::9093", "-v", f"{tmp_path}:/etc/am:ro",
         _image("alertmanager"), "--config.file=/etc/am/config.yml", "--cluster.listen-address="],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    container = started.stdout.strip()
    try:
        port = (
            subprocess.run(
                ["docker", "port", container, "9093"],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.split(":")[-1]
            .strip()
        )
        base = f"http://127.0.0.1:{port}/api/v2"
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{base}/status", timeout=1)  # noqa: S310 - local test container
                break
            except OSError:
                time.sleep(0.2)
        alerts = [
            {"labels": {"alertname": name, "severity": "page"}}
            for name in ("WorkerDown", "QueueStuck")
        ]
        request = urllib.request.Request(  # noqa: S310
            f"{base}/alerts", data=json.dumps(alerts).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )  # fmt: skip
        urllib.request.urlopen(request, timeout=5)  # noqa: S310
        shown: dict[str, list] = {}
        for _ in range(50):
            with urllib.request.urlopen(f"{base}/alerts", timeout=5) as response:  # noqa: S310
                shown = {
                    a["labels"]["alertname"]: a["status"]["inhibitedBy"]
                    for a in json.loads(response.read())
                }
            if shown.get("QueueStuck"):
                break
            time.sleep(0.2)
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)

    assert shown["WorkerDown"] == []
    assert shown["QueueStuck"], "QueueStuck must be inhibited while WorkerDown fires"
