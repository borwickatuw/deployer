# ECS GPU Capacity

One EC2 Auto Scaling group of GPU instances behind an ECS capacity provider, for services that need a card (deploy.toml `gpu = 1`). Enabled per environment with the root module's `gpu_capacity` variable; `null` means no GPU capacity.

The first posture is one instance on the environment's start/stop schedule, not autoscaling: the scheduler sets the gpu service's `desiredCount`, and the capacity provider's managed scaling launches or retires the instance to match. A warm pool keeps the retired instance **stopped** rather than terminated, so its disk (tens of gigabytes of image layers) is there the next morning and the daily start is a boot, not a pull.

The instance is also the build host for the image it runs (`build_on_gpu_host` on the image): its Docker API is exposed on loopback only, and the deployer reaches it over an SSM port-forwarding session. No key pair, no ingress rule, private subnets, IMDSv2 with hop limit 1; the only way in is `ssm:StartSession`, which the bootstrap scopes to instances tagged `deployer-build-host`.

## Usage

```hcl
module "ecs_gpu_capacity" {
  source = "../../modules/ecs-gpu-capacity"

  name_prefix          = "myapp-staging"
  cluster_name         = "myapp-staging-cluster"
  vpc_id               = module.vpc.vpc_id
  private_subnet_ids   = module.vpc.private_subnet_ids
  instance_type        = "g5.2xlarge"
  permissions_boundary = data.terraform_remote_state.bootstrap.outputs.ecs_instance_role_boundary_arn
}
```

The root module wires this from `gpu_capacity` and adds the provider to the cluster (`ecs-cluster`'s `additional_capacity_providers`). Environments set it in tfvars:

```hcl
gpu_capacity = { instance_type = "g5.2xlarge", volume_gb = 300 }
```

and read the two outputs into `config.toml`'s `[infrastructure]` as `gpu_capacity_provider` and `gpu_asg_name`.

## Key Variables

| Variable             | Type         | Default | Description                                                          |
| -------------------- | ------------ | ------- | -------------------------------------------------------------------- |
| name_prefix          | string       |         | Prefix for resource names                                            |
| cluster_name         | string       |         | ECS cluster the instances register with                              |
| vpc_id               | string       |         | VPC the instances run in                                             |
| private_subnet_ids   | list(string) |         | Private subnets the group may launch into (every AZ helps scarcity)  |
| instance_type        | string       |         | GPU instance type (g5.2xlarge: one A10G, 8 vCPU, 32 GiB)             |
| volume_gb            | number       | 300     | gp3 root volume, sized for the image layers the box keeps            |
| volume_throughput    | number       | 250     | gp3 throughput in MiB/s (pulls and layer extraction are disk-bound)  |
| permissions_boundary | string       |         | The container-instance boundary (bootstrap `ecs_instance_role_boundary_arn`) |
| ecs_agent_settings   | list(string) | []      | Extra `KEY=VALUE` lines for `/etc/ecs/ecs.config`                    |

## Outputs

| Output                 | Description                                   |
| ---------------------- | --------------------------------------------- |
| capacity_provider_name | ECS capacity provider name (`<prefix>-gpu`)   |
| asg_name               | Auto Scaling group name (the build host's)    |
| instance_role_arn      | The container instance role                   |
| security_group_id      | The instance ENI's egress-only security group |

## What it sets on the instance

- AMI: `/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended` resolved at apply (`resolve:ssm:`), so an AMI roll is `aws autoscaling start-instance-refresh`, not an edit.
- `/etc/ecs/ecs.config`: `ECS_CLUSTER`, `ECS_ENABLE_GPU_SUPPORT=true`, `ECS_WARM_POOLS_CHECK=true`, `ECS_AWSVPC_BLOCK_IMDS=true`, `ECS_IMAGE_PULL_BEHAVIOR=prefer-cached`.
- `docker-loopback.socket`: `systemd-socket-proxyd` relaying `127.0.0.1:2375` to `/var/run/docker.sock`.
- Tags: `deployer-build-host = <name_prefix>` (what the deploy role's session grant is scoped by), `AmazonECSManaged` on the group.

## Unverified until an environment has run it

- That a warm-pooled instance comes back registered after a stop/start cycle (ECS agent ≥ 1.59 with `ECS_WARM_POOLS_CHECK`). If it is terminated instead of stopped, or a warm start stalls, the fallback is a fixed instance the scheduler starts and stops directly.
- GPU launch capacity in the region: a `g5.2xlarge` that will not launch is an `instance_type` change plus an instance refresh (`g5.4xlarge`, `g6.2xlarge`).
