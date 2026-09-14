+++
title = "Advanced WAF Features"
+++

The `modules/waf` module provides baseline protection. Future enhancements could include:

### Account Fraud Protection (ATP/ACFP)

AWS offers paid rule groups for login and signup endpoints:

- **Account Takeover Prevention (ATP)**: Protects login endpoints from credential stuffing
- **Account Creation Fraud Prevention (ACFP)**: Blocks fraudulent account creation

These require app-specific configuration (login/signup endpoint paths) and cost ~$10/month + per-request fees.

### Client-Side SDK Integration

AWS WAF's advanced bot protection works best with JavaScript/mobile SDKs that provide challenge tokens:

- Enables CAPTCHA and silent challenges
- Improves bot detection accuracy
- Requires app code changes to integrate the SDK

### CloudFront WAF Integration

Currently the WAF module only supports ALB attachment. For apps using CloudFront:

- WAF can attach to CloudFront distributions for edge-level protection
- Requires scope="CLOUDFRONT" and us-east-1 region
- Blocks bad traffic before it reaches the origin

### Centralized WAF Management

For organizations with many environments:

- **AWS Firewall Manager**: Enforce WAF policies across multiple accounts/regions
- Requires AWS Organizations setup
- Provides compliance reporting and automatic remediation

### Advanced Rate Limiting

The current module uses simple IP-based rate limiting. Advanced options:

- Rate limit by URI path (different limits for /api vs /static)
- Rate limit by custom header (e.g., API key)
- Rate limit by authenticated user (requires forwarded headers)
- Aggregate by IP + URI for more precise control

### Shield Advanced

For high-value applications needing DDoS protection:

- $3,000/month subscription
- Automatic application layer DDoS mitigation
- 24/7 access to AWS DDoS Response Team
- Cost protection (credits for scaling during attacks)
