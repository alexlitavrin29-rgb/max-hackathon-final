"""Verify the submitted files without Docker, network access or credentials."""
import hashlib
import json
from pathlib import Path
import sys

root=Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve()
manifest=json.loads((root/'MANIFEST.sha256.json').read_text(encoding='utf-8'))
bad=[]
for name,digest in manifest.items():
    path=(root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        bad.append(name)
if bad:
    print(json.dumps({'ok':False,'mismatched_files':bad},ensure_ascii=False))
    raise SystemExit(1)
print(json.dumps({'ok':True,'verified_files':len(manifest)}))
