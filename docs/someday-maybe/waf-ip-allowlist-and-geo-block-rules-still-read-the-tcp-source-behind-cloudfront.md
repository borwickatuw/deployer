+++
title = "WAF IP allowlist and geo-block rules still read the TCP source behind CloudFront"
+++

Captured 2026-09-22 from the WAF forwarded-IP phase (deployer item 10),
which fixed the rate rule only.

**Current state.** `modules/waf` has IP-allowlist and geo-match rules that
inspect the TCP source address. Behind `modules/cloudfront-alb` that is a
CloudFront edge, so an office allowlist never matches (the office arrives
from an edge) and geo-blocking decides by edge location, not viewer
location. Same flaw the rate rule had; same fix is available.

**Proposed enhancement.** When `behind_cloudfront` is on, point the IP-set
and geo-match statements at the `x-viewer-ip` header via
`forwarded_ip_config` / `ip_set_forwarded_ip_config` (geo-match supports
`forwarded_ip_config` too), with the same MATCH fallback and the same
`origin_restricted_to_cloudfront` precondition item 10 introduced.

**Complexity**: Low. One dynamic block per rule; mock-provider `tofu test`
cases in the same shape as item 10's.
