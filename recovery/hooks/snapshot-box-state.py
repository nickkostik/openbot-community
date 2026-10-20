"""Make a private recovery snapshot; use SQLite's backup API for live databases."""
from pathlib import Path
import importlib.util, json, os, shutil, sqlite3, time
sd=Path('/home/box/sand-data')
dest=sd/'recovery-snapshots'/time.strftime('%Y%m%d-%H%M%S')
dest.mkdir(parents=True, mode=0o700)
db_count=0
for src in (sd/'agents').iterdir():
 if not src.is_dir(): continue
 dst=dest/'agents'/src.name
 shutil.copytree(src,dst,symlinks=True,ignore=shutil.ignore_patterns('*.db','*-wal','*-shm'))
 for db in src.rglob('*.db'):
  if db.is_symlink(): continue
  target=dst/db.relative_to(src);target.parent.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as origin, sqlite3.connect(target) as backup:
   origin.backup(backup)
   backup.execute('PRAGMA journal_mode=DELETE');backup.commit()
   if backup.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Snapshot integrity failed: '+str(db))
  db_count+=1
config_names={'openbot-plan.json','openbot-bot-models.json','openbot-bot-models.json.before-remove-bulk-pins','openbot-mode','settings.json','secrets.json','openbot-local-migration.json','openbot-box-remint.json','computer-recovery-state.json','openbot-local-only.enable','openbot-pause.json','openbot-pause-bots.json','openbot-logs.json','openbot-injection.json','provider-maps.cjs'}
# Custom features add their own configuration files; keep those in private backups too.
config_names.update(p.name for p in sd.iterdir() if p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid() and
                    p.name.startswith('openbot-') and p.suffix in ('.json','.cjs','.enable','.hold'))
for name in sorted(config_names):
 src=sd/name
 if src.exists():shutil.copy2(src,dest/name)
for name in ['openbot','openbot-hooks','openbot-recovery','openbot-local-engine','openbot-upstream','local-migration-backups','agent-transcripts','transcript-publish','cloudflared','agent-attachments','agent-uploads','uploads','openbot-subagent-state','workflows/openbot-fleet']:
 src=sd/name
 if src.exists():shutil.copytree(src,dest/name,symlinks=True,ignore=shutil.ignore_patterns('node_modules','.git','compact-state'))
# The readable tree owns the data. Retain its compatibility alias/provenance too,
# so a restored snapshot does not leave legacy host writers at a missing path.
spec=importlib.util.spec_from_file_location('message_context_snapshot',Path(__file__).with_name('migrate-message-context.py'))
context=importlib.util.module_from_spec(spec);spec.loader.exec_module(context)
context.snapshot_compatibility_state(sd,dest)
(dest/'snapshot-manifest.json').write_text(json.dumps({'createdAtMs':int(time.time()*1000),'sqliteBackups':db_count,'integrity':'quick_check ok','credentialsIncluded':True,'privateOnly':True},indent=2))
print(str(dest),flush=True)
print('SQLite snapshots verified:',db_count,flush=True)
