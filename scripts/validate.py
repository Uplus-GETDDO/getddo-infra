#!/usr/bin/env python3
"""Validate source in isolated local roots; never initialize or write remote State."""
from pathlib import Path
import argparse
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ROOTS = [ROOT / 'terraform/bootstrap', ROOT / 'terraform/prod', ROOT / 'terraform/monitoring']


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.links = []
        self.scripts = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        for name in ['href', 'src']:
            if name in attrs:
                self.links.append(attrs[name])
        if tag == 'script' and not attrs.get('src') and attrs.get('type', '') not in ['application/json', 'application/ld+json']:
            self.script = ''

    def handle_data(self, text):
        if self.script is not None:
            self.script += text

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None


def personal_reference(path):
    private_dir = ROOT / 'local-reference'
    return path == ROOT / 'infrastructure-design-migration.html' or path == private_dir or private_dir in path.parents


def documents(temp):
    errors = []
    cross_repo = 0
    cross_repo_unchecked = 0
    private_excluded = 0
    siblings = [ROOT.parent / "getddo-be", ROOT.parent / "getddo-spec"]
    paths = sorted((ROOT / 'docs').rglob('*.html')) + sorted(ROOT.rglob('*.md'))
    for path in paths:
        if personal_reference(path) or any(part in path.parts for part in ['.terraform', '.git', 'archive']):
            continue
        text = path.read_text()
        if path.suffix == '.html':
            parsed = Document()
            parsed.feed(text)
            if len(parsed.ids) != len(set(parsed.ids)):
                errors.append(f'{path.relative_to(ROOT)}: duplicate ID')
            links = parsed.links
            for i, script in enumerate(parsed.scripts):
                code = temp / f'{path.stem}-{i}.js'
                code.write_text(script)
                run('node', '--check', str(code))
        else:
            links = re.findall(r'!?\[[^\]\n]*\]\(([^)\s]+)\)', text)
        for href in links:
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            if personal_reference(target):
                private_excluded += 1
                continue
            if target != ROOT and ROOT not in target.parents:
                sibling = next((repo for repo in siblings if target == repo or repo in target.parents), None)
                if sibling is None:
                    errors.append(f'{path.relative_to(ROOT)}: unexpected outside-repository link {href}')
                elif sibling.exists():
                    cross_repo += 1
                    if not target.exists():
                        errors.append(f'{path.relative_to(ROOT)}: missing sibling link {href}')
                else:
                    cross_repo_unchecked += 1
                continue
            if not target.exists():
                errors.append(f'{path.relative_to(ROOT)} -> {href}')
    if errors:
        raise ValueError('Missing local document links:\n' + '\n'.join(sorted(set(errors))))
    print(f'PASS: document files, unique HTML IDs, JS syntax ({cross_repo} sibling links checked, {cross_repo_unchecked} explicitly deferred without sibling checkout, {private_excluded} personal reference links excluded)', flush=True)


def terraform_checks():
    for root in ROOTS:
        run('terraform', f'-chdir={root}', 'fmt', '-check', '-recursive')
    with tempfile.TemporaryDirectory(prefix='getddo-infra-validate-') as name:
        temp = Path(name)
        for root in ROOTS:
            work = temp / root.name
            shutil.copytree(root, work, ignore=shutil.ignore_patterns('.terraform', '*.tfstate*', '*.tfvars*', '*.tfplan*'))
            (work / 'backend.tf').unlink(missing_ok=True)
            env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'TF_VAR_', 'TF_LOG'))}
            env.update(AWS_EC2_METADATA_DISABLED='true', TF_IN_AUTOMATION='1', CHECKPOINT_DISABLE='1')
            args = ['terraform', f'-chdir={work}', 'init', '-backend=false', '-input=false', '-lockfile=readonly']
            provider_dir = root / '.terraform/providers'
            if provider_dir.exists():
                args.append(f'-plugin-dir={provider_dir}')
            for test in (work / 'tests').glob('*.tftest.hcl'):
                if 'mock_provider "aws"' not in test.read_text():
                    raise ValueError(f'Every local test must explicitly use mock AWS: {test}')
            run(*args, env=env)
            run('terraform', f'-chdir={work}', 'validate', '-no-color', env=env)
            run('terraform', f'-chdir={work}', 'test', '-no-color', env=env)
    print("PASS: Terraform fmt, validate and mock tests; no live AWS or State access", flush=True)


def script_checks():
    for path in (ROOT / "scripts").glob("*.py"):
        compile(path.read_text(), str(path), "exec")
    for path in list((ROOT / 'terraform').rglob('*')) + list((ROOT / 'observability').rglob('*')) + list((ROOT / 'scripts').glob('*.sh')):
        if '.terraform' in path.parts:
            continue
        if path.suffix == '.json':
            json.loads(path.read_text())
        if path.name.endswith(('.sh', '.sh.tftpl')):
            # Current shell template placeholders are shell-safe insertions; rendering tested by HCL.
            run('bash', '-n', str(path))
    run(sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "scripts"), "-p", "test_*.py")
    print("PASS: Python syntax, shell syntax, JSON configuration and unit tests", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", nargs="?", default="all", choices=["all", "terraform", "scripts", "documents"])
    check = parser.parse_args().check
    if check in ["all", "terraform"]:
        terraform_checks()
    if check in ["all", "scripts"]:
        script_checks()
    if check == "documents":
        run(sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "scripts"), "-p", "test_*.py")
        with tempfile.TemporaryDirectory(prefix="getddo-documents-") as name:
            documents(Path(name))


if __name__ == '__main__':
    main()
