# ECR

Creates ECR repositories for container images with lifecycle policies to control image retention.

## Usage

```hcl
module "ecr" {
  source = "../../modules/ecr"

  name_prefix      = "myapp-staging"
  repository_names = ["web", "celery"]
}
```

## Key Variables

| Variable               | Type         | Description                                         |
| ---------------------- | ------------ | --------------------------------------------------- |
| name_prefix            | string       | Prefix for repository names                         |
| repository_names       | list(string) | List of repository names to create                  |
| scan_on_push           | bool         | Enable image scanning on push (default: true)       |
| lifecycle_policy_count | number       | Images to keep per repo (default: 10, 0 to disable) |
| force_delete           | bool         | Delete repo even with images (default: false)       |

## Outputs

| Output           | Description                           |
| ---------------- | ------------------------------------- |
| repository_urls  | Map of repo names to URLs             |
| repository_arns  | Map of repo names to ARNs             |
| repository_names | Map of short names to full repo names |
