#!/usr/bin/env python3
"""Small authenticated client for the local kan.bn API."""
import json, os, urllib.request
from paths import KANBN_ENV_FILE

ENV_FILE = str(KANBN_ENV_FILE)

def _env():
    out = {}
    try:
        for line in open(ENV_FILE):
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1); out[k]=v
    except OSError: pass
    out.update({k:v for k,v in os.environ.items() if k.startswith('KAN_')})
    return out

class KanbnError(RuntimeError): pass
class CardText(str):
    def __new__(cls, title, public_id):
        obj=str.__new__(cls,title); obj.public_id=public_id; return obj

def _request(path, method='GET', payload=None):
    e=_env(); key=e.get('KAN_API_KEY');
    if not key: raise KanbnError('KAN_API_KEY is not configured')
    base=e.get('KAN_API_BASE_URL','http://127.0.0.1:3010/api/v1').rstrip('/')
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(base+path,data=data,method=method,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=15) as r: return json.loads(r.read())
    except Exception as exc: raise KanbnError(f'kan.bn API {method} {path} failed: {exc}') from exc

def board():
    e=_env(); bid=e.get('KAN_BOARD_PUBLIC_ID')
    if not bid: raise KanbnError('KAN_BOARD_PUBLIC_ID is not configured')
    return _request('/boards/'+bid)

def load_tasks():
    b=board(); result={'todo':[],'doing':[],'on_hold':[],'done':[]}
    for lst in b.get('lists',[]):
        n=lst.get('name','').lower()
        col=('todo' if n in ('todo','to do') else
             'doing' if n in ('doing','in progress') else
             'on_hold' if n in ('on hold','on_hold','onhold') else
             'done' if n=='done' else None)
        if col:
            result[col]=[CardText(c.get('title',''),c.get('publicId')) for c in lst.get('cards',[])]
    return result

def _card(col, idx):
    item=load_tasks()[col][idx]
    if not isinstance(item, CardText): raise KanbnError('card has no kan.bn ID')
    return item

def add_task(text):
    b=board(); lists={x.get('name','').lower():x.get('publicId') for x in b.get('lists',[])}
    lid=lists.get('todo') or lists.get('to do')
    if not lid: raise KanbnError('TODO list missing')
    _request('/cards','POST',{'listPublicId':lid,'title':text.strip(),'description':'','labelPublicIds':[],'memberPublicIds':[],'position':'end'})
    return load_tasks()

def _move_to(col, idx, target_name):
    item=_card(col,idx); b=board(); lists={x.get('name','').lower():x for x in b.get('lists',[])}
    target=lists.get(target_name)
    if not target: raise KanbnError('destination list missing')
    _request('/cards/'+item.public_id,'PUT',{'listPublicId':target['publicId']})
    return load_tasks()

def move_task(col, idx):
    targets={'todo':'doing','doing':'on hold','on_hold':'done'}
    if col=='done': raise ValueError("Can't advance from done")
    if col not in targets: raise ValueError('Invalid card column')
    return _move_to(col,idx,targets[col])

def resume_task(col, idx):
    if col != 'on_hold': raise ValueError("Only On Hold cards can be resumed")
    return _move_to(col,idx,'doing')

def remove_task(col, idx):
    item=_card(col,idx); _request('/cards/'+item.public_id,'DELETE'); return load_tasks()


COLUMN_NAMES = {'todo': ('todo', 'to do'), 'doing': ('doing', 'in progress'),
                'on_hold': ('on hold', 'on_hold', 'onhold'), 'done': ('done',)}


def _column_of(list_name):
    name = list_name.lower()
    return next((col for col, names in COLUMN_NAMES.items() if name in names), None)


def mutate_card(action, public_id, column=None):
    """Resolve a stable card ID against the current board before a mutation.

    Actions: move (one column forward), resume (On Hold -> Doing), remove (delete),
    move_to (any column, named by `column`: todo, doing, on_hold or done).
    """
    b = board()
    source = next((lst for lst in b.get('lists', [])
                   if any(c.get('publicId') == public_id for c in lst.get('cards', []))), None)
    if source is None: raise ValueError('Card no longer exists; refresh the board')
    if action == 'remove':
        _request('/cards/' + public_id, 'DELETE')
    elif action == 'move_to':
        if column not in COLUMN_NAMES: raise ValueError('Unknown destination column')
        if _column_of(source.get('name', '')) == column: return load_tasks()  # already there
        target = next((lst for lst in b.get('lists', []) if _column_of(lst.get('name', '')) == column), None)
        if target is None: raise ValueError('Destination column missing')
        _request('/cards/' + public_id, 'PUT', {'listPublicId': target['publicId']})
    else:
        name = source.get('name', '').lower()
        if action == 'resume' and name in ('on hold', 'on_hold', 'onhold'):
            target_name = 'doing'
        elif action == 'move':
            target_name = {'todo': 'doing', 'to do': 'doing', 'doing': 'on hold',
                           'in progress': 'on hold', 'on hold': 'done',
                           'on_hold': 'done', 'onhold': 'done'}.get(name)
        else: target_name = None
        if target_name is None: raise ValueError('This card cannot perform that action')
        aliases = {'doing': ('doing', 'in progress'), 'on hold': ('on hold', 'on_hold', 'onhold'), 'done': ('done',)}
        target = next((lst for lst in b.get('lists', []) if lst.get('name', '').lower() in aliases[target_name]), None)
        if target is None: raise ValueError('Destination column missing')
        _request('/cards/' + public_id, 'PUT', {'listPublicId': target['publicId']})
    return load_tasks()
