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

import argparse
import sys
from pathlib import Path

from deployer.aws import cloudwatch, ecs
from deployer.core.config import (
    get_run_command,
    load_deploy_toml,
)
from deployer.utils import (
    configure_aws_profile,
    get_linked_deploy_toml,
    run_command,
    validate_environment_deployed,
)


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
    use_migrate_credentials: bool = False,
) -> int:
    """Run a command in an ECS container.

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service to get network config from.
        container_name: Container name to run command in. If None, uses first container.
        command: Command to execute as list of strings.
        environment: Optional environment variable overrides.
        wait: Whether to wait for task completion.
        timeout: Timeout in seconds for waiting.
        show_logs: Whether to fetch and display logs after completion.
        use_migrate_credentials: If True, use the migrate task definition
            with DDL+DML database credentials instead of the service's task
            definition. Use this for migration commands.

    Returns:
        Exit code (0 on success).
    """
    import boto3

    # Create a single ECS client to reuse across all operations
    # This eliminates connection overhead for each API call
    ecs_client = boto3.client("ecs")

    # Get network config and task definition in a single API call
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

    # Determine which task definition to use
    if use_migrate_credentials:
        # Use the migrate task definition for migration commands
        # Derive from service task def: "myapp-staging-web:123" -> "myapp-staging-migrate"
        task_family = service_task_def.split("/")[-1].rsplit(":", 1)[0]  # "myapp-staging-web"
        base = task_family.rsplit("-", 1)[0]  # "myapp-staging"
        task_definition = f"{base}-migrate"
        container_name = "migrate"  # Migrate task uses "migrate" as container name
        print(f"Using migrate credentials (DDL+DML) for migration command")
    else:
        task_definition = service_task_def

    # Get container definitions (cached for later use with logs location)
    containers = ecs.get_task_containers(task_definition, ecs_client=ecs_client)

    # Get container name if not specified
    if not container_name:
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
        ecs_client=ecs_client,
    )

    if not task_arn:
        print("Error: Failed to start task", file=sys.stderr)
        return 1

    # Extract task ID from ARN for display
    task_id = task_arn.split("/")[-1]
    print(f"Task ARN: {task_arn}")
    print(f"Task ID: {task_id}")

    # Get logs location from cached containers (no additional API call)
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
    # Handle --list-commands that may have been captured by REMAINDER
    # (argparse REMAINDER grabs flags that appear after positional args)
    if hasattr(args, 'extra_args') and '--list-commands' in args.extra_args:
        args.list_commands = True
        args.extra_args = [a for a in args.extra_args if a != '--list-commands']

    # Resolve deploy.toml path: explicit --deploy-toml, or linked, or error
    deploy_toml_path = None
    used_explicit_flag = False

    if args.deploy_toml:
        # User provided --deploy-toml explicitly
        deploy_toml_path = Path(args.deploy_toml).expanduser().resolve()
        used_explicit_flag = True
    elif args.environment:
        # Try to look up from links
        linked_path = get_linked_deploy_toml(args.environment)
        if linked_path:
            deploy_toml_path = linked_path
            print(f"Using linked deploy.toml: {deploy_toml_path}")

    # For --list-commands without environment, we need --deploy-toml
    if args.list_commands and not deploy_toml_path:
        print("Error: --deploy-toml is required when using --list-commands without environment", file=sys.stderr)
        return 1

    if not deploy_toml_path:
        if not args.environment:
            print("Error: environment is required", file=sys.stderr)
            print("\nUsage: ecs-run.py run <environment> <command>", file=sys.stderr)
            print("       ecs-run.py run <environment> --list-commands", file=sys.stderr)
        else:
            print(f"Error: No deploy.toml linked for '{args.environment}'", file=sys.stderr)
            print(f"\nTo link this environment to its deploy.toml:", file=sys.stderr)
            print(f"  python bin/link-environments.py {args.environment} /path/to/deploy.toml", file=sys.stderr)
            print(f"\nOr specify --deploy-toml explicitly:", file=sys.stderr)
            print(f"  ecs-run.py run {args.environment} <command> --deploy-toml /path/to/deploy.toml", file=sys.stderr)
        return 1

    # Load deploy.toml
    try:
        deploy_toml = load_deploy_toml(deploy_toml_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Print suggestion if --deploy-toml was used explicitly
    if used_explicit_flag and args.environment:
        print(f"Tip: Run 'python bin/link-environments.py {args.environment} {deploy_toml_path}'")
        print(f"     to avoid specifying --deploy-toml next time.\n")

    # Handle --list-commands flag
    if args.list_commands:
        commands = deploy_toml.get("commands", {})
        if not commands:
            print("No commands defined in [commands] section", file=sys.stderr)
            return 1
        print("Available commands:")
        for name, cmd in commands.items():
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            print(f"  {name}: {cmd_str}")
        return 0

    # For running commands, we need environment and command_name
    if not args.environment:
        print("Error: environment is required (or use --list-commands)", file=sys.stderr)
        return 1

    if not args.command_name:
        print("Error: command_name is required (or use --list-commands)", file=sys.stderr)
        return 1

    result = resolve_environment(args.environment)
    if not result:
        return 1

    env_path, cluster_name = result

    # Get the command
    try:
        command = get_run_command(deploy_toml, args.command_name, args.extra_args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Use migrate credentials for migration commands
    # These commands need DDL privileges (CREATE, ALTER, DROP tables)
    use_migrate = args.command_name in ("migrate", "makemigrations")

    return run_ecs_command(
        cluster_name=cluster_name,
        service_name=args.service,
        container_name=args.container,
        command=command,
        wait=not args.no_wait,
        timeout=args.timeout,
        show_logs=not args.no_logs,
        use_migrate_credentials=use_migrate,
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


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a subparser."""
    parser.add_argument("-s", "--service", default="web", help="Service name (default: web)")
    parser.add_argument("-c", "--container", help="Container name override (default: first container)")
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for task completion")
    parser.add_argument("--no-logs", action="store_true", help="Don't fetch and display logs after completion")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")


def main():
    # Load .env and configure AWS profile
    configure_aws_profile("deploy")

    parser = argparse.ArgumentParser(
        description="Run commands in ECS containers for staging environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list myapp-staging

  # Link environment to deploy.toml (one-time setup)
  python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml

  # After linking, --deploy-toml is not needed
  %(prog)s run myapp-staging --list-commands
  %(prog)s run myapp-staging migrate
  %(prog)s run myapp-staging collectstatic

  # Or specify --deploy-toml explicitly
  %(prog)s run myapp-staging migrate --deploy-toml ../app/deploy.toml

  # Run arbitrary commands
  %(prog)s exec myapp-staging python -c "print('hello')"
  %(prog)s exec myapp-staging -s celery python -c "print('worker')"

The 'run' command uses named commands from deploy.toml's [commands] section.
Use 'run --list-commands' to see available commands for an application.
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    list_parser = subparsers.add_parser("list", help="List services and containers in an environment")
    list_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")

    # run
    run_parser = subparsers.add_parser(
        "run",
        help="Run a named command from deploy.toml [commands] section"
    )
    run_parser.add_argument(
        "--deploy-toml",
        metavar="PATH",
        help="Path to deploy.toml (optional if environment is linked)"
    )
    run_parser.add_argument(
        "--list-commands",
        action="store_true",
        help="List available commands from deploy.toml instead of running one"
    )
    run_parser.add_argument(
        "environment",
        nargs="?",  # Optional when using --list-commands with --deploy-toml
        help="Environment name (e.g., myapp-staging)"
    )
    run_parser.add_argument(
        "command_name",
        nargs="?",  # Optional when using --list-commands
        help="Command name defined in [commands] section (e.g., migrate, collectstatic)"
    )
    run_parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments to pass to the command"
    )
    add_common_args(run_parser)

    # exec
    exec_parser = subparsers.add_parser("exec", help="Run arbitrary command")
    exec_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    exec_parser.add_argument("command_args", nargs=argparse.REMAINDER, help="Command and arguments")
    add_common_args(exec_parser)

    args = parser.parse_args()

    # Find the base path (deployer root)
    script_path = Path(__file__).resolve()
    base_path = script_path.parent.parent

    # Dispatch to command handler
    commands = {
        "list": cmd_list,
        "run": cmd_run,
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
