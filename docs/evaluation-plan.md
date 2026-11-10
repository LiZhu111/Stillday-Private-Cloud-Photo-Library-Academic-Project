# Evaluation plan — collect real evidence after deployment

| Experiment | Method | Evidence |
| --- | --- | --- |
| Async processing | Upload a small labelled dataset; poll statuses | Upload return time, QUEUED/PROCESSING/READY timeline and output images |
| Worker scale-out | Begin at zero, submit enough varied images to build backlog | Visible/in-flight queue metrics, desired/running task counts, scaling activity timestamps |
| Safe scale-in | Stop new submissions while work is active | Task protection, completed outputs and eventual zero workers |
| Cache | Repeat authenticated photo-list requests, wait past TTL, then write metadata and read again | X-Cache MISS/HIT, expiry and invalidation, no-store headers; evidence/cloud-cache-verification.json |
| Private delivery | Test authorised, expired and absent signatures and another family | Expected denial and successful authorised playback |
| Fair queue | Compare small tenants during one noisy tenant's sustained workload, fixed worker count | Per-family enqueue-to-start delay; separate fairness from scaling effects |
| Duplicate detection | Manually label exact copies, same-day similar shots and distinct images | Precision/recall and representative false positives/negatives |
| Quality review | Include intentional blur, low light and meaningful imperfect photos | Warning relevance, user overrides and no automatic deletion |
| Model boundary | Valid prompts, unsupported rules, invalid output and service errors | Validated filters, failure behaviour and manual-filter fallback |
| Recovery | Invalid image, worker termination, duplicate delivery and queue failure | Retry/DLQ records, no resurrection after trash, recoverable completed jobs |

Capture actual workload settings, version/image tag, timestamps and units. The local
unit tests are development evidence only. Do not fabricate charts or deployment claims.

For pricing, distinguish 50 concurrent viewers from concurrent uploaders. Measure
requests per playback minute, variant sizes, cache hit ratio, processing seconds per
image, active hours, original retention, log volume and model calls/tokens. Build the
required AWS Pricing Calculator estimate and retain its public share link.

Suggested 4-5 minute video: upload/status/ready result; library review and restore;
album and memory result; actual worker scaling; actual authenticated photo-list cache behaviour.
Only include features that are deployed and working at recording time.

