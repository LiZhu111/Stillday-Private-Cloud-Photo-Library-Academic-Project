# Infrastructure and live deployment

## Current deployment

Stillday runs in the CAB432 shared AWS account in ap-southeast-2. The CloudFormation stack `n00000000-a3-runtime` manages the API and worker ECS services. As checked on 28 October 2026, both used task-definition revision 43 and image tag `a3-upload-progress-20261028-103901`.

The API runs on ECS Fargate behind the Application Load Balancer and scales between one and two tasks. The independent SQS pull-worker service scales between zero and four tasks. The deployed services reuse approved course infrastructure and selected Assignment 2 resources; `submission.yml` at the package root is the live resource inventory.

## Included templates

- `compute.template.json` defines the API, worker, load balancer, service autoscaling and operational resources managed by the live runtime stack.
- `data.template.json` describes an optional standalone A3 data stack. It is not deployed for this resource-reuse deployment.
- `delivery.template.json` describes optional CloudFront delivery. CloudFront is not deployed; the account denied the required bucket-policy operation.
- `intelligence.template.json` describes optional S3 Vectors resources. The application source supports optional semantic-search integrations; configured vector resources are listed in the live inventory.

`build_templates.py` regenerates the JSON templates locally and does not call AWS. Example parameters are included for review. Local parameter files and credentials are excluded from this package. The templates are not a substitute for `submission.yml` and should not be deployed blindly over reused course resources.

## Resource reuse and cost

Existing course networking, IAM and DNS are dependencies. Reused A2 application resources are tagged for Assignment 3 where permitted; shared course-managed resources are not retagged. Names may retain the `-a2` suffix.

The API keeps one baseline task and the load balancer incurs idle cost. Workers can scale to zero when the queue is idle, while queued jobs remain in SQS. Review task bounds, image tags, account permissions and resource tags before making deployment changes. Removing the stack does not automatically clean up retained data or reused resources.
