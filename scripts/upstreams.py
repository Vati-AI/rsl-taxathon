"""Fetch official upstream source at reviewed immutable revisions, never vendor it."""
import argparse
import json
from pathlib import Path
import subprocess
ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'integration/upstreams.lock.json'

def run(*args, cwd):
    return subprocess.check_output(list(args), cwd=cwd, text=True).strip()

def fetch(name, spec):
    dest = ROOT / 'external' / name
    if dest.exists():
        origin = run('git','remote','get-url','origin',cwd=dest)
        if origin != spec['repository']: raise ValueError(f'{name}: unexpected origin; unchanged')
        if run('git','status','--porcelain',cwd=dest): raise ValueError(f'{name}: dirty checkout; unchanged')
    else:
        dest.mkdir(parents=True)
        run('git','init',cwd=dest)
        run('git','remote','add','origin',spec['repository'],cwd=dest)
    run('git','fetch','--depth=1','origin',spec['commit'],cwd=dest)
    run('git','checkout','--detach',spec['commit'],cwd=dest)
    if run('git','rev-parse','HEAD',cwd=dest) != spec['commit']: raise ValueError('Revision mismatch')
    print(f'{name}: {spec["commit"]} at {dest}', flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['list','fetch','check']);p.add_argument('name',nargs='?');p.add_argument('--all',action='store_true');a=p.parse_args()
    specs=json.loads(LOCK.read_text())
    if a.command=='list':
        for n,s in specs.items():print(n,s['mode'],s['commit'],s['repository'])
        return
    names=list(specs) if a.all else [a.name]
    if not all(n in specs for n in names):p.error('Select a known name or --all')
    for n in names:
        if a.command=='fetch':fetch(n,specs[n])
        else:
            actual=run('git','rev-parse','HEAD',cwd=ROOT/'external'/n)
            if actual!=specs[n]['commit']:raise SystemExit(n+': revision mismatch')
            print(n+': locked revision verified')
if __name__=='__main__': main()
