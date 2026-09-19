+++
title = "Phase 69 (2026-09-18): havoc's block-stream worker moves off"
source = "havoc"
captured = "2026-09-18"
+++

Phase 69 (2026-09-18): havoc's block-stream worker moves off Fargate onto an EC2-backed ECS service with a GPU, running blocker's full image (~25 GB, weights baked in). Deployer needs: an EC2 capacity provider on the ECS GPU-optimized AMI; an autoscaling group scaling from zero on queue depth using the existing three-signal scaling (ADR-059) for one more service; GPU resourceRequirements on the task definition; the scheduler-window posture the transcoder already has; instance type in the g6.4xlarge shape (16 vCPU, 64 GiB, L4 24 GB) so OCR keeps its cores; and a cold-start answer for a 25 GB image (AMI with the image pre-pulled, warm pool, or a long cooldown). deployer-environments carries the sizing.
