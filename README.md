# Stillday - CAB432 Assignment 3

Updated: 28 October 2026

This source package contains the application, infrastructure templates, local checks and the Gradescope resource inventory. The report PDF and demonstration video are submitted separately to Canvas; no report document is included in this ZIP.

## Application

Stillday is a private family photo library. Contributors upload JPEG, PNG or WebP originals directly to private object storage. The API records each upload and queues image work without waiting for resizing. ECS workers create JPEG variants with maximum edges of 320, 1280 and 2560 pixels. The browser polls photo state and displays upload, queued, processing and ready stages.

The demo frontend shows each selected photo in Your recent photos as soon as the API creates its upload record. It uses a local preview while the original transfers. The photo-list heading displays the latest API cache result, such as API cache: MISS or API cache: HIT. This is the bounded, process-local API cache; it is not CloudFront.

Other implemented features include family roles, metadata editing, albums, orientation filtering, orientation-plus-semantic search paths, slideshow playback, memories, comparison, recycle and restore, duplicate review, and optional Bedrock/S3 Vectors integrations. Model-dependent paths require the corresponding AWS permissions and configuration; their presence in source does not mean each path was live-tested.

## Deployment

The application supports AWS deployment through the included infrastructure templates. Account-specific identifiers, live endpoints, credentials, and runtime evidence are intentionally kept out of this public source repository.

## Local quick start (PowerShell)

Run these commands from the project directory. Python 3.12 or later is intended.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:APP_MODE = "local"
$env:DATA_DIR = "$PWD\.data"
.\.venv\Scripts\python.exe -m uvicorn photo_library.api:create_app --factory --host 127.0.0.1 --port 8080
```

Open http://127.0.0.1:8080/ in a browser. In a second terminal, start the local worker:

```powershell
$env:APP_MODE = "local"
$env:DATA_DIR = "$PWD\.data"
.\.venv\Scripts\python.exe -m photo_library.worker
```

The local bearer token defaults to `local-development-only` and the local identity defaults to `local-owner`. These defaults are rejected by AWS authentication. Local SQLite data and objects persist under `.data`; they are not cloud-scaling evidence. `.env.example` documents configuration, but Python does not load it automatically.

For a local Docker run, use `docker compose up --build`. The compose services share a named data volume; `docker compose down` preserves it.

## Verification commands

Run these checks from the project directory:

```powershell
python -m unittest discover -s tests -v
python infrastructure/build_templates.py
python scripts/check_scaffold.py
node --check frontend/app.js
```

These are local checks. Their results do not establish behavior or capacity in the live AWS deployment. Browser-test files are not included in this source package.

## Source layout

```text
photo_library/     API, identity, persistence, caching, image processing, queue and worker
frontend/          Same-origin browser app, styles and upload-progress display
infrastructure/    CloudFormation generator, templates and example parameters
scripts/           CLI, AWS preflight, inventory, verification and packaging helpers
tests/             Local adapter, cache, chat, library and upload-snapshot tests
Dockerfile         ECS container image definition
compose.yaml       Local API and worker setup
submission.yml     Live inventory at the package root (outside source/)
```

Local credentials, access tokens, runtime data, account-specific evidence and generated submission bundles are excluded. The CloudFormation templates provide reusable deployment examples.

## Gradescope and Canvas

Submit the application source and `submission.yml` to Gradescope. Submit the report PDF and the 4-5 minute demonstration video to Canvas. The report and video are not included in this source package.
