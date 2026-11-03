import json
import re
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
NO_TARGETS = {"docker/app.Dockerfile", "docker/requirements/app.txt"}


def load_members(root: Path) -> dict[str, tuple[str, set[str]]]:
    members = {}
    for pyproject in sorted(root.glob("*/*/pyproject.toml")):
        member = pyproject.parent.relative_to(root).as_posix()
        if member.split("/")[0] not in ("packages", "services"):
            continue
        project = tomllib.loads(pyproject.read_text())["project"]
        dependencies = {
            re.match(r"[\w.-]+", dependency)[0] for dependency in project["dependencies"]
        }
        members[project["name"]] = (member, dependencies)
    return members


def detect(changed: list[str], root: Path = ROOT) -> tuple[bool, list[str], list[str]]:
    members = load_members(root)
    by_dir = {member: name for name, (member, _) in members.items()}
    services = [member for member in by_dir if member.startswith("services/")]
    affected = set()
    full = False
    for path in changed:
        parts = path.split("/")
        if path in NO_TARGETS or path.endswith(".md") or parts[0] == "docs":
            continue
        if parts[0] in ("packages", "services") and "/".join(parts[:2]) in by_dir:
            affected.add("/".join(parts[:2]))
        elif path.startswith("docker/") and path.endswith((".Dockerfile", ".txt")):
            service = "services/" + Path(path).name.removesuffix(".Dockerfile").removesuffix(".txt")
            if service in by_dir:
                affected.add(service)
            else:
                full = True
        else:
            full = True

    if full:
        affected = set(by_dir)
    pending = [by_dir[member] for member in affected]
    while pending:
        name = pending.pop()
        for dependent, (member, dependencies) in members.items():
            if name in dependencies and member not in affected:
                affected.add(member)
                pending.append(dependent)

    names = sorted(member.removeprefix("services/") for member in affected if member in services)
    return full, names, sorted(affected)


def main() -> None:
    full, services, members = detect(sys.stdin.read().split())
    print(f"full={json.dumps(full)}")
    print(f"services={json.dumps(services)}")
    print(f"members={' '.join(members)}")


if __name__ == "__main__":
    main()
