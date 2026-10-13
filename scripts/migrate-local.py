#!/usr/bin/env python3
"""Preserve workflow text, job/check names and tests; retarget plain Ubuntu jobs.

Usage: python3 scripts/migrate-local.py /path/to/repo [--apply]
Defaults to a unified diff. Unsupported existing job conditions, OS matrices,
reusable workflows and pull_request_target need an explicit manual review.
"""
import argparse
import difflib
import re
from pathlib import Path


GUARD = ("    if: >-\n"
         "      github.actor == github.repository_owner &&\n"
         "      (github.event_name != 'pull_request' ||\n"
         "       github.event.pull_request.head.repo.full_name == github.repository)\n")
RUNNER = "    runs-on: [self-hosted, linux, x64, ci-linux]\n"


def migrate(text):
    if re.search(r"\bpull_request_target\b", text):
        return text, ["Skipped workflow with pull_request_target"]
    lines = text.splitlines(keepends=True)
    result, warnings = [], []
    in_jobs = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^jobs:\s*(?:#.*)?$", line.rstrip()):
            in_jobs = True
        elif re.match(r"^[^\s#].*:", line):
            in_jobs = False
        if in_jobs and re.match(r"^  [A-Za-z0-9_-]+:\s*(?:#.*)?$", line.rstrip()):
            end = i + 1
            while end < len(lines) and (not lines[end].strip() or lines[end].lstrip().startswith('#') or lines[end].startswith('    ')):
                end += 1
            block = lines[i:end]
            candidates = [j for j,b in enumerate(block) if re.match(r"^    runs-on:\s*(?:ubuntu-latest|ubuntu-24\.04)\s*(?:#.*)?$", b.rstrip())]
            if candidates and any(re.match(r"^    if:", b) for b in block):
                warnings.append(f"Skipped {line.strip()}: existing job condition needs review")
            elif len(candidates) == 1:
                j = candidates[0]
                original_comment = block[j].partition('#')[2].strip()
                comment = '    # '+original_comment+'\n' if original_comment else ''
                block[j] = comment + GUARD + RUNNER
            result.extend(block)
            i = end
            continue
        result.append(line)
        i += 1
    return ''.join(result), warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    directory = args.repository / '.github' / 'workflows'
    if not directory.is_dir(): raise SystemExit('No .github/workflows directory found')
    for path in sorted(directory.glob('*')):
        if path.suffix not in {'.yml','.yaml'}: continue
        original = path.read_text()
        revised, warnings = migrate(original)
        for warning in warnings: print(f'{path.name}: {warning}')
        if original == revised: continue
        print(''.join(difflib.unified_diff(original.splitlines(True),revised.splitlines(True),
                                         fromfile=str(path),tofile=str(path))),end='')
        if args.apply: path.write_text(revised)


if __name__ == '__main__': main()
