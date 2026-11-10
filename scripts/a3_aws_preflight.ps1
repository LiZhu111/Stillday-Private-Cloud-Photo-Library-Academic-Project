[CmdletBinding()]
param(
    [string]$Profile = 'a2',
    [string]$Region = 'ap-southeast-2',
    [string]$StudentId = 'n00000000'
)
$ErrorActionPreference = 'Continue'
$resources = @{
    Bucket = "$StudentId-a2-agent-media"
    Table = "$StudentId-a2-classifications"
    Queue = "$StudentId-a2-classification"
    DLQ = "$StudentId-a2-classification-dlq"
    Cluster = "$StudentId-a2-cluster"
    BackendRepository = "$StudentId-a2-backend"
    WebRepository = "$StudentId-a2-web"
    VectorBucket = "$StudentId-a2-photo-vectors"
    VectorIndex = "$StudentId-photo-index"
}
function Invoke-AwsReadOnly {
    param([string]$Label, [string[]]$Arguments)
    Write-Host "`n===== $Label =====" -ForegroundColor Cyan
    & aws @Arguments --profile $Profile --region $Region --output json
    if ($LASTEXITCODE -ne 0) { Write-Warning "Read-only query failed: $Label (exit $LASTEXITCODE)" }
}
$identity = & aws sts get-caller-identity --profile $Profile --output json 2>&1
if ($LASTEXITCODE -ne 0) { throw "AWS CLI profile '$Profile' is unavailable. No changes were made. $identity" }
Write-Host '===== Caller identity (no credentials shown) =====' -ForegroundColor Green
$identity | ConvertFrom-Json | Select-Object Account,Arn | ConvertTo-Json -Depth 3
Invoke-AwsReadOnly 'Resource Explorer: student resources across assignments' @('resource-explorer-2','search','--query-string',"$StudentId",'--query',"Resources[?contains(Arn, '$StudentId')].{Arn:Arn,Region:Region,Type:ResourceType,Name:Name}")
Invoke-AwsReadOnly 'S3 bucket tags' @('s3api','get-bucket-tagging','--bucket',$resources.Bucket)
Invoke-AwsReadOnly 'S3 versioning' @('s3api','get-bucket-versioning','--bucket',$resources.Bucket)
Invoke-AwsReadOnly 'S3 encryption' @('s3api','get-bucket-encryption','--bucket',$resources.Bucket)
Invoke-AwsReadOnly 'S3 CORS' @('s3api','get-bucket-cors','--bucket',$resources.Bucket)
Invoke-AwsReadOnly 'S3 lifecycle' @('s3api','get-bucket-lifecycle-configuration','--bucket',$resources.Bucket)
Invoke-AwsReadOnly 'S3 notification configuration (check for old classifier triggers)' @('s3api','get-bucket-notification-configuration','--bucket',$resources.Bucket)
Invoke-AwsReadOnly 'DynamoDB table schema and billing mode' @('dynamodb','describe-table','--table-name',$resources.Table,'--query','Table.{TableName:TableName,TableStatus:TableStatus,KeySchema:KeySchema,AttributeDefinitions:AttributeDefinitions,GlobalSecondaryIndexes:GlobalSecondaryIndexes[].{IndexName:IndexName,KeySchema:KeySchema,Projection:Projection},BillingModeSummary:BillingModeSummary,TableArn:TableArn}')
Invoke-AwsReadOnly 'DynamoDB table tags' @('dynamodb','list-tags-of-resource','--resource-arn',"arn:aws:dynamodb:$Region`:$((($identity | ConvertFrom-Json).Account))`:table/$($resources.Table)")
Invoke-AwsReadOnly 'SQS primary queue attributes' @('sqs','get-queue-attributes','--queue-url',"https://sqs.$Region.amazonaws.com/$((($identity | ConvertFrom-Json).Account))/$($resources.Queue)",'--attribute-names','All')
Invoke-AwsReadOnly 'SQS primary queue tags' @('sqs','list-queue-tags','--queue-url',"https://sqs.$Region.amazonaws.com/$((($identity | ConvertFrom-Json).Account))/$($resources.Queue)")
Invoke-AwsReadOnly 'SQS DLQ attributes' @('sqs','get-queue-attributes','--queue-url',"https://sqs.$Region.amazonaws.com/$((($identity | ConvertFrom-Json).Account))/$($resources.DLQ)",'--attribute-names','All')
Invoke-AwsReadOnly 'ECS cluster services' @('ecs','list-services','--cluster',$resources.Cluster)
Invoke-AwsReadOnly 'ECR backend repository settings' @('ecr','describe-repositories','--repository-names',$resources.BackendRepository,'--query','repositories[].{RepositoryName:repositoryName,RepositoryUri:repositoryUri,ImageTagMutability:imageTagMutability,ScanOnPush:imageScanningConfiguration.scanOnPush}')
Invoke-AwsReadOnly 'ECR web repository settings' @('ecr','describe-repositories','--repository-names',$resources.WebRepository,'--query','repositories[].{RepositoryName:repositoryName,RepositoryUri:repositoryUri,ImageTagMutability:imageTagMutability,ScanOnPush:imageScanningConfiguration.scanOnPush}')
Invoke-AwsReadOnly 'Lambda SQS event source mappings (find consumers before reuse)' @('lambda','list-event-source-mappings','--event-source-arn',"arn:aws:sqs:$Region`:$((($identity | ConvertFrom-Json).Account))`:$($resources.Queue)",'--query','EventSourceMappings[].{UUID:UUID,State:State,FunctionArn:FunctionArn,EventSourceArn:EventSourceArn}')
Invoke-AwsReadOnly 'Existing A2 ALB listeners' @('elbv2','describe-load-balancers','--names',"$StudentId-a2-alb",'--query','LoadBalancers[].{Arn:LoadBalancerArn,DNSName:DNSName,VpcId:VpcId,Subnets:AvailabilityZones[].SubnetId}')
Invoke-AwsReadOnly 'A2 log groups' @('logs','describe-log-groups','--log-group-name-prefix',"/$StudentId-a2-",'--query','logGroups[].{Name:logGroupName,RetentionInDays:retentionInDays}')
Invoke-AwsReadOnly 'Cognito user pools (IDs and status only)' @('cognito-idp','list-user-pools','--max-results','60','--query',"UserPools[?contains(Name, '$StudentId')].{Name:Name,Id:Id,Status:Status}")
Write-Host "`nRead-only preflight finished. This script made no AWS changes and never reads secret values." -ForegroundColor Green
