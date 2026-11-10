"""A3 configuration; adapted from A2 common.py, without A2 resource defaults."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re


@dataclass(frozen=True)
class Settings:
    mode: str = field(default_factory=lambda: os.getenv('APP_MODE', 'local'))
    region: str = field(default_factory=lambda: os.getenv('AWS_REGION', 'ap-southeast-2'))
    student_id: str = field(default_factory=lambda: os.getenv('STUDENT_ID', 'n00000000'))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', '.data')))
    max_upload: int = 20 * 1024 * 1024
    visibility: int = 180
    max_attempts: int = 5

    def __post_init__(self):
        if self.mode not in {'local', 'aws'} or self.region != 'ap-southeast-2':
            raise ValueError('Use local/aws mode and ap-southeast-2')
        if not re.fullmatch(r'n[0-9]{8}', self.student_id):
            raise ValueError('Invalid student ID')
        if self.mode == 'aws':
            reuse_a2 = os.getenv('REUSE_ASSIGNMENT2_RESOURCES', '').lower() == 'true'
            for name in ('MEDIA_BUCKET', 'LIBRARY_TABLE', 'PROCESSING_QUEUE_URL',
                         'COGNITO_POOL_ID', 'COGNITO_CLIENT_ID'):
                if not os.getenv(name):
                    raise ValueError(f'Missing {name}')
            if reuse_a2:
                expected = {
                    'MEDIA_BUCKET': f'{self.student_id}-a2-agent-media',
                    'LIBRARY_TABLE': f'{self.student_id}-a2-classifications',
                    'PROCESSING_QUEUE_URL': f'{self.student_id}-a2-classification',
                }
                for name, resource_name in expected.items():
                    if resource_name not in os.environ[name]:
                        raise ValueError(f'{name} must identify the verified reusable Assignment 2 resource {resource_name}')
                if os.getenv('DYNAMODB_LAYOUT', 'pk-sk') != 'assignment2-classifications':
                    raise ValueError('Set DYNAMODB_LAYOUT=assignment2-classifications when reusing the Assignment 2 table')
            else:
                for name in ('MEDIA_BUCKET', 'LIBRARY_TABLE', 'PROCESSING_QUEUE_URL'):
                    if self.prefix not in os.environ[name]:
                        raise ValueError(f'{name} must identify an A3 resource')
            for name in ('VECTOR_BUCKET_NAME', 'VECTOR_INDEX_NAME'):
                if os.getenv(name) and self.prefix not in os.environ[name] and not (
                        reuse_a2 and os.environ[name] in {
                            f'{self.student_id}-a2-photo-vectors', f'{self.student_id}-photo-index'}):
                    raise ValueError(f'{name} must identify an A3 resource')
            cdn_values = [os.getenv(name) for name in ('CDN_DOMAIN','CDN_KEY_PAIR_ID','CDN_PRIVATE_KEY_SECRET_ARN')]
            if any(cdn_values) and not all(cdn_values):
                raise ValueError('Configure all three private CDN settings together')

    @property
    def prefix(self):
        return f'{self.student_id}-a3'

    @property
    def tags(self):
        return {'qut-username': os.getenv('QUT_USERNAME', f'{self.student_id}@qut.edu.au'),
                'purpose': 'assessment 3'}

