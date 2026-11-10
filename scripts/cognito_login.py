"""Interactive local login: passwords and tokens are never printed.

Run this yourself in a terminal, not through a shared transcript.
The access token is saved under the git-ignored .data directory.
"""
import getpass
import json
import urllib.request
import urllib.error
from pathlib import Path

CLIENT_ID = '5526cls2cc5k0c6gikp2encaar'
ENDPOINT = 'https://cognito-idp.ap-southeast-2.amazonaws.com/'
DEFAULT_USERNAME = 'a2-demo-student@example.edu'

def main():
    username = input(f'Existing Cognito username/email [{DEFAULT_USERNAME}]: ').strip() or DEFAULT_USERNAME
    password = getpass.getpass('Cognito password (hidden): ')
    body = json.dumps({'AuthFlow':'USER_PASSWORD_AUTH','ClientId':CLIENT_ID,
                       'AuthParameters':{'USERNAME':username,'PASSWORD':password}}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        'Content-Type':'application/x-amz-json-1.1',
        'X-Amz-Target':'AWSCognitoIdentityProviderService.InitiateAuth'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read())
        raise SystemExit('Login failed: ' + detail.get('message', 'Cognito rejected the request'))
    finally:
        password = None
        body = None
    auth = result.get('AuthenticationResult')
    if not auth:
        raise SystemExit('Additional login challenge: ' + result.get('ChallengeName','unknown') +
                         '. Complete this using your existing Cognito login flow.')
    path = Path(__file__).resolve().parents[1] / '.data' / 'cognito-access-token.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(auth['AccessToken'], encoding='utf-8')
    print('Signed in. Access token saved locally to ' + str(path))
    print('Expires in ' + str(auth.get('ExpiresIn',0)) + ' seconds. Never submit this file.')

if __name__ == '__main__':
    main()
