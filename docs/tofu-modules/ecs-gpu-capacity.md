# ECS GPU Capacity

One fixed EC2 GPU instance registered as a container instance of the ECS cluster, for services that need a card (deploy.toml `gpu = 1`, placed with the EC2 launch type). Enabled per environment with the root module's `gpu_capacity` variable; `null` means no GPU capacity.

The instance lives on the environment's start/stop schedule: the [staging scheduler](staging-scheduler.md) (and `bin/environment.py`) stop it after the services have scaled to zero and start it before they scale back up. Stopped, it costs only its volume, and that volume is the point: the disk the instance built up (the worker image's tens of gigabytes of layers, the deployer's build cache) is the disk it starts with, so the morning start is a boot and an agent reconnect, not a pull.

The instance is also the build host for the image it runs (`build_on_gpu_host` on the image): its Docker API is exposed on loopback only, and the deployer reaches it over an SSM port-forwarding session. No key pair, no ingress rule, a private subnet, IMDSv2 with hop limit 1; the only way in is `ssm:StartSession`, which the bootstrap scopes to instances tagged `deployer-build-host`.

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

The root module wires this from `gpu_capacity` and hands the instance id to the scheduler. Environments set it in tfvars:

```hcl
gpu_capacity = { instance_type = "g5.2xlarge", volume_gb = 300 }
```

and read the `gpu_instance_id` output into `config.toml`'s `[infrastructure]` as `gpu_instance_id = "${tofu:gpu_instance_id}"`.

## Key Variables

| Variable             | Type         | Default | Description                                                                  |
| -------------------- | ------------ | ------- | ---------------------------------------------------------------------------- |
| name_prefix          | string       |         | Prefix for resource names                                                    |
| cluster_name         | string       |         | ECS cluster the instance registers with                                      |
| vpc_id               | string       |         | VPC the instance runs in                                                     |
| private_subnet_ids   | list(string) |         | Private subnets; the instance takes the first                                |
| instance_type        | string       |         | GPU instance type (g5.2xlarge: one A10G, 8 vCPU, 32 GiB)                     |
| volume_gb            | number       | 300     | gp3 root volume, sized for the image layers the box keeps                    |
| volume_throughput    | number       | 250     | gp3 throughput in MiB/s (pulls and layer extraction are disk-bound)          |
| permissions_boundary | string       |         | The container-instance boundary (bootstrap `ecs_instance_role_boundary_arn`) |
| ecs_agent_settings   | list(string) | []      | Extra `KEY=VALUE` lines for `/etc/ecs/ecs.config`                            |

## Outputs

| Output            | Description                                   |
| ----------------- | --------------------------------------------- |
| instance_id       | The instance (the scheduler's and build host) |
| instance_role_arn | The container instance role                   |
| security_group_id | The instance ENI's egress-only security group |

## What it sets on the instance

- AMI: `/aws/service/ecs/optimized-ami/amazon-linux-2023/gpu/recommended` read at apply, then ignored (`lifecycle.ignore_changes = [ami]`): the instance's disk is its cache, so an AMI roll is a deliberate `tofu apply -replace=module.ecs_gpu_capacity[0].aws_instance.gpu`, and the next start after one is cold.
- `/etc/ecs/ecs.config`: `ECS_CLUSTER`, `ECS_ENABLE_GPU_SUPPORT=true`, `ECS_AWSVPC_BLOCK_IMDS=true`, `ECS_IMAGE_PULL_BEHAVIOR=prefer-cached`. Written by first-boot user data; a user-data change replaces the instance (`user_data_replace_on_change`).
- `docker-loopback.socket`: `systemd-socket-proxyd` relaying `127.0.0.1:2375` to `/var/run/docker.sock`.
- `instance_initiated_shutdown_behavior = "stop"`: nothing on the box can terminate it.
- Tags: `deployer-build-host = <name_prefix>`, what the deploy role's session grant and the scheduler's start/stop grants are scoped by.

## Operating it

- **Stop and start** are the scheduler's and `bin/environment.py`'s; a stop waits for the container instance's tasks to drain (the worker requeues its job on SIGTERM) before the instance is stopped. The ECS agent reconnects on start under the same container instance.
- **GPU launch capacity**: a start that AWS refuses (`InsufficientInstanceCapacity`) is retried at the next start or by hand (`aws ec2 start-instances`); a type that will not come back is an `instance_type` change plus `-replace` (`g5.4xlarge`, `g6.2xlarge`), and that start is cold.
- **Disk**: `docker image prune -a --filter until=720h` over the deployer's tunnel when the volume fills.
