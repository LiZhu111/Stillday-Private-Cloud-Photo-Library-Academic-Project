"""Build a source archive from explicit safe paths, excluding credentials and local state."""
from pathlib import Path
import zipfile,json,hashlib,re
ROOT=Path(__file__).resolve().parents[1]
def main():
 allowed=[]
 for dirname in ['photo_library','frontend','tests','scripts','infrastructure']:
  for p in (ROOT/dirname).rglob('*'):
   if not p.is_file() or '__pycache__' in p.parts:continue
   if p.suffix not in {'.py','.js','.css','.html','.json','.ps1','.md'}:continue
   if '.local.' in p.name or p.name.startswith('outputs') or p.name=='pricing_browser.py':continue
   allowed.append(p)
 for name in ['README.md','requirements.txt','Dockerfile','compose.yaml','.dockerignore','.env.example']:
  allowed.append(ROOT/name)
 for name in ['current-architecture.md','deployment-progress.md','api.md','limitations.md','validation.md','submission-checklist.md']:
  allowed.append(ROOT/'docs'/name)
 inventory=ROOT/'submission.generated.yml';assert inventory.is_file()
 manifest=[]
 out=ROOT/'deliverables/Stillday-source-and-infrastructure.zip'
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
  for p in sorted(set(allowed)):
   raw=p.read_bytes();text=raw.decode('utf-8-sig',errors='replace')
   assert not re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',text),str(p)
   assert not re.search(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',text),str(p)
   assert not re.search(r'eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}',text),str(p)
   relative=p.relative_to(ROOT).as_posix();archive.writestr(relative,raw);manifest.append({'file':relative,'sha256':hashlib.sha256(raw).hexdigest()})
  archive.write(inventory,'submission.yml')
  archive.writestr('SUBMISSION-NOTES.txt','Resource inventory was read from AWS on 23 October 2026. Shared course dependencies are included but were not retagged. Gradescope validation is still required. This archive contains source and infrastructure only; submit the final report and actual demonstration recording separately to Canvas. Local tokens, environment values, browser profiles and private evidence are excluded.\n')
 (ROOT/'deliverables/source-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 with zipfile.ZipFile(out) as archive:
  assert 'submission.yml' in archive.namelist()
  assert not any('/.data/' in '/'+n or '.local.' in n or 'private/' in n for n in archive.namelist())
  assert archive.testzip() is None
 print(json.dumps({'sourceFiles':len(manifest),'zipBytes':out.stat().st_size,'credentialPatternCheck':'passed','output':str(out)}))
if __name__=='__main__':main()
