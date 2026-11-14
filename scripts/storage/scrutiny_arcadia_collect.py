#!/usr/bin/env python3
"""Give Scrutiny host-side access to the three disks without broad /dev exposure."""
import json
from pathlib import Path
import subprocess
import urllib.request
from snapraid_job import DISKS
from storage_observer import find_mount,smart


def main():
    devices=[]
    for name,(mount,uuid,serial) in DISKS.items():
        record=find_mount(mount,uuid)
        if not record:raise ValueError(name+': expected filesystem is not mounted')
        parent=subprocess.check_output(['lsblk','-ndo','PKNAME',record['source']],text=True,timeout=10).strip()
        if not parent or '/' in parent:raise ValueError('Unknown physical device')
        device='/dev/'+parent;smart(device,serial)
        devices.append({'device':device,'type':'sat'})
    config={'version':1,'devices':devices,'allow_listed_devices':[d['device'] for d in devices],
            'api':{'endpoint':'http://127.0.0.1:8087'},'commands':{'metrics_smartctl_bin':'/usr/sbin/smartctl'}}
    directory=Path('/run/arcadia-scrutiny');directory.mkdir(mode=0o700,exist_ok=True)
    file=directory/'collector.yaml';file.write_text(json.dumps(config,indent=2)+'\n')
    subprocess.run(['/usr/local/libexec/scrutiny-collector-metrics','run','--config',str(file)],check=True,timeout=180)
    summary=json.load(urllib.request.urlopen('http://127.0.0.1:8087/api/summary',timeout=15))['data']['summary']
    found={value['device']['serial_number'] for value in summary.values()}
    assert all(serial in found for _,_,serial in DISKS.values()),'A named disk is absent from Scrutiny after collection'
    print('Scrutiny contains Atlas, Aegir and Argus by physical serial')

if __name__=='__main__':main()
