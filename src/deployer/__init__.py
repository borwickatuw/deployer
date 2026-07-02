"""Deployer - OpenTofu infrastructure and deployment tooling for AWS ECS applications."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("deployer")
except PackageNotFoundError:  # working from a source tree, not installed
    __version__ = "0.0.0+unknown"
