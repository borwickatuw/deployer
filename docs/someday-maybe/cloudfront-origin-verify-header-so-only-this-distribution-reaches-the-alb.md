+++
title = "CloudFront origin-verify header so only this distribution reaches the ALB"
+++

Captured 2026-09-22 from the WAF forwarded-IP phase (deployer item 10),
which restricts ALB ingress to the `com.amazonaws.global.cloudfront.origin-facing`
managed prefix list and stamps the viewer IP into a header from a CloudFront
Function. That closes plain direct-to-ALB access but not this gap:

**Current state.** The prefix list covers every CloudFront distribution in
every AWS account. An attacker can create their own distribution with the
ALB's DNS name as origin, forward `Host: myapp.example.com` so the TLS name
check passes, and set or omit the viewer-IP custom header. That forges or
escapes the rate-rule key again and bypasses the custom 503 page. It takes
more effort than curl, but the ALB still trusts CloudFront as a service, not
this distribution.

**Proposed enhancement.** The standard companion: CloudFront adds a secret
`x-origin-verify: <value>` as an origin `custom_header`, and a WAF rule (or an
ALB listener rule) blocks requests that lack it. Then only the distribution
holding the secret reaches the origin.

**Implementation approach.** Generate the secret in tofu (`random_password`)
or read it from SSM Parameter Store; pass it to `modules/cloudfront-alb` as
the origin custom header and to `modules/waf` (or `modules/alb`) as the
match value; gate it behind the same `alb_restrict_ingress_to_cloudfront`
switch. Document rotation: change the value, apply (CloudFront propagates
in minutes), during which a two-value allow rule avoids a gap.

**Complexity**: Medium. A secret lives in state and is visible in the WAF
console; the rotation story is an operator decision (which store, what
cadence), which is why this is filed rather than landed with item 10.
