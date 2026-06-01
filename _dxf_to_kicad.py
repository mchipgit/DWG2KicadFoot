#!/usr/bin/env python3
"""DXF to KiCad footprint (.kicad_mod) converter.

CSI block convention:
- CSI_SIGLEPIN_* : through-hole pads  (CIRCLE = drill, oval outline = copper pad)
- TEXT on layer 1  (single integer) : pin number label near pad
- TEXT on layer 11 "X.XX mm"        : body dimension annotation
- Body outline derived from dimension annotations; courtyard = body + 0.5 mm.
- Output footprint name = DXF filename stem.
- Converted DXF is moved to .bak/ subfolder.
"""

import re
import shutil
from pathlib import Path

import ezdxf


# ---------------------------------------------------------------------------
def _decode_text(raw: str) -> str:
    """Decode \\U+XXXX escape sequences in DXF text and strip trailing NUL."""
    return re.sub(r'\\U\+([0-9A-Fa-f]{4})',
                  lambda m: chr(int(m.group(1), 16)), raw).rstrip('\x00')


def _get_pad_size(block, base_x: float, base_y: float):
    """Return (width, height) of the oval pad from layer-1 geometry."""
    xs, ys = [], []
    for e in block:
        if str(e.dxf.layer) != '1':
            continue
        if e.dxftype() == 'LINE':
            for pt in (e.dxf.start, e.dxf.end):
                xs.append(pt.x - base_x)
                ys.append(pt.y - base_y)
        elif e.dxftype() == 'ARC':
            r = e.dxf.radius
            xs.extend([e.dxf.center.x - base_x - r, e.dxf.center.x - base_x + r])
            ys.extend([e.dxf.center.y - base_y - r, e.dxf.center.y - base_y + r])
    if not xs or not ys:
        return 1.8, 2.4
    return round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4)


def _collect_pads(doc):
    """Return list of pad dicts with DXF-space x, y, drill, w, h."""
    msp = doc.modelspace()
    pads = []
    cache = {}
    for insert in msp.query('INSERT'):
        bname = insert.dxf.name
        if 'SIGLEPIN' not in bname:
            continue
        block = doc.blocks[bname]
        bp = block.block.dxf.base_point
        ins = insert.dxf.insert

        drill_r = 0.5
        for e in block:
            if e.dxftype() == 'CIRCLE':
                drill_r = e.dxf.radius
                break

        if bname not in cache:
            cache[bname] = _get_pad_size(block, bp.x, bp.y)
        pw, ph = cache[bname]

        pads.append({'x': ins.x, 'y': ins.y,
                     'drill': round(drill_r * 2, 3),
                     'w': pw, 'h': ph})
    return pads


def _collect_labels(msp):
    """Return list of (pin_number_int, x, y) for integer TEXT on layer 1."""
    labels = []
    for e in msp:
        if e.dxftype() != 'TEXT' or str(e.dxf.layer) != '1':
            continue
        txt = _decode_text(e.dxf.text).strip()
        if re.match(r'^\d+$', txt):
            labels.append((int(txt), e.dxf.insert.x, e.dxf.insert.y))
    return labels


def _body_dims(msp):
    """Parse 'X.XX mm' TEXT annotations on layer 11 → (width, height, cx, cy)."""
    dims = []
    for e in msp:
        if e.dxftype() != 'TEXT' or str(e.dxf.layer) != '11':
            continue
        txt = _decode_text(e.dxf.text)
        m = re.match(r'([\d.]+)\s*mm', txt)
        if m:
            dims.append((float(m.group(1)), e.dxf.insert.x, e.dxf.insert.y))
    if len(dims) < 2:
        return None
    # Larger value with higher y → width (horizontal); other → height (vertical)
    dims.sort(key=lambda d: d[0], reverse=True)
    a, ax, ay = dims[0]
    b, bx, by = dims[1]
    if ay > by:
        return a, b, ax, b / 2.0   # width=a, height=b, cx from text, cy=h/2
    else:
        return b, a, bx, a / 2.0


def _match_pins(pads, labels):
    """Greedy nearest-neighbour: return {pad_index: pin_str}."""
    used = set()
    pin_map = {}
    for num, lx, ly in labels:
        best_i, best_d = None, float('inf')
        for i, p in enumerate(pads):
            if i in used:
                continue
            d = (p['x'] - lx) ** 2 + (p['y'] - ly) ** 2
            if d < best_d:
                best_d, best_i = d, i
        if best_i is not None:
            pin_map[best_i] = str(num)
            used.add(best_i)
    return pin_map


def _fp_rect(layer, x1, y1, x2, y2, width):
    f = lambda v: round(v, 3)
    return (
        f'\t(fp_rect\n'
        f'\t\t(start {f(x1)} {f(y1)})\n'
        f'\t\t(end {f(x2)} {f(y2)})\n'
        f'\t\t(stroke (width {width}) (type solid))\n'
        f'\t\t(fill no)\n'
        f'\t\t(layer "{layer}")\n'
        f'\t)'
    )


# ---------------------------------------------------------------------------

def convert_dxf(dxf_path, out_dir=None, bak_dir=None):
    dxf_path = Path(dxf_path)
    fp_name = dxf_path.stem
    out_dir = Path(out_dir) if out_dir else dxf_path.parent
    bak_dir = Path(bak_dir) if bak_dir else dxf_path.parent / '.bak'

    print(f"Converting: {dxf_path.name}")
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    # ---- pads ----
    pads = _collect_pads(doc)
    if not pads:
        print("  ERROR: no pads found.")
        return
    pads.sort(key=lambda p: (round(p['y'], 1), p['x']))

    # ---- pin numbers ----
    labels = _collect_labels(msp)
    pin_map = _match_pins(pads, labels)

    # Assign pin numbers; fill gaps sequentially
    assigned = set(pin_map.values())
    next_seq = 1
    for i, p in enumerate(pads):
        if i in pin_map:
            p['num'] = pin_map[i]
        else:
            while str(next_seq) in assigned:
                next_seq += 1
            p['num'] = str(next_seq)
            assigned.add(str(next_seq))
            next_seq += 1

    # ---- body dimensions ----
    bd = _body_dims(msp)
    if bd:
        body_w, body_h, body_cx, body_cy = bd
        print(f"  Body: {body_w} × {body_h} mm  (center DXF: {body_cx:.3f}, {body_cy:.3f})")
    else:
        all_x = [p['x'] for p in pads]
        all_y = [p['y'] for p in pads]
        body_cx = (min(all_x) + max(all_x)) / 2
        body_cy = (min(all_y) + max(all_y)) / 2
        body_w = (max(all_x) - min(all_x)) + pads[0]['w'] + 1.0
        body_h = (max(all_y) - min(all_y)) + pads[0]['h'] + 1.0
        print(f"  Body (fallback): {body_w:.3f} × {body_h:.3f} mm")

    # ---- translate to KiCad coords (body center = origin, Y flipped) ----
    for p in pads:
        p['kx'] = round(p['x'] - body_cx, 4)
        p['ky'] = round(-(p['y'] - body_cy), 4)

    pin1_list = [p for p in pads if p['num'] == '1']
    p1 = pin1_list[0] if pin1_list else pads[0]

    hw, hh = body_w / 2, body_h / 2
    cyd_l, cyd_r = round(-hw - 0.5, 3), round(hw + 0.5, 3)
    cyd_t, cyd_b = round(-hh - 0.5, 3), round(hh + 0.5, 3)

    F = lambda v: round(v, 3)

    # ---- build kicad_mod ----
    out = []
    out.append(f'(footprint "{fp_name}"')
    out.append(f'\t(version 20260206)')
    out.append(f'\t(generator "dxf2kicad")')
    out.append(f'\t(layer "F.Cu")')
    out.append(f'\t(descr "")')
    out.append(f'\t(tags "")')

    out.append(f'\t(property "Reference" "REF**"')
    out.append(f'\t\t(at 0 {F(cyd_t - 1.0)} 0)')
    out.append(f'\t\t(layer "F.SilkS")')
    out.append(f'\t\t(effects (font (size 1 1) (thickness 0.15)))')
    out.append(f'\t)')

    out.append(f'\t(property "Value" "{fp_name}"')
    out.append(f'\t\t(at 0 {F(cyd_b + 1.0)} 0)')
    out.append(f'\t\t(layer "F.Fab")')
    out.append(f'\t\t(effects (font (size 1 1) (thickness 0.15)))')
    out.append(f'\t)')

    out.append(_fp_rect('F.CrtYd', cyd_l, cyd_t, cyd_r, cyd_b, 0.05))
    out.append(_fp_rect('F.Fab',  -hw,   -hh,    hw,    hh,   0.1))

    out.append(f'\t(attr through_hole)')
    out.append(f'\t(duplicate_pad_numbers_are_jumpers no)')

    # Pin-1 triangle on F.Fab
    m = 0.4
    x1k, y1k = p1['kx'], p1['ky']
    for sx, sy, ex, ey in [
        (x1k - m, y1k - m, x1k + m, y1k - m),
        (x1k - m, y1k - m, x1k,     y1k + m),
        (x1k + m, y1k - m, x1k,     y1k + m),
    ]:
        out.append(f'\t(fp_line')
        out.append(f'\t\t(start {F(sx)} {F(sy)})')
        out.append(f'\t\t(end {F(ex)} {F(ey)})')
        out.append(f'\t\t(stroke (width 0.1) (type solid))')
        out.append(f'\t\t(layer "F.Fab")')
        out.append(f'\t)')

    # Pads
    for p in pads:
        num = p['num']
        px, py = F(p['kx']), F(p['ky'])
        pw, ph = F(p['w']), F(p['h'])
        dr = F(p['drill'])
        if num == '1':
            out.append(f'\t(pad "{num}" thru_hole roundrect')
            out.append(f'\t\t(at {px} {py})')
            out.append(f'\t\t(size {pw} {ph})')
            out.append(f'\t\t(drill {dr})')
            out.append(f'\t\t(layers "*.Cu" "*.Mask")')
            out.append(f'\t\t(remove_unused_layers no)')
            rr = round(min(float(pw), float(ph)) / max(float(pw), float(ph)) * 0.5, 6)
            out.append(f'\t\t(roundrect_rratio {rr})')
            out.append(f'\t)')
        else:
            out.append(f'\t(pad "{num}" thru_hole oval')
            out.append(f'\t\t(at {px} {py})')
            out.append(f'\t\t(size {pw} {ph})')
            out.append(f'\t\t(drill {dr})')
            out.append(f'\t\t(layers "*.Cu" "*.Mask")')
            out.append(f'\t\t(remove_unused_layers no)')
            out.append(f'\t)')

    out.append(f'\t(embedded_fonts no)')
    out.append(')')

    out_path = out_dir / f'{fp_name}.kicad_mod'
    out_path.write_text('\n'.join(out) + '\n', encoding='utf-8')

    print(f"  Written: {out_path}")
    print(f"  Pads ({len(pads)}):")
    for p in pads:
        print(f"    Pin {p['num']:>3}: ({F(p['kx']):>7}, {F(p['ky']):>6})  "
              f"{F(p['w'])}x{F(p['h'])} mm  drill={F(p['drill'])} mm")

    # Move DXF to .bak/
    bak_dir.mkdir(exist_ok=True)
    bak_dst = bak_dir / dxf_path.name
    if bak_dst.exists():
        bak_dst.unlink()
    shutil.move(str(dxf_path), str(bak_dst))
    print(f"  Moved: {dxf_path.name} -> .bak/")


# ---------------------------------------------------------------------------

def main():
    import sys
    base_dir = Path(__file__).parent
    targets = [Path(a) for a in sys.argv[1:]] if len(sys.argv) > 1 \
              else sorted(base_dir.glob('*.dxf'))
    if not targets:
        print("No .dxf files found.")
        return
    for f in targets:
        try:
            convert_dxf(f)
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()


if __name__ == '__main__':
    main()
