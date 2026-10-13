"""Package verified artifacts; run after wheel build and bench/package_check.py."""
import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.5.0'
DIST = ROOT.parent / 'distribution'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    DIST.mkdir(exist_ok=True)
    names = ['README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.txt', 'pyproject.toml', 'grapholog.pyz',
             'RAPORT.html', 'RAPORT-0.1.0.html', 'RAPORT-0.2.0.html', 'RAPORT-0.3.0.html', 'RAPORT-0.4.0.html',
             'rust/Cargo.toml', 'rust/Cargo.lock', 'bin/grapholog-rs-linux-x86_64',
             f'python-dist/grapholog-{VERSION}-py3-none-any.whl']
    paths = [ROOT / name for name in names]
    directories = []
    for name in ['src/grapholog', 'rust/src', 'tests', 'bench', 'contracts', 'examples', 'results']:
        for path in sorted((ROOT / name).rglob('*')):
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise ValueError(f'Unexpected symlink in release: {path}')
            if path.is_dir():
                directories.append(path)
            else:
                paths.append(path)
    paths = sorted(set(paths))
    checksums = ROOT / 'SHA256SUMS.txt'
    checksums.write_text(''.join(f'{sha(path)}  {path.relative_to(ROOT).as_posix()}\n' for path in paths))
    archive_path = DIST / f'grapholog-{VERSION}-python-rust.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in [*directories, *paths, checksums]:
            archive.write(path, f'grapholog-{VERSION}/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        for path in paths:
            data = archive.read(f'grapholog-{VERSION}/' + path.relative_to(ROOT).as_posix())
            assert hashlib.sha256(data).hexdigest() == sha(path)
    outputs = [archive_path]
    for source, suffix in [('RAPORT.html', 'planfile-koru-raport.html'),
                           ('results/tickets/example-plan.json', 'plan-przykladowy.json'),
                           ('results/tickets/planning-benchmark.json', 'pomiary.json')]:
        target = DIST / f'grapholog-{VERSION}-{suffix}'
        shutil.copyfile(ROOT / source, target)
        outputs.append(target)
    sums = DIST / f'grapholog-{VERSION}-SHA256SUMS.txt'
    sums.write_text(''.join(f'{sha(path)}  {path.name}\n' for path in outputs))
    for path in [*outputs, sums]:
        print(f'{path.name}: {path.stat().st_size} bytes')
    print(f'Archive CRC and SHA-256 verified for {len(paths)} files.')


if __name__ == '__main__':
    main()
