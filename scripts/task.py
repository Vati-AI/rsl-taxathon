"""Claim a remote branch atomically and maintain repo-native tasks."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'BACKLOG', 'READY', 'IN_PROGRESS', 'BLOCKED', 'IN_REVIEW', 'DONE'}

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()

def path(task_id):
    if not re.fullmatch(r'TAX-\d{3}', task_id):
        raise ValueError('Use a TAX-XXX ID')
    return ROOT / 'tasks' / (task_id + '.json')

def read(task_id):
    return json.loads(path(task_id).read_text())

def save(task):
    path(task['id']).write_text(json.dumps(task, indent=2) + '\n')

def note(task, message):
    task['notes'].append({'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'text': message})

def start(args):
    if git('status', '--porcelain'):
        raise ValueError('Working tree is not clean; preserve current work first')
    git('switch', 'main')
    git('pull', '--ff-only', 'origin', 'main')
    task = read(args.id)
    if task['status'] not in {'READY', 'BACKLOG'}:
        raise ValueError('Task is already claimed or not eligible')
    if any(read(dep)['status'] != 'DONE' for dep in task['depends_on']):
        raise ValueError('Dependencies are not DONE')
    branch = 'task/' + args.id
    # Empty expected SHA means remote ref must not exist. Server enforces this
    # precondition even if two agents race between fetch and push.
    if git('ls-remote', '--heads', 'origin', branch):
        raise ValueError('Remote claim branch exists; contact its owner')
    git('switch', '-c', branch)
    task.update(status='IN_PROGRESS', claimed_by=args.agent, branch=branch)
    note(task, 'Claimed by ' + args.agent)
    save(task)
    git('add', 'tasks/' + args.id + '.json')
    git('commit', '-m', 'chore(' + args.id + '): claim task')
    try:
        git('push', '--force-with-lease=refs/heads/' + branch + ':', '-u', 'origin', branch)
    except subprocess.CalledProcessError:
        raise ValueError('Claim push failed; do not implement until the remote claim succeeds. Local commit preserved.')
    print('Claimed ' + args.id + ' on ' + branch)

def status(args):
    if git('branch', '--show-current') == 'main':
        raise ValueError('Use a task or follow-up branch, never edit main')
    task = read(args.id)
    if args.status not in STATUSES:
        raise ValueError('Unknown status')
    if args.pr:
        if not re.fullmatch(r'https://github.com/[^/]+/[^/]+/pull/\d+', args.pr):
            raise ValueError('Expected a GitHub PR URL')
        task['pr'] = args.pr
    if args.status == 'IN_REVIEW' and not task['pr']:
        raise ValueError('IN_REVIEW requires --pr')
    if args.status == 'DONE':
        if not task['pr'] or not all(x['done'] for x in task['acceptance']) or not task['evidence']:
            raise ValueError('DONE needs all acceptance checks, evidence and a merged PR')
        state = subprocess.check_output(['gh', 'pr', 'view', task['pr'], '--json', 'state', '--jq', '.state'], text=True).strip()
        if state != 'MERGED':
            raise ValueError('Implementation PR is not merged')
    task['status'] = args.status
    note(task, args.note)
    save(task)
    print('Updated task file; commit and push it with your branch')

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('start'); p.add_argument('id'); p.add_argument('--agent', required=True)
    p = sub.add_parser('status'); p.add_argument('id'); p.add_argument('status', choices=sorted(STATUSES)); p.add_argument('--pr'); p.add_argument('--note', required=True)
    args = parser.parse_args()
    try:
        (start if args.command == 'start' else status)(args)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + '\n')

if __name__ == '__main__':
    main()
