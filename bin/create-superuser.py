#!/usr/bin/env python3
"""
Create a Django superuser in an ECS staging environment.

This is a convenience wrapper around ecs-run.py that handles password generation
and the necessary Django flags.

Usage:
    python bin/create-superuser.py myapp-staging --email admin@example.com
    python bin/create-superuser.py myapp-staging --email admin@example.com --username admin
    python bin/create-superuser.py myapp-staging --email admin@example.com -p  # prompt for password
"""

import argparse
import getpass
import sys
from pathlib import Path

from deployer.aws import ecs
from deployer.core import generate_temp_password
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


def main():
    parser = argparse.ArgumentParser(
        description="Create a Django superuser in an ECS staging environment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s myapp-staging --email admin@example.com
  %(prog)s myapp-staging --email admin@example.com --username admin
  %(prog)s myapp-staging --email admin@example.com -p  # prompt for password
        """,
    )

    parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    parser.add_argument("--email", required=True, help="Email address for the superuser")
    parser.add_argument("--username", help="Username (defaults to email)")
    parser.add_argument("-p", "--prompt-password", action="store_true", help="Prompt for password (default: generate random)")
    parser.add_argument("-s", "--service", default="web", help="Service name (default: web)")
    parser.add_argument("-c", "--container", help="Container name override (default: first container)")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds (default: 300)")

    args = parser.parse_args()

    # Load .env and configure AWS profile
    configure_aws_profile("deploy")

    # Validate environment
    env_path, error = validate_environment_deployed(args.environment)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    cluster_name = get_cluster_name(env_path)
    if not cluster_name:
        print(f"Error: Could not get ECS cluster name for '{args.environment}'", file=sys.stderr)
        sys.exit(1)

    # Handle password
    print(f"Creating superuser in {args.environment}")
    print(f"  Email: {args.email}")
    if args.username:
        print(f"  Username: {args.username}")

    if args.prompt_password:
        print()
        password = getpass.getpass("Password: ")
        if not password:
            print("Error: Password is required", file=sys.stderr)
            sys.exit(1)
        password_confirm = getpass.getpass("Password (again): ")
        if password != password_confirm:
            print("Error: Passwords do not match", file=sys.stderr)
            sys.exit(1)
    else:
        password = generate_temp_password()
        print(f"  Password: {password}")

    print()

    # Build the command
    command = [
        "uv", "run", "python", "manage.py", "createsuperuser",
        "--no-input",
        "--email", args.email,
    ]
    if args.username:
        command.extend(["--username", args.username])

    environment = [{"name": "DJANGO_SUPERUSER_PASSWORD", "value": password}]

    # Get network config from running service
    print(f"Getting network configuration from service '{args.service}'...")
    network_config = ecs.get_service_network_config(cluster_name, args.service)
    if not network_config:
        print(f"Error: Could not get network config for service '{args.service}'", file=sys.stderr)
        print("Is the service running?", file=sys.stderr)
        sys.exit(1)

    # Get task definition
    task_definition = ecs.get_service_task_definition(cluster_name, args.service)
    if not task_definition:
        print(f"Error: Could not get task definition for service '{args.service}'", file=sys.stderr)
        sys.exit(1)

    # Get container name if not specified
    container_name = args.container
    if not container_name:
        containers = ecs.get_task_containers(task_definition)
        if not containers:
            print("Error: No containers found in task definition", file=sys.stderr)
            sys.exit(1)
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
        sys.exit(1)

    task_id = task_arn.split("/")[-1]
    print(f"Task ARN: {task_arn}")
    print(f"Task ID: {task_id}")

    # Show logs location
    logs_location = ecs.get_task_logs_location(task_definition, container_name)
    if logs_location:
        print(f"\nLogs: CloudWatch {logs_location}{container_name}/{task_id}")

    print("\nWaiting for task to complete...")
    exit_code = ecs.wait_for_task(cluster_name, task_arn, args.timeout)

    if exit_code == 0:
        print("\nSuperuser created successfully!")
    elif exit_code == -1:
        print("\nTask failed or timed out", file=sys.stderr)
    else:
        print(f"\nTask exited with code {exit_code}", file=sys.stderr)

    sys.exit(exit_code if exit_code >= 0 else 1)


if __name__ == "__main__":
    main()
