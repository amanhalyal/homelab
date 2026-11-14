#!/usr/bin/env python3
"""Finish a clean initial scrub after a cooldown and exact approved deletions."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from snapraid_job import (ROOT, LOG, CONF, mounts, health, cooled_health, assert_sync_allowed,
                          parse_files, event, rotation_deadline)

APPROVAL=Path(os.environ.get('SNAPRAID_APPROVAL_FILE','/var/lib/snapraid/approved-removals.json'))


def tail(path):
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-131072))
        return f.read().decode(errors='replace')


def clean_stage(path):
    text=tail(path)
    counts=dict(re.findall(r'^summary:error_(file|io|data):(\d+)$',text,re.M))
    if counts!={'file':'0','io':'0','data':'0'} or not re.search(r'^summary:exit:ok$',text,re.M):
        raise ValueError('Prior stage is not clean: '+str(path))


def approved_diff(text, files, approval):
    counts=dict(re.findall(r'^summary:(equal|added|removed|updated|moved|copied|restored):(\d+)$',text,re.M))
    if len(counts)!=7 or not re.search(r'^summary:exit:diff$',text,re.M):
        raise ValueError('Incomplete or unexpected diff')
    if any(int(counts[k]) for k in ('added','updated','moved','copied','restored')):
        raise ValueError('Unapproved changes appeared since the review')
    expected={(e['disk'],e['path']):e['bytes'] for e in approval['removed_files']}
    actual={}
    for line in text.splitlines():
        if line.startswith('scan:remove:'):
            _,_,disk,path=line.split(':',3)
            if (disk,path) not in files:raise ValueError('Removed path absent from saved content')
            actual[(disk,path)]=files[(disk,path)]
    if actual!=expected or int(counts['removed'])!=len(expected):
        raise ValueError('Current removals differ from the exact operator-approved set')
    return {'removed':len(actual),'removed_bytes':sum(actual.values()),'scope':'exact operator-approved files only'}


def interrupted(signum,frame):
    raise KeyboardInterrupt('Maintenance interrupted')


def main():
    approval=json.loads(APPROVAL.read_text());prior=Path(approval['prior_verified_run'])
    assert prior.parent==LOG and prior.name=='20261101T103903-initial'
    clean_stage(prior/'03-sync.log');clean_stage(prior/'05-scrub.log')
    prior_status=tail(prior/'06-status.log')
    assert 'summary:has_unsynced:0\n' in prior_status and 'summary:has_unscrubbed:0\n' in prior_status and 'summary:has_bad:0:0:0\n' in prior_status
    with (ROOT/'maintenance.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run=LOG/(time.strftime('%Y%m%dT%H%M%S')+'-finalize-initial');run.mkdir(mode=0o700)
        signal.signal(signal.SIGINT,interrupted);signal.signal(signal.SIGTERM,interrupted)
        deadline=rotation_deadline();child=None;step=0
        def command(args,allowed=(0,)):
            nonlocal child,step
            if args == ['sync']:assert_sync_allowed()
            if time.time()>=deadline:raise ValueError('Immich rotation window reached')
            step+=1;log=run/f'{step:02d}-{args[-1]}.log';output=log.with_suffix('.stdout')
            print('Running '+' '.join(args)+'; log='+str(log),flush=True)
            with output.open('w') as out:
                child=subprocess.Popen(['snapraid','-c',CONF,'-l',str(log)]+args,stdout=out,stderr=subprocess.STDOUT)
                while True:
                    try:rc=child.wait(timeout=60);break
                    except subprocess.TimeoutExpired:
                        mounts()
                        if time.time()>=deadline:raise ValueError('Stopping before Immich rotation')
                child=None
            if rc not in allowed:raise ValueError('SnapRAID failed; see '+str(output))
            return log
        try:
            baseline=health(mounts());(run/'health-before.json').write_text(json.dumps(baseline,indent=2)+'\n')
            for p in [ROOT/'snapraid.content',Path('/mnt/atlas/.snapraid/content'),Path('/mnt/aegir/.snapraid/content')]:
                with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==approval['content_sha256'],'Content state changed since approval'
            files=parse_files(command(['list']).read_text())
            diff=command(['diff'],(0,2));accepted=approved_diff(diff.read_text(),files,approval)
            (run/'deletion-approval-applied.json').write_text(json.dumps({**accepted,'approval_file':str(APPROVAL)},indent=2)+'\n')
            clean_stage(command(['sync']))
            sync_time=time.time();(ROOT/'last-sync.json').write_text(json.dumps({'time':sync_time,'log':str(run)})+'\n')
            # The prior full scrub verified every old block. Verify every block
            # newly changed by this delta sync, not a second full-array pass.
            clean_stage(command(['-p','new','scrub']))
            status=tail(command(['status']))
            if not all(s in status for s in ('summary:has_unsynced:0\n','summary:has_unscrubbed:0\n','summary:has_bad:0:0:0\n')):
                raise ValueError('Array is not fully verified after incremental scrub')
            after=cooled_health(mounts());(run/'health-after.json').write_text(json.dumps(after,indent=2)+'\n')
            assert all(after[d]['crc']==baseline[d]['crc'] for d in baseline),'CRC counter increased'
            with command(['diff'],(0,2)).open() as f:final=f.read()
            if 'summary:exit:equal\n' not in final:raise ValueError('New changes appeared; final inventory is not clean')
            receipt={'mode':'initial','result':'success','completed_at':time.time(),'log':str(prior),
                     'full_scrub_completed_at':(prior/'05-scrub.stdout').stat().st_mtime,
                     'incremental_verification_log':str(run),'incremental_verification':'all newly synced blocks passed',
                     'accepted_deletions':accepted,'health_after_cooldown':after}
            (run/'result.json').write_text(json.dumps({**receipt,'log':str(run),'mode':'finalize-initial'},indent=2)+'\n')
            temp=ROOT/'initialized.tmp';temp.write_text(json.dumps(receipt,indent=2)+'\n');temp.replace(ROOT/'initialized.json')
            event('success','Initial full verification completed; exact approved media removals synced and changed blocks verified')
            print(json.dumps(receipt),flush=True)
        except BaseException as error:
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:child.wait(timeout=150)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            (run/'result.json').write_text(json.dumps({'result':'failure','time':time.time(),'error':str(error)},indent=2)+'\n')
            event('failure','Initial completion follow-up failed: '+str(error));raise

if __name__=='__main__':main()
