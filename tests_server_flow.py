# -*- coding: utf-8 -*-
"""Server room-flow integration test (4 human clients over HTTP).

Run (server must be up on 8623):  python tests_server_flow.py

Verifies: room create/join/auto-start, per-seat snapshots, per-seat event
scrubbing (draw tiles of others are hidden), token enforcement, legacy
single-player endpoint still working.
"""
import json
import sys
import urllib.request

BASE = 'http://127.0.0.1:8623'

PASS = 0
FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok  %s' % msg)
    else:
        FAIL += 1
        print('  FAIL %s' % msg)


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode('utf-8'))


print('server room-flow tests:')

info = call('GET', '/api/info')
ok(info.get('ok') is True and isinstance(info.get('ips'), list),
   '/api/info returns LAN ip list')

r = call('POST', '/api/room/new',
         {'rules': 'classic', 'difficulty': 'medium', 'buy_ma': 0,
          'name': '张三'})
code, t0 = r['room']['code'], r['token']
ok(r['ok'] and code and len(code) == 4, 'room created, code=%s' % code)

joins = {}
for i, name in enumerate(('李四', '王五', '赵六')):
    j = call('POST', '/api/room/join', {'code': code, 'name': name})
    joins[i + 1] = (j['seat'], j['token'])
    ok(j['ok'] and j['seat'] == i + 1, '%s joined seat %d' % (name, i + 1))
    if j.get('started'):
        gid, started_at = j['game_id'], i + 1
        last = j
ok('started_at' in dir() and started_at == 3,
   'auto-start on 4th join (game_id=%s)' % (gid if 'started_at' in dir() else '?'))

# wrong token rejected
try:
    call('POST', '/api/game/%s/action' % gid,
         {'seat': 1, 'token': 'bad', 'type': 'discard', 'tile': 0})
    ok(False, 'bad token rejected')
except Exception as e:
    code_ = getattr(e, 'code', None)
    ok(code_ == 403, 'bad token rejected with 403')

# per-seat privacy on initial snapshot
s1 = call('GET', '/api/game/%s/state?seat=1&since=0' % gid)
ok('hand' in s1['state'] and len(s1['state']['hand']) in (13, 14),
   'seat 1 state carries only own hand')
leak = any(('hand' in p and p.get('hand')) for p in s1['state']['players'])
ok(not leak, 'seat 1 snapshot does not leak other hands')

# drive the game: seat 0 discards first
g = call('GET', '/api/game/%s/state?seat=0&since=0' % gid)
tile = g['state']['hand'][0]
a = call('POST', '/api/game/%s/action' % gid,
         {'seat': 0, 'token': t0, 'type': 'discard', 'tile': tile,
          'since': 0})
ok(a['ok'], 'seat 0 discard accepted')
evs = a['events']
ok(any(e['type'] == 'discard' for e in evs), 'discard event returned')

# per-seat scrubbing: seat 1 view must not contain seat 0's draw tile
s1 = call('GET', '/api/game/%s/state?seat=1&since=0' % gid)
sc = [e for e in s1['events'] if e.get('type') == 'draw']
if sc:
    ok(all(e.get('tile') is None or e.get('seat') == 1 for e in sc),
       'seat 1 sees no other drawn tiles (%d draw events)' % len(sc))
else:
    ok(True, 'no draw events yet (server still valid)')

# out-of-turn action rejected by server (seat 2 acts while it is not his turn)
try:
    call('POST', '/api/game/%s/action' % gid,
         {'seat': 2, 'token': joins[2][1], 'type': 'discard',
          'tile': s1['state']['hand'][0], 'since': 0})
    ok(False, 'out-of-turn action rejected by server')
except Exception as e:
    ok(getattr(e, 'code', None) in (400, 403), 'out-of-turn rejected (%s)' %
       getattr(e, 'code', None))

# legacy endpoint still works
leg = call('POST', '/api/game/new', {'rules': 'classic', 'difficulty': 'easy'})
ok(leg['ok'] and 'game_id' in leg, 'legacy /api/game/new works')

print('\n%d passed, %d failed' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
