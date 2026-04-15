#!/usr/bin/env python3
"""
Run commands in ECS containers for staging environments.

Usage:
    # List services in environment
    python bin/ecs-run.py list myapp-staging

    # List available commands from deploy.toml (uses linked path or --deploy-toml)
    python bin/ecs-run.py run myapp-staging --list-commands

    # Run named commands from deploy.toml [commands] section
    python bin/ecs-run.py run myapp-staging migrate
    python bin/ecs-run.py run myapp-staging collectstatic

    # Run arbitrary commands
    python bin/ecs-run.py exec myapp-staging python -c "print('hi')"

    # Specify a different service (default: web)
    python bin/ecs-run.py exec myapp-staging -s celery python -c "print('hello')"

    # Link environment to deploy.toml (one-time setup)
    python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml
"""

import sys
from pathlib import Path

import boto3
import click

from deployer.aws import cloudwatch, ecs
from deployer.core.config import (
    command_requires_ddl,
    get_run_command,
    load_deploy_toml,
    load_environment_config,
)
from deployer.utils import (
    configure_aws_profile,
    get_linked_deploy_toml,
    validate_environment_deployed,
)


def resolve_environment(env_name: str) -> tuple[Path, str] | None:
    """Resolve environment name to path and cluster name."""
    env_path, error = validate_environment_deployed(env_name)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return None

    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return None

    cluster_name = config.get("infrastructure", {}).get("cluster_name")
    if not cluster_name:
        print(f"Error: Could not get ECS cluster name for '{env_name}'", file=sys.stderr)
        return None

    return env_path, cluster_name


def _display_task_logs(
    log_group: str, stream_prefix: str, container_name: str, task_id: str
) -> None:
    """Fetch and display CloudWatch logs for a task."""
    events = cloudwatch.get_task_logs(
        log_group=log_group,
        stream_prefix=stream_prefix,
        container_name=container_name,
        task_id=task_id,
        limit=500,
    )

    if events is None:
        print("(Could not fetch logs - stream may not exist yet)", file=sys.stderr)
        return

    if not events:
        print("(No log output)")
        return

    for event in events:
        message = event.get("message", "").rstrip("\n")
        print(message)


def _run_ecs_command(
    cluster_name: str,
    service_name: str,
    container_name: str | None,
    command: list[str],
    environment: list[dict] | None = None,
    wait: bool = True,
    timeout: int = 300,
    show_logs: bool = True,
    use_migrate_credentials: bool = False,
) -> int:
    """Run a command in an ECS container."""
    ecs_client = boto3.client("ecs")

    print(f"Getting service configuration for '{service_name}'...")
    network_config, service_task_def = ecs.get_service_info(
        cluster_name, service_name, ecs_client=ecs_client
    )

    if not network_config:
        print(f"Error: Could not get network config for service '{service_name}'", file=sys.stderr)
        print("Is the service running?", file=sys.stderr)
        return 1

    if not service_task_def:
        print(f"Error: Could not get task definition for service '{service_name}'", file=sys.stderr)
        return 1

    if use_migrate_credentials:
        task_family = service_task_def.split("/")[-1].rsplit(":", 1)[0]
        base = task_family.rsplit("-", 1)[0]
        task_definition = f"{base}-migrate"
        container_name = "migrate"
        print("Using migrate credentials (DDL+DML) for migration command")
    else:
        task_definition = service_task_def

    containers = ecs.get_task_containers(task_definition, ecs_client=ecs_client)

    if not container_name:
        if not containers:
            print("Error: No containers found in task definition", file=sys.stderr)
            return 1
        container_name = containers[0]["name"]

    print(f"Task definition: {task_definition}")
    print(f"Container: {container_name}")
    print(f"Command: {' '.join(command)}")
    print()

    print("Starting task...")
    task_arn = ecs.run_task(
        cluster_name=cluster_name,
        task_definition=task_definition,
        network_config=network_config,
        container_name=container_name,
        command=command,
        environment=environment,
        ecs_client=ecs_client,
    )

    if not task_arn:
        print("Error: Failed to start task", file=sys.stderr)
        return 1

    task_id = task_arn.split("/")[-1]
    print(f"Task ARN: {task_arn}")
    print(f"Task ID: {task_id}")

    logs_info = ecs.get_logs_location_from_containers(containers, container_name)
    if logs_info:
        log_group, stream_prefix = logs_info
        log_stream = f"{stream_prefix}/{container_name}/{task_id}"
        print(f"\nLogs: CloudWatch log group '{log_group}', stream '{log_stream}'")

    if not wait:
        print("\nTask started (not waiting for completion)")
        return 0

    print("\nWaiting for task to complete...")
    exit_code = ecs.wait_for_task(cluster_name, task_arn, timeout, ecs_client=ecs_client)

    if exit_code == 0:
        print("\nTask completed successfully")
    elif exit_code == -1:
        print("\nTask failed or timed out", file=sys.stderr)
    else:
        print(f"\nTask exited with code {exit_code}", file=sys.stderr)

    if show_logs and logs_info:
        print("\n" + "=" * 60)
        print("Task Output:")
        print("=" * 60)
        _display_task_logs(log_group, stream_prefix, container_name, task_id)

    return exit_code


# =============================================================================
# Commands
# =============================================================================


def cmd_list(environment: str) -> int:
    """List services and containers in an environment."""
    result = resolve_environment(environment)
    if not result:
        return 1

    env_path, cluster_name = result

    print(f"Environment: {environment}")
    print(f"Cluster: {cluster_name}")
    print()

    services = ecs.get_services(cluster_name)
    if not services:
        print("No services found in cluster.")
        return 0

    print("Services:")
    print("-" * 60)

    for svc in services:
        status_indicator = "+" if svc.running_count > 0 else "-"
        print(f"  [{status_indicator}] {svc.name}")
        print(f"      Status: {svc.status}")
        print(f"      Running: {svc.running_count}/{svc.desired_count}")

        task_def = svc.task_definition
        if task_def:
            containers = ecs.get_task_containers(task_def)
            if containers:
                container_names = [c["name"] for c in containers]
                print(f"      Containers: {', '.join(container_names)}")
        print()

    return 0


def cmd_run(  # noqa: C901 — ECS run command orchestration
    environment: str,
    command_name: str | None,
    deploy_toml: str | None,
    list_commands: bool,
    extra_args: tuple,
    service: str,
    container: str | None,
    no_wait: bool,
    no_logs: bool,
    timeout: int,
) -> int:
    """Run a named command from deploy.toml [commands] section."""
    # Handle --list-commands that may have been captured by extra_args
    if "--list-commands" in extra_args:
        list_commands = True
        extra_args = tuple(a for a in extra_args if a != "--list-commands")

    # Resolve deploy.toml path
    deploy_toml_path = None
    used_explicit_flag = False

    if deploy_toml:
        deploy_toml_path = Path(deploy_toml).expanduser().resolve()
        used_explicit_flag = True
    elif environment:
        linked_path = get_linked_deploy_toml(environment)
        if linked_path:
            deploy_toml_path = linked_path
            print(f"Using linked deploy.toml: {deploy_toml_path}")

    if list_commands and not deploy_toml_path:
        print(
            "Error: --deploy-toml is required when using --list-commands without environment",
            file=sys.stderr,
        )
        return 1

    if not deploy_toml_path:
        if not environment:
            print("Error: environment is required", file=sys.stderr)
            print("\nUsage: ecs-run.py run <environment> <command>", file=sys.stderr)
            print("       ecs-run.py run <environment> --list-commands", file=sys.stderr)
        else:
            print(f"Error: No deploy.toml linked for '{environment}'", file=sys.stderr)
            print("\nTo link this environment to its deploy.toml:", file=sys.stderr)
            print(
                f"  python bin/link-environments.py {environment} /path/to/deploy.toml",
                file=sys.stderr,
            )
            print("\nOr specify --deploy-toml explicitly:", file=sys.stderr)
            print(
                f"  ecs-run.py run {environment} <command> --deploy-toml /path/to/deploy.toml",
                file=sys.stderr,
            )
        return 1

    # Load deploy.toml
    try:
        dt = load_deploy_toml(deploy_toml_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if used_explicit_flag and environment:
        print(f"Tip: Run 'python bin/link-environments.py {environment} {deploy_toml_path}'")
        print("     to avoid specifying --deploy-toml next time.\n")

    if list_commands:
        commands = dt.get("commands", {})
        if not commands:
            print("No commands defined in [commands] section", file=sys.stderr)
            return 1
        print("Available commands:")
        for name, cmd in commands.items():
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            print(f"  {name}: {cmd_str}")
        return 0

    if not environment:
        print("Error: environment is required (or use --list-commands)", file=sys.stderr)
        return 1

    if not command_name:
        print("Error: command_name is required (or use --list-commands)", file=sys.stderr)
        return 1

    result = resolve_environment(environment)
    if not result:
        return 1

    env_path, cluster_name = result

    try:
        cmd = get_run_command(dt, command_name, list(extra_args))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    use_migrate = command_requires_ddl(dt, command_name)

    return _run_ecs_command(
        cluster_name=cluster_name,
        service_name=service,
        container_name=container,
        command=cmd,
        wait=not no_wait,
        timeout=timeout,
        show_logs=not no_logs,
        use_migrate_credentials=use_migrate,
    )


def cmd_exec(
    environment: str,
    command_args: tuple,
    service: str,
    container: str | None,
    no_wait: bool,
    no_logs: bool,
    timeout: int,
) -> int:
    """Run an arbitrary command."""
    result = resolve_environment(environment)
    if not result:
        return 1

    env_path, cluster_name = result

    if not command_args:
        print("Error: No command specified", file=sys.stderr)
        return 1

    return _run_ecs_command(
        cluster_name=cluster_name,
        service_name=service,
        container_name=container,
        command=list(command_args),
        wait=not no_wait,
        timeout=timeout,
        show_logs=not no_logs,
    )


# =============================================================================
# CLI
# =============================================================================


def _common_run_options(func):
    """Add common run/exec options."""
    func = click.option("-s", "--service", default="web", help="Service name (default: web)")(func)
    func = click.option(
        "-c", "--container", help="Container name override (default: first container)"
    )(func)
    func = click.option("--no-wait", is_flag=True, help="Don't wait for task completion")(func)
    func = click.option(
        "--no-logs", is_flag=True, help="Don't fetch and display logs after completion"
    )(func)
    func = click.option(
        "--timeout", type=int, default=300, help="Timeout in seconds (default: 300)"
    )(func)
    return func


@click.group()
def cli():
    """Run commands in ECS containers for staging environments.

    \b
    The 'run' command uses named commands from deploy.toml's [commands] section.
    Use 'run --list-commands' to see available commands for an application.
    """
    configure_aws_profile("deploy")


@cli.command("list")
@click.argument("environment")
def list_cmd(environment):
    """List services and containers in an environment."""
    sys.exit(cmd_list(environment))


@cli.command("run")
@click.argument("environment", required=False)
@click.argument("command_name", required=False)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--deploy-toml", metavar="PATH", help="Path to deploy.toml (optional if environment is linked)"
)
@click.option(
    "--list-commands",
    is_flag=True,
    help="List available commands from deploy.toml instead of running one",
)
@_common_run_options
def run_cmd(
    environment,
    command_name,
    extra_args,
    deploy_toml,
    list_commands,
    service,
    container,
    no_wait,
    no_logs,
    timeout,
):
    """Run a named command from deploy.toml [commands] section."""
    sys.exit(
        cmd_run(
            environment,
            command_name,
            deploy_toml,
            list_commands,
            extra_args,
            service,
            container,
            no_wait,
            no_logs,
            timeout,
        )
    )


@cli.command("exec")
@click.argument("environment")
@click.argument("command_args", nargs=-1, type=click.UNPROCESSED)
@_common_run_options
def exec_cmd(environment, command_args, service, container, no_wait, no_logs, timeout):
    """Run arbitrary command."""
    sys.exit(cmd_exec(environment, command_args, service, container, no_wait, no_logs, timeout))


if __name__ == "__main__":
    cli()
