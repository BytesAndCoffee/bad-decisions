from __future__ import annotations

from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "deploy.sh").read_text(encoding="utf-8").splitlines()


def curl_lines(needle):
    return [line for line in SCRIPT if line.startswith("curl ") and needle in line]


def test_health_check_retry_is_bounded():
    (line,) = curl_lines("/healthz")
    for flag in ("--retry 10", "--retry-connrefused", "--retry-delay 1", "--retry-max-time 60"):
        assert flag in line


def test_smoke_test_does_not_hard_code_the_base_pack():
    (line,) = curl_lines("/v1/round")
    assert "packs=" not in line
    assert not any("packs=base" in item for item in SCRIPT)


# --- deploy.sh rollback -----------------------------------------------------
# rollback.sh only rewrites APP_ROOT/current and good-releases, so it runs
# unprivileged here with systemctl and curl replaced by stubs found first on PATH.

import os
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "deploy" / "rollback.sh"
OLD, MID, NEW = "20260101T000000Z", "20260102T000000Z", "20260103T000000Z"


@pytest.fixture
def host(tmp_path):
    app_root = tmp_path / "app"
    for release in (OLD, MID, NEW):
        python = app_root / "releases" / release / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n")
        python.chmod(0o755)
    (app_root / "current").symlink_to(app_root / "releases" / NEW)
    (app_root / "good-releases").write_text(f"{OLD}\n{MID}\n{NEW}\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "systemctl.log"
    (stubs / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    (stubs / "curl").write_text(f'#!/bin/sh\n[ -e "{tmp_path}/unhealthy" ] && exit 22\nexit 0\n')
    for stub in stubs.iterdir():
        stub.chmod(0o755)

    class Host:
        app = app_root

        def run(self, *args):
            env = {
                "PATH": f"{stubs}:{os.environ['PATH']}",
                "APP_ROOT": str(app_root),
                "SERVICE_NAME": "svc",
            }
            return subprocess.run(["bash", str(ROLLBACK), *args], env=env, capture_output=True, text=True)

        def current(self):
            return os.path.basename(os.path.realpath(app_root / "current"))

        def restarts(self):
            return log.read_text().splitlines() if log.exists() else []

        def unhealthy(self):
            (tmp_path / "unhealthy").touch()

        def good(self, *ids):
            (app_root / "good-releases").write_text("".join(f"{item}\n" for item in ids))

        def watermark(self):
            return (app_root / "good-releases").read_text().split()

    return Host()


def test_rollback_defaults_to_the_last_good_release(host):
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert host.current() == MID
    assert host.restarts() == ["restart svc"]


def test_rollback_skips_releases_that_never_passed_their_checks(host):
    # MID exists on disk but failed its deploy, so it never reached the watermark.
    host.good(OLD, NEW)
    assert host.run().returncode == 0
    assert host.current() == OLD


def test_rollback_drops_the_release_it_left_so_it_cannot_roll_forward(host):
    assert host.run().returncode == 0
    assert host.watermark() == [OLD, MID]
    assert host.run().returncode == 0
    assert host.current() == OLD
    assert host.watermark() == [OLD]
    result = host.run()
    assert result.returncode == 1 and "No known-good release" in result.stderr
    assert host.current() == OLD


def test_explicit_release_overrides_the_watermark_and_updates_it(host):
    host.good(MID, NEW)
    assert host.run(OLD).returncode == 0
    assert host.current() == OLD
    assert host.watermark() == [MID, OLD]


def test_hostile_or_unusable_watermark_entries_are_ignored(host, tmp_path):
    outside = tmp_path / "outside"
    (outside / ".venv" / "bin").mkdir(parents=True)
    (outside / ".venv" / "bin" / "python").write_text("")
    (outside / ".venv" / "bin" / "python").chmod(0o755)
    (host.app / "releases" / "20260104T000000Z").symlink_to(outside)
    host.good(OLD, "../../outside", "20260105T000000Z", "20260104T000000Z", "", "not-an-id", NEW)
    assert host.run().returncode == 0
    assert host.current() == OLD


def test_empty_watermark_refuses_instead_of_guessing(host):
    host.good()
    result = host.run()
    assert result.returncode == 1 and "explicitly" in result.stderr
    assert host.current() == NEW
    assert host.restarts() == []


def test_host_without_a_watermark_falls_back_with_a_warning(host):
    (host.app / "good-releases").unlink()
    result = host.run()
    assert result.returncode == 0
    assert "not verified good" in result.stderr
    assert host.current() == MID


@pytest.mark.parametrize(
    "target",
    ["../releases/" + OLD, OLD + "/..", "latest", "", "20260101T000000", "x" * 300, "-h", "20260199T000000Z"],
)
def test_rollback_rejects_hostile_or_missing_release_ids(host, target):
    result = host.run(target)
    assert result.returncode == 1
    assert host.current() == NEW
    assert host.restarts() == []
    assert host.watermark() == [OLD, MID, NEW]


def test_rollback_rejects_symlinked_and_incomplete_releases(host, tmp_path):
    outside = tmp_path / "outside"
    (outside / ".venv" / "bin").mkdir(parents=True)
    (outside / ".venv" / "bin" / "python").write_text("")
    (outside / ".venv" / "bin" / "python").chmod(0o755)
    (host.app / "releases" / "20260104T000000Z").symlink_to(outside)
    (host.app / "releases" / "20260105T000000Z").mkdir()
    for target in ("20260104T000000Z", "20260105T000000Z"):
        assert host.run(target).returncode == 1
    assert host.current() == NEW
    assert host.restarts() == []


def test_rollback_refuses_current_release_extra_args_and_missing_link(host):
    assert host.run(NEW).returncode == 1
    assert host.run(OLD, MID).returncode == 1
    assert host.current() == NEW
    (host.app / "current").unlink()
    assert host.run().returncode == 1
    assert host.restarts() == []


def test_failed_health_check_restores_the_release_and_keeps_the_watermark(host):
    host.unhealthy()
    result = host.run(MID)
    assert result.returncode == 1
    assert "restoring" in result.stderr
    assert host.current() == NEW
    assert host.restarts() == ["restart svc", "restart svc"]
    assert host.watermark() == [OLD, MID, NEW]


def test_deploy_script_dispatches_rollback_and_rejects_unknown_arguments():
    text = "\n".join(SCRIPT)
    assert "[[ ${1:-} == rollback ]]" in text
    assert "deploy/rollback.sh" in text
    assert "elif [[ $# -gt 0 ]]" in text
    assert subprocess.run(["bash", "-n", str(ROLLBACK)]).returncode == 0


def test_deploy_records_the_watermark_only_after_every_check_passes():
    lines = SCRIPT
    last_check = max(i for i, line in enumerate(lines) if line.startswith(("curl ", "  curl ")))
    trap_clear = lines.index("trap - ERR")
    watermark = next(i for i, line in enumerate(lines) if "good-releases" in line)
    assert last_check < trap_clear < watermark
    text = "\n".join(lines)
    assert 'tail -n 20 > "${GOOD_FILE}.new"' in text and 'mv -f "${GOOD_FILE}.new"' in text


def run_watermark_block(app_root, release_id, previous=""):
    start = next(i for i, line in enumerate(SCRIPT) if line.startswith("GOOD_FILE="))
    end = next(i for i, line in enumerate(SCRIPT) if i > start and "rollback will lack a watermark" in line)
    script = "set -euo pipefail\n" + "\n".join(SCRIPT[start : end + 1])
    env = {"PATH": os.environ["PATH"], "APP_ROOT": str(app_root), "RELEASE_ID": release_id, "PREVIOUS_RELEASE": previous}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_deploy_watermark_seeds_from_the_previous_release_on_first_use(tmp_path):
    result = run_watermark_block(tmp_path, NEW, previous=str(tmp_path / "releases" / MID))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "good-releases").read_text().split() == [MID, NEW]
    assert not (tmp_path / "good-releases.new").exists()


def test_deploy_watermark_appends_dedupes_and_caps_history(tmp_path):
    good = tmp_path / "good-releases"
    ids = [f"202601{day:02d}T000000Z" for day in range(1, 26)]
    good.write_text("".join(f"{item}\n" for item in ids))
    assert run_watermark_block(tmp_path, ids[3]).returncode == 0  # re-deploying an old ID moves it to the end
    entries = good.read_text().split()
    assert len(entries) == 20 and entries[-1] == ids[3] and entries.count(ids[3]) == 1


def test_deploy_watermark_failure_warns_without_failing_the_deploy(tmp_path):
    result = run_watermark_block(tmp_path / "missing", NEW)
    assert result.returncode == 0
    assert "could not update" in result.stderr
