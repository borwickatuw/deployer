+++
title = "ALB WAF rate rule aggregates by TCP source IP behind CloudFront"
source = "deployer"
captured = "2026-09-11"
+++

ALB WAF rate rule aggregates by TCP source IP behind the CloudFront passthrough (no forwarded_ip_config) — distinct clients share edge-IP counters; fix via forwarded_ip_config or a CLOUDFRONT-scope web ACL (already in SOMEDAY-MAYBE)
