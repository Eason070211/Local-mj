# -*- coding: utf-8 -*-
"""Multiplayer-engine regression tests (added for the LAN multiplayer build).

Run:  python tests_multi.py

Covers: turn enforcement, per-seat snapshots (no information leaks), mixed
human/AI tables, full 4-human rounds under all three rulesets, claims and
hu resolution between multiple humans.
"""
import sys

from mahjong.game import Game
from mahjong.ai import AiPlayer

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


def drive_human_seat(g, seat):
    """Pick a legal action for a human seat.  Returns None if none."""
    leg = g.legal_for_seat(seat)
    if g.phase == 'absence_wait' and leg['absence_options']:
        return {'type': 'absence', 'suit': leg['absence_options'][0]}
    if g.phase == 'wait_human' and g.turn == seat:
        if leg['zimo']:
            return {'type': 'hu'}
        if leg['gang_options']:
            return {'type': 'gang', 'index': 0}
        if leg['can_discard']:
            return {'type': 'discard', 'tile': g.players[seat].hand[0]}
    if g.phase == 'claim_wait' and leg['claims']:
        opts = leg['claims']
        hu = next((i for i, o in enumerate(opts) if o['type'] == 'hu'), None)
        if hu is not None:
            return {'type': 'claim', 'index': hu}
        pas = next((i for i, o in enumerate(opts) if o['type'] == 'pass'), None)
        if pas is not None:
            return {'type': 'claim', 'index': pas}
        return {'type': 'claim', 'index': 0}
    if g.phase == 'round_end':
        return {'type': 'next_round'}
    return None


def run_multi_game(rules, humans, buy_ma=0, max_rounds=2, max_actions=4000,
                   names=('甲', '乙', '丙', '丁')):
    """Drive a game where the given seats are human (rest are AI)."""
    g = Game(rules, 'medium', buy_ma=buy_ma, humans=humans, names=names)
    for s in range(4):
        if s not in g.humans:
            g.ai[s] = AiPlayer('medium', g.rng, s)
    g.start_round()
    acts = 0
    while acts < max_actions:
        if g.phase == 'round_end' and g.round_num >= max_rounds:
            break
        acted = False
        for s in sorted(g.humans):
            a = drive_human_seat(g, s)
            if a is None:
                continue
            err = g.seat_action(s, a)
            if err:
                return g, 'engine rejected %r: %s' % (a, err)
            acted = True
            break
        if not acted:
            # nobody could act: AI seats run inside _advance, so a human
            # seat must be the current actor here
            return g, 'no human could act at phase %s turn %s' % (
                g.phase, g.turn)
        acts += 1
    if acts >= max_actions:
        return g, 'hit max_actions at phase %s' % g.phase
    return g, None


print('multiplayer engine tests:')

# --- turn enforcement -------------------------------------------------
g = Game('classic', 'medium', humans=(0, 1, 2, 3), names=('A', 'B', 'C', 'D'))
g.start_round()
ok(g.phase == 'wait_human' and g.turn == 0, '4-human classic starts on dealer')
leg1 = g.legal_for_seat(1)
ok(not leg1['can_discard'] and not leg1['zimo'],
   'non-turn seat 1 gets no discard/zimo legal')
err = g.seat_action(1, {'type': 'discard', 'tile': g.players[1].hand[0]})
ok(err is not None and 'turn' in err, 'out-of-turn discard rejected (%s)' % err)
err = g.seat_action(4, {'type': 'discard', 'tile': 0})
ok(err is not None, 'non-human seat rejected')
err = g.seat_action(0, {'type': 'discard', 'tile': g.players[0].hand[0]})
ok(err is None and (g.turn == 1 or g.phase == 'claim_wait'),
   'seat 0 discards, game advances (turn=%d phase=%s)' % (g.turn, g.phase))

# --- privacy -----------------------------------------------------------
g = Game('classic', 'medium', humans=(0, 1, 2, 3), names=('A', 'B', 'C', 'D'))
g.start_round()
s1 = g.snapshot_for(1)
leak = any(('hand' in p and p.get('hand') is not None) for p in s1['players'])
ok(not leak, 'seat 1 snapshot does not leak other hands pre-round-end')
ok(len(s1['hand']) == 13 or len(s1['hand']) == 14,
   'seat 1 snapshot includes only own hand')
ok('pending_seat' in s1, 'snapshot exposes pending_seat field')

# --- 4-human full rounds, all rulesets --------------------------------
for rules in ('classic', 'sichuan', 'guangdong'):
    g, err = run_multi_game(rules, (0, 1, 2, 3), buy_ma=2 if rules == 'guangdong' else 0)
    ok(err is None and g.phase == 'round_end',
       '4-human %s runs %d rounds cleanly %s' % (rules, g.round_num, '' if err is None else err))
    total = sum(p.score for p in g.players)
    ok(total == 0, '%s score sum == 0 (%d)' % (rules, total))

# --- mixed human/AI tables ---------------------------------------------
g, err = run_multi_game('classic', (0, 2))
ok(err is None and g.phase == 'round_end',
   'mixed 2-human/2-AI runs cleanly %s' % (err or ''))
g, err = run_multi_game('sichuan', (1,))
ok(err is None and g.phase == 'round_end',
   'single human at seat 1 (AI elsewhere) runs cleanly %s' % (err or ''))

# --- claim decision warnings -------------------------------------------
g = Game('classic', 'medium', humans=(0, 1, 2, 3), names=('A', 'B', 'C', 'D'))
g.start_round()
# seat 1 holds 34567 8万 (tiles 2..8) + double 7万 + honors
g.players[1].hand = [2, 3, 4, 5, 6, 6, 7, 8, 27, 27, 28, 28, 29]
g._process_claims(0, 6, False)          # seat 0 discards 7万 (tile 6)
ok(g.phase == 'claim_wait' and g.pending.get('seat') == 1,
   'human claim prompt raised for seat 1')
peng_opt = next(o for o in g.pending['options'] if o['type'] == 'peng')
ok(peng_opt.get('warn') and '拆散' in peng_opt['warn'],
   'peng on 7万 warns about breaking the 45678 run (%s)' % peng_opt.get('warn'))

g2 = Game('classic', 'medium', humans=(0, 1, 2, 3), names=('A', 'B', 'C', 'D'))
g2.start_round()
g2.players[1].hand = [2, 3, 4, 5, 6, 7, 8, 27, 27, 28, 28, 29, 30]
g2._process_claims(0, 6, False)         # seat 0 discards 7万 again
ok(g2.phase == 'claim_wait', 'chi prompt raised')
chi_opt = next(o for o in g2.pending['options']
               if o['type'] == 'chi' and tuple(o.get('tiles', [])) == (4, 5))
ok(chi_opt.get('warn') and '拆散' in chi_opt['warn'],
   'chi 4-5-6 warns about breaking the longer run (%s)' % chi_opt.get('warn'))

print('\n%d passed, %d failed' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
