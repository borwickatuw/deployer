"""Core business logic for deployment operations."""

from .audit import (
    DEFAULT_IGNORE_SERVICES,
    audit_env_vars,
    audit_images,
    audit_services,
    run_audit,
)
from .capacity import (
    ServiceMetrics,
    calculate_percentile,
    classify_service,
    estimate_savings,
    generate_suggested_tfvars,
    generate_tfvars_diff,
    get_recommended_cpu,
    get_recommended_memory,
)
from .cognito import (
    copy_to_clipboard,
    format_user,
    format_welcome_message,
    generate_temp_password,
)
from .config import (
    derive_environment_from_env_name,
    get_cluster_name_from_config,
    get_cognito_auth_token,
    get_cognito_test_credentials,
    get_cognito_user_pool_id_from_config,
    get_ecr_prefix_from_config,
    get_environment_type,
    get_rds_instance_id_from_config,
    get_service_replicas_from_config,
    get_ssm_parameter,
    get_staging_url_from_config,
    get_target_group_arn_from_config,
    is_cognito_enabled,
    load_environment_config,
)
from .deploy import topological_sort
from .ssm_secrets import (
    check_secrets_exist,
    format_missing_secrets_error,
    get_parameter_path,
    get_path_prefix,
    get_secrets_from_config,
    get_secrets_from_deploy_toml,
    parse_environment,
)

__all__ = [
    # audit
    "audit_env_vars",
    "audit_images",
    "audit_services",
    "DEFAULT_IGNORE_SERVICES",
    "run_audit",
    # capacity
    "calculate_percentile",
    "classify_service",
    "estimate_savings",
    "generate_suggested_tfvars",
    "generate_tfvars_diff",
    "get_recommended_cpu",
    "get_recommended_memory",
    "ServiceMetrics",
    # cognito
    "copy_to_clipboard",
    "format_user",
    "format_welcome_message",
    "generate_temp_password",
    # config
    "derive_environment_from_env_name",
    "get_environment_type",
    "get_cluster_name_from_config",
    "get_cognito_auth_token",
    "get_cognito_test_credentials",
    "get_cognito_user_pool_id_from_config",
    "get_ecr_prefix_from_config",
    "get_rds_instance_id_from_config",
    "get_service_replicas_from_config",
    "get_ssm_parameter",
    "get_staging_url_from_config",
    "get_target_group_arn_from_config",
    "is_cognito_enabled",
    "load_environment_config",
    # deploy
    "topological_sort",
    # secrets
    "check_secrets_exist",
    "format_missing_secrets_error",
    "get_parameter_path",
    "get_path_prefix",
    "get_secrets_from_config",
    "get_secrets_from_deploy_toml",
    "parse_environment",
]
