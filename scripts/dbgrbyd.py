#!/usr/bin/env python3

import itertools as it
import math as m
import struct


def crc32c(data, crc=0):
    crc ^= 0xffffffff
    for b in data:
        crc ^= b
        for j in range(8):
            crc = (crc >> 1) ^ ((crc & 1) * 0x82f63b78)
    return 0xffffffff ^ crc

def fromleb128(data):
    word = 0
    for i, b in enumerate(data):
        word |= ((b & 0x7f) << 7*i)
        word &= 0xffffffff
        if not b & 0x80:
            return word, i+1
    return word, len(data)

def fromtag(data):
    tag, delta1 = fromleb128(data)
    size, delta2 = fromleb128(data[delta1:])
    return tag & 1, tag >> 1, size, delta1+delta2

def popc(x):
    return bin(x).count('1')

def xxd(data, width=16, crc=False):
    for i in range(0, len(data), width):
        yield '%-*s %-*s' % (
            3*width,
            ' '.join('%02x' % b for b in data[i:i+width]),
            width,
            ''.join(
                b if b >= ' ' and b <= '~' else '.'
                for b in map(chr, data[i:i+width])))

def tagrepr(tag, size, off=None):
    type1 = tag & 0x7f
    type2 = (tag >> 7) & 0xff
    id = (tag >> 15) & 0xffff

    if (type1 & 0x7e) == 0x40:
        return '%screate x%02x id%d%s' % (
            '~' if type1 & 0x1 else '',
            type2,
            id,
            ' %d' % size if not type1 & 0x1 else '')
    elif (type1 & 0x7e) == 0x48:
        return '%sdelete x%02x id%d%s' % (
            '~' if type1 & 0x1 else '',
            type2,
            id,
            ' %d' % size if not type1 & 0x1 else '')
    elif (type1 & 0x7e) == 0x50:
        return '%sstruct x%02x id%d%s' % (
            '~' if type1 & 0x1 else '',
            type2,
            id,
            ' %d' % size if not type1 & 0x1 else '')
    elif (type1 & 0x7e) == 0x60:
        return '%suattr x%02x id%d%s' % (
            '~' if type1 & 0x1 else '',
            type2,
            id,
            ' %d' % size if not type1 & 0x1 else '')
    elif (type1 & 0x7e) == 0x08:
        return '%stail%s%s' % (
            '~' if type1 & 0x1 else '',
            ' x%02x' % type2 if type2 else '',
            ' %d' % size if not type1 & 0x1 else '')
    elif (type1 & 0x7e) == 0x10:
        return '%sgstate x%02x%s' % (
            '~' if type1 & 0x1 else '',
            type2,
            ' %d' % size if not type1 & 0x1 else '')
    elif (type1 & 0x7e) == 0x02:
        return 'crc%x%s %d' % (
            type1 >> 3,
            ' x%02x' % type2 if type2 else '',
            size)
    elif type1 == 0x0a:
        return 'fcrc%s %d' % (
            ' x%02x' % type2 if type2 else '',
            size)
    elif type1 & 0x4:
        return 'alt%s%s x%x %s' % (
            'r' if type1 & 1 else 'b',
            'gt' if type1 & 2 else 'lt',
            tag & ~0x7,
            'x%x' % (0xffffffff & (off-size))
                if off is not None
                else '-%d' % off)
    else:
        return 'x%02x x%02x id%d %d' % (type1, type2, id, size)


def main(disk, block_size, block1, block2=None, *,
        color='auto',
        **args):
    # figure out what color should be
    if color == 'auto':
        color = sys.stdout.isatty()
    elif color == 'always':
        color = True
    else:
        color = False

    # read each block
    blocks = [block for block in [block1, block2] if block is not None]
    with open(disk, 'rb') as f:
        datas = []
        for block in blocks:
            f.seek(block * block_size)
            datas.append(f.read(block_size))

    # first figure out which block as the most recent revision
    def fetch(data):
        rev, = struct.unpack('<I', data[0:4].ljust(4, b'\0'))
        crc = crc32c(data[0:4])
        off = 0
        j = 4
        while j < block_size:
            v, tag, size, delta = fromtag(data[j:])
            if v != popc(crc) & 1:
                break
            crc = crc32c(data[j:j+delta], crc)
            j += delta

            if not tag & 0x4:
                if (tag & 0x7e) != 0x2:
                    crc = crc32c(data[j:j+size], crc)
                # found a crc?
                else:
                    crc_, = struct.unpack('<I', data[j:j+4].ljust(4, b'\0'))
                    if crc != crc_:
                        break
                    # commit what we have
                    off = j + size
                j += size

        return rev, off

    revs, offs = [], []
    i = 0
    for block, data in zip(blocks, datas):
        rev, off = fetch(data)
        revs.append(rev)
        offs.append(off)

        # compare with sequence arithmetic
        if off and ((rev - revs[i]) & 0x80000000):
            i = len(revs)-1

    # print contents of the winning metadata block
    block, data, rev, off = blocks[i], datas[i], revs[i], offs[i]
    print('mdir 0x%x, rev %d, size %d%s' % (
        block, rev, off,
        ' (was 0x%x, %d, %d)' % (blocks[~i], revs[~i], offs[~i])
            if len(blocks) > 1 else ''))
    print('%-8s  %-22s  %s' % (
        'off', 'tag',
        'data (truncated)'
            if not args.get('no_truncate') else ''))

    # print revision count
    crc = crc32c(data[0:4])
    if args.get('raw'):
        print('%08x: %s' % (0, next(xxd(data[0:4]))))

    # preprocess jumps
    if args.get('jumps'):
        jumps = []
        j = 4
        while j < (block_size if args.get('all') else off):
            j_ = j
            v, tag, size, delta = fromtag(data[j:])
            j += delta
            if not tag & 0x4:
                j += size

            if tag & 0x4:
                # figure out which alt color
                if tag & 0x1:
                    _, ntag, _, _ = fromtag(data[j:])
                    if ntag & 0x1:
                        jumps.append((j_, j_-size, 0, 'y'))
                    else:
                        jumps.append((j_, j_-size, 0, 'r'))
                else:
                    jumps.append((j_, j_-size, 0, 'b'))

        # figure out x-offsets to avoid collisions between jumps
        for j in range(len(jumps)):
            a, b, _, c = jumps[j]
            x = 0
            while any(
                    max(a, b) >= min(a_, b_)
                        and max(a_, b_) >= min(a, b)
                        and x == x_
                    for a_, b_, x_, _ in jumps[:j]):
                x += 1
            jumps[j] = a, b, x, c

        def jumprepr(j):
            # render jumps
            chars = {}
            for a, b, x, c in jumps:
                c_start = (
                    '\x1b[33m' if color and c == 'y'
                    else '\x1b[31m' if color and c == 'r'
                    else '\x1b[90m' if color
                    else '')
                c_stop = '\x1b[m' if color else ''

                if j == a:
                    for x_ in range(2*x+1):
                        chars[x_] = '%s-%s' % (c_start, c_stop)
                    chars[2*x+1] = '%s\'%s' % (c_start, c_stop)
                elif j == b:
                    for x_ in range(2*x+1):
                        chars[x_] = '%s-%s' % (c_start, c_stop)
                    chars[2*x+1] = '%s.%s' % (c_start, c_stop)
                    chars[0] = '%s<%s' % (c_start, c_stop)
                elif j >= min(a, b) and j <= max(a, b):
                    chars[2*x+1] = '%s|%s' % (c_start, c_stop)

            return ''.join(chars.get(x, ' ')
                for x in range(max(chars.keys(), default=0)+1))

    # print tags
    if args.get('rbyd'):
        alts = []
    j = 4
    while j < (block_size if args.get('all') else off):
        notes = []

        j_ = j
        v, tag, size, delta = fromtag(data[j:])
        if v != popc(crc) & 1:
            notes.append('v!=%x' % (popc(crc) & 1))
        crc = crc32c(data[j:j+delta], crc)
        j += delta

        if not tag & 0x4:
            if (tag & 0x7e) != 0x2:
                crc = crc32c(data[j:j+size], crc)
            # found a crc?
            else:
                crc_, = struct.unpack('<I', data[j:j+4].ljust(4, b'\0'))
                if crc != crc_:
                    notes.append('crc!=%08x' % crc)
            j += size

        if not args.get('in_tree') or (tag & 0x6) != 2:
            if args.get('raw'):
                # show on-disk encoding of tags
                for o, line in enumerate(xxd(data[j_:j_+delta])):
                    print('%s%8s: %s%s' % (
                        '\x1b[90m' if color and j_ >= off else '',
                        '%04x' % (j_ + o*16),
                        line,
                        '\x1b[m' if color and j_ >= off else ''))

        if not args.get('in_tree') or (tag & 0x7) == 0:
            # show human-readable tag representation
            print('%s%08x: %-57s%s%s' % (
                '\x1b[90m' if color and j_ >= off else '',
                j_,
                '%-22s%s' % (
                    tagrepr(tag, size, j_),
                    '  %s' % next(xxd(
                            data[j_+delta:j_+delta+min(size, 8)], 8), '')
                        if not args.get('no_truncate')
                            and not tag & 0x4 else ''),
                '\x1b[m' if color and j_ >= off else '',
                '  (%s)' % ', '.join(notes) if notes
                else '  %s' % ''.join(
                        ('\x1b[33my\x1b[m' if color else 'y')
                            if alts[i] & 0x1
                            and i+1 < len(alts)
                            and alts[i+1] & 0x1
                        else ('\x1b[31mr\x1b[m' if color else 'r')
                            if alts[i] & 0x1
                        else ('\x1b[90mb\x1b[m' if color else 'b')
                        for i in range(len(alts)-1, -1, -1))
                    if args.get('rbyd') and (tag & 0x7) == 0
                else '  %s' % jumprepr(j_) if args.get('jumps')
                else ''))

            # show in-device representation, including some extra
            # crc/parity info
            if args.get('device'):
                print('%s%8s  %-47s  %08x %x%s' % (
                    '\x1b[90m' if color and j_ >= off else '',
                    '',
                    '%-22s%s' % (
                        '%08x %08x' % (tag, size),
                        '  %s' % ' '.join(
                                '%08x' % struct.unpack('<I',
                                    data[j_+delta+i*4:j_+delta+i*4+4])
                                for i in range(min(size//4, 3)))[:23]
                            if not tag & 0x4 else ''),
                    crc,
                    popc(crc) & 1,
                    '\x1b[m' if color and j_ >= off else ''))

        if not tag & 0x4 and (not args.get('in_tree') or (tag & 0x6) != 2):
            # show on-disk encoding of data
            if args.get('raw') or args.get('no_truncate'):
                for o, line in enumerate(xxd(data[j_+delta:j_+delta+size])):
                    print('%s%8s: %s%s' % (
                        '\x1b[90m' if color and j_ >= off else '',
                        '%04x' % (j_+delta + o*16),
                        line,
                        '\x1b[m' if color and j_ >= off else ''))

        if args.get('rbyd'):
            if tag & 0x4:
                alts.append(tag)
            else:
                alts = []


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        description="Debug rbyd metadata.",
        allow_abbrev=False)
    parser.add_argument(
        'disk',
        help="File containing the block device.")
    parser.add_argument(
        'block_size',
        type=lambda x: int(x, 0),
        help="Block size in bytes.")
    parser.add_argument(
        'block1',
        type=lambda x: int(x, 0),
        help="Block address of the first metadata block.")
    parser.add_argument(
        'block2',
        nargs='?',
        type=lambda x: int(x, 0),
        help="Block address of the second metadata block.")
    parser.add_argument(
        '--color',
        choices=['never', 'always', 'auto'],
        default='auto',
        help="When to use terminal colors. Defaults to 'auto'.")
    parser.add_argument(
        '-a', '--all',
        action='store_true',
        help="Don't stop parsing on bad commits.")
    parser.add_argument(
        '-i', '--in-tree',
        action='store_true',
        help="Only show tags in the tree.")
    parser.add_argument(
        '-r', '--raw',
        action='store_true',
        help="Show the raw data including tag encodings.")
    parser.add_argument(
        '-x', '--device',
        action='store_true',
        help="Show the device-side representation of tags.")
    parser.add_argument(
        '-T', '--no-truncate',
        action='store_true',
        help="Don't truncate, show the full contents.")
    parser.add_argument(
        '-y', '--rbyd', 
        action='store_true',
        help="Show the rbyd tree in the margin.")
    parser.add_argument(
        '-j', '--jumps',
        action='store_true',
        help="Show alt pointer jumps in the margin.")
    sys.exit(main(**{k: v
        for k, v in vars(parser.parse_intermixed_args()).items()
        if v is not None}))
