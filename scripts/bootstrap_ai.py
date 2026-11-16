#!/usr/bin/env python3
"""Install reviewed AI skills locally; never change account/MCP settings."""

import argparse
import hashlib
import io
import json
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
MAX_EXTRACTED_BYTES = 60 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_blob_sha(data):
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def install(archive_data, manifest, selected, destination):
    """Validate the entire selection before writing; refuse modified targets."""
    prepared = []
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        names = archive.namelist()
        roots = {name.split('/')[0] for name in names if name}
        if len(roots) != 1:
            raise ValueError('Archive must have one root directory')
        prefix = next(iter(roots)) + '/'
        total_size = 0
        for skill in selected:
            source_prefix = prefix + skill['path'].rstrip('/') + '/'
            files = {}
            for member in archive.infolist():
                if not member.filename.startswith(source_prefix) or member.is_dir():
                    continue
                relative = PurePosixPath(member.filename[len(source_prefix):])
                mode = member.external_attr >> 16
                if relative.is_absolute() or '..' in relative.parts or stat.S_ISLNK(mode):
                    raise ValueError('Unsafe archive entry')
                if not relative.parts or '\\' in str(relative):
                    raise ValueError('Invalid archive path')
                total_size += member.file_size
                if total_size > MAX_EXTRACTED_BYTES:
                    raise ValueError('Unpacked skills exceed size limit')
                data = archive.read(member)
                key = str(relative)
                if key in files:
                    raise ValueError('Duplicate archive path')
                files[key] = data
            if 'SKILL.md' not in files:
                raise ValueError(f"Missing SKILL.md: {skill['name']}")
            if git_blob_sha(files['SKILL.md']) != skill['skill_md_blob_sha']:
                raise ValueError(f"Pinned SKILL.md mismatch: {skill['name']}")
            target = destination / skill['name']
            hashes = {name: digest(data) for name, data in files.items()}
            receipt = {'repository': manifest['source']['repository'],
                       'commit': manifest['source']['commit'], 'files': hashes}
            if target.exists() or target.is_symlink():
                if target.is_symlink():
                    raise ValueError(f'Refusing symlink target: {target}')
                marker = target / '.ips-skill-source.json'
                expected = json.dumps(receipt, sort_keys=True)
                if not marker.is_file() or json.dumps(json.loads(marker.read_text()), sort_keys=True) != expected:
                    raise ValueError(f'Refusing existing unmanaged/different skill: {target}')
                for name, value in hashes.items():
                    file = target / name
                    if file.is_symlink() or not file.is_file() or digest(file.read_bytes()) != value:
                        raise ValueError(f'Refusing modified installation: {file}')
                print(f"Already installed: {skill['name']}")
                continue
            prepared.append((skill['name'], files, receipt))
    destination.mkdir(parents=True, exist_ok=True)
    for name, files, receipt in prepared:
        target = destination / name
        with tempfile.TemporaryDirectory(prefix='.ips-skill-', dir=destination) as temporary:
            staging = Path(temporary) / name
            staging.mkdir()
            for relative, data in files.items():
                output = staging / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                if relative.endswith('.sh'):
                    output.chmod(0o755)
            (staging / '.ips-skill-source.json').write_text(
                json.dumps(receipt, indent=2) + '\n')
            staging.rename(target)
        print(f'Installed: {name} -> {target}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Read-only inventory')
    mode.add_argument('--install', action='store_true', help='Download pinned skills only')
    parser.add_argument('--skill', action='append', help='Explicit manifest skill; repeatable')
    parser.add_argument('--dest', type=Path, help='Default: project .agents/skills')
    parser.add_argument('--archive', type=Path, help='Offline pinned archive instead of HTTPS')
    args = parser.parse_args()
    manifest = json.loads((ROOT / '.ai/tooling.json').read_text())
    allowed = {s['name']: s for s in manifest['skills']}
    names = args.skill or [s['name'] for s in manifest['skills'] if s['default_install']]
    if set(names) - allowed.keys():
        parser.error('Unknown skill; only reviewed manifest entries are allowed')
    selected = [allowed[name] for name in dict.fromkeys(names)]
    destination = (args.dest or ROOT / '.agents/skills').resolve()
    if args.check:
        for executable in ['python3', 'uv', 'node', 'npm', 'git', 'gh', 'docker']:
            print(f"{executable}: {shutil.which(executable) or 'MISSING'}")
        for skill in selected:
            found = (destination / skill['name'] / 'SKILL.md').is_file()
            print(f"skill {skill['name']}: {'present' if found else 'not installed locally'}")
        print('Inventory only; missing tools are not installed by this command.')
        print('Plugins: individual GitHub/Context7 connection must be checked in the AI client.')
        return
    if args.archive:
        if args.archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError('Archive exceeds size limit')
        data = args.archive.read_bytes()
    else:
        request = urllib.request.Request(manifest['source']['archive_url'],
                                         headers={'User-Agent': 'ips-ai-bootstrap/1'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
        except urllib.error.URLError as error:
            # Some standalone Python macOS installs lack a root CA bundle.
            # System curl uses its configured CA store; never disable TLS checks.
            if not isinstance(error.reason, ssl.SSLCertVerificationError) or not shutil.which('curl'):
                raise
            print('Python CA store unavailable; using verified system curl.')
            with tempfile.TemporaryDirectory(prefix='ips-skills-download-') as temporary:
                output = Path(temporary) / 'skills.zip'
                result = subprocess.run(
                    ['curl', '--fail', '--silent', '--show-error', '--location',
                     '--proto', '=https', '--max-time', '60', '--max-filesize',
                     str(MAX_ARCHIVE_BYTES), '--output', str(output),
                     manifest['source']['archive_url']],
                    capture_output=True, text=True, check=False)
                if result.returncode:
                    raise ValueError(f'Verified curl download failed: {result.stderr.strip()}')
                data = output.read_bytes()
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError('Archive exceeds size limit')
    install(data, manifest, selected, destination)
    print('No downloaded scripts executed; no plugins or accounts modified.')
    print('See docs/ai-setup.md for client-specific discovery and authentication.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f'Bootstrap failed: {error}', file=sys.stderr)
        sys.exit(1)
