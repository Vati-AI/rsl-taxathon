"""Configure an existing private org repo; no paid-plan purchase or automatic transfer."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gh(*args, payload=None):
    result = subprocess.run(
        ["gh", *args],
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        cwd=ROOT,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def api(endpoint, method="GET", payload=None):
    args = ["api", endpoint, "--method", method]
    if payload is not None:
        args += ["--input", "-"]
    return gh(*args, payload=payload)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--invite", action="store_true")
    a = p.parse_args()
    org, name = a.repo.split("/")
    repo = api("repos/" + a.repo)
    if not repo["private"]:
        raise SystemExit("Refusing to configure a public repository")
    if repo["owner"]["type"] != "Organization":
        raise SystemExit("Expected an organization-owned repository")
    report = {"repo": repo["html_url"], "errors": [], "invitations": []}
    report_path = ROOT / ".local/github-setup.json"
    report_path.parent.mkdir(exist_ok=True)

    def attempt(label, action):
        try:
            return action()
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            report["errors"].append({"step": label, "error": str(exc)})
            print(label + ": blocked")
            return None

    api(
        "repos/" + a.repo,
        "PATCH",
        {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
            "has_issues": True,
        },
    )
    team = json.loads((ROOT / "config/team.json").read_text())
    existing = gh("api", f"orgs/{org}/teams", "--paginate", "--slurp")
    teams = {t["slug"]: t for page in existing for t in page}
    owners = [
        "# Role teams have repository write access; maintainer remains a fallback reviewer.",
        "* @Kellybrackets",
    ]
    for person in team:
        slug = person["role"]
        t = teams.get(slug) or attempt(
            "create team " + slug,
            lambda: api(f"orgs/{org}/teams", "POST", {"name": slug, "privacy": "closed"}),
        )
        if not t:
            continue
        grant_error_count = len(report["errors"])
        attempt(
            "grant team " + slug,
            lambda: api(
                f"orgs/{org}/teams/{t['slug']}/repos/{a.repo}", "PUT", {"permission": "push"}
            ),
        )
        if len(report["errors"]) == grant_error_count:
            owners.append("/" + person["path"] + "/ @" + org + "/" + t["slug"] + " @Kellybrackets")
        if person["github"] and a.invite:
            result = attempt(
                "invite " + person["name"],
                lambda: api(
                    f"orgs/{org}/teams/{t['slug']}/memberships/{person['github']}",
                    "PUT",
                    {"role": "member"},
                ),
            )
            if result:
                report["invitations"].append({"name": person["name"], "state": result["state"]})
    (ROOT / ".github/CODEOWNERS").write_text("\n".join(owners) + "\n")
    # GitHub Free is an explicit v1 decision. Do not attempt paid-only setup.
    report["branch_protection_enforced"] = bool(
        repo.get("default_branch")
        and api("repos/" + a.repo + "/branches/" + repo["default_branch"]).get("protected")
    )
    report["review_policy"] = (
        "GitHub Free: CI plus human-reviewed PRs; no paid protection requested"
    )
    projects = attempt(
        "list projects",
        lambda: gh("project", "list", "--owner", org, "--limit", "100", "--format", "json"),
    )
    project = next(
        (
            x
            for x in (projects or {}).get("projects", [])
            if x["title"] == "RSL Taxathon — Team delivery"
        ),
        None,
    )
    if projects is not None and project is None:
        project = attempt(
            "create project",
            lambda: gh(
                "project",
                "create",
                "--owner",
                org,
                "--title",
                "RSL Taxathon — Team delivery",
                "--format",
                "json",
            ),
        )
    if project:
        number = str(project["number"])
        report["project"] = project
        attempt(
            "private project",
            lambda: gh(
                "project",
                "edit",
                number,
                "--owner",
                org,
                "--visibility",
                "PRIVATE",
                "--format",
                "json",
            ),
        )
        attempt(
            "link project", lambda: gh("project", "link", number, "--owner", org, "--repo", a.repo)
        )
        attempt(
            "project number variable",
            lambda: subprocess.check_call(
                ["gh", "variable", "set", "PROJECT_NUMBER", "--repo", a.repo, "--body", number]
            ),
        )
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
