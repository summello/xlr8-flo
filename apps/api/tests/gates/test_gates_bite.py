from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).parent / "fixtures"
FLO = ROOT / "agents" / "scripts" / "flo"


def run_gate(
    command: Sequence[str],
    *,
    cwd: Path,
    python_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for variable in (
        "COVERAGE_PROCESS_START",
        "COV_CORE_CONFIG",
        "COV_CORE_DATAFILE",
        "COV_CORE_SOURCE",
    ):
        environment.pop(variable, None)
    if python_path is not None:
        environment["PYTHONPATH"] = str(python_path)
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def assert_rejects(result: subprocess.CompletedProcess[str], *names: str) -> None:
    assert result.returncode != 0, output(result)
    gate_output = output(result)
    for name in names:
        assert name in gate_output, gate_output


def assert_accepts(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, output(result)


def workflow_step(source: str, name: str) -> str:
    marker = f"      - name: {name}"
    start = source.index(marker)
    end = source.find("\n      - name:", start + len(marker))
    return source[start:] if end == -1 else source[start:end]


def assert_release_workflow_policy(source: str) -> None:
    image_scan = workflow_step(source, "Scan built API image")
    assert "scan-type: image" in image_scan
    assert "severity: 'CRITICAL,HIGH'" in image_scan
    assert "exit-code: '1'" in image_scan
    assert "ignore-unfixed: true" in image_scan
    assert "format: sarif" in image_scan
    assert "continue-on-error" not in image_scan
    assert source.index("- name: Scan built API image") < source.index(
        'docker push "$IMAGE_TAG"'
    )
    assert source.index("- name: Generate deployed-image SBOM") < source.index(
        'docker push "$IMAGE_TAG"'
    )
    assert "category: trivy-dependency-scan" in source
    assert "category: trivy-container-image" in source
    assert source.index("gcloud run jobs execute flo-migrate") < source.index(
        "gcloud run deploy flo-api"
    )
    deploy = source[source.index("gcloud run deploy flo-api") :]
    assert '--image "${{ steps.push.outputs.digest }}"' in deploy
    assert "--source" not in deploy
    assert "credentials_json" not in source
    assert "workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}" in source
    assert "grep -RIlE -- 'sk-|-----BEGIN' apps/web/dist" in source
    assert 'grep -RIlF -- "$GCP_PROJECT" apps/web/dist' in source


def assert_single_origin_release_policy(
    workflow: str,
    production_environment: str,
    worker_configuration: str,
) -> None:
    environment = dict(
        line.partition("=")[::2]
        for line in production_environment.splitlines()
        if line and not line.startswith("#")
    )
    worker = tomllib.loads(worker_configuration)
    assert environment == {"VITE_API_BASE_URL": "/api"}
    assert "CLOUD_RUN_ORIGIN: ${{ vars.CLOUD_RUN_ORIGIN }}" in workflow
    assert "--ingress all" in workflow
    assert 'grep -RIlF -- "$cloud_run_host" apps/web/dist' in workflow
    assert "https?://xlr8flo\\.summello\\.com/api" in workflow
    assert workflow.index("npx wrangler deploy") < workflow.index("npx wrangler pages deploy")
    assert worker["workers_dev"] is False
    assert worker["routes"] == [
        {
            "pattern": "xlr8flo.summello.com/api/*",
            "zone_name": "summello.com",
        }
    ]


def make_package(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "__init__.py").write_text("", encoding="utf-8")


def copy_import_linter_project(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    project.mkdir()
    shutil.copy2(ROOT / ".importlinter", project / ".importlinter")
    for package in (
        project / "flo",
        project / "flo" / "api",
        project / "flo" / "modules",
        project / "flo" / "modules" / "a",
        project / "flo" / "modules" / "b",
        project / "flo" / "kernel",
        project / "flo" / "kernel" / "db",
        project / "flo" / "kernel" / "identity",
    ):
        make_package(package)
    (project / "flo" / "modules" / "b" / "values.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (project / "flo" / "modules" / "b" / "__init__.py").write_text(
        "from .values import VALUE\n", encoding="utf-8"
    )
    (project / "flo" / "kernel" / "identity" / "local.py").write_text(
        "class LocalIdentityProvider: pass\n", encoding="utf-8"
    )
    (project / "flo" / "kernel" / "identity" / "__init__.py").write_text(
        "from flo.kernel.identity.local import LocalIdentityProvider\n",
        encoding="utf-8",
    )
    return project


def run_import_linter(project: Path) -> subprocess.CompletedProcess[str]:
    return run_gate(
        ["lint-imports", "--config", ".importlinter", "--no-cache"],
        cwd=project,
        python_path=project,
    )


def copy_tenancy_project(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    scripts = project / "apps" / "api" / "scripts"
    source = project / "apps" / "api" / "src"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "apps" / "api" / "scripts" / "check_tenancy.py", scripts)
    shutil.copytree(ROOT / "apps" / "api" / "src" / "flo", source / "flo")
    shutil.copytree(ROOT / "migrations", project / "migrations")
    return project


def run_tenancy(project: Path) -> subprocess.CompletedProcess[str]:
    return run_gate(
        [sys.executable, "apps/api/scripts/check_tenancy.py"],
        cwd=project,
        python_path=project / "apps" / "api" / "src",
    )


def copy_flo(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    destination = project / "agents" / "scripts" / "flo"
    destination.parent.mkdir(parents=True)
    shutil.copy2(FLO, destination)
    return project


def commit_repository(project: Path) -> None:
    assert_accepts(run_gate(["git", "init", "--quiet"], cwd=project))
    assert_accepts(run_gate(["git", "add", "."], cwd=project))
    assert_accepts(
        run_gate(
            [
                "git",
                "-c",
                "user.name=Gate Self-Test",
                "-c",
                "user.email=gate-self-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            cwd=project,
        )
    )


def load_flo() -> ModuleType:
    loader = SourceFileLoader("flo_delivery_script", str(FLO))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_money_float_ban_rejects_return_annotation_and_then_passes(tmp_path: Path) -> None:
    money_module = tmp_path / "modules" / "budget"
    money_module.mkdir(parents=True)
    violation = money_module / "amounts.py"
    shutil.copy2(FIXTURES / "money_float.py", violation)
    command = [
        sys.executable,
        str(ROOT / "apps" / "api" / "scripts" / "check_money_floats.py"),
        str(money_module),
    ]

    assert_rejects(run_gate(command, cwd=tmp_path), "amounts.py", "float is banned")
    violation.unlink()
    assert_accepts(run_gate(command, cwd=tmp_path))


def test_module_boundary_rejects_cross_module_import_and_then_passes(tmp_path: Path) -> None:
    project = copy_import_linter_project(tmp_path)
    violation = project / "flo" / "modules" / "a" / "violation.py"
    shutil.copy2(FIXTURES / "module_boundary.py", violation)

    assert_rejects(
        run_import_linter(project),
        "Business modules are independent",
        "flo.modules.a",
        "flo.modules.b",
        "BROKEN",
    )
    violation.unlink()
    assert_accepts(run_import_linter(project))


@pytest.mark.parametrize(
    ("source_module", "violation_path"),
    [
        ("flo.api.violation", Path("flo/api/violation.py")),
        ("flo.modules.a.violation", Path("flo/modules/a/violation.py")),
        ("flo.kernel.db.violation", Path("flo/kernel/db/violation.py")),
    ],
    ids=("api", "module", "kernel-sibling"),
)
def test_identity_contract_rejects_adapter_import_and_then_passes(
    tmp_path: Path, source_module: str, violation_path: Path
) -> None:
    project = copy_import_linter_project(tmp_path)
    violation = project / violation_path
    shutil.copy2(FIXTURES / "identity_adapter.py", violation)

    assert_rejects(
        run_import_linter(project),
        "Local identity adapter is hidden behind the port",
        source_module,
        "flo.kernel.identity.local",
        "BROKEN",
    )
    violation.unlink()
    assert_accepts(run_import_linter(project))


def test_tenancy_rls_rejects_numeric_table_without_rls_and_then_passes(
    tmp_path: Path,
) -> None:
    project = copy_tenancy_project(tmp_path)
    violation = project / "migrations" / "9999_tenant_without_rls.py"
    shutil.copy2(FIXTURES / "tenant_without_rls.py", violation)

    assert_rejects(run_tenancy(project), "9999_tenant_without_rls.py", "tenant table t")
    violation.unlink()
    assert_accepts(run_tenancy(project))


def test_tenancy_policy_rejects_constant_predicate_and_then_passes(tmp_path: Path) -> None:
    project = copy_tenancy_project(tmp_path)
    violation = project / "migrations" / "9999_tenant_constant_policy.py"
    shutil.copy2(FIXTURES / "tenant_constant_policy.py", violation)

    assert_rejects(run_tenancy(project), "9999_tenant_constant_policy.py", "tenant table t")
    violation.unlink()
    assert_accepts(run_tenancy(project))


def test_tenancy_openapi_rejects_org_id_parameter_and_then_passes(tmp_path: Path) -> None:
    project = copy_tenancy_project(tmp_path)
    health = project / "apps" / "api" / "src" / "flo" / "api" / "health.py"
    clean_health = health.read_bytes()
    shutil.copy2(FIXTURES / "openapi_org_id.py", health)

    assert_rejects(run_tenancy(project), "OpenAPI operation accepts org_id", "GET /planted")
    health.write_bytes(clean_health)
    assert_accepts(run_tenancy(project))


def test_deploy_resource_check_rejects_argon2_memory_drift_and_then_passes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    workflow = project / ".github" / "workflows" / "ci.yml"
    config = project / "apps" / "api" / "src" / "flo" / "kernel" / "config.py"
    script = project / "apps" / "api" / "scripts" / "check_deploy_resources.py"
    workflow.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / ".github" / "workflows" / "ci.yml", workflow)
    shutil.copy2(ROOT / "apps" / "api" / "src" / "flo" / "kernel" / "config.py", config)
    shutil.copy2(ROOT / "apps" / "api" / "scripts" / "check_deploy_resources.py", script)
    clean_config = config.read_bytes()
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "default=64 * 1024,",
            "default=256 * 1024,",
            1,
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(script),
        "--workflow",
        str(workflow),
        "--config",
        str(config),
    ]

    assert_rejects(run_gate(command, cwd=project), "Argon2 invariant", "require 1280 MiB")
    config.write_bytes(clean_config)
    assert_accepts(run_gate(command, cwd=project))


def test_deploy_resource_check_rejects_a_missing_runtime_service_account(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    workflow = project / ".github" / "workflows" / "ci.yml"
    config = project / "apps" / "api" / "src" / "flo" / "kernel" / "config.py"
    script = project / "apps" / "api" / "scripts" / "check_deploy_resources.py"
    workflow.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / ".github" / "workflows" / "ci.yml", workflow)
    shutil.copy2(ROOT / "apps" / "api" / "src" / "flo" / "kernel" / "config.py", config)
    shutil.copy2(ROOT / "apps" / "api" / "scripts" / "check_deploy_resources.py", script)
    clean_workflow = workflow.read_bytes()
    source = workflow.read_text(encoding="utf-8")
    stripped = re.sub(
        r"\n\s*--service-account [^\n]*\\",
        "",
        source,
        count=1,
    )
    assert stripped != source, "the deploy step must name a runtime service account"
    workflow.write_text(stripped, encoding="utf-8")
    command = [
        sys.executable,
        str(script),
        "--workflow",
        str(workflow),
        "--config",
        str(config),
    ]

    assert_rejects(run_gate(command, cwd=project), "--service-account is missing")
    workflow.write_bytes(clean_workflow)
    assert_accepts(run_gate(command, cwd=project))


def test_release_workflow_policy_rejects_a_nonblocking_image_scan_and_then_passes() -> None:
    source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    image_scan = workflow_step(source, "Scan built API image")
    violation = source.replace(
        image_scan,
        image_scan.replace("exit-code: '1'", "exit-code: '0'"),
        1,
    )

    with pytest.raises(AssertionError):
        assert_release_workflow_policy(violation)
    assert_release_workflow_policy(source)


@pytest.mark.parametrize(
    "violation",
    (
        "absolute-api",
        "missing-host-guard",
        "missing-absolute-guard",
        "missing-ingress",
        "wrong-route",
    ),
)
def test_single_origin_release_policy_rejects_d17_violations_and_then_passes(
    violation: str,
) -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    production_environment = (ROOT / "apps" / "web" / ".env.production").read_text(
        encoding="utf-8"
    )
    worker_configuration = (ROOT / "infra" / "cloudflare" / "wrangler.toml").read_text(
        encoding="utf-8"
    )
    planted_workflow = workflow
    planted_environment = production_environment
    planted_worker = worker_configuration
    if violation == "absolute-api":
        planted_environment = production_environment.replace(
            "VITE_API_BASE_URL=/api",
            "VITE_API_BASE_URL=https://api.example.test",
        )
    elif violation == "missing-host-guard":
        planted_workflow = workflow.replace(
            'grep -RIlF -- "$cloud_run_host" apps/web/dist',
            "true",
        )
    elif violation == "missing-absolute-guard":
        planted_workflow = workflow.replace(
            "https?://xlr8flo\\.summello\\.com/api",
            "removed-absolute-api-guard",
        )
    elif violation == "missing-ingress":
        planted_workflow = workflow.replace("--ingress all", "--ingress unspecified")
    else:
        planted_worker = worker_configuration.replace(
            "xlr8flo.summello.com/api/*",
            "api.xlr8flo.summello.com/*",
        )

    with pytest.raises(AssertionError):
        assert_single_origin_release_policy(
            planted_workflow,
            planted_environment,
            planted_worker,
        )
    assert_single_origin_release_policy(
        workflow,
        production_environment,
        worker_configuration,
    )


def test_production_runbook_and_spa_environment_keep_release_configuration_external() -> None:
    production_env = (ROOT / "apps" / "web" / ".env.production").read_text(encoding="utf-8")
    configured_names = {
        line.partition("=")[0]
        for line in production_env.splitlines()
        if line and not line.startswith("#")
    }
    setup = (ROOT / "infra" / "SETUP.md").read_text(encoding="utf-8")

    assert configured_names == {"VITE_API_BASE_URL"}
    assert "VITE_API_BASE_URL=/api" in production_env
    assert "Full (strict)" in setup
    assert "Strict-Transport-Security" in setup
    assert "gcloud run services update-traffic flo-api" in setup
    assert "credentials_json" in setup
    assert "not part of this deployment" in setup


def test_gitleaks_rejects_fake_aws_key_and_then_passes(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    shutil.copy2(ROOT / ".gitleaks.toml", project)
    violation = project / "credentials.py"
    shutil.copy2(FIXTURES / "aws_key.py", violation)
    violation.write_text(
        violation.read_text(encoding="utf-8").replace("<PLANTED>", ""),
        encoding="utf-8",
    )
    commit_repository(project)
    command = ["gitleaks", "detect", "--no-banner", "--verbose"]

    assert_rejects(run_gate(command, cwd=project), "credentials.py", "aws-access-token")
    violation.unlink()
    shutil.rmtree(project / ".git")
    commit_repository(project)
    assert_accepts(run_gate(command, cwd=project))


def test_mypy_strict_rejects_untyped_function_and_then_passes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "typed.py").write_text(
        "def typed(value: str) -> str:\n    return value\n", encoding="utf-8"
    )
    violation = source / "untyped.py"
    shutil.copy2(FIXTURES / "untyped.py", violation)
    command = [sys.executable, "-m", "mypy", "--strict", "--no-color-output", str(source)]

    assert_rejects(run_gate(command, cwd=tmp_path), "untyped.py", "no-untyped-def")
    violation.unlink()
    assert_accepts(run_gate(command, cwd=tmp_path))


def test_roadmap_check_rejects_rendered_drift_and_then_passes(tmp_path: Path) -> None:
    project = copy_flo(tmp_path)
    (project / "docs").mkdir()
    (project / "agents" / "roadmap.yaml").write_text(
        json.dumps(
            {
                "milestones": [
                    {
                        "id": "M0",
                        "name": "Rails",
                        "goal": "Ship rails",
                        "branch": "milestone/M0-rails",
                    }
                ],
                "epics": [
                    {
                        "id": "E01",
                        "name": "Platform",
                        "milestone": "M0",
                        "stories": [
                            {
                                "id": "E01-S07",
                                "t": "Gate self-tests",
                                "k": "chore",
                                "s": "M",
                                "reqs": ["SEC-012"],
                                "dep": [],
                                "st": "todo",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = project / "docs" / "claude-plan.md"
    plan.write_text("<!-- ROADMAP:BEGIN -->\n<!-- ROADMAP:END -->\n", encoding="utf-8")
    (project / "agents" / "project-memory.md").write_text(
        "<!-- STATE:BEGIN -->\n<!-- STATE:END -->\n", encoding="utf-8"
    )
    yaml_module = project / "yaml.py"
    shutil.copy2(FIXTURES / "yaml.py", yaml_module)
    command = [sys.executable, "agents/scripts/flo", "roadmap"]
    assert_accepts(run_gate(command, cwd=project, python_path=project))
    clean_plan = plan.read_bytes()
    shutil.copy2(FIXTURES / "rendered_drift.md", plan)

    assert_rejects(
        run_gate([*command, "--check"], cwd=project, python_path=project),
        "docs/claude-plan.md §5 is stale",
    )
    plan.write_bytes(clean_plan)
    assert_accepts(run_gate([*command, "--check"], cwd=project, python_path=project))


def test_score_check_rejects_rendered_drift_and_then_passes(tmp_path: Path) -> None:
    project = copy_flo(tmp_path)
    shutil.copy2(ROOT / "agents" / "scorecard.json", project / "agents")
    scorecard = project / "agents" / "SCORECARD.md"
    shutil.copy2(ROOT / "agents" / "SCORECARD.md", scorecard)
    clean_scorecard = scorecard.read_bytes()
    shutil.copy2(FIXTURES / "rendered_drift.md", scorecard)
    command = [sys.executable, "agents/scripts/flo", "score", "--check"]

    assert_rejects(run_gate(command, cwd=project), "agents/SCORECARD.md is stale")
    scorecard.write_bytes(clean_scorecard)
    assert_accepts(run_gate(command, cwd=project))


def test_run_check_fails_when_required_tool_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    flo = load_flo()
    exit_127 = tmp_path / "exit_127.py"
    exit_127.write_text("raise SystemExit(127)\n", encoding="utf-8")
    monkeypatch.setattr(
        flo,
        "CHECKS",
        [
            ("missing-executable", ["missing-e01-s07-tool"]),
            ("exit-127", [sys.executable, str(exit_127)]),
        ],
    )
    monkeypatch.setattr(flo, "M0_MISSING_TOOL_OPTOUTS", {})

    assert flo.run_check(tmp_path) is False
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_status:
        flo.cmd_check([])
    assert exit_status.value.code == 1
    gate_output = capsys.readouterr().out
    assert "missing-executable" in gate_output
    assert "exit-127" in gate_output


def test_run_check_honours_only_named_missing_tool_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    flo = load_flo()
    monkeypatch.setattr(flo, "CHECKS", [("future-gate", ["missing-e01-s07-tool"])])
    monkeypatch.setattr(
        flo,
        "M0_MISSING_TOOL_OPTOUTS",
        {"future-gate": "not delivered; remove in E99-S99"},
    )

    assert flo.run_check(tmp_path) is True
    assert "remove in E99-S99" in capsys.readouterr().out


def test_run_check_points_pytest_at_local_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flo = load_flo()
    observed_environment: dict[str, str] = {}

    def successful_check(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        observed_environment.update(environment)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(flo, "CHECKS", [("pytest", ["pytest"])])
    monkeypatch.setattr(flo.subprocess, "run", successful_check)

    assert flo.run_check(tmp_path) is True
    assert observed_environment["DATABASE_URL"] == flo.LOCAL_TEST_DATABASE_URL
