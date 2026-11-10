# HTTP contract

All private requests require `Authorization: Bearer <access-token>`. AWS mode accepts
Cognito access tokens only. JSON responses have `Cache-Control: no-store`. The API
does not accept `userId` or `familyId` from a model as authority.
The responsive browser interface is served at `/` on the API's origin. In local
mode only, `X-Dev-User` switches synthetic identities for membership checks; AWS
mode ignores this header and derives identity from the signed Cognito access token.

Let `B = /a3/families/{familyId}`.

| Method | Path | Input / purpose |
| --- | --- | --- |
| GET | `/healthz` | Unauthenticated liveness only |
| POST | `/a3/families` | `{"name":"My Family"}`; returns the family ID |
| POST | `B/members` | Owner only: `{"userId":"existing-cognito-sub","role":"viewer"}` or contributor |
| POST | `B/uploads` | `{"name":"photo.jpg","mime":"image/jpeg","capturedDate":"2020-10-19"}`; capture date optional |
| PUT | `B/photos/{id}/content` | Local only: raw image bytes; returns 202 |
| POST | `B/photos/{id}/complete` | AWS: after signed POST upload; queues or retries the job |
| GET | `B/photos` | Non-trashed records; `?trash=true` includes recycle-bin records |
| GET | `B/photos/{id}` | Status and metadata |
| PATCH | `B/photos/{id}` | `people`, `favorite`, `keep`, `hideMemory`, `capturedDate` |
| GET | `B/photos/{id}/download?size=1280` | Local image bytes; AWS short-lived URL. Also 320, 2560 or original |
| POST | `B/photos/{id}/trash` | Explicit user-requested logical deletion |
| POST | `B/photos/{id}/restore` | Restore; unfinished processing requires complete/retry afterwards |
| GET | `B/suggestions` | Non-destructive exact duplicates, same-day similarity candidates and quality hints |
| GET | `B/memories/on-this-day?date=2026-10-19` | Earlier years on the requested local calendar date |
| POST | `B/albums` | `{"title":"Mum","filters":{"person":"Mum","limit":30}}` or a `prompt` |
| GET | `B/albums/{id}` | Album plus currently available photos |
| POST | `B/search` | Optional semantic retrieval: `{"query":"A family picnic beside the sea"}` |
| POST | `B/recreations` | `{"originalPhotoId":"...","newPhotoId":"..."}` |
| GET | `B/recreations` | Available before/after pairs |

## Upload state and message contract

`UPLOADING -> QUEUED -> PROCESSING -> READY`. Explicit failures are `QUEUE_FAILED`
or `FAILED`. `TRASHED` removes a photo from playback and future suggestions. A busy
worker cannot turn a concurrently trashed photo back into READY.

```json
{"schemaVersion":1,"familyId":"authorised-family-id","photoId":"server-generated-photo-id"}
```

SQS `MessageGroupId` is the family ID on a Standard Queue. This enables the fair-queue
feature; it is not FIFO ordering, an upload quota or a per-family concurrency cap.
The worker resolves the original key/version from the database and uses conditional
revision writes. S3 and DynamoDB writes and SQS delivery are not a distributed transaction.

## Natural-language restrictions

The planner accepts only `person`, `since`, `until`, `orientation`, `limit` and
`minSharpness`. It cannot delete files, change membership or choose another family.
Without a configured model, explicit filters still work. Semantic scene search is
a separate optional endpoint; free-form scene descriptions are not yet composed
automatically with album rules.

## Playback integration

A browser can choose a variant based on display size and device pixel density.
The API supplies variants but does not yet implement responsive HTML, slideshow
controls, image preloading or account-management screens. Local playback deliberately
does not simulate CDN cache hits. Cross-origin browser support requires an explicit
CORS policy or a same-origin reverse proxy before frontend integration.

