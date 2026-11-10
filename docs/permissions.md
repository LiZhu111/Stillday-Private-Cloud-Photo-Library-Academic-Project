# Permission and configuration prerequisites

Use existing CAB432 roles. This scaffold does not modify shared IAM policies.
Check the exact resource-scoped permissions with the teaching account policy before
deployment. These are required capability groups, not a request for wildcard grants.

| Principal | Required capabilities |
| --- | --- |
| API task role | A3 DynamoDB GetItem/PutItem/Query; S3 PutObject for signed uploads and GetObject/GetObjectVersion for authorised reads/head; SQS SendMessage |
| Worker task role | A3 SQS ReceiveMessage/DeleteMessage/ChangeMessageVisibility; DynamoDB GetItem/PutItem; S3 GetObject/GetObjectVersion and PutObject on derived images; ECS UpdateTaskProtection for its tasks |
| Optional AI task capabilities | Bedrock InvokeModel on approved model/inference-profile resources; S3 Vectors PutVectors/QueryVectors on the A3 index |
| Optional private CDN API capability | Secrets Manager GetSecretValue for the A3 signing-key secret; KMS decrypt only if the selected secret key requires it |
| ECS execution role | ECR image pull and CloudWatch log delivery |
| Deployment principal | CloudFormation and create/update/tag actions for the resources in each chosen stack; iam:PassRole for existing ECS roles |
| Application Auto Scaling | Appropriate existing service-linked role, or permission to create it if the account has not already done so |

For initial compatibility, the compute template accepts one shared course task role
for API and worker. In an account permitting custom roles, split these privileges.
Do not claim least-privilege role separation is implemented by this template.

Reused Cognito must issue access tokens for the configured public app client. The
application checks token issuer, expiry, token_use and client_id. Registration and
account recovery stay with the existing Cognito flow; family membership is application
data, not inferred from a client-provided family ID. Owner-managed additions currently
use an existing Cognito subject; invitation email and subject-directory lookup are not
implemented.

