> Network constraint (2026-10-22): the course explicitly denies CreateSecurityGroup.
> The deployment uses existing CAB432SG without modifying or retagging it. It permits
> public TCP 8080 as well as HTTPS, so API tasks with public addresses are reachable
> directly. JWT and family authorisation are enforced, but network-level ALB-only
> isolation is NOT implemented. A production deployment needs dedicated security
> groups/private task networking. Worker containers do not listen on inbound ports.

> Deployment update (2026-10-22): see [validation](validation.md) for actual progress.
> The course account denies bucket versioning. For unversioned originals, completion
> now uses an ETag-conditional copy to a fresh server-controlled key. This prevents a
> still-valid upload form from overwriting the worker input, but retains an extra
> staging copy and is not S3 Object Lock or protection from privileged administrators.
> Earlier version-pinning and undeployed statements below describe the initial plan.

# Implemented boundaries and next steps

## Product scope

- Responsive browser interface, developer CLI and HTTP API are implemented. Full
  Cognito signup and confirmation screens, invitation email, account discovery, user
  quotas and subscription billing are not built.
- People labels are manual. Face recognition, pose estimation, live camera overlays,
  AI-generated recreation and Rekognition quality analysis are not implemented.
- Album prompts support a fixed filter schema. Rich semantic search is separate;
  arbitrary multi-step autonomous album management is not implemented.
- On This Day is computed on request. Unknown capture dates are excluded, not replaced
  with upload dates. Callers should send their local date; omission defaults to UTC.
  February 29 only matches on February 29. Scheduled notifications are not implemented.

## Data and image analysis

- The initial metadata store queries whole family collections. Add pagination and
  access-pattern-specific indexes before large-library operation.
- Similarity review is deliberately limited to 500 READY photos. It compares exact
  SHA-256 and same-capture-day dHash candidates; this is not a scalable vector duplicate
  detector. Simple scenes can generate false positives. Quality thresholds are
  uncalibrated heuristics, not objective judgments of personal or artistic value.
- Image input supports single-frame JPEG, PNG and WebP, at most 20 MiB and 40 megapixels.
  HEIC, RAW, animation and video require future decoders and separate cost limits.
- Variant generation preserves aspect ratio and never upscales. Client-side crop/fit
  controls and screen-density selection remain frontend work.
- Original files retain their EXIF. Derived JPEGs omit EXIF. Original downloads may
  expose location metadata to authorised family members.

## Consistency, deletion and resilience

- Upload, metadata and enqueue are not atomic. An explicit retry of `/complete`
  recovers enqueue failures; a crash between record and queue writes still needs
  reconciliation. Uncompleted upload intents/orphan S3 objects are not auto-cleaned.
- S3 versioning pins the original used for processing. Do not expire pinned noncurrent
  versions. No automatic permanent deletion or storage deduplication is implemented.
- Recycle-bin deletion is logical and does not save S3 space. Previously issued image
  URLs remain valid for up to five minutes. CDN invalidation, permanent deletion,
  vector purging and retention scheduling are not implemented.
- Worker leases and deterministic outputs handle common retries but do not establish
  exactly-once external model calls. A crash may repeat billable work.
- Cached CloudFront signing keys do not automatically rotate in running processes.
  Key rotation needs a controlled deployment and overlap of trusted keys.
- SQLite is for local development only. The local queue does not emulate SQS fairness.

## AWS and evaluation

- No A3 resources have been deployed or retagged. No live Cognito, Bedrock, S3 Vectors,
  CloudFront or ECS integration results are claimed.
- The API and ALB have a baseline cost. Only the image worker is configured to scale
  to zero. No load/cost measurements or 50-user capacity claims have been made.
- Optional global CloudFront and service-linked-role permissions need course-account
  confirmation. Existing A2 IAM restrictions may prevent some CloudFormation actions.
- Existing `report.docx` remains an earlier framework. Update its scope in your own
  writing to reflect photo-library management and only actually demonstrated features.

