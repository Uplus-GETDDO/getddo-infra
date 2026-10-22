#!/usr/bin/env python3
"""Package reviewed Infra SHA, clean backend checkout and pinned CI image for Stage."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile


def command(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=300)
    if result.returncode:raise RuntimeError('Bundle preparation failed: '+Path(args[0]).name)
    return result.stdout.strip()


def file_sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def package(infra, infra_sha, backend, backend_sha, artifact, output):
    if not all(re.fullmatch(r'[a-f0-9]{40}',sha) for sha in (infra_sha,backend_sha)):
        raise ValueError('Full reviewed commit identities required')
    for repo,sha in ((infra,infra_sha),(backend,backend_sha)):
        if command(['git','-C',str(repo),'rev-parse','HEAD'])!=sha:
            raise ValueError('Checkout SHA differs from reviewed source')
        if command(['git','-C',str(repo),'status','--porcelain','--untracked-files=no']):
            raise ValueError('Tracked changes are not part of the reviewed source')
    image_id=(artifact/'image-id.txt').read_text().strip()
    checksum=(artifact/'image.tar.gz.sha256').read_text().split()[0]
    spec=importlib.util.spec_from_file_location('stage_artifact_validation',backend/'infra/scripts/deploy-staging.py')
    validation=importlib.util.module_from_spec(spec);spec.loader.exec_module(validation)
    validation.validate_artifact(backend,artifact,backend_sha,image_id,checksum)
    if output.exists():raise ValueError('Refuse to overwrite an existing source bundle')
    with tempfile.TemporaryDirectory(prefix='getddo-stage-package-') as name:
        temp=Path(name);source=temp/'source';source.mkdir()
        archive=temp/'infra.tar'
        command(['git','-C',str(infra),'archive','--format=tar','--output',str(archive),infra_sha])
        with tarfile.open(archive) as bundle:
            for item in bundle.getmembers():
                if not (item.isfile() or item.isdir()) or Path(item.name).is_absolute() or '..' in Path(item.name).parts:
                    raise ValueError('Immutable source archive contains unsafe entries')
            bundle.extractall(source,filter='data')
        required=['scripts/staging_hooks/prepare.py','scripts/staging_hooks/collect.py','scripts/staging_hooks/detach.py',
                  'scripts/staging_runner.py','loadtest/control.py','terraform/staging/.terraform.lock.hcl']
        if any(not (source/path).is_file() for path in required):
            raise ValueError('Reviewed source does not contain complete Stage runtime')
        code=source/'backend/source';code.parent.mkdir()
        command(['git','clone','--no-local','--no-checkout',str(backend),str(code)])
        command(['git','-C',str(code),'config','core.autocrlf','false'])
        command(['git','-C',str(code),'checkout','--detach',backend_sha])
        command(['git','-C',str(code),'remote','set-url','origin','https://github.com/Uplus-GETDDO/getddo-be.git'])
        image=source/'backend/image';image.mkdir()
        for filename in ('image.tar.gz','image.tar.gz.sha256','image-id.txt'):
            shutil.copyfile(artifact/filename,image/filename)
        metadata={'source_sha':backend_sha,'image_id':image_id,'archive_sha256':checksum,
                  'artifact_dir':'backend/image','checkout_dir':'backend/source'}
        (source/'scripts/staging_hooks/backend.json').write_text(json.dumps(metadata,indent=2)+'\n')
        with tarfile.open(output,'w:gz') as bundle:
            for path in sorted(source.rglob('*')):
                if path.is_symlink():raise ValueError('Bundle cannot contain filesystem links')
                if path.is_file():bundle.add(path,arcname=str(path.relative_to(source)),recursive=False)
    output.chmod(0o600)
    return {'infra_source_sha':infra_sha,'backend_source_sha':backend_sha,'image_id':image_id,
            'image_archive_sha256':checksum,'bundle_sha256':file_sha(output),'bundle':str(output)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infra',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--infra-sha',required=True)
    parser.add_argument('--backend',type=Path,required=True)
    parser.add_argument('--backend-sha',required=True)
    parser.add_argument('--artifact-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(package(args.infra,args.infra_sha,args.backend,args.backend_sha,args.artifact_dir,args.output)))


if __name__=='__main__':main()
