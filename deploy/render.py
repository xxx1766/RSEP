#!/usr/bin/env python3
"""Render ${NAME} placeholders in deploy templates from hosts.example.yaml."""
import argparse
from pathlib import Path
import string

ROOT = Path(__file__).resolve().parent


def load_hosts(path):
    values = {}
    section = None
    for raw in Path(path).read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if ":" not in text:
            raise ValueError(f"unsupported host line: {raw!r}")
        key, value = text.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if indent == 0:
            if value:
                values[key.upper()] = value
                section = None
            else:
                section = key.upper()
        elif indent == 2 and section is not None:
            values[f"{section}_{key}".upper()] = value
        else:
            raise ValueError(f"unsupported indentation: {raw!r}")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", type=Path, default=ROOT / "hosts.example.yaml")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    values = load_hosts(args.hosts)
    rendered = 0
    templates = []
    for suffix in ("*.yaml.tmpl", "*.sh.tmpl", "*.json.tmpl"):
        templates.extend(ROOT.glob(f"**/{suffix}"))
    for template in sorted(templates):
        relative = template.relative_to(ROOT)
        target = args.out / str(relative)[: -len(".tmpl")]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(string.Template(template.read_text()).safe_substitute(values))
        rendered += 1
        print(target)
    print(f"rendered {rendered} template(s); unknown placeholders are left as ${{NAME}}")


if __name__ == "__main__":
    main()
