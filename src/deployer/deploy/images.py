"""Docker image building and ECR operations."""

import base64
import fnmatch
import hashlib
import subprocess
from pathlib import Path

from botocore.exceptions import ClientError

from ..core import topological_sort
from ..timing import get_timer
from ..utils import Colors, log, log_error, log_status, log_success


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
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    patterns.append(line)

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
        if pattern.endswith("/"):
            pattern = pattern[:-1]

        # Check if any part of the path matches
        # e.g., ".git" should match ".git/config"
        parts = rel_path.parts
        for i, part in enumerate(parts):
            partial_path = str(Path(*parts[: i + 1]))
            if fnmatch.fnmatch(partial_path, pattern):
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
        if file_path.is_file():
            if not should_ignore(file_path, context_path, patterns):
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
    cmd = [
        "docker", "login",
        "--username", "AWS",
        "--password-stdin",
        registry
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.communicate(input=password.encode())

    if proc.returncode != 0:
        raise RuntimeError("ECR login failed")

    log_success("ECR login")


def get_build_args(image_config: dict, environment: str) -> dict[str, str]:
    """Get merged build arguments for an image.

    Merge order (later values override earlier):
    1. build_args = { KEY = "value" } - base args
    2. build_args.{environment} = { KEY = "value" } - environment-specific

    Args:
        image_config: The image configuration dictionary.
        environment: The target environment (staging, production).

    Returns:
        Merged build arguments dictionary.
    """
    build_args_config = image_config.get("build_args", {})

    # Start with base build_args - filter out sub-tables (staging, production, etc.)
    merged = {k: v for k, v in build_args_config.items() if not isinstance(v, dict)}

    # Merge environment-specific build_args if exists
    env_override = build_args_config.get(environment, {})
    merged.update(env_override)

    return merged


def build_and_push_images(
    config: dict,
    source_dir: Path,
    ecr_prefix: str,
    account_id: str,
    region: str,
    environment: str,
    dry_run: bool = False,
    ecr_client=None,
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
    images = config.get("images", {})

    # Sort images by dependencies
    try:
        build_order = topological_sort(images)
    except ValueError as e:
        log_error(str(e))
        raise

    for image_name in build_order:
        image_config = images[image_name]
        context = source_dir / image_config["context"]
        dockerfile = image_config.get("dockerfile", "Dockerfile")
        should_push = image_config.get("push", True)

        # Compute content hash for cache key
        content_hash = compute_context_hash(context, dockerfile)

        # Include build args in hash (they affect the image)
        build_args = get_build_args(image_config, environment)
        if build_args:
            args_str = ",".join(f"{k}={v}" for k, v in sorted(build_args.items()))
            combined = f"{content_hash}:{args_str}"
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
            if ecr_client and not dry_run and not force_build:
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
            "docker", "build",
            "--platform", "linux/amd64",
            "-t", local_tag,
            "-f", str(context / dockerfile),
        ]

        # Add build arguments
        for key, value in build_args.items():
            build_cmd.extend(["--build-arg", f"{key}={value}"])

        build_cmd.append(str(context))

        if dry_run:
            print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} {' '.join(build_cmd)}")
        else:
            timer = get_timer()
            if timer and timer._current_step:
                with timer.sub_step(f"{image_name}_build"):
                    result = subprocess.run(build_cmd, capture_output=True, text=True)
            else:
                result = subprocess.run(build_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                log_error(f"Build failed for {image_name}")
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr)
                raise RuntimeError(f"Build failed for {image_name}")

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
                timer = get_timer()
                if timer and timer._current_step:
                    with timer.sub_step(f"{image_name}_push"):
                        result = subprocess.run(push_cmd, capture_output=True, text=True)
                else:
                    result = subprocess.run(push_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    log_error(f"Push failed for {image_name}")
                    if result.stdout:
                        print(result.stdout)
                    if result.stderr:
                        print(result.stderr)
                    raise RuntimeError(f"Push failed for {image_name}")

            log_success(f"{image_name} (push)")
            image_uris[image_name] = ecr_uri
        else:
            log_status(f"{image_name}", "local only")

    return image_uris
