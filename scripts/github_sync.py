"""Upsert task issues and project statuses without running privileged PR code."""

import argparse
import json
import subprocess
from pathlib import Path

from github_setup import api, gh

ROOT = Path(__file__).resolve().parents[1]


def command(*args, stdin=None):
    return subprocess.check_output(["gh", *args], text=True, cwd=ROOT, input=stdin).strip()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--project-number", required=True)
    p.add_argument("--task")
    p.add_argument(
        "--allow-branch", help="Maintainer-approved branch name for initial backlog seeding"
    )
    a = p.parse_args()
    org, _ = a.repo.split("/")
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if not a.task and branch != "main" and a.allow_branch != branch:
        raise SystemExit("Full sync requires trusted main; use --task on a task branch")
    tasks = [json.loads(f.read_text()) for f in sorted((ROOT / "tasks").glob("TAX-*.json"))]
    if a.task:
        tasks = [t for t in tasks if t["id"] == a.task]
        if not tasks:
            raise SystemExit("Unknown task")
    people = {x["name"]: x for x in json.loads((ROOT / "config/team.json").read_text())}
    project = gh("project", "view", a.project_number, "--owner", org, "--format", "json")
    if project.get("public"):
        raise SystemExit("Refusing to sync private tasks to a public project")
    fields = gh("project", "field-list", a.project_number, "--owner", org, "--format", "json")[
        "fields"
    ]
    status = next(f for f in fields if f["name"] == "Status")
    options = {o["name"]: o["id"] for o in status["options"]}
    issues = gh(
        "issue",
        "list",
        "--repo",
        a.repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,title,url,labels",
    )
    known_labels = {
        x["name"]
        for page in gh("api", f"repos/{a.repo}/labels", "--paginate", "--slurp")
        for x in page
    }
    permissions = {}
    for t in tasks:
        labels = [
            "task",
            "owner:" + t["owner"],
            "status:" + t["status"],
            "area:" + people[t["owner"]]["role"],
            "phase:" + t.get("phase", "foundation"),
            "kind:" + t.get("kind", "task"),
        ]
        for label in labels:
            if label not in known_labels:
                command("label", "create", label, "--repo", a.repo, "--color", "2563EB")
                known_labels.add(label)
        matches = [i for i in issues if i["title"].startswith(t["id"] + ":")]
        if len(matches) > 1:
            raise SystemExit("Duplicate task issues: " + t["id"])
        body = (
            "Canonical task: `tasks/"
            + t["id"]
            + ".json`\n\nOwner: "
            + t["owner"]
            + "\nStatus: "
            + t["status"]
            + "\nDependencies: "
            + (", ".join(t["depends_on"]) or "None")
            + "\n\n## Acceptance\n"
            + "\n".join(
                "- [" + ("x" if x["done"] else " ") + "] " + x["text"] for x in t["acceptance"]
            )
            + "\n\nPR: "
            + (t["pr"] or "Not opened")
            + "\n\nUpdate the repository task first; this issue mirrors it.\n"
        )
        body += (
            "\n## Scope\n"
            + t.get(
                "scope",
                "Planning epic; select its implementation children."
                if t.get("kind") == "epic"
                else t["title"],
            )
            + "\n\nPhase: "
            + t.get("phase", "foundation")
            + "\n\nVerification: "
            + t.get("verification", "See canonical task acceptance criteria")
            + "\n"
        )
        # Pass the body on stdin: an open NamedTemporaryFile cannot be read by
        # another process on Windows.
        if matches:
            issue = matches[0]
            command(
                "issue",
                "edit",
                str(issue["number"]),
                "--repo",
                a.repo,
                "--title",
                t["id"] + ": " + t["title"],
                "--body-file",
                "-",
                stdin=body,
            )
            # Preserve human labels; replace only managed labels.
            preserved = [
                x["name"]
                for x in issue["labels"]
                if not x["name"].startswith(("owner:", "status:", "area:", "phase:", "kind:"))
                and x["name"] != "task"
            ]
            api(
                f"repos/{a.repo}/issues/{issue['number']}/labels",
                "PUT",
                {"labels": preserved + labels},
            )
            url = issue["url"]
        else:
            url = command(
                "issue",
                "create",
                "--repo",
                a.repo,
                "--title",
                t["id"] + ": " + t["title"],
                "--body-file",
                "-",
                "--label",
                ",".join(labels),
                stdin=body,
            )
        number = url.rsplit("/", 1)[1]
        api(
            f"repos/{a.repo}/issues/{number}",
            "PATCH",
            {"state": "closed" if t["status"] == "DONE" else "open"},
        )
        user = people[t["owner"]]["github"]
        if user:
            if user not in permissions:
                permissions[user] = api(f"repos/{a.repo}/collaborators/{user}/permission")[
                    "permission"
                ]
            permission = permissions[user]
            if permission in {"write", "maintain", "admin"}:
                api(f"repos/{a.repo}/issues/{number}/assignees", "POST", {"assignees": [user]})
        item = gh(
            "project",
            "item-add",
            a.project_number,
            "--owner",
            org,
            "--url",
            url,
            "--format",
            "json",
        )
        target = (
            "Done"
            if t["status"] == "DONE"
            else "In Progress"
            if t["status"] in {"IN_PROGRESS", "IN_REVIEW", "BLOCKED"}
            else "Todo"
        )
        command(
            "project",
            "item-edit",
            "--id",
            item["id"],
            "--project-id",
            project["id"],
            "--field-id",
            status["id"],
            "--single-select-option-id",
            options[target],
        )
        print(t["id"] + " → " + url + " (" + t["status"] + ")", flush=True)


if __name__ == "__main__":
    main()
