"""Generate reviewable CloudFormation JSON. This script never calls AWS."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ref(name):
    return {'Ref': name}


def sub(value):
    return {'Fn::Sub': value}


def attr(resource, name):
    return {'Fn::GetAtt': [resource, name]}


TAGS = [{'Key': 'qut-username', 'Value': ref('QutUsername')}, {'Key': 'purpose', 'Value': 'assessment 3'}]


def base(description):
    return {'AWSTemplateFormatVersion': '2010-09-09', 'Description': description,
            'Parameters': {'StudentId': {'Type': 'String', 'Default': 'n00000000', 'AllowedPattern': 'n[0-9]{8}'},
                           'QutUsername': {'Type': 'String', 'Default': 'student@example.edu'}},
            'Rules': {'SydneyOnly': {'Assertions': [{'Assert': {'Fn::Equals': [ref('AWS::Region'), 'ap-southeast-2']},
                                                     'AssertDescription': 'Deploy regional resources in ap-southeast-2'}]}},
            'Resources': {}, 'Outputs': {}}


def resource(kind, properties, retain=False):
    item = {'Type': 'AWS::' + kind, 'Properties': properties}
    if retain:
        item.update(DeletionPolicy='Retain', UpdateReplacePolicy='Retain')
    return item


def data_stack():
    t = base('Optional standalone A3 data resources; do not deploy when repurposing A2 resources')
    t['Parameters']['ClientOrigin'] = {'Type': 'String', 'Description': 'Exact HTTPS browser origin, without trailing slash', 'AllowedPattern': 'https://[^/]+(:[0-9]+)?'}
    r = t['Resources']
    r['MediaBucket'] = resource('S3::Bucket', {
        'BucketName': sub('${StudentId}-a3-media-${AWS::AccountId}'), 'Tags': TAGS,
        'VersioningConfiguration': {'Status': 'Enabled'},
        'PublicAccessBlockConfiguration': {x: True for x in ('BlockPublicAcls','BlockPublicPolicy','IgnorePublicAcls','RestrictPublicBuckets')},
        'BucketEncryption': {'ServerSideEncryptionConfiguration': [{'ServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]},
        'CorsConfiguration': {'CorsRules': [{'AllowedOrigins': [ref('ClientOrigin')], 'AllowedMethods': ['POST','GET','HEAD'], 'AllowedHeaders': ['*'], 'MaxAge': 600}]},
        'LifecycleConfiguration': {'Rules': [{'Id': 'AbortIncompleteUploads', 'Status': 'Enabled', 'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}}]}}, True)
    r['LibraryTable'] = resource('DynamoDB::Table', {'TableName': sub('${StudentId}-a3-library'),
        'BillingMode': 'PAY_PER_REQUEST', 'AttributeDefinitions': [{'AttributeName': x,'AttributeType': 'S'} for x in ('pk','sk')],
        'KeySchema': [{'AttributeName': 'pk','KeyType': 'HASH'}, {'AttributeName': 'sk','KeyType': 'RANGE'}],
        'SSESpecification': {'SSEEnabled': True}, 'Tags': TAGS}, True)
    r['ProcessingDLQ'] = resource('SQS::Queue', {'QueueName': sub('${StudentId}-a3-processing-dlq'),
        'MessageRetentionPeriod': 1209600, 'SqsManagedSseEnabled': True, 'Tags': TAGS})
    r['ProcessingQueue'] = resource('SQS::Queue', {'QueueName': sub('${StudentId}-a3-processing'),
        'VisibilityTimeout': 180, 'ReceiveMessageWaitTimeSeconds': 20, 'MessageRetentionPeriod': 345600,
        'SqsManagedSseEnabled': True, 'Tags': TAGS,
        'RedrivePolicy': {'deadLetterTargetArn': attr('ProcessingDLQ','Arn'), 'maxReceiveCount': 5}})
    r['Repository'] = resource('ECR::Repository', {'RepositoryName': sub('${StudentId}-a3-library'),
        'ImageScanningConfiguration': {'ScanOnPush': True}, 'ImageTagMutability': 'IMMUTABLE', 'Tags': TAGS}, True)
    for name in ('ApiLogs','WorkerLogs'):
        r[name] = resource('Logs::LogGroup', {'LogGroupName': sub('${StudentId}-a3-' + name.lower()), 'RetentionInDays': 7, 'Tags': TAGS})
    r['DeadLetterAlarm'] = resource('CloudWatch::Alarm', {'AlarmName': sub('${StudentId}-a3-dead-letters'),
        'Namespace': 'AWS/SQS', 'MetricName': 'ApproximateNumberOfMessagesVisible',
        'Dimensions': [{'Name':'QueueName','Value':attr('ProcessingDLQ','QueueName')}], 'Statistic':'Maximum',
        'Period':60, 'EvaluationPeriods':1, 'Threshold':0, 'ComparisonOperator':'GreaterThanThreshold',
        'TreatMissingData':'notBreaching', 'Tags':TAGS})
    outputs = {'MediaBucketName': ref('MediaBucket'), 'MediaBucketArn': attr('MediaBucket','Arn'),
               'LibraryTableName':ref('LibraryTable'), 'LibraryTableArn':attr('LibraryTable','Arn'),
               'ProcessingQueueUrl':ref('ProcessingQueue'), 'ProcessingQueueArn':attr('ProcessingQueue','Arn'),
               'ProcessingQueueName':attr('ProcessingQueue','QueueName'), 'ProcessingDLQUrl':ref('ProcessingDLQ'),
               'ProcessingDLQArn':attr('ProcessingDLQ','Arn'), 'RepositoryUri':attr('Repository','RepositoryUri'),
               'ApiLogGroup':ref('ApiLogs'), 'WorkerLogGroup':ref('WorkerLogs'), 'DeadLetterAlarm':ref('DeadLetterAlarm')}
    t['Outputs'] = {k:{'Value':v} for k,v in outputs.items()}
    return t


def compute_stack():
    t = base('A3 API and worker services, queue-driven autoscaling, existing course IAM roles')
    p = t['Parameters']
    for name, description in {
        'ImageUri':'Immutable ECR image tag or digest', 'ExecutionRoleArn':'Existing course ECS execution role ARN',
        'TaskRoleArn':'Existing course task role ARN; check docs/permissions.md',
        'CertificateArn':'Existing ACM certificate ARN for HTTPS API hostname',
        'MediaBucket':'Data stack bucket name', 'LibraryTable':'Data stack table name',
        'ProcessingQueueUrl':'Data stack queue URL', 'ProcessingQueueName':'Data stack queue name',
        'CognitoPoolId':'Reuse the permitted existing Cognito user pool',
        'CognitoClientId':'Existing public app client for user login',
    }.items():
        p[name] = {'Type':'String','Description':description,'MinLength':1}
    p['ReuseAssignment2Resources'] = {'Type':'String','Default':'true','AllowedValues':['true','false']}
    for name in ('ApiLogGroup','WorkerLogGroup'):
        p[name] = {'Type':'String','Default':'','Description':'Existing log group; leave empty to create a dedicated A3 group'}
    p['VpcId'] = {'Type':'AWS::EC2::VPC::Id'}
    p['LoadBalancerSubnets'] = {'Type':'List<AWS::EC2::Subnet::Id>','Description':'At least two public subnets in different AZs'}
    p['TaskSubnets'] = {'Type':'List<AWS::EC2::Subnet::Id>','Description':'Subnets with outbound AWS connectivity'}
    p['AssignPublicIp'] = {'Type':'String','Default':'DISABLED','AllowedValues':['ENABLED','DISABLED']}
    for name in ('VectorBucketName','VectorIndexName','CdnDomain','CdnKeyPairId','CdnPrivateKeySecretArn'):
        p[name] = {'Type':'String','Default':''}
    p['ChatModelId'] = {'Type':'String','Default':'nvidia.nemotron-super-3-120b'}
    p['VisionModelId'] = {'Type':'String','Default':'google.gemma-3-12b-it'}
    p['EmbeddingModelId'] = {'Type':'String','Default':'amazon.titan-embed-text-v2:0'}
    p['CreateA3Cluster'] = {'Type':'String','Default':'false','AllowedValues':['true','false'],
        'Description':'Set true only when the existing A2 ECS cluster cannot be reused'}
    p['ExistingClusterName'] = {'Type':'String','Default':'n00000000-a2-cluster',
        'Description':'Reusable CAB432 ECS cluster; ignored when CreateA3Cluster is true'}
    t['Conditions'] = {'UseDedicatedA3Cluster': {'Fn::Equals':[ref('CreateA3Cluster'),'true']}}
    t['Conditions']['ReuseA2Data'] = {'Fn::Equals':[ref('ReuseAssignment2Resources'),'true']}
    r = t['Resources']
    log_refs = {}
    for name in ('Api','Worker'):
        condition = 'Create' + name + 'Logs'
        t['Conditions'][condition] = {'Fn::Equals':[ref(name+'LogGroup'),'']}
        r[name+'Logs'] = resource('Logs::LogGroup', {
            'LogGroupName':sub('/${StudentId}/a3/' + name.lower()),'RetentionInDays':7,'Tags':TAGS}, True)
        r[name+'Logs']['Condition'] = condition
        log_refs[name] = {'Fn::If':[condition,ref(name+'Logs'),ref(name+'LogGroup')]}
    r['Cluster'] = resource('ECS::Cluster', {'ClusterName':sub('${StudentId}-a3-cluster'),'Tags':TAGS})
    r['Cluster']['Condition'] = 'UseDedicatedA3Cluster'
    cluster_ref = {'Fn::If':['UseDedicatedA3Cluster',ref('Cluster'),ref('ExistingClusterName')]}
    p['LoadBalancerSecurityGroup'] = {'Type':'AWS::EC2::SecurityGroup::Id','Description':'Existing course-approved ALB security group'}
    p['TaskSecurityGroup'] = {'Type':'AWS::EC2::SecurityGroup::Id','Description':'Existing course-approved task security group'}
    r['LoadBalancer'] = resource('ElasticLoadBalancingV2::LoadBalancer', {'Name':sub('${StudentId}-a3-alb'),
        'Scheme':'internet-facing','Type':'application','Subnets':ref('LoadBalancerSubnets'),
        'SecurityGroups':[ref('LoadBalancerSecurityGroup')],'Tags':TAGS})
    r['TargetGroup'] = resource('ElasticLoadBalancingV2::TargetGroup', {'Name':sub('${StudentId}-a3-api'),
        'Port':8080,'Protocol':'HTTP','TargetType':'ip','VpcId':ref('VpcId'),'HealthCheckPath':'/healthz','Tags':TAGS})
    r['Listener'] = resource('ElasticLoadBalancingV2::Listener', {'LoadBalancerArn':ref('LoadBalancer'),
        'Port':443,'Protocol':'HTTPS','Certificates':[{'CertificateArn':ref('CertificateArn')}],
        'DefaultActions':[{'Type':'forward','TargetGroupArn':ref('TargetGroup')}]})
    env = [{'Name':name,'Value':value} for name,value in {
        'APP_MODE':'aws','AWS_REGION':ref('AWS::Region'),'STUDENT_ID':ref('StudentId'),
        'MEDIA_BUCKET':ref('MediaBucket'),'LIBRARY_TABLE':ref('LibraryTable'),
        'PROCESSING_QUEUE_URL':ref('ProcessingQueueUrl'),'COGNITO_POOL_ID':ref('CognitoPoolId'),
        'COGNITO_CLIENT_ID':ref('CognitoClientId'),'CHAT_MODEL_ID':ref('ChatModelId'),
        'REUSE_ASSIGNMENT2_RESOURCES':ref('ReuseAssignment2Resources'),
        'DYNAMODB_LAYOUT':{'Fn::If':['ReuseA2Data','assignment2-classifications','pk-sk']},
        'VISION_MODEL_ID':ref('VisionModelId'),'EMBEDDING_MODEL_ID':ref('EmbeddingModelId'),
        'VECTOR_BUCKET_NAME':ref('VectorBucketName'),'VECTOR_INDEX_NAME':ref('VectorIndexName'),
        'CDN_DOMAIN':ref('CdnDomain'),'CDN_KEY_PAIR_ID':ref('CdnKeyPairId'),
        'CDN_PRIVATE_KEY_SECRET_ARN':ref('CdnPrivateKeySecretArn')}.items()]
    for name in ('Api','Worker'):
        worker = name == 'Worker'
        container = {'Name':name.lower(),'Image':ref('ImageUri'),'Essential':True,'Environment':env,
            'StopTimeout':120,'LogConfiguration':{'LogDriver':'awslogs','Options':{
                'awslogs-group':log_refs[name],'awslogs-region':ref('AWS::Region'),'awslogs-stream-prefix':name.lower()}}}
        if worker:
            container['Command'] = ['python','-m','photo_library.worker']
        else:
            container['PortMappings'] = [{'ContainerPort':8080,'Protocol':'tcp'}]
        r[name+'Task'] = resource('ECS::TaskDefinition', {'Family':sub('${StudentId}-a3-'+name.lower()),
            'Cpu':'1024' if worker else '256','Memory':'2048' if worker else '512',
            'NetworkMode':'awsvpc','RequiresCompatibilities':['FARGATE'],
            'ExecutionRoleArn':ref('ExecutionRoleArn'),'TaskRoleArn':ref('TaskRoleArn'),
            'ContainerDefinitions':[container],'Tags':TAGS})
        service = {'ServiceName':sub('${StudentId}-a3-'+name.lower()),'Cluster':cluster_ref,
            'TaskDefinition':ref(name+'Task'),'DesiredCount':0 if worker else 1,'LaunchType':'FARGATE',
            'DeploymentConfiguration':{'DeploymentCircuitBreaker':{'Enable':True,'Rollback':True}},
            'NetworkConfiguration':{'AwsvpcConfiguration':{'AssignPublicIp':ref('AssignPublicIp'),
                'Subnets':ref('TaskSubnets'),'SecurityGroups':[ref('TaskSecurityGroup')]}},
            'PropagateTags':'SERVICE','EnableECSManagedTags':True,'Tags':TAGS}
        if not worker:
            service['LoadBalancers'] = [{'TargetGroupArn':ref('TargetGroup'),'ContainerName':'api','ContainerPort':8080}]
            service['HealthCheckGracePeriodSeconds'] = 30
        r[name+'Service'] = resource('ECS::Service', service)
        if not worker:
            r[name+'Service']['DependsOn'] = 'Listener'
        r[name+'ScalingTarget'] = resource('ApplicationAutoScaling::ScalableTarget', {
            'MinCapacity':0 if worker else 1,'MaxCapacity':4 if worker else 2,
            'ResourceId':{'Fn::Join':['/', ['service',cluster_ref,attr(name+'Service','Name')]]},
            'ScalableDimension':'ecs:service:DesiredCount','ServiceNamespace':'ecs','Tags':TAGS})
    r['ApiScalingPolicy'] = resource('ApplicationAutoScaling::ScalingPolicy', {
        'PolicyName':sub('${StudentId}-a3-api-cpu'),'PolicyType':'TargetTrackingScaling',
        'ScalingTargetId':ref('ApiScalingTarget'),'TargetTrackingScalingPolicyConfiguration':{
            'TargetValue':60,'ScaleInCooldown':180,'ScaleOutCooldown':60,
            'PredefinedMetricSpecification':{'PredefinedMetricType':'ECSServiceAverageCPUUtilization'}}})
    for name, adjustment in [('ScaleOut',1),('ScaleIn',0)]:
        r[name] = resource('ApplicationAutoScaling::ScalingPolicy', {
            'PolicyName':sub('${StudentId}-a3-'+name.lower()),'PolicyType':'StepScaling','ScalingTargetId':ref('WorkerScalingTarget'),
            'StepScalingPolicyConfiguration':{'AdjustmentType':'ChangeInCapacity' if adjustment else 'ExactCapacity',
                'Cooldown':60 if adjustment else 300,'MetricAggregationType':'Maximum',
                'StepAdjustments':[{'MetricIntervalLowerBound':0,'ScalingAdjustment':1}] if adjustment else
                                  [{'MetricIntervalUpperBound':0,'ScalingAdjustment':0}]}})
    r['QueueBusy'] = resource('CloudWatch::Alarm', {'AlarmName':sub('${StudentId}-a3-queue-busy'),
        'Namespace':'AWS/SQS','MetricName':'ApproximateNumberOfMessagesVisible','Statistic':'Maximum',
        'Dimensions':[{'Name':'QueueName','Value':ref('ProcessingQueueName')}], 'Period':60,'EvaluationPeriods':1,
        'Threshold':1,'ComparisonOperator':'GreaterThanOrEqualToThreshold','TreatMissingData':'notBreaching',
        'AlarmActions':[ref('ScaleOut')],'Tags':TAGS})
    metrics = []
    for metric_id, metric in [('visible','ApproximateNumberOfMessagesVisible'),('inflight','ApproximateNumberOfMessagesNotVisible'),('delayed','ApproximateNumberOfMessagesDelayed')]:
        metrics.append({'Id':metric_id,'ReturnData':False,'MetricStat':{'Metric':{'Namespace':'AWS/SQS','MetricName':metric,
            'Dimensions':[{'Name':'QueueName','Value':ref('ProcessingQueueName')}]},'Period':60,'Stat':'Maximum'}})
    metrics.append({'Id':'total','Expression':'visible+inflight+delayed','ReturnData':True})
    r['QueueIdle'] = resource('CloudWatch::Alarm', {'AlarmName':sub('${StudentId}-a3-queue-idle'),
        'Metrics':metrics,'EvaluationPeriods':5,'DatapointsToAlarm':5,'Threshold':0,
        'ComparisonOperator':'LessThanOrEqualToThreshold','TreatMissingData':'notBreaching',
        'AlarmActions':[ref('ScaleIn')],'Tags':TAGS})
    dashboard = {'widgets':[{'type':'metric','x':0,'y':0,'width':24,'height':8,'properties':{
        'title':'Processing backlog and in-flight work','region':'${AWS::Region}','period':60,'stat':'Maximum',
        'metrics':[['AWS/SQS',name,'QueueName','${ProcessingQueueName}'] for name in
                   ('ApproximateNumberOfMessagesVisible','ApproximateNumberOfMessagesNotVisible','ApproximateAgeOfOldestMessage')]}}]}
    r['Dashboard'] = resource('CloudWatch::Dashboard', {'DashboardName':sub('${StudentId}-a3-operations'),
        'DashboardBody':sub(json.dumps(dashboard))})
    t['Outputs'] = {k:{'Value':v} for k,v in {'ClusterName':cluster_ref, 'ApiLogGroup':log_refs['Api'], 'WorkerLogGroup':log_refs['Worker'], 'ApiServiceArn':ref('ApiService'),
        'WorkerServiceArn':ref('WorkerService'),'LoadBalancerArn':ref('LoadBalancer'),
        'LoadBalancerDns':attr('LoadBalancer','DNSName'),'TargetGroupArn':ref('TargetGroup'),
        'DashboardName':ref('Dashboard'),'ScaleOutAlarm':ref('QueueBusy'),'ScaleInAlarm':ref('QueueIdle')}.items()}
    return t


def delivery_stack():
    t = base('A3 optional private CloudFront image delivery; confirm course global-service rules')
    t['Parameters'].update({'MediaBucket':{'Type':'String'}, 'PublicKeyPem':{'Type':'String','Description':'RSA public key only; store the private key in Secrets Manager'}})
    r = t['Resources']
    r['PublicKey'] = resource('CloudFront::PublicKey', {'PublicKeyConfig':{'CallerReference':sub('${AWS::StackName}-key'),
        'Name':sub('${StudentId}-a3-image-key'),'EncodedKey':ref('PublicKeyPem')}})
    r['KeyGroup'] = resource('CloudFront::KeyGroup', {'KeyGroupConfig':{'Name':sub('${StudentId}-a3-images'),'Items':[ref('PublicKey')]}})
    r['OriginAccess'] = resource('CloudFront::OriginAccessControl', {'OriginAccessControlConfig':{
        'Name':sub('${StudentId}-a3-s3'),'OriginAccessControlOriginType':'s3','SigningBehavior':'always','SigningProtocol':'sigv4'}})
    r['Distribution'] = resource('CloudFront::Distribution', {'Tags':TAGS,'DistributionConfig':{
        'Enabled':True,'Comment':sub('${StudentId} assessment 3 private image variants'),'PriceClass':'PriceClass_100',
        'Origins':[{'Id':'media','DomainName':sub('${MediaBucket}.s3.${AWS::Region}.amazonaws.com'),
                    'OriginAccessControlId':ref('OriginAccess'),'S3OriginConfig':{'OriginAccessIdentity':''}}],
        'DefaultCacheBehavior':{'TargetOriginId':'media','ViewerProtocolPolicy':'redirect-to-https',
            'AllowedMethods':['GET','HEAD'],'CachedMethods':['GET','HEAD'],'Compress':True,
            'TrustedKeyGroups':[ref('KeyGroup')],'CachePolicyId':'658327ea-f89d-4fab-a63d-7e88639e58f6'},
        'ViewerCertificate':{'CloudFrontDefaultCertificate':True}}})
    r['BucketPolicy'] = resource('S3::BucketPolicy', {'Bucket':ref('MediaBucket'),'PolicyDocument':{
        'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'cloudfront.amazonaws.com'},
        'Action':'s3:GetObject','Resource':sub('arn:${AWS::Partition}:s3:::${MediaBucket}/derived/*'),
        'Condition':{'StringEquals':{'AWS:SourceArn':sub('arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}')}}}]}})
    t['Outputs'] = {k:{'Value':v} for k,v in {'CdnDomain':attr('Distribution','DomainName'),
        'DistributionId':ref('Distribution'),'CdnKeyPairId':ref('PublicKey'),'KeyGroupId':ref('KeyGroup')}.items()}
    return t


def intelligence_stack():
    t = base('A3 optional S3 Vectors resources adapted from A2; no A2 vector reuse')
    t['Resources']['VectorBucket'] = resource('S3Vectors::VectorBucket', {
        'VectorBucketName':sub('${StudentId}-a3-photo-vectors'), 'Tags':TAGS}, True)
    t['Resources']['VectorIndex'] = resource('S3Vectors::Index', {
        'VectorBucketArn':ref('VectorBucket'), 'IndexName':sub('${StudentId}-a3-photo-index'),
        'DataType':'float32','Dimension':1024,'DistanceMetric':'cosine','Tags':TAGS}, True)
    t['Outputs'] = {'VectorBucketArn':{'Value':ref('VectorBucket')},
                    'VectorBucketName':{'Value':sub('${StudentId}-a3-photo-vectors')},
                    'VectorIndexArn':{'Value':ref('VectorIndex')},
                    'VectorIndexName':{'Value':sub('${StudentId}-a3-photo-index')}}
    return t


def main():
    for name, template in [('data',data_stack()),('compute',compute_stack()),('delivery',delivery_stack()),('intelligence',intelligence_stack())]:
        (HERE / f'{name}.template.json').write_text(json.dumps(template, indent=2)+'\n', encoding='utf-8')
        print(f'Generated {name}.template.json ({len(template["Resources"])} resources)')


if __name__ == '__main__':
    main()

