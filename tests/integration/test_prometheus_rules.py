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
