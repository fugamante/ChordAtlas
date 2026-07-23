import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def test_ci_runs_release_validation_with_pinned_actions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'python-version: "3.11"' in workflow
    assert 'python -m pip install -e ".[dev]"' in workflow
    assert "chordchart schemas --check" in workflow
    assert "chordchart release-check" in workflow
    assert "python -m pytest" not in workflow
    assert "chordchart snapshots check --format json --diff-dir snapshot-diffs" in workflow
    assert "> snapshot-result.json" in workflow
    assert 'for filename in payload["drift_files"]' in workflow
    assert "::error file={filename}::Snapshot drift detected" in workflow
    assert "path: snapshot-diffs" in workflow
    assert "if-no-files-found: ignore" in workflow

    install_step = re.search(
        r"- name: Install project\n(?P<body>.*?)(?=\n\s+- name: Check schema mirror)",
        workflow,
        re.DOTALL,
    )
    assert install_step is not None
    assert "id: install" in install_step.group(0)

    diagnostic_condition = "if: ${{ !cancelled() && steps.install.outcome == 'success' }}"

    snapshot_step = re.search(
        r"- name: Check snapshots\n(?P<body>.*?)(?=\n\s+- name: Upload snapshot diffs)",
        workflow,
        re.DOTALL,
    )
    assert snapshot_step is not None
    assert diagnostic_condition in snapshot_step.group(0)
    assert "continue-on-error" not in snapshot_step.group(0)

    upload_step = re.search(
        r"- name: Upload snapshot diffs\n(?P<body>.*)",
        workflow,
        re.DOTALL,
    )
    assert upload_step is not None
    assert diagnostic_condition in upload_step.group(0)
    assert "always()" not in upload_step.group(0)

    upload_pin = re.search(r"actions/upload-artifact@([0-9a-f]{40})", workflow)
    assert upload_pin is not None
    assert "<PINNED_UPLOAD_ARTIFACT_SHA>" not in workflow

    checkout_pin = re.search(
        r"# actions/checkout v\d+\.\d+\.\d+\s*\n\s+uses: actions/checkout@([0-9a-f]{40})",
        workflow,
    )
    setup_python_pin = re.search(
        r"# actions/setup-python v\d+\.\d+\.\d+\s*\n\s+uses: actions/setup-python@([0-9a-f]{40})",
        workflow,
    )
    assert checkout_pin is not None
    assert setup_python_pin is not None
    assert "@v" not in workflow


def test_publish_workflow_is_release_and_environment_gated() -> None:
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)

    assert parsed["on"] == {"release": {"types": ["published"]}}
    assert "workflow_dispatch" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "if: ${{ !github.event.release.prerelease }}" in workflow
    assert 'expected_tag = f"v{version}"' in workflow
    assert 'if tag_type != "tag"' in workflow
    assert '["git", "merge-base", "--is-ancestor", tag_commit, "origin/main"]' in workflow

    assert workflow.count("id-token: write") == 1
    publish_job = workflow.split("\n  publish:\n", maxsplit=1)[1]
    assert "needs: build" in publish_job
    assert "name: pypi" in publish_job
    assert "url: https://pypi.org/p/chordatlas" in publish_job
    assert "password:" not in workflow
    assert "user:" not in workflow
    assert "skip-existing" not in workflow

    expected_pins = {
        "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "pypa/gh-action-pypi-publish": "cef221092ed1bacb1cc03d23a2d87d1d172e277b",
    }
    pins = dict(re.findall(r"uses: ([\w-]+/[\w-]+)@([0-9a-f]{40})", workflow))
    assert pins == expected_pins


def test_publish_workflow_separates_build_from_oidc_permission() -> None:
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    build_job, publish_job = workflow.split("\n  publish:\n", maxsplit=1)

    assert "python -m build" in build_job
    assert "python -m twine check dist/*" in build_job
    assert "id-token: write" not in build_job
    assert "pypa/gh-action-pypi-publish" not in build_job
    assert "python -m build" not in publish_job
    assert "id-token: write" in publish_job
    assert "pypa/gh-action-pypi-publish" in publish_job
