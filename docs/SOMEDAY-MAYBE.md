# Someday/Maybe Ideas

Ideas for future improvements that aren't urgent.

## Separate Generic Framework from Environment-Specific Config

The repository has a clean separation between generic infrastructure code and environment-specific configurations:

**Generic (could be open-sourced or shared):**
- Root `.tf` files (main.tf, variables.tf, outputs.tf, versions.tf)
- `modules/` directory
- `bin/` (deploy.py, manage-environment.py, etc.)
- `example-deploy.toml`
- `example-deployer-environments/`
- `docs/`

**Environment-specific:**
- `environments/` (myapp-staging, myapp-production, etc.)

Possible restructuring options:
1. Publish generic parts as an open-source "ECS deployer" template
2. Move environments to a separate private repo that references this one
3. Use Git submodules to keep framework and environments separate
4. Keep as-is but gitignore environments/ for public sharing

## Advanced WAF Features

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

## Direct-to-S3 Uploads for Large Files

The current large file upload approach requires increasing timeouts at multiple layers (gunicorn, ALB, WAF). A better pattern for UI uploads is **direct-to-S3 with presigned URLs**:

### How It Works
1. User selects file in browser
2. JavaScript requests a presigned upload URL from Django
3. Django generates S3 presigned POST/PUT URL (with size limits, content-type restrictions)
4. Browser uploads directly to S3 (bypasses Django, ALB, gunicorn entirely)
5. S3 notifies Django via Lambda/SQS, or browser calls Django to confirm upload
6. Django creates the File record and queues transcoding

### Benefits
- **No timeout concerns**: S3 handles the upload, not Django
- **Resumable**: Can use S3 multipart uploads for pause/resume support
- **Progress tracking**: Browser can show real upload progress
- **Scalable**: Django workers aren't blocked during uploads
- **Cost effective**: Less ALB/ECS compute time

### Implementation Notes
- Use `boto3.client('s3').generate_presigned_post()` for browser uploads
- Set conditions: content-length-range, content-type, key prefix
- Consider S3 event notifications (Lambda or SQS) for upload completion
- For multipart (resumable), look at libraries like Uppy, Evaporate.js, or AWS Amplify
- CORS configuration needed on S3 bucket

### When to Use
- Files > 100 MB
- When upload reliability matters (resumable uploads)
- High-traffic sites where Django worker availability is precious

### Current Approach (for reference)
The timeout-based approach works for occasional large uploads:
- `DATA_UPLOAD_MAX_MEMORY_SIZE` (Django): 1 GB
- `--timeout 1800` (gunicorn): 30 minutes
- `alb_idle_timeout`: 1800 seconds
- WAF `SizeRestrictions_BODY`: excluded
