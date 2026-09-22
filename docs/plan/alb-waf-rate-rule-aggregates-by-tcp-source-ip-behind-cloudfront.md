+++
title = "ALB WAF rate rule aggregates by TCP source IP behind CloudFront"
source = "deployer"
captured = "2026-09-11"
number = 10
+++

Promoted 2026-09-22 from someday-maybe (captured 2026-09-11). Repo-local
phase; no fleet family, nothing blocked-on. **Trigger for the apply: the
next `tofu apply` of any environment with `waf` and `cloudfront` both
enabled** — the code change lands now, the behaviour changes only on apply.

**Current state.** `modules/waf/main.tf` rate rule uses
`aggregate_key_type = "IP"` with no `forwarded_ip_config`. Behind
`modules/cloudfront-alb` the ALB sees CloudFront edge addresses as the TCP
source, so every client arriving through the same edge shares one rate
counter: a busy edge trips the limit for everyone behind it, and a single
abusive client spreads across edges and never trips it. The module is
attached at the ALB (`scope = "REGIONAL"`); a CLOUDFRONT-scope web ACL is
a separate, larger idea kept in someday-maybe (Advanced WAF Features).

**The trust question this cannot skip.** `forwarded_ip_config` reads the
first address in `X-Forwarded-For`. Traffic arriving through CloudFront
carries the real client there; traffic sent straight to the ALB's public
DNS name carries whatever the sender wrote. Today nothing restricts the
ALB security group to CloudFront (`grep prefix_list modules/alb` is
empty), so a client can bypass the rule by setting the header — no worse
than today, where the same client is rate-limited under its own IP, but
the fix is only as good as the header. Restricting ALB ingress to the
`com.amazonaws.global.cloudfront.origin-facing` managed prefix list is the
standard companion and is a behaviour change of its own (health checks,
any direct-to-ALB use an operator relies on).

**Approach.** In `modules/waf`: `aggregate_key_type = "FORWARDED_IP"` with
`forwarded_ip_config { header_name = "X-Forwarded-For" fallback_behavior =
"MATCH" }` (a request with no usable header counts against the limit
rather than escaping it), gated by a new `behind_cloudfront` bool variable
the root module sets from the cloudfront-alb module's presence so the
regional ALB-only deployment keeps `IP`. Add the ALB ingress restriction
as a separately-gated option in `modules/alb` rather than folding it into
the same switch. Checkov and `make tofu-validate` green; document both
switches in `docs/CONFIG-REFERENCE.md` and the operational consequence in
`docs/operations/PRODUCTION.md`'s WAF notes.

**Complexity**: Low for the rate rule; Medium with the ingress restriction.

### Sub-phases
- **10-1 — modules/waf: behind_cloudfront variable gating FORWARDED_IP aggregation with X-Forwarded-For and fallback MATCH; root module sets it from cloudfront-alb presence; validate + checkov green**
- **10-2 — modules/alb: separately-gated ingress restriction to the CloudFront origin-facing managed prefix list, so the header the rate rule reads is one only CloudFront can write**
- **10-3 — CONFIG-REFERENCE.md and PRODUCTION.md: both switches, the apply trigger, and what an operator loses when direct-to-ALB access is closed**
