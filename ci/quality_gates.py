#!/usr/bin/env python3
"""Repository quality gates, runnable inside CI with no third-party packages.

Each gate is a measurement with a path, so the Code Quality report points at the
offending file instead of a vague "something failed". Exit code 1 when a blocking
gate fails.
"""
from __future__ import annotations

import json
import os
import re
import sys

SECRET_RE = re.compile(r'(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{24,}|ghp_[A-Za-z0-9]{20,}|'
                       r'BEGIN (RSA|OPENSSH) PRIVATE KEY)')
CLAIM_RE = re.compile(r'(?<!not )zero false positives|100% accurate', re.I)
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.next', 'out'}


def text_files(root: str) -> list[str]:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(('.py', '.md', '.html', '.yml', '.yaml', '.txt', '.json')):
                found.append(os.path.join(base, name))
    return found


def read(path: str) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def run(root: str) -> list[dict]:
    findings: list[dict] = []

    def add(gate: str, ok: bool, detail: str, path: str) -> None:
        findings.append({'gate': gate, 'ok': ok, 'detail': detail, 'path': path})

    license_path = os.path.join(root, 'LICENSE')
    add('license', os.path.exists(license_path), 'LICENSE missing', 'LICENSE')

    for doc in ('README.md', 'ARCHITECTURE.md', 'EVIDENCE.md'):
        add('documentation', os.path.exists(os.path.join(root, doc)), f'{doc} missing', doc)

    for path in text_files(root):
        rel = os.path.relpath(path, root)
        if rel == os.path.join('ci', 'quality_gates.py'):
            continue  # the gate file names the patterns it forbids
        if rel in ('quality-gates.json', 'codequality.json'):
            continue  # reports quote the findings they describe
        body = read(path)
        for match in SECRET_RE.finditer(body):
            if 'AKIAIOSFODNN7EXAMPLE' in match.group(0):
                continue
            add('secret-scan', False, f'possible secret: {match.group(0)[:12]}…', rel)
        for match in CLAIM_RE.finditer(body):
            add('honest-claims', False, f'unverifiable claim: {match.group(0)}', rel)

    index = read(os.path.join(root, 'web', 'index.html'))
    if index:
        add('accessibility', 'name="viewport"' in index, 'missing viewport meta', 'web/index.html')
        add('accessibility', 'aria-live' in index, 'missing aria-live region', 'web/index.html')
        add('motion', 'prefers-reduced-motion' in index, 'missing reduced-motion rule', 'web/index.html')
        add('responsive', '@media' in index, 'missing responsive breakpoint', 'web/index.html')
    return findings


def codeclimate(findings: list[dict]) -> list[dict]:
    issues = []
    for i, item in enumerate(findings):
        if item['ok']:
            continue
        issues.append({
            'type': 'issue',
            'check_name': item['gate'],
            'description': item['detail'],
            'categories': ['Bug Risk'],
            'severity': 'critical' if item['gate'] in ('secret-scan', 'license') else 'major',
            'fingerprint': f"{item['gate']}:{item['path']}:{i}",
            'location': {'path': item['path'], 'lines': {'begin': 1}},
        })
    return issues


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else '.'
    findings = run(root)
    failed = [f for f in findings if not f['ok']]
    with open('quality-gates.json', 'w', encoding='utf-8') as fh:
        json.dump({'findings': findings, 'failed': len(failed)}, fh, ensure_ascii=False, indent=1)
    with open('codequality.json', 'w', encoding='utf-8') as fh:
        json.dump(codeclimate(findings), fh, ensure_ascii=False, indent=1)
    for item in findings:
        print(f"{'PASS' if item['ok'] else 'FAIL'} {item['gate']:14s} {item['path']}")
    print(f"\n{len(findings) - len(failed)}/{len(findings)} gates passed")
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
