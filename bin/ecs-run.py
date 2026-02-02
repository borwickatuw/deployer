#!/usr/bin/env python3
"""
Run commands in ECS containers for staging environments.

Usage:
    # List services in environment
    python bin/ecs-run.py myapp-staging list

    # Run named commands from deploy.toml [commands] section
    python bin/ecs-run.py myapp-staging run migrate --deploy-toml ../app/deploy.toml
    python bin/ecs-run.py myapp-staging run shell --deploy-toml ../app/deploy.toml

    # Run Django management commands (backward compatible, falls back to Django defaults)
    python bin/ecs-run.py myapp-staging manage createsuperuser
    python bin/ecs-run.py myapp-staging manage migrate

    # Run arbitrary commands
    python bin/ecs-run.py myapp-staging exec uv run python -c "print('hi')"

    # Specify a different service (default: web)
    python bin/ecs-run.py myapp-staging -s celery exec python -c "print('hello')"
"""

import argparse
import sys
from pathlib import Path

from deployer.aws import cloudwatch, ecs
from deployer.core.config import (
    get_manage_command,
    get_run_command,
    load_deploy_toml,
)
from deployer.utils import configure_aws_profile, run_command, validate_environment_deployed


def get_cluster_name(env_path: Path) -> str | None:
    """Get the ECS cluster name from terraform outputs."""
    success, output = run_command(
        ["tofu", "output", "-raw", "ecs_cluster_name"],
        cwd=str(env_path),
    )

    if not success or not output.strip():
        return None

    return output.strip()


def resolve_environment(env_name: str) -> tuple[Path, str] | None:
    """Resolve environment name to path and cluster name."""
    env_path, error = validate_environment_deployed(env_name)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return None

    cluster_name = get_cluster_name(env_path)
    if not cluster_name:
        print(f"Error: Could not get ECS cluster name for '{env_name}'", file=sys.stderr)
        return None

    return env_path, cluster_name


def _display_task_logs(
    log_group: str, stream_prefix: str, container_name: str, task_id: str
) -> None:
    """Fetch and display CloudWatch logs for a task.

    Args:
        log_group: CloudWatch log group name.
        stream_prefix: Log stream prefix from task definition.
        container_name: Container name.
        task_id: ECS task ID.
    """
    events = cloudwatch.get_task_logs(
        log_group=log_group,
        stream_prefix=stream_prefix,
        container_name=container_name,
        task_id=task_id,
        limit=500,  # Get up to 500 log lines
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


def run_ecs_command(
    cluster_name: str,
    service_name: str,
    container_name: str | None,
    command: list[str],
    environment: list[dict] | None = None,
    wait: bool = True,
    timeout: int = 300,
    show_logs: bool = True,
) -> int:
    """Run a command in an ECS container.

    Returns exit code (0 on success).
    """
    # Get network config from running service
    print(f"Getting network configuration from service '{service_name}'...")
    network_config = ecs.get_service_network_config(cluster_name, service_name)
    if not network_config:
        print(f"Error: Could not get network config for service '{service_name}'", file=sys.stderr)
        print("Is the service running?", file=sys.stderr)
        return 1

    # Get task definition
    task_definition = ecs.get_service_task_definition(cluster_name, service_name)
    if not task_definition:
        print(f"Error: Could not get task definition for service '{service_name}'", file=sys.stderr)
        return 1

    # Get container name if not specified
    if not container_name:
        containers = ecs.get_task_containers(task_definition)
        if not containers:
            print("Error: No containers found in task definition", file=sys.stderr)
            return 1
        # Use first container (usually the main app container)
        container_name = containers[0]["name"]

    print(f"Task definition: {task_definition}")
    print(f"Container: {container_name}")
    print(f"Command: {' '.join(command)}")
    print()

    # Run the task
    print("Starting task...")
    task_arn = ecs.run_task(
        cluster_name=cluster_name,
        task_definition=task_definition,
        network_config=network_config,
        container_name=container_name,
        command=command,
        environment=environment,
    )

    if not task_arn:
        print("Error: Failed to start task", file=sys.stderr)
        return 1

    # Extract task ID from ARN for display
    task_id = task_arn.split("/")[-1]
    print(f"Task ARN: {task_arn}")
    print(f"Task ID: {task_id}")

    # Show logs location
    logs_info = ecs.get_task_logs_location(task_definition, container_name)
    if logs_info:
        log_group, stream_prefix = logs_info
        log_stream = f"{stream_prefix}/{container_name}/{task_id}"
        print(f"\nLogs: CloudWatch log group '{log_group}', stream '{log_stream}'")

    if not wait:
        print("\nTask started (not waiting for completion)")
        return 0

    print("\nWaiting for task to complete...")
    exit_code = ecs.wait_for_task(cluster_name, task_arn, timeout)

    if exit_code == 0:
        print("\nTask completed successfully")
    elif exit_code == -1:
        print("\nTask failed or timed out", file=sys.stderr)
    else:
        print(f"\nTask exited with code {exit_code}", file=sys.stderr)

    # Fetch and display logs if requested
    if show_logs and logs_info:
        print("\n" + "=" * 60)
        print("Task Output:")
        print("=" * 60)
        _display_task_logs(log_group, stream_prefix, container_name, task_id)

    return exit_code


# =============================================================================
# Commands
# =============================================================================


def cmd_list(args, base_path: Path) -> int:
    """List services and containers in an environment."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    env_path, cluster_name = result

    print(f"Environment: {args.environment}")
    print(f"Cluster: {cluster_name}")
    print()

    services = ecs.get_services(cluster_name)
    if not services:
        print("No services found in cluster.")
        return 0

    print("Services:")
    print("-" * 60)

    for svc in services:
        status_indicator = "+" if svc["running_count"] > 0 else "-"
        print(f"  [{status_indicator}] {svc['name']}")
        print(f"      Status: {svc['status']}")
        print(f"      Running: {svc['running_count']}/{svc['desired_count']}")

        # Get containers for this service
        task_def = svc.get("task_definition")
        if task_def:
            containers = ecs.get_task_containers(task_def)
            if containers:
                container_names = [c["name"] for c in containers]
                print(f"      Containers: {', '.join(container_names)}")
        print()

    return 0


def cmd_run(args, base_path: Path) -> int:
    """Run a named command from deploy.toml [commands] section."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    env_path, cluster_name = result

    # Load deploy.toml if specified
    deploy_toml = None
    if args.deploy_toml:
        deploy_toml_path = Path(args.deploy_toml).resolve()
        try:
            deploy_toml = load_deploy_toml(deploy_toml_path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Get the command
    try:
        command = get_run_command(deploy_toml, args.command_name, args.extra_args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return run_ecs_command(
        cluster_name=cluster_name,
        service_name=args.service,
        container_name=args.container,
        command=command,
        wait=not args.no_wait,
        timeout=args.timeout,
        show_logs=not args.no_logs,
    )


def cmd_manage(args, base_path: Path) -> int:
    """Run a Django management command (backward compatible).

    Uses [commands].manage from deploy.toml if specified, otherwise falls back
    to Django defaults (uv run python manage.py).
    """
    result = resolve_environment(args.environment)
    if not result:
        return 1

    env_path, cluster_name = result

    # Load deploy.toml if specified
    deploy_toml = None
    if args.deploy_toml:
        deploy_toml_path = Path(args.deploy_toml).resolve()
        try:
            deploy_toml = load_deploy_toml(deploy_toml_path)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Get the first argument as the management command, rest as extra args
    if not args.command_args:
        print("Error: No management command specified", file=sys.stderr)
        print("Example: ecs-run.py <env> manage migrate", file=sys.stderr)
        return 1

    management_command = args.command_args[0]
    extra_args = args.command_args[1:] if len(args.command_args) > 1 else None

    command = get_manage_command(deploy_toml, management_command, extra_args)

    return run_ecs_command(
        cluster_name=cluster_name,
        service_name=args.service,
        container_name=args.container,
        command=command,
        wait=not args.no_wait,
        timeout=args.timeout,
        show_logs=not args.no_logs,
    )


def cmd_exec(args, base_path: Path) -> int:
    """Run an arbitrary command."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    env_path, cluster_name = result

    if not args.command_args:
        print("Error: No command specified", file=sys.stderr)
        return 1

    return run_ecs_command(
        cluster_name=cluster_name,
        service_name=args.service,
        container_name=args.container,
        command=args.command_args,
        wait=not args.no_wait,
        timeout=args.timeout,
        show_logs=not args.no_logs,
    )


# =============================================================================
# Main
# =============================================================================


def main():
    # Load .env and configure AWS profile
    configure_aws_profile("deploy")

    parser = argparse.ArgumentParser(
        description="Run commands in ECS containers for staging environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s myapp-staging list
  %(prog)s myapp-staging run migrate --deploy-toml ../app/deploy.toml
  %(prog)s myapp-staging manage check
  %(prog)s myapp-staging manage migrate
  %(prog)s myapp-staging exec uv run python -c "print('hello')"
  %(prog)s myapp-staging -s celery exec python -c "print('worker')"
  %(prog)s myapp-staging manage showmigrations --no-wait

The 'run' command uses named commands from deploy.toml's [commands] section.
The 'manage' command is a Django-specific shortcut (backward compatible).

For creating superusers, use create-superuser.py instead.
        """,
    )

    # Positional argument for environment
    parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")

    # Optional arguments
    parser.add_argument("-s", "--service", default="web", help="Service name (default: web)")
    parser.add_argument("-c", "--container", help="Container name override (default: first container)")
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for task completion")
    parser.add_argument("--no-logs", action="store_true", help="Don't fetch and display logs after completion")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")
    parser.add_argument(
        "--deploy-toml",
        metavar="PATH",
        help="Path to deploy.toml for [commands] section (optional, falls back to Django defaults)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="List services and containers in an environment")

    # run (new generic command)
    run_parser = subparsers.add_parser(
        "run",
        help="Run a named command from deploy.toml [commands] section"
    )
    run_parser.add_argument(
        "command_name",
        help="Command name defined in [commands] section (e.g., migrate, shell)"
    )
    run_parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments to pass to the command"
    )

    # manage (backward compatible Django shortcut)
    manage_parser = subparsers.add_parser(
        "manage",
        help="Run Django management command (backward compatible)"
    )
    manage_parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="Management command and arguments (e.g., migrate, shell)"
    )

    # exec
    exec_parser = subparsers.add_parser("exec", help="Run arbitrary command")
    exec_parser.add_argument("command_args", nargs=argparse.REMAINDER, help="Command and arguments")

    args = parser.parse_args()

    # Find the base path (deployer root)
    script_path = Path(__file__).resolve()
    base_path = script_path.parent.parent

    # Dispatch to command handler
    commands = {
        "list": cmd_list,
        "run": cmd_run,
        "manage": cmd_manage,
        "exec": cmd_exec,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args, base_path))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
