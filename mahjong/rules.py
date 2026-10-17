# -*- coding: utf-8 -*-
"""Scoring rules for the three supported rule sets.

  classic   : 经典推倒胡 (136 tiles, chi allowed, first win ends the round)
  sichuan   : 四川血战到底 (108 tiles, no chi, 缺一门, 刮风下雨,
              查大叫/花猪 at wall end)
  guangdong : 广东鸡平胡简化版 (136 tiles, optional 买马)

Fan values are multipliers of the base score (1).  Tables are documented
in RULES.md.  Payments are returned as [(seat, delta)] lists.
"""
from .tiles import (suit_of, from_counts, counts, HONOR_START, HORSE_SEAT)
from .hand import decompose, chiitoi_pairs, is_kokushi

# form fan values per ruleset
FORM_FAN = {
    'classic':   {'十三幺': 8, '龙七对': 8, '七对': 4, '清一色': 4,
                  '混一色': 2, '对对胡': 2, '平胡': 1, '字一色': 8},
    'guangdong': {'十三幺': 8, '龙七对': 8, '七对': 4, '清一色': 4,
                  '混一色': 2, '对对胡': 2, '平胡': 1, '字一色': 8},
    'sichuan':   {'龙七对': 8, '七对': 4, '清一色': 2, '对对胡': 2,
                  '平胡': 1},
}
SICHUAN_EXTRA = {'全带幺': 2, '根': 1, '杠上开花': 2, '杠上炮': 2, '抢杠': 2}
CLASSIC_EXTRA = {'杠上开花': 2, '抢杠': 2, '海底捞月': 2}


def _hand_form(c, ruleset):
    """Detect hand form.  Returns (form_key, names) where names is a list of
    (fan_name, fan) tuples already valued for the ruleset."""
    table = FORM_FAN[ruleset]
    names = []
    if is_kokushi(c):
        return 'kokushi', [('十三幺', table['十三幺'])]
    pairs = chiitoi_pairs(c)
    if pairs:
        if any(c[t] == 4 for t in pairs):
            return 'chiitoi', [('龙七对', table['龙七对'])]
        return 'chiitoi', [('七对', table['七对'])]

    hand = from_counts(c)
    sols = decompose(hand)
    all_seq = any(all(k == 'seq' for (k, _) in sols[i][0]) for i in range(len(sols)))
    all_triplet = any(all(k == 'triplet' for (k, _) in sols[i][0]) for i in range(len(sols)))

    suits = {suit_of(t) for t in range(34) if c[t] > 0}
    suited = suits - {'honor'}
    if 'honor' in suits and len(suited) == 1:
        names.append(('混一色', table['混一色']))
    elif 'honor' not in suits and len(suited) == 1:
        names.append(('清一色', table['清一色']))
    if 'honor' in suits and len(suited) == 0:
        names.append(('字一色', table['字一色']))

    if all_triplet and not all_seq:
        names.append(('对对胡', table['对对胡']))
    else:
        names.append(('平胡', table['平胡']))
    return 'standard', names


def _all_daiyao(c, melds):
    """全带幺: every meld and the pair contain a 1/9/honor tile."""
    def meld_ok(tiles):
        return any(t >= HONOR_START or (t < HONOR_START and t % 9 in (0, 8))
                   for t in tiles)
    for m in melds:
        if not meld_ok(m['tiles']):
            return False
    hand = from_counts(c)
    sols = decompose(hand)
    for (concealed_melds, pair) in sols:
        if meld_ok(pair) and all(meld_ok(m[1]) for m in concealed_melds):
            return True
    return False


def _root_count(c, melds):
    """Sichuan 根: each tile type with 4 copies across hand+melds adds 1."""
    cc = list(c)
    for m in melds:
        for t in m['tiles']:
            cc[t] += 1
    return sum(1 for n in cc if n >= 4)


def detect_fan(ruleset, hand14, melds, win_kind, flags):
    """Compute fan names and total for a winning hand.

    win_kind : 'zimo' | 'dianpao'
    flags    : dict with 'gang_draw', 'gang_pao', 'rob_gang', 'haidi'.
    Returns (names, total).
    """
    c = counts(hand14)
    form, names = _hand_form(c, ruleset)

    if ruleset == 'sichuan':
        if form == 'standard':
            if _all_daiyao(c, melds):
                names.append(('全带幺', SICHUAN_EXTRA['全带幺']))
            for _ in range(_root_count(c, melds)):
                names.append(('根', SICHUAN_EXTRA['根']))
        if flags.get('gang_draw'):
            names.append(('杠上开花', SICHUAN_EXTRA['杠上开花']))
        if flags.get('gang_pao'):
            names.append(('杠上炮', SICHUAN_EXTRA['杠上炮']))
        if flags.get('rob_gang'):
            names.append(('抢杠', SICHUAN_EXTRA['抢杠']))
    else:
        if flags.get('gang_draw'):
            names.append(('杠上开花', CLASSIC_EXTRA['杠上开花']))
        if flags.get('rob_gang'):
            names.append(('抢杠', CLASSIC_EXTRA['抢杠']))
        if flags.get('haidi') and win_kind == 'zimo':
            names.append(('海底捞月', CLASSIC_EXTRA['海底捞月']))

    # dedupe, keep order
    seen = set()
    uniq = []
    for (n, f) in names:
        if n not in seen:
            seen.add(n)
            uniq.append((n, f))
    total = sum(f for (_, f) in uniq)
    return uniq, max(total, 1)


# --------------------------------------------------------------------------
# payments
# --------------------------------------------------------------------------

def settle_win(ruleset, winner, from_seat, fan, active_seats):
    """classic/guangdong: zimo -> the 3 others pay fan; dianpao -> discarder.
    sichuan 血战: zimo -> every other *active* player pays fan; dianpao ->
    discarder pays.  Active means still in the round (not yet won)."""
    deltas = []
    if from_seat is None:
        for s in active_seats:
            if s != winner:
                deltas.append((s, -fan))
                deltas.append((winner, +fan))
    else:
        deltas.append((from_seat, -fan))
        deltas.append((winner, +fan))
    return deltas


def settle_gang_sichuan(gang_type, gang_seat, from_seat, active_seats):
    """刮风下雨 immediate gang payments (Sichuan):
    直杠(gang_ming): the discarder pays 2 fan; 补杠(gang_bu): every other
    active player pays 1; 暗杠(gang_an): every other active player pays 2."""
    deltas = []
    if gang_type == 'gang_ming':
        deltas.append((from_seat, -2))
        deltas.append((gang_seat, +2))
    elif gang_type == 'gang_bu':
        for s in active_seats:
            if s != gang_seat:
                deltas.append((s, -1))
                deltas.append((gang_seat, +1))
    elif gang_type == 'gang_an':
        for s in active_seats:
            if s != gang_seat:
                deltas.append((s, -2))
                deltas.append((gang_seat, +2))
    return deltas


def settle_ma(winner, fan, horses):
    """Guangdong 买马 settlement.  Each horse tile maps to a seat; if that
    seat is the winner every other player pays one extra `fan`, otherwise
    the matched seat pays one extra `fan` to the winner.
    Returns (horse_events, deltas)."""
    deltas = []
    events = []
    for t in horses:
        seat = HORSE_SEAT.get(t, 0)
        if seat == winner:
            for s in range(4):
                if s != winner:
                    deltas.append((s, -fan))
                    deltas.append((winner, +fan))
            events.append({'tile': t, 'seat': seat, 'is_winner': True})
        else:
            deltas.append((seat, -fan))
            deltas.append((winner, +fan))
            events.append({'tile': t, 'seat': seat, 'is_winner': False})
    return events, deltas
