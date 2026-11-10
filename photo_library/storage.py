"""Private originals and deterministic derived images; no public bucket access."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import os
import uuid
from .aws import client


class LocalStorage:
    def __init__(self, directory):
        self.root = (directory / 'objects').resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        result = (self.root / key).resolve()
        if self.root not in result.parents:
            raise ValueError('Invalid object key')
        return result

    def put(self, key, content, content_type):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_bytes(content)
        temporary.replace(target)

    def read(self, key, version=None, limit=20 * 1024 * 1024):
        with self.path(key).open('rb') as file:
            result = file.read(limit + 1)
        if len(result) > limit:
            raise ValueError('Image exceeds the upload limit')
        return result

    def head(self, key):
        return {'ContentLength': self.path(key).stat().st_size}


@lru_cache
def cdn_signer():
    from botocore.signers import CloudFrontSigner
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    pem = client('secretsmanager').get_secret_value(SecretId=os.environ['CDN_PRIVATE_KEY_SECRET_ARN'])['SecretString']
    private = serialization.load_pem_private_key(pem.encode(), password=None)
    return CloudFrontSigner(os.environ['CDN_KEY_PAIR_ID'],
                            lambda message: private.sign(message, padding.PKCS1v15(), hashes.SHA1()))


class AwsStorage:
    def __init__(self):
        self.bucket = os.environ['MEDIA_BUCKET']

    def put(self, key, content, content_type):
        client('s3').put_object(Bucket=self.bucket, Key=key, Body=content,
                                ContentType=content_type, CacheControl='max-age=86400')

    def head(self, key):
        return client('s3').head_object(Bucket=self.bucket, Key=key)

    def read(self, key, version=None, limit=20 * 1024 * 1024):
        args = {'Bucket': self.bucket, 'Key': key}
        if version:
            args['VersionId'] = version
        response = client('s3').get_object(**args)
        with response['Body'] as stream:
            result = stream.read(limit + 1)
        if len(result) > limit:
            raise ValueError('Image exceeds the upload limit')
        return result

    def freeze(self, key, head):
        """Pin a version, or make a private snapshot when versioning is unavailable."""
        version = head.get('VersionId')
        if version and version != 'null':
            return key, version
        etag = head.get('ETag')
        if not etag:
            raise ValueError('Missing upload ETag')
        # This key is never the target of a browser upload form. A fresh key also
        # prevents concurrent completion attempts from overwriting each other.
        frozen = key + '.sealed-' + uuid.uuid4().hex
        result = client('s3').copy_object(
            Bucket=self.bucket, Key=frozen,
            CopySource={'Bucket': self.bucket, 'Key': key},
            CopySourceIfMatch=etag, MetadataDirective='COPY')
        return frozen, result.get('VersionId')

    def upload(self, key, mime, limit):
        return client('s3').generate_presigned_post(Bucket=self.bucket, Key=key,
            Fields={'Content-Type': mime}, Conditions=[{'Content-Type': mime}, ['content-length-range', 1, limit]], ExpiresIn=600)

    def download(self, key, version=None):
        if os.getenv('CDN_DOMAIN') and key.startswith('derived/'):
            return cdn_signer().generate_presigned_url('https://' + os.environ['CDN_DOMAIN'] + '/' + key,
                date_less_than=datetime.now(timezone.utc) + timedelta(minutes=5))
        args = {'Bucket': self.bucket, 'Key': key}
        if version:
            args['VersionId'] = version
        return client('s3').generate_presigned_url('get_object', Params=args, ExpiresIn=300)

