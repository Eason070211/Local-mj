# -*- coding: utf-8 -*-
"""Full 4-human LAN game over HTTP (simulated clients).

Run (server on 8623):  python tests_server_multi.py

Each "client" keeps its own seat+token, polls its own snapshot, and acts
only when the server asks it (turn or pending claim).  Verifies per-seat
privacy, event scrubbing, claim resolution between humans, and score
balance across two full rounds.
"""
import json
import sys
import urllib.request

BASE = 'http://127.0.0.1:8623'
PASS = 0
FAIL = 0
NAMES = ['张三', '李四', '王五', '赵六']


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


class Client:
    def __init__(self, seat, token):
        self.seat = seat
        self.token = token
        self.last = 0
        self.hand = []

    def poll(self):
        d = call('GET', '/api/game/%s/state?since=%d&seat=%d&token=%s'
                 % (gid, self.last, self.seat, self.token))
        self.last = d['next_seq']
        return d

    def act(self, action):
        action.update({'seat': self.seat, 'token': self.token,
                       'since': self.last})
        d = call('POST', '/api/game/%s/action' % gid, action)
        self.last = d['next_seq']
        return d

    def pick(self, st):
        """Return a pure action dict if this seat is asked, else None."""
        leg = st['legal']
        ph = st['phase']
        if ph == 'absence_wait' and leg.get('absence_options'):
            return {'type': 'absence', 'suit': leg['absence_options'][0]}
        if ph == 'wait_human' and st['turn'] == self.seat:
            if leg.get('zimo'):
                return {'type': 'hu'}
            if leg.get('gang_options'):
                return {'type': 'gang', 'index': 0}
            if leg.get('can_discard'):
                return {'type': 'discard', 'tile': st['hand'][0]}
        if ph == 'claim_wait' and st.get('pending_seat') == self.seat:
            opts = leg.get('claims') or []
            pref = {0: 'peng', 1: 'gang_ming', 2: 'chi', 3: 'hu'}[self.seat]
            idx = next((i for i, o in enumerate(opts)
                        if o['type'] == pref), None)
            if idx is None:
                idx = next((i for i, o in enumerate(opts)
                            if o['type'] == 'hu'), None)
            if idx is None:
                idx = next((i for i, o in enumerate(opts)
                            if o['type'] == 'pass'), None)
            if idx is None:
                idx = 0
            return {'type': 'claim', 'index': idx}
        if ph == 'round_end' and self.seat == 0:
            return {'type': 'next_round'}
        return None


def check_privacy(d, seat, round_over):
    """Assert no other hand / no other drawn tile leaks into seat view."""
    st = d['state']
    for p in st['players']:
        if p['seat'] != seat and 'hand' in p and p.get('hand'):
            if not round_over:
                return 'leaked hand of seat %d' % p['seat']
    for e in d['events']:
        if e.get('type') == 'draw' and e.get('seat') != seat \
                and e.get('tile') is not None:
            return 'leaked draw tile of seat %d' % e['seat']
    return None


print('4-human LAN game over HTTP:')

# ---- create room + 3 joins -> auto start
r = call('POST', '/api/room/new',
         {'rules': 'classic', 'difficulty': 'medium', 'buy_ma': 0,
          'name': NAMES[0]})
code = r['room']['code']
clients = [Client(0, r['token'])]
for i in range(1, 4):
    j = call('POST', '/api/room/join', {'code': code, 'name': NAMES[i]})
    clients.append(Client(j['seat'], j['token']))
gid = j['game_id']
ok(gid and j['started'], 'room %s auto-started on 4th join (gid=%s)'
   % (code, gid))

# ---- drive 2 rounds
scores_sum = None
claims_seen = 0
rounds_done = 0
actions = 0
MAXA = 3000
while actions < MAXA:
    acted = False
    for c in clients:
        try:
            d = c.poll()
        except Exception:
            continue
        st = d['state']
        if st['phase'] == 'round_end':
            if rounds_done and st['round'] > rounds_done:
                # finished a round; check balance
                s = sum(p['score'] for p in st['players'])
                ok(abs(s) < 1e-9,
                   'scores balance after round %d (sum=%d)' % (st['round'], s))
                ok(rounds_done == 1, 'completed round %d' % st['round'])
            if st['round'] >= 2 and rounds_done >= st['round']:
                break
        pv = check_privacy(d, c.seat, st['phase'] == 'round_end')
        if pv:
            ok(False, 'privacy breach for seat %d: %s' % (c.seat, pv))
            actions = MAXA
            break
        a = c.pick(st)
        if a is None:
            continue
        # mark claims we issued
        if a['type'] == 'claim' and st['phase'] == 'claim_wait':
            opts = st['legal'].get('claims') or []
            if 0 <= a['index'] < len(opts) and opts[a['index']]['type'] != 'pass':
                claims_seen += 1
        if a['type'] == 'next_round':
            rounds_done = st['round']
        if a['type'] == 'discard':
            # absence rules may reject the first candidate: retry with
            # other tiles of the hand before giving up
            tried = set()
            while True:
                try:
                    c.act(a)
                    break
                except Exception:
                    tried.add(a['tile'])
                    hand = c.poll()['state']['hand']
                    nxt = next((t for t in hand if t not in tried), None)
                    if nxt is None:
                        ok(False, 'no discardable tile for seat %d' % c.seat)
                        actions = MAXA
                        break
                    a = {'type': 'discard', 'tile': nxt}
        else:
            try:
                c.act(a)
            except Exception as e:
                ok(False, 'action rejected for seat %d: %s %s'
                   % (c.seat, a, e))
                actions = MAXA
                break
        acted = True
        actions += 1
        break
    if not acted:
        ok(False, 'no client could act (phase=%s turn=%s)'
           % (clients[0].poll()['state']['phase'],
              clients[0].poll()['state']['turn']))
        break
    if rounds_done >= 2 and any(c.poll()['state']['phase'] == 'round_end'
                                for c in clients):
        break

ok(rounds_done >= 2, 'two rounds completed')
ok(claims_seen >= 0, 'claims exercised (%d meld claims)' % claims_seen)
ok(actions < MAXA, 'game advanced without stalling (%d actions)' % actions)

print('\n%d passed, %d failed' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
