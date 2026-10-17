# -*- coding: utf-8 -*-
"""Local HTTP server for the Mahjong game (single-player + LAN multiplayer).

Run:  python server.py [--port 8623] [--host 0.0.0.0] [--no-open]

Pure Python standard library (no pip installs).  The server:

  * deals with a cryptographically secure shuffle unless a seed is given,
  * NEVER exposes other players' hands or draws (privacy is enforced by
    the engine's snapshot_for() and per-seat event scrubbing),
  * auto-advances all AI turns and returns an event log the web client
    replays with animations.

LAN multiplayer ("rooms"):
  * one device runs this server — that device is the game host; friends
    open  http://<host-lan-ip>:<port>  in their browsers (mobile hotspot
    works too, just join the hotspot and share its IP);
  * POST /api/room/new  -> create a room (creator = seat 0, can start
    early and fill empty seats with AI);
  * POST /api/room/join  -> join a free seat with a name; the game
    auto-starts when 4 humans have joined;
  * every action must carry {seat, token}; draw events are scrubbed per
    seat so no client ever sees another player's drawn tile.
"""
import argparse
import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from mahjong import Game, AiPlayer, RULESETS, DIFFICULTIES

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, 'static')
MAX_GAMES = 24
ROOM_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # no 0/O/1/I/L

CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
}


def room_code():
    return ''.join(secrets.choice(ROOM_ALPHABET) for _ in range(4))


def lan_ips():
    """Best-effort list of non-loopback IPv4 addresses of this machine."""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
        except Exception:
            pass
    return ips


def scrub_events(events, seat):
    """Hide the drawn-tile content of other seats from this seat."""
    out = []
    for e in events:
        if e.get('type') == 'draw' and e.get('seat') != seat \
                and 'tile' in e:
            e = dict(e)
            e['tile'] = None
        out.append(e)
    return out


def _pack(g, since, seat):
    with g.lock:
        snap = g.snapshot_for(seat)
        if since is None or since < g.events_start:
            events = list(g.events)
        else:
            events = [e for e in g.events if e['seq'] >= since]
        return {'state': snap, 'events': scrub_events(events, seat),
                'next_seq': g.seq}


class Room:
    def __init__(self, code, rules, difficulty, buy_ma, host_name):
        self.code = code
        self.rules = rules
        self.difficulty = difficulty
        self.buy_ma = buy_ma
        self.seats = [None] * 4
        self.seats[0] = {'name': host_name or '玩家1',
                         'token': secrets.token_hex(8), 'host': True}
        self.gid = None
        self.phase = 'lobby'          # lobby -> playing

    def info(self):
        return {
            'code': self.code, 'rules': self.rules,
            'difficulty': self.difficulty, 'buy_ma': self.buy_ma,
            'phase': self.phase, 'gid': self.gid,
            'seats': [None if s is None else
                      {'seat': i, 'name': s['name'], 'is_human': True,
                       'is_host': bool(s.get('host', False))}
                      for i, s in enumerate(self.seats)],
        }

    def free_seats(self):
        return [i for i, s in enumerate(self.seats) if s is None]

    def is_full(self):
        return all(s is not None for s in self.seats)


class GameManager:
    def __init__(self, policy=None):
        self.games = {}               # gid -> Game
        self.order = []               # LRU
        self.tokens = {}              # gid -> {seat: token}
        self.rooms = {}               # code -> Room
        self.lock = threading.RLock()
        self.policy = policy or {}

    # ------------------------------------------------------------ legacy
    def create(self, rules, difficulty, buy_ma=0, seed=None):
        if rules not in RULESETS:
            raise ValueError('unknown rules: %s' % rules)
        if difficulty not in DIFFICULTIES:
            raise ValueError('unknown difficulty: %s' % difficulty)
        buy_ma = int(buy_ma)
        if buy_ma < 0 or buy_ma > 4:
            raise ValueError('buy_ma out of range')
        g = Game(rules, difficulty, buy_ma=buy_ma, seed=seed)
        for s in range(1, 4):
            pol = self.policy if difficulty == 'hard' else None
            g.ai[s] = AiPlayer(difficulty, g.rng, s, policy=pol)
        gid = secrets.token_hex(6)
        g.game_id = gid
        g.start_round()
        with self.lock:
            self.games[gid] = g
            self.order.append(gid)
            self._evict()
        return gid

    def _evict(self):
        while len(self.order) > MAX_GAMES:
            old = self.order.pop(0)
            self.games.pop(old, None)

    def get(self, gid):
        return self.games.get(gid)

    # ------------------------------------------------------------ rooms
    def room_new(self, rules, difficulty, buy_ma, host_name):
        if rules not in RULESETS:
            raise ValueError('unknown rules: %s' % rules)
        if difficulty not in DIFFICULTIES:
            raise ValueError('unknown difficulty: %s' % difficulty)
        buy_ma = int(buy_ma)
        if buy_ma < 0 or buy_ma > 4:
            raise ValueError('buy_ma out of range')
        with self.lock:
            code = room_code()
            while code in self.rooms:
                code = room_code()
            room = Room(code, rules, difficulty, buy_ma, host_name)
            self.rooms[code] = room
        return room

    def room_get(self, code):
        with self.lock:
            return self.rooms.get(code)

    def room_join(self, code, name):
        with self.lock:
            room = self.rooms.get(code.upper())
            if room is None:
                raise KeyError('room not found')
            if room.phase != 'lobby':
                raise ValueError('room already started')
            free = room.free_seats()
            if not free:
                raise ValueError('room full')
            seat = free[0]
            room.seats[seat] = {'name': name or ('玩家%d' % (seat + 1)),
                                'token': secrets.token_hex(8)}
            auto = room.is_full()
        if auto:
            self.room_start(code, 0, None, autostart=True)
        with self.lock:
            return room, seat, auto

    def room_start(self, code, seat, token, autostart=False):
        with self.lock:
            room = self.rooms.get(code)
            if room is None:
                raise KeyError('room not found')
            if room.phase != 'lobby':
                raise ValueError('already started')
            if not autostart:
                entry = room.seats[seat]
                if entry is None or entry.get('token') != token:
                    raise PermissionError('only the host can start')
            humans = [i for i, s in enumerate(room.seats) if s is not None]
            wind = '东南西北'
            names = [(room.seats[i]['name'] if room.seats[i]
                      else ('电脑·' + wind[i])) for i in range(4)]
            g = Game(room.rules, room.difficulty, buy_ma=room.buy_ma,
                     humans=humans, names=names)
            for s in range(4):
                if s not in humans:
                    pol = self.policy if room.difficulty == 'hard' else None
                    g.ai[s] = AiPlayer(room.difficulty, g.rng, s, policy=pol)
            gid = secrets.token_hex(6)
            g.game_id = gid
            g.start_round()
            self.games[gid] = g
            self.tokens[gid] = {i: room.seats[i]['token'] for i in humans}
            self.order.append(gid)
            self._evict()
            room.gid = gid
            room.phase = 'playing'
            return gid

    def verify(self, gid, seat, token):
        """Check that {seat, token} is allowed to act in game gid."""
        tmap = self.tokens.get(gid)
        if tmap is None:
            return seat == 0          # legacy single-player game
        return tmap.get(seat) == token


class Handler(BaseHTTPRequestHandler):
    manager = None

    # ------------------------------------------------------------ helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        return self._json({'ok': False, 'error': msg}, code)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _path_parts(self):
        parsed = urlparse(self.path)
        return parsed.path.rstrip('/').split('/'), parse_qs(parsed.query)

    def _since(self, query):
        since = query.get('since', [None])[0]
        try:
            return int(since) if since is not None else None
        except ValueError:
            return None

    def log_message(self, fmt, *args):
        sys.stderr.write('[server] %s\n' % (fmt % args))

    # --------------------------------------------------------------- routes
    def do_GET(self):
        parts, query = self._path_parts()
        if parts == [''] or parts == ['', 'index.html']:
            return self._serve_file('index.html')
        if parts[:2] == ['', 'static']:
            rel = '/'.join(parts[2:])
            return self._serve_file(rel, subdir=STATIC)
        if parts == ['', 'api', 'info']:
            return self._json({'ok': True,
                               'ips': lan_ips(),
                               'port': self.server.server_port})
        if parts[:3] == ['', 'api', 'room'] and len(parts) == 4:
            room = self.manager.room_get(parts[3])
            if room is None:
                return self._err('room not found', 404)
            return self._json({'ok': True, 'room': room.info()})
        if parts[:3] == ['', 'api', 'game'] and len(parts) == 5 \
                and parts[4] == 'state':
            gid = parts[3]
            g = self.manager.get(gid)
            if g is None:
                return self._err('game not found', 404)
            seat = query.get('seat', ['0'])[0]
            try:
                seat = int(seat)
            except ValueError:
                seat = 0
            token = query.get('token', [None])[0] or ''
            if not self.manager.verify(gid, seat, token):
                return self._err('permission denied', 403)
            return self._json({'ok': True, **_pack(g, self._since(query),
                                                   seat)})
        return self._err('not found', 404)

    def do_POST(self):
        parts, _ = self._path_parts()
        body = self._body()

        if parts == ['', 'api', 'game', 'new']:            # legacy 1-player
            try:
                gid = self.manager.create(
                    body.get('rules', 'classic'),
                    body.get('difficulty', 'medium'),
                    body.get('buy_ma', 0),
                    body.get('seed'))
            except ValueError as e:
                return self._err(str(e))
            g = self.manager.get(gid)
            return self._json({'ok': True, 'game_id': gid,
                               **_pack(g, 0, 0)})

        if parts == ['', 'api', 'room', 'new']:
            try:
                room = self.manager.room_new(
                    body.get('rules', 'classic'),
                    body.get('difficulty', 'medium'),
                    body.get('buy_ma', 0),
                    body.get('name', ''))
            except ValueError as e:
                return self._err(str(e))
            return self._json({'ok': True, 'room': room.info(),
                               'seat': 0,
                               'token': room.seats[0]['token']})

        if parts == ['', 'api', 'room', 'join']:
            try:
                room, seat, started = self.manager.room_join(
                    body.get('code', ''), body.get('name', ''))
            except (KeyError, ValueError) as e:
                return self._err(str(e))
            out = {'ok': True, 'room': room.info(), 'seat': seat,
                   'token': room.seats[seat]['token'],
                   'started': started}
            if started and room.gid:
                g = self.manager.get(room.gid)
                out['game_id'] = room.gid
                out.update(_pack(g, 0, seat))
            return self._json(out)

        if parts[:3] == ['', 'api', 'room'] and len(parts) == 5 \
                and parts[4] == 'start':
            code = parts[3]
            try:
                gid = self.manager.room_start(
                    code, int(body.get('seat', 0)),
                    body.get('token', ''), autostart=False)
            except (KeyError, ValueError, PermissionError) as e:
                return self._err(str(e))
            seat = int(body.get('seat', 0))
            g = self.manager.get(gid)
            return self._json({'ok': True, 'game_id': gid,
                               'room': self.manager.room_get(code).info(),
                               **_pack(g, 0, seat)})

        if parts[:3] == ['', 'api', 'game'] and len(parts) == 5 \
                and parts[4] == 'action':
            gid = parts[3]
            g = self.manager.get(gid)
            if g is None:
                return self._err('game not found', 404)
            seat = int(body.pop('seat', 0))
            token = body.pop('token', '')
            if not self.manager.verify(gid, seat, token):
                return self._err('permission denied', 403)
            since = body.pop('since', None)
            try:
                since = int(since) if since is not None else None
            except (TypeError, ValueError):
                since = None
            try:
                with g.lock:
                    err = g.seat_action(seat, body)
            except Exception as e:
                err = 'engine error: %s' % e
            if err:
                return self._err(err)
            return self._json({'ok': True, **_pack(g, since, seat)})
        return self._err('not found', 404)

    # ------------------------------------------------------------ static
    def _serve_file(self, name, subdir=None):
        root = subdir or STATIC
        path = os.path.normpath(os.path.join(root, name))
        if not path.startswith(os.path.normpath(root)) or not os.path.isfile(path):
            return self._json({'ok': False, 'error': 'not found'}, 404)
        ext = os.path.splitext(path)[1].lower()
        ctype = CONTENT_TYPES.get(ext, 'application/octet-stream')
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)


def load_policy(path):
    """Optional trained-policy bias file ({tile_id: weight}) for hard AI."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and 'tile_weights' in data:
            data = data['tile_weights']
        return {int(k): float(v) for k, v in data.items()}
    except Exception as e:
        print('[server] warning: cannot load policy %s: %s' % (path, e))
        return {}


def main():
    ap = argparse.ArgumentParser(description='Mahjong server (LAN multiplayer)')
    ap.add_argument('--port', type=int, default=8623)
    ap.add_argument('--host', default='0.0.0.0',
                    help='bind address; keep 0.0.0.0 so friends on the LAN '
                         'can connect (Windows firewall may prompt)')
    ap.add_argument('--no-open', action='store_true',
                    help='do not auto-open the browser')
    ap.add_argument('--policy', default=None,
                    help='path to a trained-policy JSON (optional)')
    args = ap.parse_args()

    Handler.manager = GameManager(policy=load_policy(args.policy))
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    ips = [ip for ip in lan_ips() if ip]
    print('=' * 56)
    print('  麻将 · Mahjong (LAN multiplayer)')
    print('  local:   http://127.0.0.1:%d' % args.port)
    for ip in ips:
        print('  LAN:     http://%s:%d   <- share this with friends' % (ip, args.port))
    print('  host is the one who runs this; others open the LAN URL above.')
    print('  quit:    Ctrl+C')
    print('=' * 56)
    if not args.no_open:
        try:
            webbrowser.open('http://127.0.0.1:%d' % args.port)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nbye')
        httpd.server_close()


if __name__ == '__main__':
    main()
