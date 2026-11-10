"""Package the Gradescope source and infrastructure submission safely."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / 'deliverables' / 'assignment3-submission'
SOURCE_DIR = OUTPUT_DIR / 'source'
ARCHIVE_PATH = ROOT / 'deliverables' / 'assignment3-submission.zip'
ALLOWED_SUFFIXES = {'.py', '.js', '.css', '.html', '.json', '.ps1', '.md', '.txt'}


def is_safe_source(path):
    if not path.is_file() or '__pycache__' in path.parts:
        return False
    if path.suffix not in ALLOWED_SUFFIXES:
        return False
    if '.local.' in path.name or path.name.startswith('outputs'):
        return False
    if path.name in {'pricing_browser.py', 'parameters.local.json'}:
        return False
    return True


def check_text(path, raw):
    text = raw.decode('utf-8-sig', errors='replace')
    patterns = [
        r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
        r'eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}',
    ]
    for pattern in patterns:
        if re.search(pattern, text):
            raise ValueError(f'Credential-like content found in {path}')


def main():
    inventory = ROOT / 'submission.yml'
    if not inventory.is_file():
        raise FileNotFoundError('submission.yml is required')

    source_paths = []
    for dirname in ['photo_library', 'frontend', 'tests', 'scripts', 'infrastructure']:
        source_paths.extend(path for path in (ROOT / dirname).rglob('*') if is_safe_source(path))
    for name in ['README.md', 'requirements.txt', 'requirements-dev.txt', 'Dockerfile', 'compose.yaml', '.dockerignore', '.env.example']:
        path = ROOT / name
        if path.is_file():
            source_paths.append(path)
    source_paths = sorted(set(source_paths))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for path in source_paths:
        raw = path.read_bytes()
        check_text(path, raw)
        relative = path.relative_to(ROOT)
        target = SOURCE_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest.append({'file': f'source/{relative.as_posix()}', 'sha256': hashlib.sha256(raw).hexdigest()})

    inventory_raw = inventory.read_bytes()
    check_text(inventory, inventory_raw)
    (OUTPUT_DIR / 'submission.yml').write_bytes(inventory_raw)
    readme = (
        '# Gradescope source package\n\n'
        'This folder contains the application source, tests, deployment templates, '
        'and the resource inventory file `submission.yml`. Submit the YAML file and '
        'source code to Gradescope. Submit the final report PDF and demonstration '
        'video to Canvas.\n\n'
        'The included resource inventory is based on the existing AWS snapshot. '
        'Refresh it after authenticating the AWS SSO profile if resources changed '
        'after that snapshot. Local credentials, runtime data, and private evidence '
        'are excluded.\n'
    )
    (OUTPUT_DIR / 'README-SUBMISSION.md').write_text(readme, encoding='utf-8')
    manifest.append({'file': 'submission.yml', 'sha256': hashlib.sha256(inventory_raw).hexdigest()})

    with zipfile.ZipFile(ARCHIVE_PATH, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(OUTPUT_DIR / 'submission.yml', 'submission.yml')
        archive.write(OUTPUT_DIR / 'README-SUBMISSION.md', 'README-SUBMISSION.md')
        for path in sorted(SOURCE_DIR.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(OUTPUT_DIR).as_posix())

    with zipfile.ZipFile(ARCHIVE_PATH) as archive:
        names = archive.namelist()
        if archive.testzip() is not None:
            raise ValueError('The submission archive failed its integrity check')
        if 'submission.yml' not in names or not any(name.startswith('source/infrastructure/') for name in names):
            raise ValueError('The archive is missing the resource inventory or infrastructure source')
        if any('.local.' in name or '/.data/' in f'/{name}' or '__pycache__' in name for name in names):
            raise ValueError('Local configuration or runtime data was included')

    manifest_path = ROOT / 'deliverables' / 'assignment3-source-manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({
        'sourceFiles': len(source_paths),
        'resourceEntries': len(re.findall(r'(?m)^\s+- type:', inventory_raw.decode('utf-8'))),
        'archiveBytes': ARCHIVE_PATH.stat().st_size,
        'credentialPatternCheck': 'passed',
        'outputFolder': str(OUTPUT_DIR),
        'archive': str(ARCHIVE_PATH),
    }, indent=2))


if __name__ == '__main__':
    main()
