> Current cache architecture: process-local bounded photo-list cache in the API,
> not CloudFront. See deployment-progress.md for TTL, invalidation, privacy checks,
> limits and the observed account-policy reason. The CDN diagram below is the initial
> rejected design and must be replaced in the final report.

> Deployment update (2026-10-22): see [validation](validation.md) for actual progress.
> The course account denies bucket versioning. For unversioned originals, completion
> now uses an ETag-conditional copy to a fresh server-controlled key. This prevents a
> still-valid upload form from overwriting the worker input, but retains an extra
> staging copy and is not S3 Object Lock or protection from privileged administrators.
> Earlier version-pinning and undeployed statements below describe the initial plan.

# Architecture working notes

These are implementation notes for development, not report prose or deployment evidence.

```mermaid
flowchart LR
  C[CLI or future browser client] --> A[Authenticated library API]
  A --> D[Family-scoped metadata]
  C -->|Signed upload| O[Private original storage]
  A --> Q[Processing queue]
  Q -->|Worker pull| W[Image workers]
  W --> O
  W --> D
  W --> V[Optional vision and vector index]
  A --> L[Optional constrained language planner]
  A --> V
  C -->|Authorised image URL| E[Private edge delivery]
  E --> O
  M[Queue metrics and scaling controller] -.-> W
```

## Upload and indexing workflow

```mermaid
sequenceDiagram
  participant U as Browser
  participant A as Library API
  participant D as Metadata store
  participant S as Private object store
  participant Q as Durable job queue
  participant W as Pull worker
  participant B as Vision and embedding models
  participant V as Vector index
  U->>A: Request upload (authenticated family)
  A->>D: Create UPLOADING record and fixed object key
  A-->>U: Short-lived signed upload form
  U->>S: Upload original directly
  U->>A: Complete upload
  A->>S: Verify size and current version
  A->>D: Save pinned version and QUEUED state
  A->>Q: Enqueue {schemaVersion, familyId, photoId}
  W->>Q: Long-poll and claim one job
  W->>D: Acquire conditional lease
  W->>S: Read pinned original version
  W->>W: Validate, hash, score quality, create display variants
  W->>S: Write deterministic derived objects
  opt Vision indexing configured
    W->>B: Caption selected-size variant and embed caption
    B-->>W: Caption and vector
    W->>V: Upsert family-scoped vector metadata
  end
  W->>D: Commit READY state and variant metadata
  W->>Q: Delete message after successful commit
```

The browser never proxies image bytes through the API. This reduces API bandwidth
and lets object storage absorb upload concurrency. The queue separates user-facing
upload latency from CPU/AI work. The metadata record remains authoritative: vector
search first applies a family filter, then checks each hit against the caller's
family membership and current READY photo record. A stale or trashed vector cannot
reintroduce a photo into user-visible results.

S3 Vectors stores semantic caption embeddings, not the original image and not a
visual duplicate fingerprint. Exact-file detection uses SHA-256 and near-duplicate
review uses a deterministic difference hash within same-day candidates. This keeps
similarity warnings explainable and non-destructive. Vision indexing is optional
because each invocation adds model latency and cost; without approved model access,
ordinary upload, resize, quality review and date/person-based albums still work.

Local adapters use SQLite, filesystem objects and a durable queue. They let API and
worker run in separate processes but are not intended for distributed cloud storage.
AWS adapters use DynamoDB, S3, SQS and ECS/Fargate. Optional image delivery uses
CloudFront OAC and signed URLs. Cognito authenticates users; membership records
authorise family actions.

## Data and consistency

DynamoDB uses `pk=FAMILY#<id>` and sort keys `MEMBER#`, `PHOTO#`, `ALBUM#`,
`RECREATION#`. A revision guards each update. This single-family partition design
is adequate for the initial small dataset but needs access-pattern and hot-partition
review before commercial scale. Family listing and queries currently retrieve all
matching records; pagination and date/quality indexes are follow-up work.

The completed S3 upload is pinned to a version. Original retention therefore must
not automatically expire noncurrent versions referenced by photo records. Derived
objects use deterministic per-photo keys and may be safely overwritten by a retry.
EXIF orientation is applied before variant generation; the original is preserved.
Derived JPEGs do not carry the source EXIF metadata.

## Scaling and fault handling

The worker receives one message at a time, renews SQS visibility and its database
lease, and uses ECS task scale-in protection while processing. SIGTERM stops future
polling and allows current work to drain within the platform stop window. Unexpected
termination can cause replay; conditional updates and stable output keys limit damage.
External model calls may still be repeated after a crash. Exactly-once execution is
not claimed.

The initial scaling policy adds a worker when queued work exists, with a maximum of
four. It scales to zero only after five consecutive empty periods considering visible,
in-flight and delayed messages. These are initial experiment settings, not tuned
service-level guarantees. API CPU scaling is independent. Record actual running
tasks and scaling activities during evaluation; templates alone are not evidence.

## Cache and deletion

CloudFront verifies signed URLs and uses OAC to fetch only `derived/*` from S3.
The optional distribution uses the AWS managed CachingOptimized cache policy.
The API emits short-lived URLs only after membership and READY-state checks.
Logical deletion prevents new URLs but cannot revoke an already-issued URL before
its expiry. Existing browser downloads cannot be recalled. Permanent deletion,
cache invalidation and orphan cleanup are future coordinated workflows.

