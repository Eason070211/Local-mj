# -*- coding: utf-8 -*-
"""Mahjong AI players.

Three difficulty levels, all running real-time on CPU:

  easy   : greedily minimize shanten, random tie-breaks, occasional
           deliberate mistakes, no defense.
  medium : shanten + effective-tile (ukeire) optimization, a simple danger
           model for late-game folding, smart claims.
  hard   : Monte Carlo simulation.  For each candidate discard the AI plays
           the hand forward against sampled opponent hands and walls
           (fast policy), then picks the discard with the highest simulated
           win rate.  Combined with the medium-level heuristic and a danger
           veto.  This is the "sees the future" level.

All tile counting uses only public information (discards, open melds, the
AI's own hand) — nothing is leaked from other players' hands.
"""
import time

from . import hand as H
from .tiles import (counts, sort_hand, suit_of, HONOR_START,
                    value_of, is_terminal_or_honor)

# base danger by tile id (honors low because they are usually safe mid-game)
_BASE_DANGER = [0] * 34
for t in range(34):
    if t >= HONOR_START:
        _BASE_DANGER[t] = 1.0
    else:
        v = t % 9 + 1
        _BASE_DANGER[t] = {1: 1.2, 2: 2.0, 3: 3.0, 4: 3.4, 5: 3.6,
                           6: 3.4, 7: 3.0, 8: 2.0, 9: 1.2}[v]

MC_PLIES = 6
MC_BUDGET = 1.4          # seconds per hard discard
MC_MAX_ITER = 400


def _dedup(lst):
    seen = set()
    out = []
    for x in lst:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


class AiPlayer:
    def __init__(self, difficulty, rng, seat, policy=None):
        self.difficulty = difficulty
        self.rng = rng
        self.seat = seat
        self.policy = policy or {}     # optional trained tile bias {tile: w}

    # ------------------------------------------------------------- helpers
    def _visible(self, game, seat):
        """Counts of tiles visible to this player (public + own hand)."""
        vis = [0] * 34
        for q in range(4):
            p = game._p(q)
            if q == seat:
                for t in p.hand:
                    vis[t] += 1
            for t in p.discards:
                vis[t] += 1
            for m in p.melds:
                # own concealed gang is known to self; others' are not
                if m['kind'] == 'gang_an' and q != seat:
                    continue
                for t in m['tiles']:
                    vis[t] += 1
        return vis

    def _opp_tenpai_est(self, game, seat):
        """0..1 estimate that opponents are close to / already in tenpai."""
        total = 0.0
        n = 0
        for q in range(4):
            if q == seat or not game._active(q):
                continue
            op = game._p(q)
            w = 0.0
            w += len(op.melds) * 0.22
            if len(op.melds) >= 2:
                w += 0.15
            disc = op.discards
            if disc:
                last = disc[-1]
                if last >= HONOR_START:
                    w += 0.25
                elif last % 9 in (0, 8):
                    w += 0.12
            if len(disc) >= 12:
                w += 0.20
            total += min(w, 0.85)
            n += 1
        return total / n if n else 0.0

    def _danger(self, game, seat, t):
        d = _BASE_DANGER[t]
        for q in range(4):
            if q == seat or not game._active(q):
                continue
            op = game._p(q)
            disc = op.discards
            if disc and t in disc[-3:]:
                d *= 0.15
            elif t in disc:
                d *= 0.5
            meld_suits = {suit_of(x) for m in op.melds for x in m['tiles']}
            if suit_of(t) in meld_suits and suit_of(t) != 'honor':
                d *= 1.5
            if disc:
                recent = disc[-3:]
                for x in recent:
                    if x < HONOR_START and t < HONOR_START \
                            and suit_of(x) == suit_of(t):
                        dv = abs(x % 9 - t % 9)
                        if dv == 1:
                            d *= 1.35
                        elif dv == 2:
                            d *= 1.15
        # late game: everything becomes more dangerous
        progress = 1.0 - len(game.wall) / 136.0
        d *= 1.0 + 0.6 * max(0.0, progress - 0.6)
        return d

    def _ukeire(self, c, ext, vis):
        tot = 0
        for (t, copies) in H.effective_tiles(c, ext):
            left = 4 - vis[t]
            if left > 0:
                tot += left * copies
        return tot

    def _value_bonus(self, c, ext):
        """Small bonus for high-value shapes: one-suit push, chiitoi,
        kokushi."""
        b = 0.0
        n_hon = sum(c[HONOR_START:])
        n_man, n_pin, n_sou = sum(c[0:9]), sum(c[9:18]), sum(c[18:27])
        total = n_man + n_pin + n_sou + n_hon
        if total == 0:
            return 0.0
        if n_hon == 0 and max(n_man, n_pin, n_sou) == total:
            b += 2.5
        elif n_hon > 0 and max(n_man, n_pin, n_sou) + n_hon == total \
                and max(n_man, n_pin, n_sou) > 0:
            b += 1.2
        pairs = sum(1 for n in c if n >= 2)
        if pairs >= 4:
            b += 1.5
        kokushi_types = sum(1 for t in range(34)
                            if is_terminal_or_honor(t) and c[t] > 0)
        if kokushi_types >= 9:
            b += 2.0
        return b

    # ------------------------------------------------------------ decisions
    def choose_absence(self, game, seat):
        """定缺: discard the suit with the fewest tiles (keep it playable)."""
        p = game._p(seat)
        c = counts(p.hand)
        n = (sum(c[0:9]), sum(c[9:18]), sum(c[18:27]))
        mn = min(n)
        cands = [i for i, v in enumerate(n) if v == mn]
        suits = ('man', 'pin', 'sou')
        if len(cands) == 1:
            return suits[cands[0]]
        # tie: keep the suit whose tiles are more valuable (fewer 1/9)
        def keep_value(i):
            seg = c[i * 9:i * 9 + 9]
            return sum(seg[j] * (2 if j in (0, 8) else 1) for j in range(9))
        return suits[min(cands, key=keep_value)]

    def choose_gang(self, game, seat, opts):
        """Own-turn gang decision (concealed / bu-gang)."""
        p = game._p(seat)
        c = counts(p.hand)
        pairs = sum(1 for n in c if n >= 2)
        kokushi_types = sum(1 for t in range(34)
                            if is_terminal_or_honor(t) and c[t] > 0)
        # protect special hands
        if pairs >= 5 or kokushi_types >= 10:
            return None
        if game._is_tenpai(seat) and self.difficulty != 'easy':
            # gang may break a tenpai shape; only bu-gang is shape-neutral
            for o in opts:
                if o['type'] == 'gang_bu':
                    return o if self.rng.random() < 0.6 else None
            return None
        if self.difficulty == 'easy':
            return None if self.rng.random() < 0.5 else opts[0]
        if self.difficulty == 'medium':
            return None if self.rng.random() < 0.25 else opts[0]
        return opts[0]

    def wants_claim(self, game, seat, info):
        """Claim decision.  Returns True/False, or a chosen pair tuple for
        chi.  info: {'type':'peng'|'gang_ming'|'chi', 'from', 'tile'|
        'tiles':[pairs]}"""
        typ = info['type']
        p = game._p(seat)
        # Sichuan 定缺: never claim tiles of the absent suit
        if game.rules == 'sichuan' and p.absence:
            t = info.get('tile', -1)
            if t >= 0 and suit_of(t) == p.absence:
                return False
        ext = p.external()
        c = counts(p.hand)
        base = H.shanten(c, ext)
        if typ in ('peng', 'gang_ming'):
            need = 2 if typ == 'peng' else 3
            t = info['tile']
            c2 = list(c)
            c2[t] -= need
            if c2[t] < 0:
                return False
            s2 = H.shanten(c2, ext + 1)
            if s2 < base:
                return True
            if s2 == base:
                thr = 0.65 if typ == 'gang_ming' else 0.5
                if self.difficulty == 'easy':
                    thr = 0.3
                return self.rng.random() < thr
            return False
        if typ == 'chi':
            best = None
            best_s = 99
            for pair in info['tiles']:
                c2 = list(c)
                c2[pair[0]] -= 1
                c2[pair[1]] -= 1
                if c2[pair[0]] < 0 or c2[pair[1]] < 0:
                    continue
                s2 = H.shanten(c2, ext + 1)
                if s2 < best_s:
                    best_s = s2
                    best = pair
            if best is None:
                return None
            if best_s < base:
                return best
            if best_s == base:
                thr = 0.55 if self.difficulty != 'easy' else 0.3
                if self.rng.random() < thr:
                    return best
            return None
        return False

    # ------------------------------------------------------------ discard
    def choose_discard(self, game, seat):
        p = game._p(seat)
        hand = p.hand
        ext = p.external()
        c = counts(hand)
        base = H.shanten(c, ext)
        vis = self._visible(game, seat)

        # Sichuan 定缺: absence-suit tiles must be cleared first
        if game.rules == 'sichuan' and p.absence:
            abs_tiles = [t for t in _dedup(hand)
                         if suit_of(t) == p.absence]
            if abs_tiles:
                return self._clear_absence(hand, abs_tiles, ext)

        cands = _dedup(hand)
        if not cands:
            cands = _dedup(hand)

        if self.difficulty == 'hard' and base >= 1 \
                and len(game.wall) >= 30 and len(cands) > 1:
            best = self._monte_carlo(game, seat, hand, ext, cands, vis)
            if best is not None:
                return best
        return self._heuristic_discard(game, seat, c, ext, base, cands, vis)

    def _clear_absence(self, hand, abs_tiles, ext):
        """Dump the declared absence suit.  Pick the tile that hurts the
        hand least (shanten-minimizing), with a little noise."""
        c = counts(hand)
        best = 99
        bests = []
        for t in abs_tiles:
            c[t] -= 1
            s = H.shanten(c, ext)
            c[t] += 1
            if s < best:
                best = s
                bests = [t]
            elif s == best:
                bests.append(t)
        if len(bests) > 1 and self.rng.random() < 0.35:
            return self.rng.choice(bests)
        return bests[0]

    def _heuristic_discard(self, game, seat, c, ext, base, cands, vis):
        easy = self.difficulty == 'easy'
        opp_tp = 0.0 if easy else self._opp_tenpai_est(game, seat)
        # Fold only when clearly behind: 2+ shanten, opponents look strong,
        # and NEVER while pushing (0/1 shanten is always a push).
        fold = (not easy and base >= 3 and opp_tp >= 0.75
                and self.difficulty in ('medium', 'hard'))
        scored = []
        for t in cands:
            c2 = list(c)
            c2[t] -= 1
            s2 = H.shanten(c2, ext)
            uke = self._ukeire(c2, ext, vis)
            val = self._value_bonus(c2, ext)
            danger = self._danger(game, seat, t)
            scored.append((t, s2, uke, val, danger))
        # Greedy shanten filter: never regress while an improving or
        # shanten-neutral discard exists (ukeire must never outweigh
        # shanten progress — that pathology stalls hands at 2-3 shanten).
        if not fold:
            best_s = min(r[1] for r in scored)
            pool = [r for r in scored if r[1] == best_s]
        else:
            pool = scored
        scores = []
        for (t, s2, uke, val, danger) in pool:
            if fold:
                if s2 > base:
                    # forced regression while folding: play the safest tile
                    scores.append((t, -danger))
                elif s2 < base:
                    # the tile advances the hand: keep pushing through
                    s = (base - s2) * 10.0 + uke * 0.6 + val * 0.4 \
                        - danger * 0.25
                    scores.append((t, s))
                else:
                    # shanten-neutral while folding: safest first
                    scores.append((t, -danger))
                continue
            pb = self.policy.get(t, 0.0) if self.policy else 0.0
            s = uke * 0.9 + val * 0.4 - danger * (0.3 + 0.55 * opp_tp) \
                + pb * 2.0
            if easy:
                s += self.rng.uniform(-0.9, 0.9)
            else:
                s += self.rng.uniform(-0.25, 0.25)
            scores.append((t, s))
        if not scores:
            scores = [(t, 0.0) for t in cands]
        # easy AI sometimes blunders on purpose
        if easy and self.rng.random() < 0.08 and len(cands) > 1:
            return self.rng.choice(cands)
        scores.sort(key=lambda kv: -kv[1])
        return scores[0][0]

    # ------------------------------------------------------------ monte carlo
    def _monte_carlo(self, game, seat, hand, ext, cands, vis):
        """Simulate forward from each candidate discard against sampled
        opponent hands and walls; return the discard with the highest win
        rate (ties broken by the heuristic)."""
        unknown = [4 - vis[t] for t in range(34)]
        if sum(unknown) < 30:
            return None
        pool = []
        for t, n in enumerate(unknown):
            pool.extend([t] * n)

        active_others = [q for q in range(4)
                         if q != seat and game._active(q)]
        # sample opponent hand sizes (after a draw)
        opp_size = {q: 14 - 3 * game._p(q).external() for q in active_others}

        wins = {t: 0 for t in cands}
        trials = {t: 0 for t in cands}
        t0 = time.perf_counter()
        iters = 0
        while time.perf_counter() - t0 < MC_BUDGET and iters < MC_MAX_ITER:
            iters += 1
            deck = pool[:]
            self.rng.shuffle(deck)
            # deal opponents
            opp_hands = {}
            for q in active_others:
                opp_hands[q] = deck[:opp_size[q]]
                del deck[:opp_size[q]]
            wall = deck
            for cand in cands:
                trials[cand] += 1
                if self._simulate_one(game, seat, hand, cand, ext, wall,
                                      opp_hands, active_others):
                    wins[cand] += 1
        if not trials or sum(trials.values()) == 0:
            return None

        best = None
        best_key = None
        for t in cands:
            if trials[t] == 0:
                continue
            rate = wins[t] / trials[t]
            key = (rate, -self._danger(game, seat, t),
                   self._value_bonus_with(list(counts(hand)), ext, t))
            if best_key is None or key > best_key:
                best_key = key
                best = t
        return best

    def _value_bonus_with(self, c, ext, remove_tile):
        c[remove_tile] -= 1
        return self._value_bonus(c, ext)

    def _simulate_one(self, game, seat, hand, cand, ext, wall, opp_hands,
                      active_others):
        """Play ~MC_PLIES rounds forward from discarding `cand`.
        Returns True if the AI's hand wins."""
        my = list(hand)
        my.remove(cand)
        opp = {q: list(h) for q, h in opp_hands.items()}
        ext_opp = {q: game._p(q).external() for q in active_others}
        wi = 0
        n_wall = len(wall)
        order = [(seat + i) % 4 for i in (1, 2, 3)]

        for _ in range(MC_PLIES):
            # opponents draw + discard
            for q in order:
                if q not in opp:
                    continue
                if wi >= n_wall:
                    return False
                opp[q].append(wall[wi])
                wi += 1
                oc = counts(opp[q])
                if H.is_win(oc, ext_opp[q]):
                    return False
                d = self._fast_discard(opp[q], ext_opp[q])
                opp[q].remove(d)
                # can I win on their discard?
                mc = counts(my)
                mc[d] += 1
                if H.is_win(mc, ext):
                    return True
            # my draw
            if wi >= n_wall:
                return False
            my.append(wall[wi])
            wi += 1
            mc = counts(my)
            if H.is_win(mc, ext):
                return True
            d = self._fast_discard(my, ext)
            my.remove(d)
        return False

    def _fast_discard(self, hand14, ext):
        """Speed-oriented discard: minimize shanten, random tie-break."""
        c = counts(hand14)
        best = 99
        bests = []
        for t in _dedup(hand14):
            c[t] -= 1
            s = H.shanten(c, ext)
            c[t] += 1
            if s < best:
                best = s
                bests = [t]
            elif s == best:
                bests.append(t)
        return self.rng.choice(bests)
