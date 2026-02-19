"""ECR operations for emergency response.

Provides functions for:
- Getting vulnerability scan findings
- Listing images with scan status
"""

from typing import Any

import boto3
from botocore.exceptions import ClientError


def get_ecr_client() -> Any:
    """Get boto3 ECR client."""
    return boto3.client("ecr")


def get_image_scan_findings(
    repository_name: str,
    image_tag: str = "latest",
    severity_filter: list[str] | None = None,
) -> dict:
    """Get vulnerability scan findings for an ECR image.

    Args:
        repository_name: ECR repository name
        image_tag: Image tag to scan (default: latest)
        severity_filter: Filter by severity (default: CRITICAL, HIGH)

    Returns:
        Dict with scan findings:
        {
            "image_digest": "sha256:...",
            "scan_status": "COMPLETE",
            "vulnerability_counts": {"CRITICAL": 2, "HIGH": 5, ...},
            "findings": [
                {
                    "name": "CVE-2024-1234",
                    "severity": "CRITICAL",
                    "description": "...",
                    "uri": "https://...",
                },
                ...
            ],
        }
    """
    if severity_filter is None:
        severity_filter = ["CRITICAL", "HIGH"]

    client = get_ecr_client()
    result = {
        "image_digest": None,
        "scan_status": None,
        "vulnerability_counts": {},
        "findings": [],
    }

    try:
        response = client.describe_image_scan_findings(
            repositoryName=repository_name,
            imageId={"imageTag": image_tag},
        )

        image_scan = response.get("imageScanFindings", {})
        result["image_digest"] = response.get("imageId", {}).get("imageDigest")
        result["scan_status"] = response.get("imageScanStatus", {}).get("status")
        result["vulnerability_counts"] = image_scan.get("findingSeverityCounts", {})

        for finding in image_scan.get("findings", []):
            severity = finding.get("severity", "")
            if severity in severity_filter:
                result["findings"].append(
                    {
                        "name": finding.get("name", ""),
                        "severity": severity,
                        "description": finding.get("description", ""),
                        "uri": finding.get("uri", ""),
                    }
                )

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ScanNotFoundException":
            result["scan_status"] = "NOT_SCANNED"
        elif error_code == "ImageNotFoundException":
            result["scan_status"] = "IMAGE_NOT_FOUND"
        else:
            result["scan_status"] = f"ERROR: {error_code}"

    return result


def get_repository_scan_summary(repository_name: str, max_images: int = 5) -> list[dict]:
    """Get scan summary for recent images in a repository.

    Args:
        repository_name: ECR repository name
        max_images: Maximum number of images to check

    Returns:
        List of image scan summaries:
        [
            {
                "image_tag": "latest",
                "image_digest": "sha256:...",
                "pushed_at": "2026-02-04T10:00:00Z",
                "scan_status": "COMPLETE",
                "critical_count": 0,
                "high_count": 2,
            },
            ...
        ]
    """
    client = get_ecr_client()
    result = []

    try:
        response = client.describe_images(
            repositoryName=repository_name,
            maxResults=max_images,
        )

        # Sort by push date, newest first
        images = sorted(
            response.get("imageDetails", []),
            key=lambda x: x.get("imagePushedAt", ""),
            reverse=True,
        )[:max_images]

        for image in images:
            tags = image.get("imageTags", [])
            tag = tags[0] if tags else "(untagged)"
            pushed_at = image.get("imagePushedAt")
            if hasattr(pushed_at, "isoformat"):
                pushed_at = pushed_at.isoformat()

            scan_status = image.get("imageScanStatus", {}).get("status", "NOT_SCANNED")
            scan_findings = image.get("imageScanFindingsSummary", {})
            counts = scan_findings.get("findingSeverityCounts", {})

            result.append(
                {
                    "image_tag": tag,
                    "image_digest": image.get("imageDigest", ""),
                    "pushed_at": pushed_at,
                    "scan_status": scan_status,
                    "critical_count": counts.get("CRITICAL", 0),
                    "high_count": counts.get("HIGH", 0),
                }
            )

    except ClientError:
        pass

    return result


def list_repositories_for_environment(
    environment: str, service_names: list[str] | None = None
) -> list[str]:
    """List ECR repositories for an environment.

    Repositories are named {environment}-{service} (e.g., myapp-staging-web).

    Args:
        environment: Environment name (e.g., myapp-staging)
        service_names: Optional list of service names to check. If provided,
            constructs repository names directly instead of listing all repos
            (which may fail due to IAM permissions).

    Returns:
        List of repository names that exist
    """
    client = get_ecr_client()
    result = []

    # If service names provided, check those specific repositories
    if service_names:
        repo_names = [f"{environment}-{svc}" for svc in service_names]
        try:
            response = client.describe_repositories(repositoryNames=repo_names)
            for repo in response.get("repositories", []):
                result.append(repo.get("repositoryName", ""))
        except ClientError as e:
            # RepositoryNotFoundException is expected if some don't exist
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "RepositoryNotFoundException":
                # Try each one individually
                for repo_name in repo_names:
                    try:
                        response = client.describe_repositories(repositoryNames=[repo_name])
                        for repo in response.get("repositories", []):
                            result.append(repo.get("repositoryName", ""))
                    except ClientError:
                        pass
        return result

    # Otherwise try to list all and filter (may fail due to permissions)
    try:
        paginator = client.get_paginator("describe_repositories")
        for page in paginator.paginate():
            for repo in page.get("repositories", []):
                name = repo.get("repositoryName", "")
                # Match environment prefix with hyphen to avoid partial matches
                # e.g., "myapp-staging-" matches "myapp-staging-web"
                if name.startswith(f"{environment}-"):
                    result.append(name)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "AccessDeniedException":
            # IAM policy may restrict listing all repos
            # Return empty - caller should try with service_names
            pass

    return result
