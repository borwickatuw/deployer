"""Docker image building and ECR operations."""

import base64
import fnmatch
import hashlib
import subprocess
from pathlib import Path

from botocore.exceptions import ClientError

from ..config import DeployConfig, ImageConfig
from ..core.deploy import topological_sort
from ..timing import get_timer
from ..utils import Colors, log, log_error, log_status, log_success


def _run_timed_subprocess(cmd: list[str], step_name: str) -> subprocess.CompletedProcess:
    """Run a subprocess with optional timer integration."""
    timer = get_timer()
    if timer and timer._current_step:
        with timer.sub_step(step_name):
            return subprocess.run(cmd, capture_output=True, text=True, check=False)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _check_subprocess_result(
    result: subprocess.CompletedProcess, image_name: str, operation: str
) -> None:
    """Check subprocess result and raise with output on failure."""
    if result.returncode != 0:
        log_error(f"{operation.capitalize()} failed for {image_name}")
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        raise RuntimeError(f"{operation.capitalize()} failed for {image_name}")


def parse_dockerignore(context_path: Path) -> list[str]:
    """Parse .dockerignore file and return list of patterns.

    Args:
        context_path: Path to the build context directory.

    Returns:
        List of ignore patterns.
    """
    dockerignore_path = context_path / ".dockerignore"
    patterns = []

    # Always ignore .git directory
    patterns.append(".git")

    if dockerignore_path.exists():
        with open(dockerignore_path) as f:
            for raw_line in f:
                stripped = raw_line.strip()
                # Skip empty lines and comments
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)

    return patterns


def should_ignore(file_path: Path, context_path: Path, patterns: list[str]) -> bool:
    """Check if a file should be ignored based on .dockerignore patterns.

    Args:
        file_path: Path to the file to check.
        context_path: Path to the build context directory.
        patterns: List of ignore patterns from .dockerignore.

    Returns:
        True if the file should be ignored.
    """
    rel_path = file_path.relative_to(context_path)
    rel_str = str(rel_path)

    for pattern in patterns:
        # Handle negation patterns (!)
        if pattern.startswith("!"):
            continue  # Simplified: don't handle negation for now

        # Handle directory patterns ending with /
        effective_pattern = pattern[:-1] if pattern.endswith("/") else pattern

        # Check if any part of the path matches
        # e.g., ".git" should match ".git/config"
        parts = rel_path.parts
        for i, part in enumerate(parts):
            partial_path = str(Path(*parts[: i + 1]))
            if fnmatch.fnmatch(partial_path, effective_pattern):
                return True
            if fnmatch.fnmatch(part, pattern):
                return True

        # Also check full path
        if fnmatch.fnmatch(rel_str, pattern):
            return True

    return False


def compute_context_hash(context_path: Path, dockerfile: str) -> str:
    """Compute a hash of the build context for cache detection.

    The hash includes the Dockerfile and all files in the context,
    respecting .dockerignore patterns.

    Args:
        context_path: Path to the build context directory.
        dockerfile: Name of the Dockerfile.

    Returns:
        Short hash string (12 characters, like git short hash).
    """
    hasher = hashlib.sha256()

    # Parse .dockerignore
    patterns = parse_dockerignore(context_path)

    # Hash the Dockerfile first
    dockerfile_path = context_path / dockerfile
    if dockerfile_path.exists():
        with open(dockerfile_path, "rb") as f:
            hasher.update(b"Dockerfile:")
            hasher.update(f.read())

    # Collect and sort all files for deterministic hashing
    files_to_hash = []
    for file_path in context_path.rglob("*"):
        if file_path.is_file() and not should_ignore(file_path, context_path, patterns):
            files_to_hash.append(file_path)

    # Sort by relative path for determinism
    files_to_hash.sort(key=lambda p: str(p.relative_to(context_path)))

    # Hash each file (path + content)
    for file_path in files_to_hash:
        rel_path = file_path.relative_to(context_path)
        hasher.update(f"\n{rel_path}:".encode())
        try:
            with open(file_path, "rb") as f:
                hasher.update(f.read())
        except (PermissionError, OSError):
            # Skip files we can't read
            pass

    return hasher.hexdigest()[:12]


def image_exists_in_ecr(ecr_client, repository_name: str, image_tag: str) -> bool:
    """Check if an image with the given tag exists in ECR.

    Args:
        ecr_client: boto3 ECR client.
        repository_name: Name of the ECR repository.
        image_tag: Tag to check for.

    Returns:
        True if the image exists, False otherwise.
    """
    try:
        response = ecr_client.describe_images(
            repositoryName=repository_name,
            imageIds=[{"imageTag": image_tag}],
        )
        return len(response.get("imageDetails", [])) > 0
    except ClientError as e:
        if e.response["Error"]["Code"] == "ImageNotFoundException":
            return False
        # Repository might not exist yet
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            return False
        raise


def ecr_login(ecr_client, dry_run: bool = False) -> None:
    """Log in to ECR.

    Args:
        ecr_client: boto3 ECR client.
        dry_run: If True, only print what would be done.
    """
    log("Logging into ECR...")

    if dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecr get-login-password | docker login")
        return

    token = ecr_client.get_authorization_token()
    auth_data = token["authorizationData"][0]
    registry = auth_data["proxyEndpoint"]

    # Decode the base64 token to get the password
    # The token is base64-encoded "AWS:password"
    encoded_token = auth_data["authorizationToken"]
    decoded = base64.b64decode(encoded_token).decode()
    _, password = decoded.split(":", 1)

    # Use docker login
    cmd = ["docker", "login", "--username", "AWS", "--password-stdin", registry]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    proc.communicate(input=password.encode())

    if proc.returncode != 0:
        raise RuntimeError("ECR login failed")

    log_success("ECR login")


def build_and_push_images(  # noqa: C901 — image build/push with cache, dry-run, and ECR logic
    config: DeployConfig | dict,
    source_dir: Path,
    ecr_prefix: str,
    account_id: str,
    region: str,
    environment: str,
    ecr_client,
    dry_run: bool = False,
    force_build: bool = False,
) -> dict[str, str]:
    """Build and push all images, returning a map of image name to ECR URI.

    Images are built in dependency order (topological sort based on depends_on).
    Images with push=false are only built locally and tagged with their image name.

    Uses content-based hashing to skip building/pushing unchanged images.
    The hash is computed from the Dockerfile and all files in the build context
    (respecting .dockerignore). If an image with the same hash tag exists in ECR,
    the build and push are skipped.

    Args:
        config: The deployment configuration dictionary.
        source_dir: Path to the source code directory.
        ecr_prefix: ECR repository prefix.
        account_id: AWS account ID.
        region: AWS region.
        environment: Target environment (staging, production).
        dry_run: If True, only print what would be done.
        ecr_client: boto3 ECR client for checking existing images.
        force_build: If True, skip cache check and always build.

    Returns:
        Dictionary mapping image names to their ECR URIs.
    """
    log("Building and pushing images...")

    image_uris = {}

    # Support both DeployConfig dataclass and raw dict
    if isinstance(config, DeployConfig):
        images = config.images
        # Convert to dict format for topological_sort
        images_for_sort = {name: {"depends_on": img.depends_on} for name, img in images.items()}
    else:
        images = config.get("images", {})
        images_for_sort = images

    # Sort images by dependencies
    try:
        build_order = topological_sort(images_for_sort)
    except ValueError as e:
        log_error(str(e))
        raise

    for image_name in build_order:
        image_config = images[image_name]

        # Handle both ImageConfig dataclass and raw dict
        if isinstance(image_config, ImageConfig):
            context = source_dir / image_config.context
            dockerfile = image_config.dockerfile
            should_push = image_config.push
            build_args = image_config.get_build_args(environment)
            target = image_config.get_target(environment)
        else:
            context = source_dir / image_config["context"]
            dockerfile = image_config.get("dockerfile", "Dockerfile")
            should_push = image_config.get("push", True)
            # Legacy dict support - inline the logic
            build_args_config = image_config.get("build_args", {})
            build_args = {k: v for k, v in build_args_config.items() if not isinstance(v, dict)}
            build_args.update(build_args_config.get(environment, {}))
            target_config = image_config.get("target")
            target = (
                target_config.get(environment) if isinstance(target_config, dict) else target_config
            )

        # Compute content hash for cache key
        content_hash = compute_context_hash(context, dockerfile)

        # pysmelly: ignore temp-accumulators — accumulator appropriate for conditional appends
        hash_modifiers = []
        if build_args:
            args_str = ",".join(f"{k}={v}" for k, v in sorted(build_args.items()))
            hash_modifiers.append(f"args:{args_str}")
        if target:
            hash_modifiers.append(f"target:{target}")

        if hash_modifiers:
            combined = f"{content_hash}:{';'.join(hash_modifiers)}"
            content_hash = hashlib.sha256(combined.encode()).hexdigest()[:12]

        tag = content_hash

        # Local-only images are tagged with just their name (for FROM references)
        # Pushed images get the ecr_prefix
        if should_push:
            repo_name = f"{ecr_prefix}-{image_name}"
            local_tag = f"{repo_name}:{tag}"
            ecr_repo = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
            ecr_uri = f"{ecr_repo}:{tag}"

            # Check if image already exists in ECR (skip if no client or dry_run)
            if ecr_client and not dry_run and not force_build:  # noqa: SIM102
                if image_exists_in_ecr(ecr_client, repo_name, tag):
                    log_status(f"{image_name}", f"cached ({tag[:8]})")
                    image_uris[image_name] = ecr_uri
                    continue
        else:
            local_tag = f"{image_name}:{tag}"

        # Build image
        # --platform ensures consistent builds for Fargate (x86_64) regardless of host architecture
        # Note: We rely on content-based hashing to detect changes, so Docker layer
        # caching is safe and speeds up rebuilds when only some files change.
        build_cmd = [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "-t",
            local_tag,
            "-f",
            str(context / dockerfile),
        ]

        # Add target if specified (for multi-stage builds)
        if target:
            build_cmd.extend(["--target", target])

        # Add build arguments
        for key, value in build_args.items():
            build_cmd.extend(["--build-arg", f"{key}={value}"])

        build_cmd.append(str(context))

        if dry_run:
            print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} {' '.join(build_cmd)}")
        else:
            result = _run_timed_subprocess(build_cmd, f"{image_name}_build")
            _check_subprocess_result(result, image_name, "build")

        log_success(f"{image_name} (build {tag[:8]})")

        if should_push:
            # Tag for ECR
            tag_cmd = ["docker", "tag", local_tag, ecr_uri]
            if dry_run:
                print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} {' '.join(tag_cmd)}")
            else:
                subprocess.run(tag_cmd, check=True)

            # Push to ECR
            push_cmd = ["docker", "push", ecr_uri]
            if dry_run:
                print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} {' '.join(push_cmd)}")
            else:
                result = _run_timed_subprocess(push_cmd, f"{image_name}_push")
                _check_subprocess_result(result, image_name, "push")

            log_success(f"{image_name} (push)")
            image_uris[image_name] = ecr_uri
        else:
            log_status(f"{image_name}", "local only")

    return image_uris


def validate_ecr_repositories(
    ecr_client,
    config: DeployConfig | dict,
    ecr_prefix: str,
) -> list[str]:
    """Validate that all required ECR repositories exist.

    Iterates through images defined in deploy.toml that have push=true (default)
    and checks that the corresponding ECR repository exists.

    Args:
        ecr_client: boto3 ECR client.
        config: The deployment configuration (DeployConfig or dict).
        ecr_prefix: ECR repository prefix.

    Returns:
        List of missing repository names. Empty list if all exist.
    """
    missing = []

    # Support both DeployConfig dataclass and raw dict
    images = config.images if isinstance(config, DeployConfig) else config.get("images", {})

    for image_name, image_config in images.items():
        # Skip images that won't be pushed
        if isinstance(image_config, ImageConfig):
            should_push = image_config.push
        else:
            should_push = image_config.get("push", True)

        if not should_push:
            continue

        repo_name = f"{ecr_prefix}-{image_name}"
        try:
            ecr_client.describe_repositories(repositoryNames=[repo_name])
        except ClientError as e:
            if e.response["Error"]["Code"] == "RepositoryNotFoundException":
                missing.append(repo_name)
            else:
                # Re-raise unexpected errors
                raise

    return missing


def format_missing_ecr_error(missing_repos: list[str], environment: str) -> str:
    """Format an error message for missing ECR repositories with remediation.

    Args:
        missing_repos: List of missing repository names.
        environment: The target environment name.

    Returns:
        Formatted error message with instructions.
    """
    repo_list = "\n".join(f"  - {repo}" for repo in missing_repos)
    create_commands = "\n".join(
        f"  aws ecr create-repository --repository-name {repo}" for repo in missing_repos
    )

    return f"""Missing ECR repositories:
{repo_list}

ECR repositories are typically created by OpenTofu. To fix:

1. Run OpenTofu to create infrastructure (recommended):
   ./bin/tofu.sh {environment} apply

2. Or create repositories manually:
{create_commands}

If this is a new environment, ensure you've run 'tofu init' and 'tofu apply'
in the environment directory first."""
