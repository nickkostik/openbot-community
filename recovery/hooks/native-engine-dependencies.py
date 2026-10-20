"""Restore two removed vendor dependency links for an explicitly retained BOX engine.

Never downloads packages, replaces installed modules, or changes an engine/mode.
The update image retains these tested packages beside its execution daemon.
"""
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import uuid

PACKAGES = {'tree-sitter': '0.21.1', 'tree-sitter-bash': '0.21.0'}
DAEMON_MODULES = pathlib.Path('/exec-daemon/node_modules')
HOST_MODULES = pathlib.Path('/opt/sand/sand-host/node_modules')
NODE = pathlib.Path('/exec-daemon/node')


def regular_directory(path, owners):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
        raise RuntimeError('Untrusted native dependency directory')
    return info


def package_metadata(path, name, version, owners):
    regular_directory(path, owners)
    descriptor = os.open(path / 'package.json', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022
                or not 0 < info.st_size <= 65536):
            raise RuntimeError('Untrusted native dependency metadata')
        raw = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    value = json.loads(raw)
    if value.get('name') != name or value.get('version') != version:
        raise RuntimeError('Native dependency version requires review')
    return hashlib.sha256(raw).hexdigest()


def probe_packages(source, names):
    # Import only the reviewed packages in a short-lived process. This catches
    # missing native addons/ABI failures without loading or restarting the host.
    script = '''const {createRequire}=require('node:module');
const r=createRequire(process.argv[1]);
for(const name of JSON.parse(process.argv[2])) r(name);'''
    result = subprocess.run([str(NODE), '-e', script, str(source / 'package.json'), json.dumps(names)],
                            capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Retained native dependency import failed')


def repair(sd, engine, *, apply=False, source=None, target=None, vendor_owners=None, probe=None):
    sd = pathlib.Path(sd)
    source = pathlib.Path(source) if source is not None else DAEMON_MODULES
    target = pathlib.Path(target) if target is not None else HOST_MODULES
    owners = {0} if vendor_owners is None else set(vendor_owners)

    def selected():
        return ((sd / 'openbot-mode').read_text().strip() == 'custom'
                and (sd / 'openbot-local-only.enable').is_file())

    if not selected():
        return {'state': 'native-dependencies'}
    policy = engine.policy(sd)
    if policy is None:
        return {'state': 'not-selected'}
    target_info = regular_directory(target, {0, os.getuid()})
    missing = [name for name in PACKAGES if not os.path.lexists(target / name)]
    if not missing:
        return {'state': 'present'}
    # Verify the retained engine before granting any compatibility links.
    directory = engine.artifact_directory(sd, policy)
    raw = engine.owned_bytes(directory / 'stock.cjs', engine.MAX_BYTES)
    if hashlib.sha256(raw).hexdigest() != policy['stockSha256']:
        raise RuntimeError('Retained engine dependency authority changed')
    engine.require_runner(raw.decode('utf8'))
    regular_directory(source, owners)
    metadata = {name: package_metadata(source / name, name, PACKAGES[name], owners) for name in missing}
    (probe or probe_packages)(source, missing)
    report = {'state': 'validated', 'packages': missing, 'packageMetadataSha256': metadata}
    if not apply:
        return report
    created = []
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_info = os.fstat(descriptor)
    try:
        if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid not in (0, os.getuid())
                or directory_info.st_mode & 0o022
                or (directory_info.st_dev, directory_info.st_ino) != (target_info.st_dev, target_info.st_ino)):
            raise RuntimeError('Native module directory changed before publication')
        for name in missing:
            if not selected() or engine.policy(sd) != policy:
                raise RuntimeError('Local dependency policy changed before publication')
            if package_metadata(source / name, name, PACKAGES[name], owners) != metadata[name]:
                raise RuntimeError('Native dependency changed before publication')
            # No rename/overwrite: an intervening installer remains authoritative.
            # Capture our inode BEFORE publishing the destination. Statting the
            # destination after publication could record an installer's inode.
            stage = '.openbot-native-dependency-' + uuid.uuid4().hex
            os.symlink(str(source / name), stage, dir_fd=descriptor)
            info = os.stat(stage, dir_fd=descriptor, follow_symlinks=False)
            try:
                os.link(stage, name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
                created.append((name, info.st_dev, info.st_ino))
            finally:
                held = os.stat(stage, dir_fd=descriptor, follow_symlinks=False)
                if (held.st_dev, held.st_ino) == (info.st_dev, info.st_ino):
                    os.unlink(stage, dir_fd=descriptor)
        if not selected() or engine.policy(sd) != policy:
            raise RuntimeError('Local dependency policy changed during publication')
        current = target.lstat()
        if (current.st_dev, current.st_ino) != (directory_info.st_dev, directory_info.st_ino):
            raise RuntimeError('Native module directory changed during publication')
        os.fsync(descriptor)
    except BaseException:
        # Roll back only links created here, preserving concurrent replacements.
        for name, device, inode in reversed(created):
            try:
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (current.st_dev, current.st_ino) == (device, inode):
                os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)
        raise
    finally:
        os.close(descriptor)
    return {**report, 'state': 'repaired'}
