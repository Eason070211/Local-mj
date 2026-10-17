# -*- coding: utf-8 -*-
"""Hand analysis: shanten (distance to win), effective tiles, win checks,
and winning-hand decomposition (used for fan detection).

All functions accept `external` = number of open melds (chi/peng/gang).
Open melds already count toward the 4 melds of a standard hand, so the
concealed part only needs (4 - external) more melds + a pair.  Chiitoi and
kokushi are only legal with external == 0.

Shanten formula (standard form):
    shanten = 8 - 2*(m+e) - min(t, 4-m-e) - h
m = melds formed from the concealed part, e = external melds,
t = taatsu (partial groups; pairs count as taatsu when not the head),
h = 1 if a pair is used as the head.  A winning hand scores -1.
"""
from functools import lru_cache

from .tiles import (TILE_TYPES, SUITS, HONOR_START, is_terminal_or_honor,
                    counts as tile_counts, from_counts)

_SUIT_LEN = 9


# --------------------------------------------------------------------------
# per-suit option enumeration (memoized)
# --------------------------------------------------------------------------

@lru_cache(maxsize=400000)
def _suit_options(c_tuple):
    """All attainable (melds, taatsu, head) triples for a 9-length count
    tuple.  head == 1 means one pair of this suit is the hand head."""
    c = list(c_tuple)
    results = set()

    def dfs(pos, m, t, h):
        if pos == _SUIT_LEN:
            results.add((m, t, h))
            return
        if c[pos] == 0:
            dfs(pos + 1, m, t, h)
            return
        dfs(pos + 1, m, t, h)                      # skip
        if c[pos] >= 3:                            # triplet
            c[pos] -= 3
            dfs(pos, m + 1, t, h)
            c[pos] += 3
        if pos <= 6 and c[pos] > 0 and c[pos + 1] > 0 and c[pos + 2] > 0:
            c[pos] -= 1; c[pos + 1] -= 1; c[pos + 2] -= 1
            dfs(pos, m + 1, t, h)
            c[pos] += 1; c[pos + 1] += 1; c[pos + 2] += 1
        if h == 0 and c[pos] >= 2:                 # pair as head
            c[pos] -= 2
            dfs(pos + 1, m, t, 1)
            c[pos] += 2
        if c[pos] >= 2:                            # pair as taatsu
            c[pos] -= 2
            dfs(pos + 1, m, t + 1, h)
            c[pos] += 2
        if pos <= 7 and c[pos] > 0 and c[pos + 1] > 0:   # seq taatsu
            c[pos] -= 1; c[pos + 1] -= 1
            dfs(pos + 1, m, t + 1, h)
            c[pos] += 1; c[pos + 1] += 1
        if pos <= 6 and c[pos] > 0 and c[pos + 2] > 0:   # gapped taatsu
            c[pos] -= 1; c[pos + 2] -= 1
            dfs(pos + 1, m, t + 1, h)
            c[pos] += 1; c[pos + 2] += 1

    dfs(0, 0, 0, 0)
    return frozenset(results)


@lru_cache(maxsize=20000)
def _honor_options(c_tuple):
    combos = {(0, 0, 0)}
    for i in range(7):
        n = c_tuple[i]
        new = set()
        for (m, t, h) in combos:
            new.add((m, t, h))
            if n >= 3:
                new.add((m + 1, t, h))
            if n >= 2:
                new.add((m, t + 1, h))
                if h == 0:
                    new.add((m, t, 1))
        combos = new
    return frozenset(combos)


# --------------------------------------------------------------------------
# shanten / win checks
# --------------------------------------------------------------------------

def _standard_shanten(c, external):
    cap = 4 - external
    groups = [_suit_options(tuple(c[s:e + 1])) for (s, e) in SUITS]
    groups.append(_honor_options(tuple(c[HONOR_START:HONOR_START + 7])))
    reachable = {(0, 0, 0)}
    for opts in groups:
        nxt = set()
        for (m, t, h) in reachable:
            for (m2, t2, h2) in opts:
                if h + h2 > 1:
                    continue
                nm = m + m2
                nt = t + t2
                if nm <= cap and nt <= cap:
                    nxt.add((nm, nt, h + h2))
        reachable = nxt
    best = 8
    for (m, t, h) in reachable:
        v = 2 * (m + external) + min(t, 4 - (m + external)) + h
        s = 8 - v
        if s < best:
            best = s
    return best


def _chiitoi_shanten(c):
    pairs = sum(n // 2 for n in c)
    return max(6 - min(pairs, 7), -1)


def _kokushi_shanten(c):
    types = 0
    has_pair = False
    for t in range(TILE_TYPES):
        if not is_terminal_or_honor(t):
            continue
        if c[t] > 0:
            types += 1
            if c[t] >= 2:
                has_pair = True
    return max(13 - types - (1 if has_pair else 0), -1)


def shanten(c, external=0):
    """Minimum shanten over legal hand forms.  c is a count vector."""
    if external > 0:
        return _standard_shanten(c, external)
    return min(_standard_shanten(c, 0), _chiitoi_shanten(c), _kokushi_shanten(c))


def shanten_of_hand(hand, external=0):
    return shanten(tile_counts(hand), external)


def is_win(c, external=0):
    """True if a (14 - 3*external)-tile concealed count vector completes a
    winning hand given `external` open melds."""
    return shanten(c, external) == -1


def is_win_hand(hand, external=0):
    return is_win(tile_counts(hand), external)


def effective_tiles(c, external=0):
    """Tiles whose addition strictly reduces shanten: [(tile, copies_left)]."""
    base = shanten(c, external)
    out = []
    for t in range(TILE_TYPES):
        if c[t] >= 4:
            continue
        c[t] += 1
        if shanten(c, external) < base:
            out.append((t, 4 - c[t] + 1))
        c[t] -= 1
    return out


def winning_tiles(c, external=0):
    """Tiles completing the hand (13-tile concealed part + external melds)."""
    out = []
    for t in range(TILE_TYPES):
        if c[t] >= 4:
            continue
        c[t] += 1
        if is_win(c, external):
            out.append(t)
        c[t] -= 1
    return out


# --------------------------------------------------------------------------
# decomposition of a winning concealed part (for fan detection)
# --------------------------------------------------------------------------

def decompose(hand, required_melds=4):
    """All decompositions of the concealed part as (melds, pair) with
    len(melds) == required_melds.  Empty if impossible."""
    c = tile_counts(hand)
    solutions = []

    def go(pos, melds, pair):
        if len(melds) > required_melds:
            return
        if pos == TILE_TYPES:
            if len(melds) == required_melds and pair is not None:
                solutions.append((list(melds), list(pair)))
            return
        n = c[pos]
        if n == 0:
            go(pos + 1, melds, pair)
            return
        if pos >= HONOR_START:
            if n >= 3:
                c[pos] -= 3
                melds.append(('triplet', (pos, pos, pos)))
                go(pos + 1, melds, pair)
                melds.pop()
                c[pos] += 3
            if n >= 2 and pair is None:
                c[pos] -= 2
                go(pos + 1, melds, (pos, pos))
                c[pos] += 2
            return
        if n >= 3:
            c[pos] -= 3
            melds.append(('triplet', (pos, pos, pos)))
            go(pos, melds, pair)
            melds.pop()
            c[pos] += 3
        if pos % 9 <= 6 and c[pos] > 0 and c[pos + 1] > 0 and c[pos + 2] > 0:
            c[pos] -= 1; c[pos + 1] -= 1; c[pos + 2] -= 1
            melds.append(('seq', (pos, pos + 1, pos + 2)))
            go(pos, melds, pair)
            melds.pop()
            c[pos] += 1; c[pos + 1] += 1; c[pos + 2] += 1
        if n >= 2 and pair is None:
            c[pos] -= 2
            go(pos + 1, melds, (pos, pos))
            c[pos] += 2

    go(0, [], None)
    return solutions


def chiitoi_pairs(c):
    """If the concealed part is chiitoi, return pair tile ids (a 4-of-a-kind
    counts as two pairs).  Empty otherwise."""
    if sum(c) != 14:
        return []
    pairs = []
    for t in range(TILE_TYPES):
        for _ in range(c[t] // 2):
            pairs.append(t)
    if len(pairs) == 7:
        return pairs
    return []


def is_kokushi(c):
    if sum(c) != 14:
        return False
    types = sum(1 for t in range(TILE_TYPES)
                if is_terminal_or_honor(t) and c[t] > 0)
    pair = any(c[t] >= 2 for t in range(TILE_TYPES)
               if is_terminal_or_honor(t))
    return types == 13 and pair and _kokushi_shanten(c) == -1


def hand_from_counts(c):
    return from_counts(c)
