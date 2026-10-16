"""Keep X readiness local: displays 80/81 overlap noVNC TCP ports 6080/6081."""
import hashlib
import os
import pathlib
import stat
import subprocess
import tempfile

MARK = 'openbot-window-readiness-v1'
ANCHOR = 'function windowAnswers(windowIndex) {'
OLD = 'xdpyinfo -display :${windowIndex} >/dev/null 2>&1'
NEW = 'timeout -k 1 2 xdpyinfo -display unix/:${windowIndex} >/dev/null 2>&1'
SCRIPT_OLD = 'display_alive() { xdpyinfo -display "${DISP}" >/dev/null 2>&1; }'
SCRIPT_NEW = 'display_alive() { timeout -k 1 2 xdpyinfo -display "unix/${DISP}" >/dev/null 2>&1; }'
SCRIPT = pathlib.Path('/usr/local/bin/start-window')
DESKTOP = pathlib.Path('/usr/local/bin/start-desktop.sh')
XVFB = pathlib.Path('/usr/local/bin/box-xvfb')
DBUS_OLD = 'dbus-launch --sh-syntax'
DBUS_NEW = 'env DISPLAY="unix/${DISPLAY#unix/}" timeout -k 1 3 dbus-launch --sh-syntax'
DESKTOP_OLD = 'xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1'
DESKTOP_NEW = 'timeout -k 1 2 xdpyinfo -display "unix/${DISPLAY#unix/}" >/dev/null 2>&1'
XVFB_OLD = 'DISPLAY=":${n}" xdpyinfo >/dev/null 2>&1'
XVFB_NEW = 'DISPLAY="unix/:${n}" timeout -k 1 2 xdpyinfo >/dev/null 2>&1'
SD = pathlib.Path('/home/box/sand-data')


def patch(source):
    if MARK in source:
        if source.count('/* openbot-latest-box-compatibility-v1 */') == 1:
            # A latest-host composition keeps an isolated legacy helper beside
            # the live upstream helper. Bound both; never leave the new helper
            # probing X through an overlapping VNC TCP port after an upgrade.
            if source.count(MARK) != 1 or source.count(ANCHOR) != 1:
                raise RuntimeError('Ambiguous compatibility window readiness patch')
            if source.count(NEW) == 2 and source.count(OLD) == 2:
                return source.replace(OLD, NEW)
            if source.count(NEW) == 4 and OLD not in source:
                return source
            raise RuntimeError('Incomplete compatibility window readiness patch')
        if source.count(MARK) != 1 or source.count(NEW) != 2 or OLD in source:
            raise RuntimeError('Incomplete native window readiness patch')
        return source
    if source.count(ANCHOR) != 1 or source.count(OLD) != 2 or NEW in source:
        raise RuntimeError('Native window readiness anchors changed')
    return source.replace(ANCHOR, '/* ' + MARK + ' */\n' + ANCHOR, 1).replace(OLD, NEW)


def patch_script(source):
    if source.count(SCRIPT_NEW) == 1 and SCRIPT_OLD not in source:
        return source
    if source.count(SCRIPT_OLD) != 1 or SCRIPT_NEW in source:
        raise RuntimeError('Native start-window readiness anchor changed')
    return source.replace(SCRIPT_OLD, SCRIPT_NEW, 1)


def patch_desktop_script(source):
    if source.count(DBUS_NEW) == 2 and source.count(DESKTOP_NEW) == 1 and DESKTOP_OLD not in source:
        return source
    if source.count(DBUS_OLD) != 2 or source.count(DESKTOP_OLD) != 1 or DBUS_NEW in source or DESKTOP_NEW in source:
        raise RuntimeError('Native desktop D-Bus/readiness anchors changed')
    return source.replace(DBUS_OLD, DBUS_NEW).replace(DESKTOP_OLD, DESKTOP_NEW)


def patch_xvfb_script(source):
    if source.count(XVFB_NEW) == 1 and XVFB_OLD not in source:
        return source
    if source.count(XVFB_OLD) != 1 or XVFB_NEW in source:
        raise RuntimeError('Native Xvfb readiness anchor changed')
    return source.replace(XVFB_OLD, XVFB_NEW)


def repair_script(path, transform):
    """CAS one fixed native script, keeping a private box-owned backup."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Untrusted native start-window script')
    before = path.read_bytes()
    updated = transform(before.decode()).encode()
    if updated == before:
        return {'state': 'healthy'}
    backup = SD / 'recovery-snapshots'
    if backup.is_symlink() or not backup.is_dir() or backup.stat().st_uid != os.getuid():
        raise RuntimeError('Native script backup directory is not owned')
    fd, staged = tempfile.mkstemp(prefix='.start-window-readiness-', dir=backup)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(updated); handle.flush(); os.fsync(handle.fileno())
        checked = subprocess.run(['/bin/bash', '-n', staged], capture_output=True, timeout=3)
        if checked.returncode:
            raise RuntimeError('Native readiness script syntax failed')
        program = '''import os,pathlib,hashlib,stat,sys,tempfile,time
p=pathlib.Path(sys.argv[4]);stage=pathlib.Path(sys.argv[1]);uid=int(sys.argv[3])
assert str(p) in ['/usr/local/bin/start-window','/usr/local/bin/start-desktop.sh','/usr/local/bin/box-xvfb']
i=p.lstat();si=stage.lstat()
assert stat.S_ISREG(i.st_mode) and i.st_uid==0 and not i.st_mode&0o022
assert stat.S_ISREG(si.st_mode) and si.st_uid==uid and si.st_size<65536
before=p.read_bytes();assert hashlib.sha256(before).hexdigest()==sys.argv[2]
backup=stage.parent/(p.name+'-before-'+str(time.time_ns())+'.sh')
with backup.open('xb') as f:
 os.fchmod(f.fileno(),0o600);os.fchown(f.fileno(),uid,-1);f.write(before);f.flush();os.fsync(f.fileno())
fd,tmp=tempfile.mkstemp(prefix='.start-window-readiness-',dir=p.parent)
try:
 with os.fdopen(fd,'wb') as f:
  f.write(stage.read_bytes());os.fchmod(f.fileno(),i.st_mode&511);os.fchown(f.fileno(),0,i.st_gid);f.flush();os.fsync(f.fileno())
 assert p.read_bytes()==before
 os.replace(tmp,p)
 d=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(d)
 finally:os.close(d)
finally:pathlib.Path(tmp).unlink(missing_ok=True)
'''
        applied = subprocess.run(['sudo', '-n', '/usr/bin/python3', '-c', program, staged,
                                  hashlib.sha256(before).hexdigest(), str(os.getuid()), str(path)],
                                 capture_output=True, timeout=10)
        if applied.returncode or path.read_bytes() != updated:
            raise RuntimeError('Native readiness script CAS failed')
        return {'state': 'repaired', 'sha256': hashlib.sha256(updated).hexdigest()}
    finally:
        pathlib.Path(staged).unlink(missing_ok=True)


def ensure_native_script():
    return {path.name: repair_script(path, transform) for path, transform in
            [(SCRIPT, patch_script), (DESKTOP, patch_desktop_script), (XVFB, patch_xvfb_script)]}
