+++
title = "Phase 69 (2026-09-18): havoc's block-stream worker moves off"
source = "havoc"
captured = "2026-09-18"
+++

Phase 69 (2026-09-18, reshaped 2026-09-25 against havoc's ADR-092):
havoc's block-stream worker moves off Fargate onto an EC2-backed ECS
service with a GPU, running a thin image built FROM blocker's published
`describe` image (~54 GB on disk, weights baked in). Deployer needs: one
fixed EC2 instance on the ECS GPU-optimized AMI, registered in the
cluster as a container instance, **no** queue-depth autoscaling — the
first posture is one scheduled box on havoc-staging's existing
start/stop schedule: the scheduler Lambda stops and starts the instance
directly, so its disk keeps the layers between working days (a code
patch in blocker changes only the top layers), and it already sets
desiredCount for every service in `var.services`; GPU `resourceRequirements` on the
task definition (`requiresCompatibilities = ["EC2"]`, `gpu = 1` in
`ServiceConfig`); the instance in the **g5.2xlarge** shape (8 vCPU,
32 GiB, A10G 24 GB — blocker's design target); a 300 GB gp3 root
volume; a bootstrap instance-role boundary (the ECS agent and SSM agent
actions the existing `deployer-ecs-role-boundary` lacks); and the image
built **on the box's own Docker daemon** over an SSM port-forward
(`build_on_gpu_host` on the image), so the laptop never holds the base.
deployer-environments carries the sizing (`gpu_capacity` variable).
Closed by havoc's 69-3 when it lands.
