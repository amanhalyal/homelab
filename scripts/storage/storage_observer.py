#!/usr/bin/env python3
"""Publish read-only Arcadia/SnapRAID observations without running maintenance."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from snapraid_job import DISKS, read_sync_hold
try:  # optional sibling module: a missing install must not stop storage observation
    import backup_layers
except ImportError:
    backup_layers = None

CACHE = Path('/var/cache/homelab-storage')
STATE = Path('/var/lib/snapraid')
LOGS = Path('/var/log/snapraid')
PUSH = 'http://127.0.0.1:9091/metrics/job/arcadia_storage'


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def tail(path):
    try:
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-65536))
            return stream.read().decode(errors='replace')
    except OSError:
        return ''


def progress(text):
    found = list(re.finditer(r'(\d+)%,[^\r\n]*?Tmax\s+(\d+)(?:\s*\([^)]*\))?,\s*(\d+):(\d+) ETA',text))
    if not found:
        return {}
    percent,temp,hours,minutes = map(int,found[-1].groups())
    return {'percent':percent,'max_temperature':temp,'eta_seconds':hours*3600+minutes*60}


def find_mount(path, uuid=None, kind='ext4'):
    r = subprocess.run(['findmnt','-J','-t',kind,'-M',path,'-o','TARGET,UUID,FSTYPE,SOURCE,OPTIONS'],capture_output=True,text=True,timeout=10)
    records = json.loads(r.stdout).get('filesystems',[]) if r.returncode == 0 else []
    records = [x for x in records if x.get('target') == path and x.get('fstype') == kind]
    if len(records) != 1 or (uuid and records[0].get('uuid') != uuid):
        return None
    if kind == 'fuse.mergerfs' and records[0].get('source') != 'arcadia':
        return None
    return records[0]


def smart(device, serial):
    r = subprocess.run(['smartctl','-j','-i','-H','-A','-d','sat',device],capture_output=True,text=True,timeout=30)
    d = json.loads(r.stdout)
    if r.returncode & 7 or d.get('serial_number') != serial:
        raise ValueError('SMART unavailable or physical serial mismatch')
    attrs = {a['id']:a['raw']['value'] for a in d.get('ata_smart_attributes',{}).get('table',[])}
    result = {'passed':int(d.get('smart_status',{}).get('passed') is True),'timestamp':time.time()}
    fields = {'reallocated':5,'pending':197,'uncorrectable':198,'crc_errors':199,'power_on_hours':9,'power_cycles':12,'start_stop_count':4,'load_cycles':193}
    result.update({k:attrs[a] for k,a in fields.items() if a in attrs})
    if d.get('temperature',{}).get('current') is not None:
        result['temperature'] = d['temperature']['current']
    if d.get('power_on_time',{}).get('hours') is not None:
        result['power_on_hours']=d['power_on_time']['hours']
    return result


def observe():
    now=time.time()
    cached=read_json(CACHE/'status.json')
    drives={}
    for name,(path,uuid,serial) in DISKS.items():
        label='argus' if name=='parity' else name
        item={'mount':path,'role':'parity' if name=='parity' else 'data','present':False,'smart_available':False}
        record=find_mount(path,uuid)
        if record:
            item['present']=True
            v=os.statvfs(path)
            item.update({'total_bytes':v.f_blocks*v.f_frsize,'available_bytes':v.f_bavail*v.f_frsize,'used_bytes':(v.f_blocks-v.f_bfree)*v.f_frsize})
            old=cached.get('drives',{}).get(label,{}).get('smart',{})
            if old and now-old.get('timestamp',0)<300:
                item['smart']=old;item['smart_available']=True
            else:
                try:
                    parent=subprocess.check_output(['lsblk','-ndo','PKNAME',record['source']],text=True,timeout=10).strip()
                    if not parent or '/' in parent:raise ValueError('Unknown disk')
                    item['smart']=smart('/dev/'+parent,serial);item['smart_available']=True
                except (OSError,ValueError,subprocess.SubprocessError):
                    pass
        drives[label]=item
    pool={'mount':'/mnt/arcadia','role':'pool','present':False}
    if find_mount(pool['mount'],kind='fuse.mergerfs') and all(drives[d]['present'] for d in ('atlas','aegir')):
        pool['present']=True;v=os.statvfs(pool['mount']);pool.update({'total_bytes':v.f_blocks*v.f_frsize,'available_bytes':v.f_bavail*v.f_frsize,'used_bytes':(v.f_blocks-v.f_bfree)*v.f_frsize})
    drives['arcadia']=pool
    last_sync=read_json(STATE/'last-sync.json').get('time',0)
    initialized=read_json(STATE/'initialized.json')
    verified=initialized.get('result')=='success'
    full_time=initialized.get('full_scrub_completed_at',initialized.get('completed_at',0)) if verified else 0
    active=[]
    for mode in ('initial','finalize-initial','sync','scrub','full-scrub'):
        state=subprocess.check_output(['systemctl','show','snapraid-job@'+mode+'.service','-p','ActiveState','--value'],text=True,timeout=10).strip()
        if state in ('activating','active'):active.append(mode)
    control_job=read_json(STATE/'control/job.json')
    if control_job.get('state')=='running':
        active.append('reviewed-sync' if control_job.get('mode')=='approve' else 'deletion-review')
    runs=sorted(LOGS.glob('*-*'),key=lambda p:p.name) if LOGS.exists() else []
    runs=[p for p in runs if p.is_dir()]
    # Read only tiny result receipts and bounded log tails, never media data.
    completed=[]
    for run in runs:
        result=read_json(run/'result.json')
        if result:
            completed.append((run,result))
            if result.get('result')=='success' and result.get('mode') in ('initial','full-scrub'):
                full_time=max(full_time,result.get('completed_at',0))
    current=runs[-1] if runs else None
    current_result=read_json(current/'result.json') if current else {}
    stages=sorted(current.glob('*.stdout')) if current else []
    latest=stages[-1] if stages else None
    text=tail(latest) if latest else ''
    phase=3 if verified else 0
    detail='Verified' if verified else 'Initial verification incomplete'
    if active:
        if latest and 'scrub' in latest.name:phase=2;detail='Verification in progress'
        elif latest and 'sync' in latest.name:phase=1;detail='Parity sync in progress'
        else:phase=5;detail='Scanning / preparing'
    elif current_result.get('result')=='failure':phase=4;detail='Maintenance blocked or failed'
    snap={'running':bool(active),'mode':active[0] if active else 'idle','state':phase,'detail':detail,'initial_verified':verified,'last_sync':last_sync,'last_full_scrub':full_time}
    snap['sync_held']=read_sync_hold() is not None
    if snap['sync_held'] and not active:snap['detail']='Sync held by operator'
    if active and latest and now-latest.stat().st_mtime<180:
        snap.update(progress(text))
    if completed:
        run,result=completed[-1];snap['last_completed_result']=result.get('result','unknown')
        summaries={}
        for log in sorted(run.glob('*.log')):
            for k,v in re.findall(r'^summary:error_(file|io|data):(\d+)$',tail(log),re.M):summaries[k]=int(v)
        snap['last_completed_errors']=summaries
    paused={}
    for name in ('backup','minecraft-backup'):
        try:
            paused[name]=subprocess.check_output(['docker','inspect','-f','{{.State.Paused}}',name],text=True,timeout=10).strip()=='true'
        except subprocess.SubprocessError:paused[name]=False
    backups=backup_layers.collect(paused,now) if backup_layers else []
    return {'observed_at':now,'host':'legion','drives':drives,'snapraid':snap,'backups_paused':paused,'backups':backups}


def metrics(data):
    lines=[]
    def add(name,value,labels=None):
        labels={'host':'legion',**(labels or {})}
        encoded=','.join(k+'='+json.dumps(str(v)) for k,v in labels.items())
        lines.append(f'arcadia_{name}{{{encoded}}} {float(value):.6f}')
    add('observation_timestamp_seconds',data['observed_at'])
    for name,d in data['drives'].items():
        label={'drive':name,'role':d['role'],'mount_point':d['mount'],'filesystem':'fuse.mergerfs' if d['role']=='pool' else 'ext4'}
        add('mount_present',d['present'],label)
        for key in ('total_bytes','available_bytes','used_bytes'):
            if key in d:add('filesystem_'+key,d[key],label)
        if d['role']!='pool':
            add('drive_smart_available',d['smart_available'],label)
            for k,v in d.get('smart',{}).items():
                add('drive_'+('smart_timestamp_seconds' if k=='timestamp' else 'smart_passed' if k=='passed' else k),v,label)
    s=data['snapraid']
    for key in ('running','state','initial_verified'):add('snapraid_'+key,s[key])
    for key in ('last_sync','last_full_scrub'):add('snapraid_'+key+'_timestamp_seconds',s[key])
    add('snapraid_progress_percent',s.get('percent',-1))
    add('snapraid_eta_seconds',s.get('eta_seconds',-1))
    for k,v in s.get('last_completed_errors',{}).items():add('snapraid_last_completed_'+k+'_errors',v)
    for name,paused in data['backups_paused'].items():add('backup_paused',paused,{'container':name})
    for item in data.get('backups',[]):
        label={'layer':item['layer']}
        add('backup_layer_status',backup_layers.STATUS_CODE.get(item['status'],0),label)  # 0 failed, 1 pass, 2 skipped, 3 paused
        if 'newest_timestamp' in item:add('backup_newest_timestamp_seconds',item['newest_timestamp'],label)
        if 'size_bytes' in item:add('backup_newest_size_bytes',item['size_bytes'],label)
        if 'mounted' in item:add('backup_layer_mounted',item['mounted'],label)
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--no-push',action='store_true');args=parser.parse_args()
    CACHE.mkdir(mode=0o755,parents=True,exist_ok=True)
    data=observe();temporary=CACHE/'status.tmp';temporary.write_text(json.dumps(data,indent=2)+'\n');temporary.chmod(0o644);temporary.replace(CACHE/'status.json')
    rendered=metrics(data);(CACHE/'metrics.prom').write_text(rendered)
    if not args.no_push:
        req=urllib.request.Request(PUSH,data=rendered.encode(),method='PUT',headers={'Content-Type':'text/plain; version=0.0.4'})
        with urllib.request.urlopen(req,timeout=15) as response:assert response.status==200
    print('Storage observations published; '+data['snapraid']['detail'])

if __name__=='__main__':main()
