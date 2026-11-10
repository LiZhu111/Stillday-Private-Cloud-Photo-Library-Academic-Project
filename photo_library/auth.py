"""Cognito access-token verification adapted from A2 agent.authenticate."""
from functools import lru_cache
import hmac
import os
import re


@lru_cache
def jwks(issuer):
    import jwt
    return jwt.PyJWKClient(issuer + '/.well-known/jwks.json')


def authenticate(header, settings, local_user=None):
    if not header.startswith('Bearer '):
        raise PermissionError('A bearer access token is required')
    token = header[7:]
    if settings.mode == 'local':
        if not hmac.compare_digest(token, os.getenv('DEV_TOKEN', 'local-development-only')):
            raise PermissionError('Invalid development token')
        user = local_user if isinstance(local_user, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', local_user) else None
        return user or os.getenv('DEV_USER', 'local-owner')
    import jwt
    issuer = f'https://cognito-idp.{settings.region}.amazonaws.com/{os.environ["COGNITO_POOL_ID"]}'
    try:
        key = jwks(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=['RS256'], issuer=issuer,
                            options={'verify_aud': False, 'require':
                                     ['exp', 'sub', 'iss', 'token_use', 'client_id']})
        if claims['token_use'] != 'access' or claims['client_id'] != os.environ['COGNITO_CLIENT_ID']:
            raise ValueError('Wrong token type or client')
        return claims['sub']
    except Exception as error:
        raise PermissionError('Invalid or expired access token') from error

