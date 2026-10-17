# -*- coding: utf-8 -*-
"""Mahjong game state machine.

A single Game object owns one continuing session (many rounds).  The engine
is fully event-driven: every state change appends a public event with a
monotonic seq, so the web client can replay animations from an event log.

Phases:
  absence_wait : Sichuan 定缺 prompt for the human player
  wait_human   : human must discard / declare zimo-胡 / gang
  claim_wait   : human may respond to a discard (胡/杠/碰/吃/过)
  round_end    : round finished, waiting for next-round request
The engine auto-advances all AI turns until it needs human input.
"""
import random
import secrets
import threading

from . import hand as H
from . import rules as R
from .tiles import (full_wall, counts, sort_hand, tile_name, suit_of,
                    HONOR_START)

HUMAN_SEAT = 0
RULESETS = ('classic', 'sichuan', 'guangdong')
DIFFICULTIES = ('easy', 'medium', 'hard')
SEAT_NAMES = ('你', '电脑·南', '电脑·西', '电脑·北')
SUIT_OPTIONS = ('man', 'pin', 'sou')

EVENT_CAP = 400


class Player:
    __slots__ = ('seat', 'name', 'is_human', 'hand', 'melds', 'discards',
                 'score', 'absence', 'active', 'hu_count', 'is_dealer')

    def __init__(self, seat, name, is_human):
        self.seat = seat
        self.name = name
        self.is_human = is_human
        self.hand = []
        self.melds = []        # {'kind','tiles','from'}
        self.discards = []
        self.score = 0
        self.absence = None
        self.active = True
        self.hu_count = 0
        self.is_dealer = False

    def external(self):
        return len(self.melds)


class Game:
    def __init__(self, rules='classic', difficulty='medium', buy_ma=0,
                 seed=None, humans=None, names=None):
        if rules not in RULESETS:
            raise ValueError(rules)
        if difficulty not in DIFFICULTIES:
            raise ValueError(difficulty)
        self.rules = rules
        self.difficulty = difficulty
        self.buy_ma = buy_ma if rules == 'guangdong' else 0
        self.seed = seed
        self.rng = random.Random(seed)          # AI noise (seedable)
        self.sys_rng = secrets.SystemRandom()   # dealing randomness

        if humans is None:
            humans = (HUMAN_SEAT,)
        self.humans = set(humans)
        if names is None:
            names = SEAT_NAMES
        self.players = [Player(i, names[i], i in self.humans)
                        for i in range(4)]
        self.dealer = 0
        self.round_num = 0
        self.wall = []
        self.turn = -1
        self.phase = 'idle'
        self.pending = None       # human claim options
        self._ctx = None          # claim resolution context
        self.absence_queue = []
        self.active_seats = [0, 1, 2, 3]
        self.round_winners = []
        self._drawn = False       # current turn player already drew
        self._gang_pao_seat = None
        self.last_discard = None
        self.events = []
        self.seq = 0
        self.events_start = 0
        self.ai = {}              # seat -> AiPlayer, wired by the server
        self.wall_used = 0        # number of draws (for stats/display)
        self.ma_removed = 0       # tiles drawn as Guangdong buy-ma horses
        self.lock = threading.RLock()  # serialize concurrent access

    # ------------------------------------------------------------------ util
    def _emit(self, ev):
        ev['seq'] = self.seq
        self.seq += 1
        self.events.append(ev)
        if len(self.events) > EVENT_CAP:
            drop = len(self.events) - EVENT_CAP
            self.events = self.events[drop:]
            self.events_start += drop

    def _p(self, seat):
        return self.players[seat]

    def _active(self, seat):
        return self.players[seat].active

    def _can_win(self, seat, tile=None):
        p = self._p(seat)
        c = counts(p.hand)
        if tile is not None:
            c[tile] += 1
        if self.rules == 'sichuan' and p.absence:
            for t in range(HONOR_START):
                if suit_of(t) == p.absence and c[t]:
                    return False
        return H.is_win(c, p.external())

    def _is_tenpai(self, seat):
        p = self._p(seat)
        if self.rules == 'sichuan' and p.absence:
            c = counts(p.hand)
            if any(suit_of(t) == p.absence and c[t] for t in range(HONOR_START)):
                return False
        return bool(H.winning_tiles(counts(p.hand), p.external()))

    # --------------------------------------------------------------- rounds
    def start_round(self):
        self.round_num += 1
        for p in self.players:
            p.hand = []
            p.melds = []
            p.discards = []
            p.active = True
            p.absence = None
            p.hu_count = 0
            p.is_dealer = (p.seat == self.dealer)
        self.round_winners = []
        self.active_seats = [0, 1, 2, 3]
        self.turn = self.dealer
        self._drawn = False
        self._gang_pao_seat = None
        self.last_discard = None
        self.pending = None
        self._ctx = None

        include_honors = self.rules != 'sichuan'
        wall = full_wall(include_honors)
        if self.seed is not None:
            self.rng.shuffle(wall)
        else:
            self.sys_rng.shuffle(wall)
        self.wall = wall
        self.wall_used = 0
        self._emit({'type': 'round_start', 'round': self.round_num,
                    'dealer': self.dealer, 'wall': len(wall)})

        # deal: 4-4-4-1 pattern, dealer +1
        for _ in range(3):
            for s in range(4):
                for _ in range(4):
                    self._p(s).hand.append(self.wall.pop(0))
        for s in range(4):
            self._p(s).hand.append(self.wall.pop(0))
        self._p(self.dealer).hand.append(self.wall.pop(0))
        for p in self.players:
            p.hand = sort_hand(p.hand)

        if self.rules == 'sichuan':
            self.phase = 'absence_wait'
            self.absence_queue = [(self.dealer + i) % 4 for i in range(4)]
        else:
            self.phase = 'play'
            self._drawn = True          # dealer has 14 tiles, discards first
            self._emit({'type': 'turn', 'seat': self.turn})
        self._advance()

    def next_round(self):
        if self.phase != 'round_end':
            return
        winners = self.round_winners
        if self.dealer in winners or not winners:
            pass                            # dealer stays (连庄)
        else:
            self.dealer = (self.dealer + 1) % 4
        self.start_round()

    # ------------------------------------------------------------- advance
    def _advance(self):
        """Run the game forward until human input is needed or round ends."""
        while True:
            if self.phase in ('round_end', 'game_end'):
                return
            if self.phase == 'absence_wait':
                if not self._advance_absence():
                    return
                continue
            if self.phase in ('wait_human', 'claim_wait'):
                return
            if self.phase != 'play':
                return

            seat = self.turn
            if not self._active(seat):
                self.turn = (seat + 1) % 4
                continue

            # draw phase
            if not self._drawn:
                if not self.wall:
                    self._end_wall()
                    continue
                tile = self.wall.pop(0)
                self.wall_used += 1
                haidi = not self.wall
                self._p(seat).hand.append(tile)
                self._p(seat).hand = sort_hand(self._p(seat).hand)
                self._drawn = True
                self._emit({'type': 'draw', 'seat': seat,
                            'tile': tile,
                            'replacement': False})
                if self._can_win(seat):
                    flags = {'gang_draw': False, 'haidi': haidi}
                    if seat in self.humans:
                        self.phase = 'wait_human'
                        return
                    self._apply_hu(seat, None, None, flags)
                    continue

            # gang before discard (human: options; AI: decide)
            gang_opts = self._gang_options(seat)
            if gang_opts:
                if seat in self.humans:
                    self.phase = 'wait_human'
                    return
                pick = self.ai[seat].choose_gang(self, seat, gang_opts)
                if pick is not None:
                    self._apply_gang(seat, pick)
                    # replacement drawn inside _apply_gang; must discard now
                    self._drawn = True
                    continue

            # discard
            if seat in self.humans:
                self.phase = 'wait_human'
                return
            tile = self.ai[seat].choose_discard(self, seat)
            self._discard(seat, tile)
            # _discard resolves claims; if it paused for human, loop exits
            # via phase check; otherwise continues to next turn.

    def _advance_absence(self):
        """Process the absence queue.  Returns False if waiting for human."""
        while self.absence_queue:
            seat = self.absence_queue[0]
            if seat in self.humans:
                self.phase = 'absence_wait'
                return False
            suit = self.ai[seat].choose_absence(self, seat)
            self._apply_absence(seat, suit)
            self.absence_queue.pop(0)
        # all declared -> play begins
        self.phase = 'play'
        self.turn = self.dealer
        self._drawn = True
        self._emit({'type': 'turn', 'seat': self.turn})
        return True

    def _apply_absence(self, seat, suit):
        self._p(seat).absence = suit
        self._emit({'type': 'absence', 'seat': seat, 'suit': suit})

    # ------------------------------------------------------------- discard
    def _can_discard(self, seat, tile):
        """Legality of a discard.  Sichuan 定缺: the declared absence suit
        MUST be cleared from the hand first — absence tiles are priority
        discards, and no OTHER suit may be discarded while any absence tile
        remains in hand (real rule: 摸到缺门牌必须打)."""
        p = self._p(seat)
        if self.rules == 'sichuan' and p.absence:
            has_abs = any(suit_of(t) == p.absence for t in p.hand)
            if suit_of(tile) != p.absence and has_abs:
                return False
        return True

    def _discard(self, seat, tile):
        p = self._p(seat)
        if tile not in p.hand:
            raise ValueError('tile not in hand')
        if not self._can_discard(seat, tile):
            raise ValueError('cannot discard absence-suit tile')
        p.hand.remove(tile)
        p.discards.append(tile)
        self._emit({'type': 'discard', 'seat': seat, 'tile': tile})
        self.last_discard = {'tile': tile, 'seat': seat,
                             'seq': self.seq - 1}
        gang_pao = (self._gang_pao_seat == seat)
        self._gang_pao_seat = None
        self._ctx = None
        self.pending = None
        self._process_claims(seat, tile, gang_pao)

    def _process_claims(self, discarder, tile, gang_pao):
        order = [(discarder + i) % 4 for i in (1, 2, 3)]
        hu_list = [s for s in order
                   if self._active(s) and self._can_win(s, tile)]
        ctx = {'stage': 'hu', 'tile': tile, 'from': discarder,
               'order': order, 'hu_list': hu_list,
               'flags': {'gang_pao': gang_pao, 'rob_gang': False}}
        self._ctx = ctx
        r = self._resume_claims()
        if r == 'pause':
            self.phase = 'claim_wait'
        elif r == 'claimed':
            if self.phase != 'round_end':
                self.phase = 'play'
        else:  # none
            self.turn = (discarder + 1) % 4
            self._drawn = False
            self.phase = 'play'
            self._emit({'type': 'turn', 'seat': self.turn})

    def _resume_claims(self):
        """Continue claim resolution.  Returns 'pause' (human asked),
        'claimed' (tile consumed by a claim) or 'none'."""
        ctx = self._ctx
        while True:
            stage = ctx['stage']
            if stage == 'hu':
                if not ctx['hu_list']:
                    if ctx.get('hu_happened'):
                        # tile consumed by a win; continue with next player
                        self.turn = (ctx['from'] + 1) % 4
                        self._drawn = False
                        self._emit({'type': 'turn', 'seat': self.turn})
                        return 'claimed'
                    ctx['stage'] = 'gang'
                    continue
                s = ctx['hu_list'].pop(0)
                if ('hu', s) in ctx.get('passed', ()):
                    continue
                if s in self.humans:
                    self.pending = {
                        'kind': 'claim', 'seat': s, 'tile': ctx['tile'],
                        'from': ctx['from'],
                        'options': [{'type': 'hu', 'from': ctx['from']},
                                    {'type': 'pass'}]}
                    return 'pause'
                self._apply_hu(s, ctx['from'], ctx['tile'], ctx['flags'])
                ctx['hu_happened'] = True
                if self.phase == 'round_end':
                    return 'claimed'
                continue
            if stage == 'gang':
                for s in ctx['order']:
                    if not self._active(s):
                        continue
                    if ('gang', s) in ctx.get('passed', ()):
                        continue
                    if self._p(s).hand.count(ctx['tile']) < 3:
                        continue
                    # Sichuan 定缺: cannot claim tiles of the absent suit
                    if self.rules == 'sichuan' and self._p(s).absence \
                            and suit_of(ctx['tile']) == self._p(s).absence:
                        continue
                    if s in self.humans:
                        self.pending = {
                            'kind': 'claim', 'seat': s, 'tile': ctx['tile'],
                            'from': ctx['from'],
                            'options': [
                                {'type': 'gang_ming', 'from': ctx['from'],
                                 'warn': self._claim_warn(
                                     s, 'gang_ming', ctx['tile'])},
                                {'type': 'pass'}]}
                        return 'pause'
                    if self.ai[s].wants_claim(self, s,
                                              {'type': 'gang_ming',
                                               'from': ctx['from'],
                                               'tile': ctx['tile']}):
                        self.turn = s
                        self._apply_gang(s, {'type': 'gang_ming',
                                             'from': ctx['from'],
                                             'tile': ctx['tile']})
                        return 'claimed'
                ctx['stage'] = 'peng'
                continue
            if stage == 'peng':
                for s in ctx['order']:
                    if not self._active(s):
                        continue
                    if ('peng', s) in ctx.get('passed', ()):
                        continue
                    if self._p(s).hand.count(ctx['tile']) < 2:
                        continue
                    # Sichuan 定缺: cannot claim tiles of the absent suit
                    if self.rules == 'sichuan' and self._p(s).absence \
                            and suit_of(ctx['tile']) == self._p(s).absence:
                        continue
                    if s in self.humans:
                        self.pending = {
                            'kind': 'claim', 'seat': s, 'tile': ctx['tile'],
                            'from': ctx['from'],
                            'options': [
                                {'type': 'peng', 'from': ctx['from'],
                                 'warn': self._claim_warn(
                                     s, 'peng', ctx['tile'])},
                                {'type': 'pass'}]}
                        return 'pause'
                    if self.ai[s].wants_claim(self, s,
                                              {'type': 'peng',
                                               'from': ctx['from'],
                                               'tile': ctx['tile']}):
                        self._apply_peng(s, ctx['from'], ctx['tile'])
                        return 'claimed'
                ctx['stage'] = 'chi'
                continue
            if stage == 'chi':
                if self.rules != 'sichuan':
                    s = ctx['order'][0]
                    if self._active(s) and \
                            ('chi', s) not in ctx.get('passed', ()):
                        chi_opts = self._chi_options(s, ctx['tile'])
                        if chi_opts:
                            if s in self.humans:
                                self.pending = {
                                    'kind': 'claim', 'seat': s,
                                    'tile': ctx['tile'],
                                    'from': ctx['from'],
                                    'options': (
                                        [{'type': 'chi',
                                          'from': ctx['from'],
                                          'tiles': list(o),
                                          'warn': self._claim_warn(
                                              s, 'chi', ctx['tile'],
                                              list(o))}
                                         for o in chi_opts] +
                                        [{'type': 'pass'}])}
                                return 'pause'
                            pick = self.ai[s].wants_claim(
                                self, s, {'type': 'chi',
                                          'from': ctx['from'],
                                          'tiles': chi_opts})
                            if pick is not None:
                                self._apply_chi(s, ctx['from'],
                                                ctx['tile'], pick)
                                return 'claimed'
                return 'none'
            return 'none'

    # ------------------------------------------------------------- claims
    def _chi_options(self, seat, tile):
        """All (tile1, tile2) pairs in hand that form a sequence with tile."""
        if tile >= HONOR_START:
            return []
        p = self._p(seat)
        s = set(p.hand)
        v = tile % 9
        base = tile - v
        out = []
        if v >= 2 and (base + v - 2) in s and (base + v - 1) in s:
            out.append((base + v - 2, base + v - 1))
        if 1 <= v <= 7 and (base + v - 1) in s and (base + v + 1) in s:
            out.append((base + v - 1, base + v + 1))
        if v <= 6 and (base + v + 1) in s and (base + v + 2) in s:
            out.append((base + v + 1, base + v + 2))
        return out

    def _claim_warn(self, seat, kind, tile, chi_pair=None):
        """Decision hint: would this claim break an already-built sequence?

        Returns a short Chinese message or None.  Pure heuristics for the
        UI — never blocks a legal claim, only warns.
        """
        if tile >= HONOR_START:
            return None
        p = self._p(seat)
        hand = list(p.hand)
        if kind in ('peng', 'gang_ming'):
            n = 3 if kind == 'gang_ming' else 2
            if hand.count(tile) < n:
                return None
            for _ in range(n):
                hand.remove(tile)
            left = tile - 1 if tile % 9 != 0 else -1
            right = tile + 1 if tile % 9 != 8 else -1
            if left in hand and right in hand:
                return ('%s %s 会拆散顺子 %s·%s·%s'
                        % ('杠' if kind == 'gang_ming' else '碰',
                           tile_name(tile), tile_name(left),
                           tile_name(tile), tile_name(right)))
            return None
        a, b = sorted(chi_pair)
        for t in chi_pair:
            hand.remove(t)
        ext_l = a - 1 if a % 9 != 0 else -1
        ext_r = b + 1 if b % 9 != 8 else -1
        if ext_l in hand or ext_r in hand:
            return '吃 %s 会拆散顺子' % tile_name(tile)
        return None

    def _consume_discard(self, from_seat, tile):
        """Remove the claimed discard from the discarder's pile (guard
        against double-claiming the same tile by multiple winners)."""
        d = self._p(from_seat).discards
        if d and d[-1] == tile:
            d.pop()

    def _apply_peng(self, seat, from_seat, tile):
        p = self._p(seat)
        for _ in range(2):
            p.hand.remove(tile)
        self._consume_discard(from_seat, tile)
        p.melds.append({'kind': 'peng', 'tiles': [tile, tile, tile],
                        'from': from_seat})
        self._emit({'type': 'peng', 'seat': seat, 'tiles': [tile] * 3,
                    'from': from_seat})
        self.turn = seat
        self._drawn = True

    def _apply_chi(self, seat, from_seat, tile, pair):
        p = self._p(seat)
        for t in pair:
            p.hand.remove(t)
        self._consume_discard(from_seat, tile)
        meld_tiles = sorted(pair + (tile,))
        p.melds.append({'kind': 'chi', 'tiles': meld_tiles, 'from': from_seat})
        self._emit({'type': 'chi', 'seat': seat, 'tiles': meld_tiles,
                    'from': from_seat})
        self.turn = seat
        self._drawn = True

    def _gang_options(self, seat):
        """Own-turn gang options: concealed gangs and bu-gangs."""
        p = self._p(seat)
        opts = []
        if not self.wall:
            return opts
        seen = set()
        for t in p.hand:
            if t in seen:
                continue
            seen.add(t)
            if p.hand.count(t) == 4:
                opts.append({'type': 'gang_an', 'tiles': [t] * 4})
        for m in p.melds:
            if m['kind'] == 'peng' and p.hand.count(m['tiles'][0]) >= 1:
                t = m['tiles'][0]
                opts.append({'type': 'gang_bu', 'tiles': m['tiles'] + [t],
                             'meld': m})
        return opts

    def _gang_deltas(self, kind, seat, from_seat):
        """Sichuan 刮风下雨 gang payments; other rulesets pay nothing."""
        if self.rules != 'sichuan':
            return []
        return R.settle_gang_sichuan(kind, seat, from_seat,
                                     self.active_seats)

    def _apply_gang(self, seat, opt):
        """opt: {'type':'gang_an'|'gang_bu', ...} or a claim gang_ming
        {'type':'gang_ming','from':f,'tile':t}."""
        p = self._p(seat)
        gtype = opt['type']
        if gtype == 'gang_an':
            t = opt['tiles'][0]
            for _ in range(4):
                p.hand.remove(t)
            p.melds.append({'kind': 'gang_an', 'tiles': [t] * 4, 'from': None})
            deltas = self._gang_deltas('gang_an', seat, None)
            self._emit({'type': 'gang', 'seat': seat, 'kind': 'gang_an',
                        'tiles': [t] * 4, 'from': None, 'deltas': deltas})
        elif gtype == 'gang_bu':
            t = opt['tiles'][-1]
            meld = opt.get('meld')
            # 抢杠 check first: others may rob this bu-gang
            robbers = [s for s in range(4)
                       if s != seat and self._active(s)
                       and self._can_win(s, t)]
            if robbers:
                ctx = {'stage': 'hu', 'tile': t, 'from': seat,
                       'order': [(seat + i) % 4 for i in (1, 2, 3)],
                       'hu_list': robbers,
                       'flags': {'gang_pao': False, 'rob_gang': True}}
                self._ctx = ctx
                r = self._resume_claims()
                if r == 'pause':
                    self.phase = 'claim_wait'
                    return
                # if any robber hu'd, gang is aborted; continue with the
                # player after the would-be ganger
                if r == 'claimed':
                    if self.phase != 'round_end':
                        self.turn = (seat + 1) % 4
                        self._drawn = False
                        self._emit({'type': 'turn', 'seat': self.turn})
                        self.phase = 'play'
                    return
            p.hand.remove(t)
            if meld is not None:
                p.melds.remove(meld)
                p.melds.append({'kind': 'gang_bu', 'tiles': meld['tiles'] + [t],
                                'from': meld['from']})
            else:
                p.melds.append({'kind': 'gang_bu', 'tiles': [t] * 4,
                                'from': None})
            deltas = self._gang_deltas('gang_bu', seat, None)
            self._emit({'type': 'gang', 'seat': seat, 'kind': 'gang_bu',
                        'tiles': [t] * 4, 'from': None, 'deltas': deltas})
        elif gtype == 'gang_ming':
            t = opt['tile']
            from_seat = opt['from']
            for _ in range(3):
                p.hand.remove(t)
            self._consume_discard(from_seat, t)
            p.melds.append({'kind': 'gang_ming', 'tiles': [t] * 4,
                            'from': from_seat})
            deltas = self._gang_deltas('gang_ming', seat, from_seat)
            self._emit({'type': 'gang', 'seat': seat, 'kind': 'gang_ming',
                        'tiles': [t] * 4, 'from': from_seat,
                        'deltas': deltas})
            # the ganger discards next (own-turn gang_an/gang_bu already
            # had turn == seat; a claimed ming-gang must advance the turn)
            self.turn = seat
        else:
            raise ValueError(gtype)

        # apply payments (Sichuan only; others rulesets produce no deltas)
        for (s, d) in deltas:
            self._p(s).score += d

        # replacement draw (岭上牌)
        if self.wall:
            repl = self.wall.pop()
            self.wall_used += 1
            self._p(seat).hand.append(repl)
            self._p(seat).hand = sort_hand(self._p(seat).hand)
            self._emit({'type': 'draw', 'seat': seat,
                        'tile': repl,
                        'replacement': True})
        self._gang_pao_seat = seat
        self._drawn = True

    # -------------------------------------------------------------- hu
    def _apply_hu(self, seat, from_seat, tile, flags):
        p = self._p(seat)
        virtual = False
        if from_seat is not None:
            # One physical discard can feed several winners (multi-ron) or a
            # rob-gang (the tile still sits in the ganger's hand, not in the
            # discard pile): only the first winner physically takes it; later
            # winners settle against a virtual copy and give it back so that
            # tile conservation holds.
            d = self._p(from_seat).discards
            if d and d[-1] == tile:
                self._consume_discard(from_seat, tile)
            else:
                virtual = True
            p.hand.append(tile)
            p.hand = sort_hand(p.hand)
            win_kind = 'dianpao'
        else:
            win_kind = 'zimo'
        hand14 = list(p.hand)
        fan_names, fan = R.detect_fan(self.rules, hand14, p.melds, win_kind,
                                      flags)
        if self.rules == 'sichuan':
            active = [s for s in self.active_seats]
            deltas = R.settle_win('sichuan', seat, from_seat, fan, active)
        else:
            deltas = R.settle_win('classic', seat, from_seat, fan,
                                  [0, 1, 2, 3])
        for (s, d) in deltas:
            self._p(s).score += d
        p.hu_count += 1
        self.round_winners.append(seat)
        self._emit({'type': 'hu', 'seat': seat, 'from': from_seat,
                    'tile': tile, 'zimo': from_seat is None,
                    'fan_names': fan_names, 'fan': fan, 'deltas': deltas})

        if self.rules == 'sichuan':
            p.active = False  # 血战: winner leaves the table (turn machine)
            self.active_seats = [s for s in self.active_seats if s != seat]
            self._emit({'type': 'reveal', 'seat': seat, 'hand': hand14})
            if len(self.active_seats) <= 1:
                self._end_round()
            else:
                self.phase = 'play'
        else:
            # guangdong 买马
            if self.buy_ma > 0 and self.wall:
                n = min(self.buy_ma, len(self.wall))
                horses = [self.wall.pop() for _ in range(n)]
                self.ma_removed += n
                events, deltas = R.settle_ma(seat, fan, horses)
                for (s, d) in deltas:
                    self._p(s).score += d
                self._emit({'type': 'ma', 'horses': horses, 'deltas': deltas})
            self._end_round()

        # give the virtual copy back AFTER fan/score/reveal used it, so the
        # winner keeps a legal 13-tile hand and the wall/discards stay flat
        if virtual:
            p.hand.remove(tile)
            p.hand = sort_hand(p.hand)

    # ---------------------------------------------------------- round end
    def _end_wall(self):
        """Wall exhausted: settle Sichuan penalties, then end the round."""
        if self.rules == 'sichuan' and len(self.active_seats) > 1:
            deltas = []

            def is_hua(s):
                p = self._p(s)
                return (p.absence is not None and
                        any(suit_of(t) == p.absence and n
                            for t, n in enumerate(counts(p.hand))
                            if t < HONOR_START))

            tenpai_players = [s for s in self.active_seats
                              if not is_hua(s) and self._is_tenpai(s)]
            for s in self.active_seats:
                if is_hua(s):
                    for q in range(4):
                        if q != s:
                            deltas.append((s, -2))
                            deltas.append((q, +2))
                elif s not in tenpai_players:
                    for q in tenpai_players:
                        if q != s:
                            deltas.append((s, -1))
                            deltas.append((q, +1))
            for (s, d) in deltas:
                self._p(s).score += d
            self._emit({'type': 'wall_empty', 'deltas': deltas,
                        'tenpai': tenpai_players})
        self._end_round()

    def _end_round(self):
        self.phase = 'round_end'
        for p in self.players:
            if p.active or p.seat not in self.round_winners:
                self._emit({'type': 'reveal', 'seat': p.seat,
                            'hand': sorted(p.hand)})
        self._emit({'type': 'round_end', 'round': self.round_num,
                    'winners': list(self.round_winners),
                    'scores': [p.score for p in self.players]})

    # --------------------------------------------------- human legal info
    def legal_for_seat(self, seat):
        leg = {'phase': self.phase, 'absence_options': [],
               'can_discard': False, 'zimo': False,
               'gang_options': [], 'claims': [], 'passable': False,
               'winning_tiles': []}
        p = self._p(seat)
        if self.phase == 'absence_wait' and self.absence_queue \
                and self.absence_queue[0] == seat:
            leg['absence_options'] = list(SUIT_OPTIONS)
        elif self.phase == 'wait_human' and self.turn == seat:
            if p.active:
                leg['can_discard'] = True
                if self._can_win(seat):
                    leg['zimo'] = True
                leg['gang_options'] = self._gang_options(seat)
                if self._is_tenpai(seat):
                    leg['winning_tiles'] = H.winning_tiles(
                        counts(p.hand), p.external())
        elif self.phase == 'claim_wait' and self.pending \
                and self.pending['kind'] == 'claim':
            if self.pending.get('seat') == seat:
                leg['claims'] = self.pending['options']
                leg['passable'] = any(o['type'] == 'pass'
                                      for o in self.pending['options'])
        return leg

    def legal_for_human(self):
        """Legacy alias: legal actions for the default human seat 0."""
        return self.legal_for_seat(HUMAN_SEAT)

    def snapshot_for(self, seat):
        """Public info + the private hand of the requesting seat only."""
        players = []
        for p in self.players:
            entry = {
                'seat': p.seat, 'name': p.name, 'is_human': p.is_human,
                'score': p.score, 'hand_count': len(p.hand),
                'discards': list(p.discards),
                'melds': [{'kind': m['kind'], 'tiles': list(m['tiles']),
                           'from': m['from']} for m in p.melds],
                'absence': p.absence, 'active': p.active,
                'hu_count': p.hu_count, 'is_dealer': p.is_dealer,
            }
            # once the round is over, reveal every hand (no more secrecy)
            if self.phase == 'round_end':
                entry['hand'] = sorted(p.hand)
            # Sichuan 血战: reveal winner's hand immediately when they win
            elif self.rules == 'sichuan' and p.seat in self.round_winners:
                entry['hand'] = sorted(p.hand)
            players.append(entry)
        me = self._p(seat)
        snap = {
            'game_id': getattr(self, 'game_id', None),
            'rules': self.rules, 'difficulty': self.difficulty,
            'buy_ma': self.buy_ma, 'round': self.round_num,
            'dealer': self.dealer, 'wall_remaining': len(self.wall),
            'phase': self.phase, 'turn': self.turn,
            'players': players,
            'hand': sorted(me.hand),
            'legal': self.legal_for_seat(seat),
            'last_discard': self.last_discard,
            'tenpai': self._is_tenpai(seat),
            'pending_seat': self.pending.get('seat') if self.pending
                            else None,
        }
        return snap

    def public_snapshot(self):
        """Legacy alias: snapshot as seen by the default human seat 0."""
        snap = self.snapshot_for(HUMAN_SEAT)
        snap['human_hand'] = snap.pop('hand')
        snap['human_tenpai'] = snap.pop('tenpai')
        return snap

    # --------------------------------------------------------- seat actions
    def seat_action(self, seat, action):
        """Process an action for a specific human seat.
        Returns an error string or None."""
        if seat not in self.humans:
            return 'seat %d is not human' % seat
        a = action
        phase = self.phase

        if phase == 'wait_human' and a['type'] in ('discard', 'hu', 'gang') \
                and self.turn != seat:
            return 'not your turn'

        if a['type'] == 'absence' and phase == 'absence_wait':
            if not self.absence_queue or self.absence_queue[0] != seat:
                return 'not your turn to declare absence'
            suit = a.get('suit')
            if suit not in SUIT_OPTIONS:
                return 'invalid absence suit'
            self._apply_absence(seat, suit)
            self.absence_queue.pop(0)
            self._advance_absence()
            self._advance()
            return None

        if a['type'] == 'discard' and phase == 'wait_human':
            if not self._p(seat).active:
                return 'you have already won this round'
            tile = a.get('tile')
            if tile not in self._p(seat).hand:
                return 'tile not in hand'
            if not self._can_discard(seat, tile):
                return 'cannot discard absence-suit tile'
            self._discard(seat, tile)
            self._advance()
            return None

        if a['type'] == 'hu' and phase == 'wait_human':
            if not self._can_win(seat):
                return 'not a winning hand'
            self._apply_hu(seat, None, None, {})
            self._advance()
            return None

        if a['type'] == 'gang' and phase == 'wait_human':
            idx = a.get('index', -1)
            opts = self._gang_options(seat)
            if not (0 <= idx < len(opts)):
                return 'invalid gang option'
            self._apply_gang(seat, opts[idx])
            # if bu-gang was robbed, gang aborted -> now someone else's
            # turn; _advance continues
            self._advance()
            return None

        if a['type'] == 'claim' and phase == 'claim_wait':
            if not self.pending or self.pending['kind'] != 'claim':
                return 'nothing to claim'
            if self.pending.get('seat') != seat:
                return 'not your turn to claim'
            idx = a.get('index', -1)
            opts = self.pending['options']
            if not (0 <= idx < len(opts)):
                return 'invalid claim option'
            o = opts[idx]
            if o['type'] == 'pass':
                self.pending = None
                # remember this seat passed at this stage so _resume_claims
                # advances instead of re-asking forever (engine bug fix)
                self._ctx.setdefault('passed', set()).add(
                    (self._ctx['stage'], seat))
                r = self._resume_claims()
                if r == 'pause':
                    self.phase = 'claim_wait'
                else:
                    if self.phase != 'round_end':
                        self.phase = 'play'
                    if r != 'claimed':
                        self.turn = (self._ctx['from'] + 1) % 4
                        self._drawn = False
                        self._emit({'type': 'turn', 'seat': self.turn})
                self._advance()
                return None
            if o['type'] == 'hu':
                self.pending = None
                self._apply_hu(seat, o.get('from'), self._ctx['tile'],
                               self._ctx['flags'])
                r = self._resume_claims() if self.phase != 'round_end' else 'none'
                if r == 'pause':
                    self.phase = 'claim_wait'
                else:
                    if self.phase != 'round_end':
                        self.phase = 'play'
                    if r != 'claimed':
                        self.turn = (self._ctx['from'] + 1) % 4
                        self._drawn = False
                        self._emit({'type': 'turn', 'seat': self.turn})
                self._advance()
                return None
            if o['type'] == 'peng':
                self.pending = None
                self._apply_peng(seat, o['from'], self._ctx['tile'])
                self.phase = 'wait_human'
                return None
            if o['type'] == 'gang_ming':
                self.pending = None
                self._apply_gang(seat,
                                 {'type': 'gang_ming',
                                  'from': o['from'], 'tile': self._ctx['tile']})
                self.phase = 'wait_human'
                return None
            if o['type'] == 'chi':
                self.pending = None
                self._apply_chi(seat, o['from'], self._ctx['tile'],
                                tuple(o['tiles']))
                self.phase = 'wait_human'
                return None
            return 'invalid claim option'

        if a['type'] == 'next_round' and phase == 'round_end':
            self.next_round()
            return None

        return 'action not allowed in phase %s' % phase

    def human_action(self, action):
        """Legacy alias: act as the default human seat 0."""
        return self.seat_action(HUMAN_SEAT, action)
