# -*- coding: utf-8 -*-
"""Drive the live browser game to round end over HTTP, using the real
tokens extracted from the four browser tabs."""
import json
import sys
import urllib.request

BASE = 'http://127.0.0.1:8623'
GID = '703aa9407ac0'
SEATS = [('49304ebce214f4f8', 0), ('84e0054655ee176f', 1),
         ('f3d235a4d3cb2ffc', 2), ('9c500ffb22b75fda', 3)]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


class C:
    def __init__(self, token, seat):
        self.token, self.seat, self.last = token, seat, 0

    def poll(self):
        d = call('GET', '/api/game/%s/state?since=%d&seat=%d&token=%s'
                 % (GID, self.last, self.seat, self.token))
        self.last = d['next_seq']
        return d

    def act(self, a):
        a.update({'seat': self.seat, 'token': self.token, 'since': self.last})
        d = call('POST', '/api/game/%s/action' % GID, a)
        self.last = d['next_seq']
        return d


cs = [C(t, s) for t, s in SEATS]
round_over = None
n = 0
while n < 2000:
    d = cs[0].poll()
    st = d['state']
    if st['phase'] == 'round_end':
        round_over = st
        break
    acted = False
    for c in cs:
        dd = c.poll()
        stt = dd['state']
        if stt['phase'] == 'round_end':
            round_over = stt
            break
        leg = stt['legal']
        a = None
        if stt['phase'] == 'absence_wait' and leg.get('absence_options'):
            a = {'type': 'absence', 'suit': leg['absence_options'][0]}
        elif stt['phase'] == 'wait_human' and stt['turn'] == c.seat:
            if leg.get('zimo'):
                a = {'type': 'hu'}
            elif leg.get('gang_options'):
                a = {'type': 'gang', 'index': 0}
            elif leg.get('can_discard'):
                a = {'type': 'discard', 'tile': stt['hand'][0]}
        elif stt['phase'] == 'claim_wait' and stt.get('pending_seat') == c.seat:
            opts = leg.get('claims') or []
            a = {'type': 'claim',
                 'index': next((i for i, o in enumerate(opts)
                                if o['type'] == 'pass'), 0)}
        if a is None:
            continue
        if a['type'] == 'discard':
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
                        raise SystemExit('no discard for seat %d' % c.seat)
                    a = {'type': 'discard', 'tile': nxt}
        else:
            try:
                c.act(a)
            except Exception as e:
                print('rejected', c.seat, a, e)
                sys.exit(2)
        acted = True
        break
    n += 1
    if not acted:
        raise SystemExit('stalled at phase %s turn %s'
                         % (st['phase'], st['turn']))

if round_over is None:
    raise SystemExit('no round end reached')
print('ROUND END:', json.dumps({
    'round': round_over['round'], 'dealer': round_over['dealer'],
    'winner': round_over.get('winner'),
    'scores': [[p['seat'], p['name'], p['score']]
               for p in round_over['players']],
    'events': [e.get('type') for e in
               json.loads(json.dumps(round_over)) if False]}, ensure_ascii=False))
win_ev = [e for e in round_over['events'] if e.get('type') == 'win']
print('win events:', win_ev)
