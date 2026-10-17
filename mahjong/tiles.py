# -*- coding: utf-8 -*-
"""Tile definitions and helpers for Chinese Mahjong.

Tile ids:
  0..8    : 万 (man, characters)
  9..17   : 筒 (pin, dots)
  18..26  : 条 (sou, bamboos)
  27..33  : honors: 东 南 西 北 中 发 白
"""

# --- tile type constants ---------------------------------------------------
MAN_START = 0
PIN_START = 9
SOU_START = 18
HONOR_START = 27

TILE_TYPES = 34

SUITS = ((0, 8), (9, 17), (18, 26))          # man, pin, sou ranges
SUIT_NAMES = ('man', 'pin', 'sou')
HONORS = tuple(range(HONOR_START, HONOR_START + 7))

HONOR_NAMES = ('东', '南', '西', '北', '中', '发', '白')
SUIT_GLYPHS = ('万', '筒', '条')
NUMBER_GLYPHS = ('一', '二', '三', '四', '五', '六', '七', '八', '九')
NUMBER_GLYPHS_CN = ('壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖')

WIND_ORDER = ('东', '南', '西', '北')

# tile value (1..9) -> horse seat index for Guangdong buy-ma (common variant)
#   1,5,9 and 中发白 -> dealer seat; 2,6 -> south; 3,7 -> west; 4,8 -> north
HORSE_SEAT = {}
for v in (1, 5, 9):
    HORSE_SEAT[v] = 0
HORSE_SEAT[2] = HORSE_SEAT[6] = 1
HORSE_SEAT[3] = HORSE_SEAT[7] = 2
HORSE_SEAT[4] = HORSE_SEAT[8] = 3
for i in range(4):
    HORSE_SEAT[HONOR_START + i] = i
for i in range(4, 7):  # 中发白 -> dealer
    HORSE_SEAT[HONOR_START + i] = 0


def suit_of(t):
    """Return suit key ('man'|'pin'|'sou'|'honor') for tile id."""
    if t < PIN_START:
        return 'man'
    if t < SOU_START:
        return 'pin'
    if t < HONOR_START:
        return 'sou'
    return 'honor'


def value_of(t):
    """Return 1..9 for suited tiles, -1 for honors."""
    if t >= HONOR_START:
        return -1
    return t % 9 + 1


def is_terminal_or_honor(t):
    """True for 1/9 tiles and all honors (needed for kokushi / 幺九)."""
    return t >= HONOR_START or value_of(t) in (1, 9)


def tile_name(t):
    """Human readable name, e.g. '三萬', '七筒', '西'."""
    if t >= HONOR_START:
        return HONOR_NAMES[t - HONOR_START]
    suit = SUIT_GLYPHS[t // 9]
    return NUMBER_GLYPHS[t % 9] + suit


def tile_name_en(t):
    if t >= HONOR_START:
        return HONOR_NAMES[t - HONOR_START]
    return '%d%s' % (t % 9 + 1, SUIT_NAMES[t // 9])


def full_wall(include_honors=True):
    """Standard 136 (with honors) or 108 (Sichuan, no honors) tile wall."""
    end = HONOR_START + 7 if include_honors else HONOR_START
    return [t for t in range(end) for _ in range(4)]


def counts(hand):
    """Count vector (length 34) for a list of tile ids."""
    c = [0] * TILE_TYPES
    for t in hand:
        c[t] += 1
    return c


def from_counts(c):
    """Expand a count vector back into a tile-id list."""
    out = []
    for t, n in enumerate(c):
        out.extend([t] * n)
    return out


def sort_hand(hand):
    return sorted(hand)


def is_same_suit_pair(t1, t2):
    return suit_of(t1) == suit_of(t2)
