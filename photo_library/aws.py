"""Reuse A2's role-based AWS clients and bounded SDK timeouts."""
from functools import lru_cache
import os
import boto3
from botocore.config import Config


@lru_cache
def search_model_client():
    return boto3.client('bedrock-runtime', region_name=os.getenv('AWS_REGION', 'ap-southeast-2'),
                        config=Config(connect_timeout=5, read_timeout=180,
                                      retries={'total_max_attempts': 1}))


@lru_cache
def client(service):
    return boto3.client(service, region_name=os.getenv('AWS_REGION', 'ap-southeast-2'),
                        config=Config(connect_timeout=5, read_timeout=60,
                                      retries={'max_attempts': 2}))

