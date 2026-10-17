# -*- coding: utf-8 -*-
"""Engine test suite.  Run:  python tests.py

Covers hand-analysis math, rule enforcement, and many full all-AI games per
rule set to exercise the whole state machine (deals, claims, gangs, rob,
Sichuan 缺一门/刮风下雨/查大叫, Guangdong 买马, round rotation).
"""
import random
import sys
import time

from mahjong import hand as H
from mahjong.tiles import counts, full_wall
from mahjong.game import Game
from mahjong.ai import AiPlayer

PASS = 0
FAIL = 0


def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok  %s' % name)
    else:
        FAIL += 1
        print('FAIL  %s  %s' % (name, extra))


def test_hand_math():
    print('== hand math ==')
    check('win: 123m456m789m111p22p',
          H.is_win(counts([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 10, 10])))
    check('shanten -1 on win', H.shanten(counts([0, 1, 2, 3, 4, 5, 6, 7, 8,
                                                 9, 9, 9, 10, 10])) == -1)
    check('kokushi win',
          H.is_win(counts([0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33, 0])))
    check('kokushi tenpai (13 tiles)',
          H.shanten(counts([0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33])) == 0)
    check('chiitoi win',
          H.is_win(counts([0, 0, 1, 1, 2, 2, 9, 9, 10, 10, 11, 11, 27, 27])))
    tp = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 10]
    check('tenpai shanten 0', H.shanten(counts(tp)) == 0)
    check('winning tiles 2p/3p',
          sorted(H.winning_tiles(counts(tp))) == [10, 11])
    check('1-shanten', H.shanten(counts([0, 1, 2, 3, 4, 5, 6, 7, 8,
                                         9, 9, 10, 27])) == 1)
    check('ext meld win (111p222p333p44p + 1 open)',
          H.is_win(counts([9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12]),
                   external=1))
    check('ext meld tenpai',
          H.shanten(counts([9, 9, 9, 10, 10, 10, 11, 11, 12, 13]),
                    external=1) == 0)
    check('decompose returns solutions',
          bool(H.decompose([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 10, 10])))
    check('dragon chiitoi', H.chiitoi_pairs(counts([0, 0, 0, 0, 1, 1, 2, 2,
                                                    9, 9, 10, 10, 11, 11])) ==
          [0, 0, 1, 2, 9, 10, 11])


def make_game(rules, difficulty='medium', seed=1):
    g = Game(rules, difficulty, buy_ma=2, seed=seed)
    for s in range(1, 4):
        g.ai[s] = AiPlayer(difficulty, g.rng, s)
    g.start_round()
    return g


def auto_play(g, max_actions=4000):
    """Drive a game to round end using a scripted human (seat 0) policy."""
    n = 0
    while g.phase not in ('round_end', 'game_end') and n < max_actions:
        n += 1
        phase = g.phase
        if phase == 'absence_wait':
            # declare the suit with the fewest tiles (same as the AI policy)
            from mahjong.tiles import counts as _cnt
            c = _cnt(g.players[0].hand)
            suits3 = (sum(c[0:9]), sum(c[9:18]), sum(c[18:27]))
            r = g.human_action({'type': 'absence',
                                'suit': ('man', 'pin', 'sou')
                                [suits3.index(min(suits3))]})
        elif phase == 'wait_human':
            leg = g.legal_for_human()
            if leg['zimo']:
                r = g.human_action({'type': 'hu'})
            elif leg['gang_options']:
                r = g.human_action({'type': 'gang', 'index': 0})
            else:
                hand = g.players[0].hand
                absence = g.players[0].absence
                cand = None
                if g.rules == 'sichuan' and absence:
                    abs_c = [t for t in hand
                             if t // 9 == {'man': 0, 'pin': 1,
                                           'sou': 2}[absence]]
                    if abs_c:
                        # 定缺: absence tiles must be cleared first
                        cand = abs_c[0]
                if cand is None:
                    for t in hand:
                        cand = t
                        break
                if cand is None:
                    cand = hand[0]
                r = g.human_action({'type': 'discard', 'tile': cand})
        elif phase == 'claim_wait':
            r = g.human_action({'type': 'claim', 'index': 0})
        elif phase == 'play':
            r = g.human_action({'type': 'discard', 'tile': g.players[0].hand[0]})
            if r:
                return 'stuck-play'
        else:
            return 'stuck-%s' % phase
        if r:
            return 'err-%s' % r
    if g.phase not in ('round_end', 'game_end'):
        return 'not-ended'
    return None


def invariants(g):
    """Tile conservation + score balance."""
    total = 0
    for p in g.players:
        total += len(p.hand)
        for m in p.melds:
            total += len(m['tiles'])
        total += len(p.discards)
    total += len(g.wall)
    expect = 136 if g.rules != 'sichuan' else 108
    expect -= g.ma_removed
    if total != expect:
        return 'tile-count %d != %d' % (total, expect)
    if abs(sum(p.score for p in g.players)) > 1e-9:
        return 'score not balanced: %r' % [p.score for p in g.players]
    return None


def test_full_games():
    print('== full all-AI games ==')
    # keep the test suite fast: shorten the MC budget (still exercises the
    # Monte Carlo code path); the real server uses the full budget
    import mahjong.ai as ai_mod
    saved = ai_mod.MC_BUDGET
    ai_mod.MC_BUDGET = 0.25
    try:
        t0 = time.time()
        games = 0
        for rules in ('classic', 'sichuan', 'guangdong'):
            for diff in ('easy', 'medium', 'hard'):
                for i in range(4):
                    g = make_game(rules, diff, seed=games * 13 + i)
                    err = auto_play(g)
                    games += 1
                    if err:
                        check('game %s/%s/%d' % (rules, diff, i), False, err)
                        continue
                    inv = invariants(g)
                    check('game %s/%s/%d ends cleanly' % (rules, diff, i),
                          inv is None, str(inv))
        print('  %d games in %.1fs' % (games, time.time() - t0))
    finally:
        ai_mod.MC_BUDGET = saved


def test_fan_detection():
    print('== fan detection ==')
    from mahjong.rules import detect_fan
    # classic 平胡
    names, total = detect_fan('classic',
                              [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 10, 10, 11],
                              [], 'zimo', {})
    check('classic 平胡 1', total == 1, str(names))
    # 清一色
    names, total = detect_fan('classic',
                              [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4],
                              [], 'zimo', {})
    check('classic 清一色', any(n == '清一色' for n, _ in names), str(names))
    # 七对
    names, total = detect_fan('classic',
                              [0, 0, 1, 1, 2, 2, 9, 9, 10, 10, 11, 11, 27, 27],
                              [], 'zimo', {})
    check('classic 七对 4', total == 4, str(names))
    # sichuan 清一色 2
    names, total = detect_fan('sichuan',
                              [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4],
                              [], 'zimo', {})
    check('sichuan 清一色 2', any(n == '清一色' and f == 2 for n, f in names),
          str(names))


def test_gang_settlement_rules():
    print('== gang settlement by ruleset ==')
    from mahjong.game import Game as G
    four = [4, 4, 4, 4]
    g = G('classic', 'easy', 0, seed=7)
    g.players[0].hand = four + [0, 1, 2, 10, 11, 19, 20, 27, 28]
    before = [p.score for p in g.players]
    g._apply_gang(0, {'type': 'gang_an', 'tiles': four})
    check('classic gang_an pays nothing',
          [p.score for p in g.players] == before,
          str([p.score for p in g.players]))

    g = G('guangdong', 'easy', 0, seed=7)
    g.players[0].hand = four + [0, 1, 2, 10, 11, 19, 20, 27, 28]
    before = [p.score for p in g.players]
    g._apply_gang(0, {'type': 'gang_an', 'tiles': four})
    check('guangdong gang_an pays nothing',
          [p.score for p in g.players] == before,
          str([p.score for p in g.players]))

    g = G('sichuan', 'easy', 0, seed=7)
    g.players[0].hand = four + [0, 1, 2, 10, 11, 19, 20, 27, 28]
    g._apply_gang(0, {'type': 'gang_an', 'tiles': four})
    scores = [p.score for p in g.players]
    check('sichuan gang_an pays 2 from each',
          scores == [6, -2, -2, -2], str(scores))


def test_multi_ron_conservation():
    print('== multi-ron / rob tile conservation ==')
    from mahjong.game import Game as G
    from mahjong.hand import winning_tiles
    from mahjong.tiles import counts

    # both seat 1 and seat 2 are one tile away from 七对 on tile X
    hand13 = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6]
    X = winning_tiles(counts(hand13))[0]
    g = G('sichuan', 'easy', 0, seed=9)
    g.start_round()
    g._p(1).hand = list(hand13)
    g._p(2).hand = list(hand13)
    g._p(0).discards = [X]
    g.wall.pop()              # balance: the planted discard came from the wall
    flags = {'gang_pao': False, 'rob_gang': False}
    g._apply_hu(1, 0, X, flags)   # first winner physically takes X
    g._apply_hu(2, 0, X, flags)   # second winner settles virtual copy
    check('multi-ron keeps tile conservation',
          invariants(g) is None, str(invariants(g)))
    check('second winner returns the virtual tile',
          g._p(2).hand.count(X) == hand13.count(X),
          str(sorted(g._p(2).hand)))

    # rob-gang: the robbed tile is still in the ganger's hand, not in the
    # discard pile, so the winner must not consume anything from discards
    g = G('sichuan', 'easy', 0, seed=11)
    g.start_round()
    g._p(1).hand = list(hand13)
    g._p(0).hand.append(X)        # the would-be ganger holds the 4th copy
    g._p(0).discards = [3, 7]     # unrelated discards, X never discarded
    g.wall.pop()                  # balance: hand +1, discards +2, wall -3
    g.wall.pop()
    g.wall.pop()
    g._apply_hu(1, 0, X, {'gang_pao': False, 'rob_gang': True})
    check('rob-gang keeps tile conservation',
          invariants(g) is None, str(invariants(g)))
    check('rob-gang does not eat the discard pile',
          g._p(0).discards == [3, 7], str(g._p(0).discards))


def main():
    test_hand_math()
    test_fan_detection()
    test_gang_settlement_rules()
    test_multi_ron_conservation()
    test_full_games()
    print('\n%d passed, %d failed' % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
