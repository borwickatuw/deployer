+++
title = "Direct-to-S3 Uploads for Large Files"
+++

The current large file upload approach requires increasing timeouts at multiple layers (gunicorn, ALB, WAF). A better pattern for UI uploads is **direct-to-S3 with presigned URLs**:

### How It Works

1. User selects file in browser
1. JavaScript requests a presigned upload URL from Django
1. Django generates S3 presigned POST/PUT URL (with size limits, content-type restrictions)
1. Browser uploads directly to S3 (bypasses Django, ALB, gunicorn entirely)
1. S3 notifies Django via Lambda/SQS, or browser calls Django to confirm upload
1. Django creates the File record and queues transcoding

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
