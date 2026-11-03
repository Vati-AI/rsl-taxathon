"""Claim a remote branch atomically and maintain repo-native tasks."""

import argparse
import datetime
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"BACKLOG", "READY", "IN_PROGRESS", "BLOCKED", "IN_REVIEW", "DONE"}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def path(task_id):
    if not re.fullmatch(r"TAX-\d{3}", task_id):
        raise ValueError("Use a TAX-XXX ID")
    return ROOT / "tasks" / (task_id + ".json")


def read(task_id):
    return json.loads(path(task_id).read_text())


def save(task):
    path(task["id"]).write_text(json.dumps(task, indent=2) + "\n")


def note(task, message):
    task["notes"].append(
        {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "text": message}
    )


def start(args):
    if git("status", "--porcelain"):
        raise ValueError("Working tree is not clean; preserve current work first")
    git("switch", "main")
    git("pull", "--ff-only", "origin", "main")
    task = read(args.id)
    if task.get("kind") == "epic":
        raise ValueError("Planning epics cannot be claimed; select a leaf task")
    if task["status"] not in {"READY", "BACKLOG"}:
        raise ValueError("Task is already claimed or not eligible")
    if any(read(dep)["status"] != "DONE" for dep in task["depends_on"]):
        raise ValueError("Dependencies are not DONE")
    branch = "task/" + args.id
    # Empty expected SHA means remote ref must not exist. Server enforces this
    # precondition even if two agents race between fetch and push.
    if git("ls-remote", "--heads", "origin", branch):
        raise ValueError("Remote claim branch exists; contact its owner")
    git("switch", "-c", branch)
    task.update(status="IN_PROGRESS", claimed_by=args.agent, branch=branch)
    note(task, "Claimed by " + args.agent)
    save(task)
    git("add", "tasks/" + args.id + ".json")
    git("commit", "-m", "chore(" + args.id + "): claim task")
    try:
        git("push", "--force-with-lease=refs/heads/" + branch + ":", "-u", "origin", branch)
    except subprocess.CalledProcessError:
        raise ValueError(
            "Claim push failed; do not implement until the remote claim succeeds. Local commit preserved."
        )
    print("Claimed " + args.id + " on " + branch)
    sync_task(args.id, optional=True)


def status(args):
    if git("branch", "--show-current") == "main":
        raise ValueError("Use a task or follow-up branch, never edit main")
    task = read(args.id)
    if args.status not in STATUSES:
        raise ValueError("Unknown status")
    if args.pr:
        if not re.fullmatch(r"https://github.com/[^/]+/[^/]+/pull/\d+", args.pr):
            raise ValueError("Expected a GitHub PR URL")
        task["pr"] = args.pr
    if args.status == "IN_REVIEW" and not task["pr"]:
        raise ValueError("IN_REVIEW requires --pr")
    if args.status == "DONE":
        if not task["pr"] or not all(x["done"] for x in task["acceptance"]) or not task["evidence"]:
            raise ValueError("DONE needs all acceptance checks, evidence and a merged PR")
        state = subprocess.check_output(
            ["gh", "pr", "view", task["pr"], "--json", "state", "--jq", ".state"], text=True
        ).strip()
        if state != "MERGED":
            raise ValueError("Implementation PR is not merged")
    task["status"] = args.status
    note(task, args.note)
    save(task)
    print("Updated task file; commit and push it with your branch")


def sync_task(task_id, optional=False):
    remote = git("remote", "get-url", "origin")
    if "github.com" not in remote:
        if not optional:
            raise ValueError("GitHub remote required for issue/Project sync")
        return
    config = json.loads((ROOT / "config/project.json").read_text())
    result = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts/github_sync.py"),
            "--repo",
            config["repository"],
            "--project-number",
            str(config["project_number"]),
            "--task",
            task_id,
        ],
        cwd=ROOT,
    )
    if result.returncode:
        print(
            "WARNING: Git task state is saved, but GitHub synchronization failed. Run task.py sync after fixing access."
        )
        if not optional:
            raise ValueError("GitHub synchronization failed")


def list_tasks(args):
    tasks = {p.stem: json.loads(p.read_text()) for p in (ROOT / "tasks").glob("TAX-*.json")}
    for key, t in sorted(tasks.items()):
        if t.get("kind") == "epic":
            continue
        if args.owner and t["owner"].casefold() != args.owner.casefold():
            continue
        eligible = t["status"] in {"READY", "BACKLOG"} and all(
            tasks[d]["status"] == "DONE" for d in t["depends_on"]
        )
        if args.ready and not eligible:
            continue
        print(
            f"{key} | {t['owner']} | {t.get('phase', 'foundation')} | {'READY' if eligible else t['status']} | {t['title']}"
        )


def evidence(args):
    if git("branch", "--show-current") == "main":
        raise ValueError("Use a task branch")
    t = read(args.id)
    if not 1 <= args.criterion <= len(t["acceptance"]):
        raise ValueError("Criterion number is out of range (one-based)")
    t["acceptance"][args.criterion - 1]["done"] = True
    t["evidence"].append({"criterion": args.criterion, "text": args.text})
    save(t)
    print("Evidence recorded; commit with the task branch")


def refresh(args):
    task = read(args.id)
    branch = git("branch", "--show-current")
    if branch == "main" or task.get("branch") != branch:
        raise ValueError("Use this task's claimed branch")
    if git("status", "--porcelain"):
        raise ValueError("Commit intended changes before refreshing; never discard other work")
    git("fetch", "origin", "main")
    # Merge preserves reviewed history and requires no force-push. Conflicts are
    # left visible for the agent to resolve and retest, never auto-selected.
    git("merge", "--no-edit", "origin/main")


def finish(args):
    t = read(args.id)
    branch = git("branch", "--show-current")
    if branch == "main" or t.get("branch") != branch:
        raise ValueError("Use this task's claimed branch")
    if git("status", "--porcelain"):
        raise ValueError("Commit intended changes before finishing")
    body = Path(args.body_file).resolve()
    if not body.is_file():
        raise ValueError("PR body file is missing")
    refresh(args)
    t = read(args.id)
    subprocess.check_call(["python3", str(ROOT / "scripts/dev.py"), "check"], cwd=ROOT)
    subprocess.check_call(["python3", str(ROOT / "scripts/quality.py")], cwd=ROOT)
    git("push", "-u", "origin", branch)
    config = json.loads((ROOT / "config/project.json").read_text())
    prs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                config["repository"],
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "url",
            ],
            text=True,
        )
    )
    url = (
        prs[0]["url"]
        if prs
        else subprocess.check_output(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                config["repository"],
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                t["id"] + ": " + t["title"],
                "--body-file",
                str(body),
            ],
            text=True,
        ).strip()
    )
    t.update(status="IN_REVIEW", pr=url)
    note(t, "Agent finished implementation and opened human review")
    save(t)
    git("add", "tasks/" + args.id + ".json")
    git("commit", "-m", "chore(" + args.id + "): record review PR")
    git("push", "origin", branch)
    sync_task(args.id, optional=True)
    print("Human review required: " + url)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("id")
    p.add_argument("--agent", required=True)
    p = sub.add_parser("status")
    p.add_argument("id")
    p.add_argument("status", choices=sorted(STATUSES))
    p.add_argument("--pr")
    p.add_argument("--note", required=True)
    p = sub.add_parser("list")
    p.add_argument("--owner")
    p.add_argument("--ready", action="store_true")
    p = sub.add_parser("refresh")
    p.add_argument("id")
    p = sub.add_parser("sync")
    p.add_argument("id")
    p = sub.add_parser("evidence")
    p.add_argument("id")
    p.add_argument("--criterion", type=int, required=True)
    p.add_argument("--text", required=True)
    p = sub.add_parser("finish")
    p.add_argument("id")
    p.add_argument("--body-file", required=True)
    args = parser.parse_args()
    try:
        actions = {
            "start": start,
            "status": status,
            "list": list_tasks,
            "evidence": evidence,
            "finish": finish,
            "refresh": refresh,
            "sync": lambda a: sync_task(a.id),
        }
        actions[args.command](args)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
