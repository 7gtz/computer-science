#!/usr/bin/env python3
"""Make MSI PRO Z690-A DDR4 ALC897 native layouts 100 and 101."""
import copy
import pathlib
import plistlib
import re
import struct
import sys

CODEC = 0x10EC0897
INIT_VERBS = [
    0x00270500, 0x01470500,
    0x0023A057, 0x00239057,
    0x00C36000, 0x00C35000,
    0x0143A000, 0x01439000,
    0x01470740, 0x01470C02,
]
HEADER = (b'<?xml version="1.0"?><!DOCTYPE plist PUBLIC '
          b'"-//Apple//DTD PLIST 1.0//EN" '
          b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
          b'<plist version="1.0">')


def load_frag(path):
    raw = path.read_bytes().strip()
    if not raw.startswith(b'<dict>') or not raw.endswith(b'</dict>'):
        raise ValueError(f'Unexpected AppleALC XML fragment: {path}')
    return plistlib.loads(HEADER + raw + b'</plist>')


def save_frag(path, obj):
    raw = plistlib.dumps(obj, fmt=plistlib.FMT_XML, sort_keys=False)
    match = re.search(rb'<plist version="1\.0">\s*(<dict>.*</dict>)\s*</plist>', raw, re.S)
    if not match:
        raise ValueError(f'Could not serialize {path}')
    path.write_bytes(match.group(1) + b'\n')
    if load_frag(path) != obj:
        raise ValueError(f'Fragment round-trip failed: {path}')


def words(raw):
    if len(raw) % 4:
        raise ValueError('Misaligned HDA verb data')
    return list(struct.unpack('>' + 'I' * (len(raw) // 4), raw))


def bytes_of(nums):
    return struct.pack('>' + 'I' * len(nums), *nums)


def pin_default(nums, nid, value):
    for pos, verb in enumerate(range(0x71C, 0x720)):
        prefix = (nid << 20) | (verb << 8)
        found = [i for i, x in enumerate(nums) if (x >> 8) == (prefix >> 8)]
        if len(found) != 1:
            raise ValueError(f'Expected unique pin verb {nid:#x}:{verb:#x}; got {len(found)}')
        nums[found[0]] = prefix | ((value >> (8 * pos)) & 0xFF)


def make_pin(base, layout):
    cfg = copy.deepcopy(base)
    cfg['LayoutID'] = layout
    cfg['Codec'] = f'MSI PRO Z690-A DDR4 ALC897 native layout {layout}'
    cfg.pop('Comment', None)
    nums = words(cfg['ConfigData'])
    pin_default(nums, 0x14, 0x01014010)
    pin_default(nums, 0x1B, 0x02214120 if layout == 100 else 0x411111F0)
    nums.extend(INIT_VERBS)
    cfg['ConfigData'] = bytes_of(nums)
    cfg['WakeConfigData'] = bytes_of(words(cfg.get('WakeConfigData', b'')) + INIT_VERBS)
    cfg['WakeVerbReinit'] = True
    return cfg


def make_platform(base, layout):
    plat = copy.deepcopy(base)
    pathmaps = plat['PathMaps']
    if len(pathmaps) != 1 or pathmaps[0]['PathMapID'] != 1:
        raise ValueError('Unexpected layout 66 path-map container')
    paths = pathmaps[0]['PathMap']
    if len(paths) != 5 or len(paths[3]) != 2:
        raise ValueError('Unexpected layout 66 output grouping')
    rear, front = copy.deepcopy(paths[3])
    if [x['NodeID'] for x in rear[0]] != [20, 12, 2]:
        raise ValueError('Rear path no longer matches ALC897 hardware')
    if [x['NodeID'] for x in front[0]] != [27, 15, 5]:
        raise ValueError('Headphone path no longer matches ALC897 hardware')
    rear[0][0]['Amp'] = {
        'Channels': [{'Bind': 1, 'Channel': 1},
                     {'Bind': 2, 'Channel': 2}],
        'MuteInputAmp': False,
        'PublishMute': True,
        'PublishVolume': False,
        'VolumeInputAmp': False,
    }
    rear[0][1]['Amp']['PublishVolume'] = False
    rear[0][1]['Amp']['VolumeInputAmp'] = False
    rear[0][2]['Amp']['MuteInputAmp'] = False
    paths[3:4] = [[rear], [front]] if layout == 100 else [[rear]]
    pathmaps[0]['PathMapID'] = layout
    return plat


def make_layout(base, layout):
    info = copy.deepcopy(base)
    info['LayoutID'] = layout
    ref = info['PathMapRef'][0]
    ref['PathMapID'] = layout
    ref.pop('IntSpeaker', None)
    if layout == 100:
        ref['Outputs'] = ['LineOut', 'Headphone', 'SPDIFOut']
    else:
        ref['Outputs'] = ['LineOut', 'SPDIFOut']
        ref.pop('Headphone', None)
    return info


def main():
    root = pathlib.Path(sys.argv[1]).resolve()
    codecdir = root / 'Resources' / 'ALC897'
    cfile = codecdir / 'Info.plist'
    pfile = root / 'Resources' / 'PinConfigs.kext' / 'Contents' / 'Info.plist'
    layout66 = load_frag(codecdir / 'layout66.xml')
    platform66 = load_frag(codecdir / 'Platforms66.xml')
    codec = plistlib.loads(cfile.read_bytes())
    pins = plistlib.loads(pfile.read_bytes())
    entries = pins['IOKitPersonalities']['as.vit9696.AppleALC']['HDAConfigDefault']
    basematches = [e for e in entries if e.get('CodecID') == CODEC and e.get('LayoutID') == 66]
    if len(basematches) != 1:
        raise ValueError('Expected exactly one existing ALC897 layout 66 pin config')
    for layout in (100, 101):
        if any(e.get('CodecID') == CODEC and e.get('LayoutID') == layout for e in entries):
            raise ValueError(f'Layout {layout} already exists in pin configs')
        if any(e['Id'] == layout for group in ('Layouts', 'Platforms')
               for e in codec['Files'][group]):
            raise ValueError(f'Layout {layout} already exists in codec list')
        save_frag(codecdir / f'layout{layout}.xml', make_layout(layout66, layout))
        save_frag(codecdir / f'Platforms{layout}.xml', make_platform(platform66, layout))
        entries.append(make_pin(basematches[0], layout))
        for kind, name in [('Layouts', f'layout{layout}'),
                           ('Platforms', f'Platforms{layout}')]:
            codec['Files'][kind].append({
                'Comment': f'MSI PRO Z690-A DDR4 layout {layout}',
                'Id': layout,
                'Path': f'{name}.xml.zlib',
            })
    pfile.write_bytes(plistlib.dumps(pins, sort_keys=False))
    cfile.write_bytes(plistlib.dumps(codec, sort_keys=False))
    for layout in (100, 101):
        config = next(e for e in entries if e.get('CodecID') == CODEC and e.get('LayoutID') == layout)
        if not set(INIT_VERBS).issubset(set(words(config['ConfigData']))):
            raise ValueError(f'Init verbs missing from layout {layout}')
        if load_frag(codecdir / f'Platforms{layout}.xml')['PathMaps'][0]['PathMapID'] != layout:
            raise ValueError(f'Path-map ID mismatch on layout {layout}')
    print('Verified native ALC897 layouts 100 and 101. No fixed stream tags or formats.')


if __name__ == '__main__':
    main()
