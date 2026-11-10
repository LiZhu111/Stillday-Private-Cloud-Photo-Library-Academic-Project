# Assignment 3 submission readiness

This is a checklist, not a claim that the assignment is ready to submit.

| Requirement | Current evidence / next action |
| --- | --- |
| New application, not Strobe | Stillday family photo library and image-processing workers |
| Structured persistence | DynamoDB family membership and photo state; explain conditional updates |
| Unstructured persistence | Private S3 originals, sealed snapshots and derived images |
| Caching | In-process bounded list cache; live TTL, HIT and invalidation evidence saved |
| Service communication | SQS messages decouple upload completion from image processing |
| Compute | Separate ECS Fargate API and worker containers |
| Non-Lambda scaling | Queue alarms and Application Auto Scaling; single-job 0 -> 1 -> 0 verified; evaluate batch results |
| Load distribution | Workers pull jobs from the shared queue; API requests distributed by ALB |
| Additional patterns | Authentication and family roles, observability, stateless containers, retries and leases |
| Asynchronous demonstration | HTTP 202 completion followed by eventual READY and generated variants |
| Region and ownership | Check all final declared resources in ap-southeast-2; distinguish shared dependencies from owned resources |
| Tags | Owned reused resources retagged assessment 3; do not modify shared course resources |
| Independent learning | Describe the actual S3 versioning/CDN restrictions, snapshot alternative, cache design and SQS idle-metric investigation with sources |
| Architecture diagrams | Current Mermaid sources in current-architecture.md; render two readable figures, add AWS icons to implementation diagram |
| Main report | report.docx remains a scaffold; write project-specific reasoning and real results, <=30 pages excluding references/appendices |
| Cost | AWS Pricing Calculator estimate and public share link still required |
| Sustainability | Explain idle worker reduction, continuing API/ALB use, cache trade-offs and retained data/logs |
| Video | Record 4-5 minutes showing actual functionality, async flow, cache and scaling; upload and click Submit on Canvas |
| Gradescope | Populate final resource YAML from live inventory and include application source; empty template is not a submission |

## Cost measurement worksheet

Do not equate 50 concurrent users with 50 continuously active uploaders. State the
usage scenario and active hours before multiplying measured demand. Record:

- Photo-list requests per active user per minute and cache hit ratio.
- Photos uploaded per active user per hour and average original/variant bytes.
- Measured worker seconds per photo and peak queue delay for the chosen image sizes.
- API vCPU/memory, average and peak task counts, and active worker hours.
- Monthly retained originals, staging snapshots and variants, plus data transfer.
- S3 requests, DynamoDB reads/writes, SQS operations, log ingestion/storage,
  load-balancer usage, DNS and container-image storage.

Prepare normal and burst scenarios in the AWS Pricing Calculator for Sydney, and
copy each service total plus the overall monthly total into the report. Any measured
48-photo batch is a separate workload from the required 50-user cost scenario.
Do not invent prices, a calculator share URL or resource utilisation.

## Suggested video timing (4 minutes 40 seconds)

| Time | Screen evidence |
| --- | --- |
| 0:00-0:35 | Open deployed library and demonstrate authenticated access |
| 0:35-1:25 | Upload photo; show accepted/queued status, eventual READY and preview |
| 1:25-2:10 | Show actual photo-list MISS/HIT, TTL expiry and write invalidation |
| 2:10-3:35 | Show batch evidence and AWS queue/task charts; use only observed counts |
| 3:35-4:15 | Show completed results and automatic scale-in, editing out waiting time |
| 4:15-4:40 | Demonstrate one working family permission or metadata feature |

Keep the screen text readable and never display bearer tokens, passwords or signed
image URLs. The report explains decisions; the video shows the working result.
