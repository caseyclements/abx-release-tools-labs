"""``setup.sh`` is where a release's credentials are turned into files on disk.

Every other action depends on what it exports, and until these tests existed nothing exercised it
at all: it needs AWS to run for real, so it was reasoned about and shipped. These cover the parts
that do not need AWS, which is all of it except where the variables come from.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ACTIONS

SETUP = ACTIONS / "setup" / "setup.sh"

# The script runs with a deliberately small environment, so that a variable it needs has to be
# passed rather than inherited from whoever ran the tests. git's own directory is added because on
# macOS `/usr/bin/git` is an xcrun shim that cannot load under a stripped environment, which failed
# the whole file for a reason that had nothing to do with the script.
_GIT = shutil.which("git")
PATH = ":".join(
    dict.fromkeys(
        ([str(Path(_GIT).parent)] if _GIT else [])
        + ["/usr/bin", "/bin", "/usr/local/bin"]
    )
)

SECRET = {
    "GARASIGN_USERNAME": "ai-builder-experience",
    "GARASIGN_PASSWORD": "a password",
    "GPG_KEY_ID": "DEADBEEFCAFE",
}


@pytest.fixture
def runner(tmp_path: Path) -> dict[str, str]:
    """A runner's temp directory, and a HOME of its own for the git identity to land in.

    Deliberately not a git repository. An earlier version of this fixture ran `git init` here, which
    made every test in this file pass while `setup.sh` could not run in a job that checks nothing
    out -- the shape every artifact-only job has. The fixture was supplying the one thing the script
    silently required.
    """
    home = tmp_path / "home"
    home.mkdir()
    return {
        "HOME": str(home),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(tmp_path / "github-env"),
        "GITHUB_RUN_ID": "424242",
        "AWS_SECRET_ID": "abx/release/strands",
    }


def setup(
    tmp_path: Path, runner: dict[str, str], **secret: str
) -> subprocess.CompletedProcess[str]:
    Path(runner["GITHUB_ENV"]).touch()
    return subprocess.run(
        ["bash", str(SETUP)],
        check=False,
        cwd=tmp_path,
        env={"PATH": PATH, **runner, **(SECRET | secret)},
        capture_output=True,
        text=True,
    )


def exported(runner: dict[str, str]) -> dict[str, str]:
    lines = Path(runner["GITHUB_ENV"]).read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def test_it_writes_the_garasign_env_file_docker_expects(tmp_path: Path, runner) -> None:
    result = setup(tmp_path, runner)

    assert result.returncode == 0, result.stderr
    envfile = Path(exported(runner)["GARASIGN_ENVFILE"])
    assert envfile.read_text() == (
        "GRS_CONFIG_USER1_USERNAME=ai-builder-experience\nGRS_CONFIG_USER1_PASSWORD=a password\n"
    )


def test_the_env_file_is_not_world_readable(tmp_path: Path, runner) -> None:
    """It holds a password in plain text, on a runner other steps also run on."""
    setup(tmp_path, runner)

    envfile = Path(exported(runner)["GARASIGN_ENVFILE"])
    assert envfile.stat().st_mode & 0o077 == 0, oct(envfile.stat().st_mode)


@pytest.mark.parametrize("missing", sorted(SECRET))
def test_a_missing_value_is_named(tmp_path: Path, runner, missing: str) -> None:
    """Named, because the alternative is a Garasign auth failure that points nowhere near here."""
    result = setup(tmp_path, runner, **{missing: ""})

    assert result.returncode != 0
    assert missing in result.stderr


@pytest.mark.parametrize("credential", ["GARASIGN_USERNAME", "GARASIGN_PASSWORD"])
def test_a_newline_in_a_credential_is_refused(
    tmp_path: Path, runner, credential: str
) -> None:
    """A docker --env-file is line-oriented. A newline truncates the value silently, and the run
    then fails as a Garasign auth error, which sends the next person looking in the wrong place.
    """
    result = setup(tmp_path, runner, **{credential: "first\nsecond"})

    assert result.returncode != 0
    assert "newline" in result.stdout + result.stderr


def test_it_can_be_run_twice(tmp_path: Path, runner) -> None:
    """Upstream used a bare mkdir, so rerunning a failed job failed on the directory instead."""
    assert setup(tmp_path, runner).returncode == 0
    second = setup(tmp_path, runner)

    assert second.returncode == 0, second.stderr


def test_kondukto_is_optional_but_wired_when_present(tmp_path: Path, runner) -> None:
    without = setup(tmp_path, runner)
    assert without.returncode == 0, without.stderr
    assert exported(runner)["KONDUKTO_ENVFILE"] == ""

    with_token = setup(tmp_path, runner, KONDUKTO_TOKEN="a-token")
    assert with_token.returncode == 0, with_token.stderr
    envfile = Path(exported(runner)["KONDUKTO_ENVFILE"])
    assert envfile.read_text() == "KONDUKTO_TOKEN=a-token\n"


def test_it_exports_what_the_other_actions_read(tmp_path: Path, runner) -> None:
    """The contract the README states. Every one of these is read by another action by name."""
    setup(tmp_path, runner)

    assert set(exported(runner)) >= {
        "AWS_BUCKET",
        "GPG_KEY_ID",
        "GARASIGN_ENVFILE",
        "KONDUKTO_ENVFILE",
        "RELEASE_ASSETS",
        "S3_ASSETS",
    }


def test_the_asset_directories_exist_and_record_the_run(tmp_path: Path, runner) -> None:
    setup(tmp_path, runner)
    env = exported(runner)

    assert Path(env["RELEASE_ASSETS"]).is_dir()
    assert Path(env["S3_ASSETS"]).is_dir()
    assert (
        Path(env["RELEASE_ASSETS"]) / "release_run_id.txt"
    ).read_text().strip() == "424242"


def test_it_runs_in_a_job_that_checked_nothing_out(tmp_path: Path, runner) -> None:
    """The shape of every artifact-only job: download, sign, upload, no working tree.

    `git config` without `--global` needs a repository, and `set -e` turns that into a failed setup
    rather than a warning -- so a job wired exactly as the signing guide describes dies on
    `fatal: not in a git directory` before any credential has been written anywhere.
    """
    assert not (tmp_path / ".git").exists(), "this test is meaningless in a repository"

    result = setup(tmp_path, runner)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "not in a git directory" not in result.stderr


def test_it_sets_the_bot_identity_where_a_later_checkout_will_find_it(
    tmp_path: Path, runner
) -> None:
    """Setting it globally is only useful if it is actually readable as the committer identity."""
    setup(tmp_path, runner)

    # `check=False`, deliberately: `git config --get` exits 1 when the key is simply unset, which is
    # the exact failure this test exists to catch. Raising on it would report an exit code and skip
    # the assertion below, so the message would never name what was expected.
    shown = subprocess.run(
        ["git", "config", "--global", "--get", "user.name"],
        check=False,
        env={"PATH": PATH, "HOME": runner["HOME"]},
        capture_output=True,
        text=True,
    )
    assert shown.stdout.strip() == "mongodb-dbx-release-bot[bot]", shown.stderr
