"""Shared constants for the deployer package."""

# Default AWS region for all operations
AWS_REGION = "us-west-2"

# AWS API error codes the deployer branches on. Named here because the branch
# is a string comparison: a typo in any one of them reverses the branch
# silently -- "absent" starts reading as "broken", or a permissions failure
# starts reading as "not there".
AWS_ERROR_RESOURCE_NOT_FOUND = "ResourceNotFoundException"
AWS_ERROR_ACCESS_DENIED = "AccessDeniedException"
