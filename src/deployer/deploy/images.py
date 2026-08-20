"""Docker image building and ECR operations."""

import base64
import fnmatch
import hashlib
import subprocess
from pathlib import Path
from typing import NamedTuple

from botocore.exceptions import ClientError

from ..config import DeployConfig, ImageConfig, merge_build_args
from ..core.deploy import topological_sort
from ..timing import get_timer
from ..utils import Colors, log, log_error, log_status, log_success


def _run_timed_subprocess(cmd: list[str], step_name: str) -> subprocess.CompletedProcess:
    """Run a subprocess with optional timer integration."""
    timer = get_timer()
    if timer and timer.in_step:
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
        with open(dockerignore_path, encoding="utf-8") as f:
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

    Raises:
        RuntimeError: If ``docker login`` exits non-zero, carrying whatever
            docker wrote to stderr.
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

    # Use docker login. stderr is captured rather than discarded: a failed
    # login used to raise a bare "ECR login failed" with both streams sent to
    # DEVNULL, leaving the operator nothing to act on. stdout stays discarded
    # -- it carries only docker's "Login Succeeded" and its credential-store
    # warning, and echoing it on success would be noise.
    cmd = ["docker", "login", "--username", "AWS", "--password-stdin", registry]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    _, stderr = proc.communicate(input=password.encode())

    if proc.returncode != 0:
        detail = (stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"ECR login failed: {detail or 'docker printed nothing'}")

    log_success("ECR login")


class ImageBuildSpec(NamedTuple):
    """The build inputs for one image, resolved for a single environment."""

    context: Path
    dockerfile: str
    should_push: bool
    build_args: dict
    target: str | None


def _resolve_context(image_name: str, source_dir: Path, context: str) -> Path:
    """Resolve one image's build context and check that it exists.

    Args:
        image_name: Name of the image, for the error message.
        source_dir: Root of the source tree that ``context`` is relative to.
        context: The image's ``context`` value from deploy.toml.

    Returns:
        The resolved context directory.

    Raises:
        RuntimeError: If the resolved path is not an existing directory. Without
            this check ``rglob`` yields nothing, the image silently takes the
            digest of the empty string, and ``docker build`` then fails against a
            path that does not exist -- two failures for one typo, neither of
            which names the typo.
    """
    resolved = source_dir / context
    if not resolved.is_dir():
        raise RuntimeError(f"Image '{image_name}': build context '{resolved}' is not a directory.")
    return resolved


def _resolve_image_spec(
    image_name: str, image_config: ImageConfig | dict, source_dir: Path, environment: str
) -> ImageBuildSpec:
    """Resolve one image's config entry into concrete build inputs.

    The two arms read the same deploy.toml through two shapes and must agree.
    Both route build args through :func:`merge_build_args` and both resolve the
    context through :func:`_resolve_context`, so neither can drift from the
    other on a config error.

    Args:
        image_name: Name of the image, for error messages.
        image_config: Either an ``ImageConfig`` or the raw dict form.
        source_dir: Root of the source tree that ``context`` is relative to.
        environment: Target environment, selecting per-environment build
            args and build target.

    Returns:
        The resolved :class:`ImageBuildSpec`.

    Raises:
        RuntimeError: If the build context does not exist, or if
            ``build_args.<environment>`` is not a table.
    """
    if isinstance(image_config, ImageConfig):
        return ImageBuildSpec(
            context=_resolve_context(image_name, source_dir, image_config.context),
            dockerfile=image_config.dockerfile,
            should_push=image_config.push,
            build_args=image_config.get_build_args(environment),
            target=image_config.get_target(environment),
        )

    # Legacy dict support - inline the logic
    target_config = image_config.get("target")
    return ImageBuildSpec(
        context=_resolve_context(image_name, source_dir, image_config["context"]),
        dockerfile=image_config.get("dockerfile", "Dockerfile"),
        should_push=image_config.get("push", True),
        build_args=merge_build_args(image_name, image_config.get("build_args", {}), environment),
        target=target_config.get(environment) if isinstance(target_config, dict) else target_config,
    )


def _cache_tag(spec: ImageBuildSpec) -> str:
    """Compute the content-addressed cache tag for one image.

    The tag starts as a hash of the build context, then is re-hashed together
    with the build args and target so that changing either yields a new tag.

    Args:
        spec: The resolved build inputs for the image.

    Returns:
        A 12-character hex tag.
    """
    content_hash = compute_context_hash(spec.context, spec.dockerfile)

    hash_modifiers = []
    if spec.build_args:
        args_str = ",".join(f"{k}={v}" for k, v in sorted(spec.build_args.items()))
        hash_modifiers.append(f"args:{args_str}")
    if spec.target:
        hash_modifiers.append(f"target:{spec.target}")

    if hash_modifiers:
        combined = f"{content_hash}:{';'.join(hash_modifiers)}"
        content_hash = hashlib.sha256(combined.encode()).hexdigest()[:12]

    return content_hash


def _docker_build_cmd(spec: ImageBuildSpec, local_tag: str) -> list[str]:
    """Assemble the ``docker build`` argv for one image.

    ``--platform`` ensures consistent builds for Fargate (x86_64) regardless
    of host architecture. We rely on content-based hashing to detect changes,
    so Docker layer caching is safe and speeds up rebuilds when only some
    files change.

    Args:
        spec: The resolved build inputs for the image.
        local_tag: The local ``name:tag`` to build into.

    Returns:
        The full ``docker build`` argv.
    """
    build_cmd = [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-t",
        local_tag,
        "-f",
        str(spec.context / spec.dockerfile),
    ]

    # Add target if specified (for multi-stage builds)
    if spec.target:
        build_cmd.extend(["--target", spec.target])

    # Add build arguments
    for key, value in spec.build_args.items():
        build_cmd.extend(["--build-arg", f"{key}={value}"])

    build_cmd.append(str(spec.context))
    return build_cmd


def _run_docker(cmd: list[str], image_name: str, operation: str, dry_run: bool) -> None:
    """Run one docker command for an image, honouring dry-run.

    Args:
        cmd: The argv to run.
        image_name: Image the command belongs to, used in the timer sub-step
            name and in failure messages.
        operation: Short verb ("build", "push") naming the step.
        dry_run: If True, print the command instead of running it.

    Raises:
        RuntimeError: If the command exits non-zero. Both captured streams
            are echoed first.
    """
    if dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} {' '.join(cmd)}")
        return

    result = _run_timed_subprocess(cmd, f"{image_name}_{operation}")
    _check_subprocess_result(result, image_name, operation)


def _tag_and_push(local_tag: str, ecr_uri: str, image_name: str, dry_run: bool) -> None:
    """Tag a locally built image for ECR and push it.

    The ``docker tag`` call deliberately does *not* go through
    :func:`_run_docker`. It is neither captured nor timed, so a tag failure
    raises ``CalledProcessError`` with nothing echoed to the operator, unlike
    build and push which raise ``RuntimeError`` after printing both streams.
    Unifying the three would silently change all of that at once.

    Args:
        local_tag: The local ``name:tag`` produced by the build.
        ecr_uri: The fully qualified ECR URI to tag and push.
        image_name: Image name, used in messages.
        dry_run: If True, print the commands instead of running them.
    """
    # Tag for ECR
    tag_cmd = ["docker", "tag", local_tag, ecr_uri]
    if dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} {' '.join(tag_cmd)}")
    else:
        subprocess.run(tag_cmd, check=True)

    # Push to ECR
    _run_docker(["docker", "push", ecr_uri], image_name, "push", dry_run)
    log_success(f"{image_name} (push)")


def _normalize_images(config: DeployConfig | dict) -> tuple[dict, dict]:
    """Split a deploy config into its image map and a dependency-only map.

    Args:
        config: Deployment configuration, either a ``DeployConfig`` or the
            raw dict form the legacy callers still pass.

    Returns:
        An ``(images, images_for_sort)`` pair. ``images`` holds whatever the
        config carried -- ``ImageConfig`` values or raw dicts. The second is
        always a plain dict shaped for :func:`topological_sort`.
    """
    if isinstance(config, DeployConfig):
        images = config.images
        # Convert to dict format for topological_sort
        return images, {name: {"depends_on": img.depends_on} for name, img in images.items()}

    images = config.get("images", {})
    return images, images


def build_and_push_images(
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

    images, images_for_sort = _normalize_images(config)

    # Sort images by dependencies
    try:
        build_order = topological_sort(images_for_sort)
    except ValueError as e:
        log_error(str(e))
        raise

    for image_name in build_order:
        spec = _resolve_image_spec(image_name, images[image_name], source_dir, environment)
        tag = _cache_tag(spec)

        # Local-only images are tagged with just their name (for FROM references)
        # Pushed images get the ecr_prefix
        if spec.should_push:
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

        _run_docker(_docker_build_cmd(spec, local_tag), image_name, "build", dry_run)
        log_success(f"{image_name} (build {tag[:8]})")

        if spec.should_push:
            _tag_and_push(local_tag, ecr_uri, image_name, dry_run)
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
