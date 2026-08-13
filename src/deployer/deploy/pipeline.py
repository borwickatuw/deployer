"""The shared back half of the two deploy entry points.

bin/deploy.py and the ci-deploy console script resolve their inputs very
differently — deploy.py reads deploy.toml from --deploy-toml or the link
registry, configures an AWS profile and loads config.toml; ci-deploy takes a
pre-resolved config JSON from a file or S3 and configures no profile at all,
because CI runs on role credentials. From preflight onward they were clones.
That shared half lives here.
"""

from pathlib import Path

from deployer.config import parse_deploy_config
from deployer.deploy.context import DeployOptions, EnvironmentTarget
from deployer.deploy.deployer import Deployer, handle_push_error
from deployer.deploy.preflight import PreflightError, PreflightOptions, run_preflight_checks
from deployer.timing import DeploymentTimer
from deployer.utils import log, log_error, log_success

# Exit code both entry points use to signal "deployed, but health checks failed".
HEALTH_FAILURE_EXIT_CODE = 2


def run_deploy_pipeline(
    deploy_toml_path: Path,
    target: EnvironmentTarget,
    *,
    preflight: PreflightOptions,
    options: DeployOptions,
    timer: DeploymentTimer | None = None,
    timing_output: Path | None = None,
    ecr_hint: bool = False,
) -> int:
    """Run pre-flight checks and deploy, returning the process exit code.

    Args:
        deploy_toml_path: Path to the application's deploy.toml.
        target: The environment being deployed to — name, type and resolved
            config (from config.toml or a resolved-config JSON).
        preflight: Which pre-flight checks to skip.
        options: How the deploy itself behaves (dry run, force, force build).
        timer: Collects per-stage timings; its report is printed when present.
        timing_output: Where to also save the timing report as JSON.
        ecr_hint: Add the "verify ECR repository access" hint to push errors.
            deploy.py sets this; ci-deploy does not.

    Returns:
        0 on success, 1 on a handled failure, 2 if services deployed but their
        health checks failed.

    Raises:
        RuntimeError: If deployment failed for a reason other than an image push.
    """
    try:
        deploy_config = parse_deploy_config(deploy_toml_path)
    except Exception as e:  # noqa: BLE001 — CLI error handler for deploy.toml parse
        log_error(f"Failed to parse deploy.toml: {e}")
        return 1

    try:
        run_preflight_checks(
            deploy_config=deploy_config,
            target=target,
            project_dir=deploy_toml_path.parent,
            options=preflight,
        )
    except PreflightError as e:
        log_error(str(e))
        return 1

    try:
        deployer = Deployer(
            config_path=str(deploy_toml_path),
            environment=target.type,
            env_config=target.config,
            options=options,
            timer=timer,
        )
    except ValueError as e:
        log_error(str(e))
        return 1

    try:
        _, health_failures = deployer.deploy()
    except RuntimeError as e:
        if handle_push_error(e, include_ecr_hint=ecr_hint):
            return 1
        raise

    if timer:
        print()
        log("Timing report:")
        print(timer.report.to_json())

        if timing_output:
            timer.report.save_json(timing_output)
            log_success(f"Timing saved to {timing_output}")

    return HEALTH_FAILURE_EXIT_CODE if health_failures else 0
