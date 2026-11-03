"""Validate task ownership, dependencies, statuses and synthetic fixtures."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def validate(root=ROOT):
    errors = []
    members = {m['name'] for m in json.loads((root / 'config/team.json').read_text())}
    tasks = {}
    for p in sorted((root / 'tasks').glob('TAX-*.json')):
        t = json.loads(p.read_text())
        if not re.fullmatch(r'TAX-\d{3}', t.get('id', '')) or p.stem != t['id']:
            errors.append(f'{p.name}: invalid ID')
        tasks[t['id']] = t
        if t.get('owner') not in members:
            errors.append(f'{p.name}: unknown owner')
        if t.get('status') not in {'BACKLOG','READY','IN_PROGRESS','BLOCKED','IN_REVIEW','DONE'}:
            errors.append(f'{p.name}: invalid status')
        if not t.get('acceptance') or any(not isinstance(c.get('done'), bool) or not c.get('text') for c in t['acceptance']):
            errors.append(f'{p.name}: invalid acceptance criteria')
        if t.get('status') == 'IN_REVIEW' and not t.get('pr'):
            errors.append(f'{p.name}: review needs a PR')
        if t.get('status') == 'DONE' and (not t.get('pr') or not t.get('evidence') or not all(c['done'] for c in t['acceptance'])):
            errors.append(f'{p.name}: DONE needs acceptance, evidence and PR')
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            errors.append(f'{key}: cyclic dependency'); return
        if key in visited: return
        visiting.add(key)
        for dep in tasks[key].get('depends_on', []):
            if dep not in tasks: errors.append(f'{key}: unknown dependency {dep}')
            else: visit(dep)
        visiting.remove(key); visited.add(key)
    for key in tasks: visit(key)
    if not tasks: errors.append('No tasks found')
    for case in json.loads((root / 'data/scenarios/cases.json').read_text()):
        if case.get('synthetic') is not True: errors.append('Only synthetic fixtures allowed')
        for key in ['refund_claim', 'supported_refund']:
            if not isinstance(case.get(key), str) or not re.fullmatch(r'\d+\.\d{2}', case[key]):
                errors.append(f'{case.get("id")}: invalid decimal amount')
    skills=root / '.agents/skills'
    if skills.exists():
        for folder in skills.iterdir():
            text=(folder/'SKILL.md').read_text()
            if not text.startswith('---\n') or 'name: '+folder.name not in text or 'description:' not in text:
                errors.append(f'{folder.name}: invalid skill frontmatter')
    for key,t in tasks.items():
        if t.get('kind')=='epic' and t['status'] in {'IN_PROGRESS','IN_REVIEW'}:
            errors.append(f'{key}: planning epic cannot be claimed')
        if t.get('kind')=='task' and int(key.split('-')[1])>=18:
            for field in ['scope','verification','phase','skills']:
                if not t.get(field):errors.append(f'{key}: missing {field}')
    return errors

if __name__ == '__main__':
    errors = validate()
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Task ownership, dependencies, completion evidence and synthetic fixtures validated')
