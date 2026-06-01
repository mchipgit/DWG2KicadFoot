from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re
from typing import Iterable, Sequence

import ezdxf
from ezdxf import bbox


@dataclass(frozen=True)
class ConverterOptions:
    footprint_name: str | None = None
    pad_name_regex: str = r"SIGLEPIN"
    pad_layers: Sequence[str] = ("1",)
    pin1_roundrect: bool = True
    graphic_layer: str = "F.SilkS"
    fab_layer: str = "F.Fab"
    courtyard_layer: str = "F.CrtYd"


@dataclass(frozen=True)
class ConversionResult:
    source_path: Path
    output_path: Path
    footprint_text: str
    pad_count: int
    graphic_count: int
    graphic_primitive_count: int


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _point(x: float, y: float) -> str:
    return f"{_fmt(x)} {_fmt(y)}"


def _transform_xy(x: float, y: float, origin_x: float, origin_y: float) -> tuple[float, float]:
    return x - origin_x, -(y - origin_y)


def _line(x1: float, y1: float, x2: float, y2: float, width: float, layer: str) -> str:
    return (
        "\t(fp_line\n"
        f"\t\t(start {_point(x1, y1)})\n"
        f"\t\t(end {_point(x2, y2)})\n"
        "\t\t(stroke\n"
        f"\t\t\t(width {_fmt(width)})\n"
        "\t\t\t(type solid)\n"
        "\t\t)\n"
        f"\t\t(layer \"{layer}\")\n"
        "\t)"
    )


def _rect(x1: float, y1: float, x2: float, y2: float, width: float, layer: str) -> str:
    return (
        "\t(fp_rect\n"
        f"\t\t(start {_point(x1, y1)})\n"
        f"\t\t(end {_point(x2, y2)})\n"
        "\t\t(stroke\n"
        f"\t\t\t(width {_fmt(width)})\n"
        "\t\t\t(type solid)\n"
        "\t\t)\n"
        "\t\t(fill no)\n"
        f"\t\t(layer \"{layer}\")\n"
        "\t)"
    )


def _circle(center: tuple[float, float], radius: float, width: float, layer: str) -> str:
    return (
        "\t(fp_circle\n"
        f"\t\t(center {_point(*center)})\n"
        f"\t\t(end {_point(center[0] + radius, center[1])})\n"
        "\t\t(stroke\n"
        f"\t\t\t(width {_fmt(width)})\n"
        "\t\t\t(type solid)\n"
        "\t\t)\n"
        "\t\t(fill no)\n"
        f"\t\t(layer \"{layer}\")\n"
        "\t)"
    )


def _arc_points(center: tuple[float, float], radius: float, start_angle: float, end_angle: float) -> list[tuple[float, float]]:
    sweep = end_angle - start_angle
    if sweep <= 0:
        sweep += 360.0
    segments = max(6, int(abs(sweep) / 15.0) + 1)
    points: list[tuple[float, float]] = []
    for index in range(segments + 1):
        angle_deg = start_angle + sweep * (index / segments)
        angle = math.radians(angle_deg)
        points.append((center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)))
    return points


def _entity_lines(entity, layer_name: str, origin_x: float, origin_y: float) -> list[str]:
    etype = entity.dxftype()
    if etype == "LINE":
        x1, y1 = _transform_xy(float(entity.dxf.start.x), float(entity.dxf.start.y), origin_x, origin_y)
        x2, y2 = _transform_xy(float(entity.dxf.end.x), float(entity.dxf.end.y), origin_x, origin_y)
        return [_line(x1, y1, x2, y2, 0.12, layer_name)]
    if etype == "ARC":
        center = (float(entity.dxf.center.x), float(entity.dxf.center.y))
        points = _arc_points(center, float(entity.dxf.radius), float(entity.dxf.start_angle), float(entity.dxf.end_angle))
        return [
            _line(
                *_transform_xy(start[0], start[1], origin_x, origin_y),
                *_transform_xy(end[0], end[1], origin_x, origin_y),
                0.12,
                layer_name,
            )
            for start, end in zip(points, points[1:])
        ]
    if etype == "CIRCLE":
        center = (float(entity.dxf.center.x), float(entity.dxf.center.y))
        points = _arc_points(center, float(entity.dxf.radius), 0.0, 360.0)
        return [
            _line(
                *_transform_xy(start[0], start[1], origin_x, origin_y),
                *_transform_xy(end[0], end[1], origin_x, origin_y),
                0.12,
                layer_name,
            )
            for start, end in zip(points, points[1:])
        ]
    if etype == "LWPOLYLINE":
        points = list(entity.get_points("xy"))
        if len(points) < 2:
            return []
        lines = [
            _line(
                *_transform_xy(start[0], start[1], origin_x, origin_y),
                *_transform_xy(end[0], end[1], origin_x, origin_y),
                0.12,
                layer_name,
            )
            for start, end in zip(points, points[1:])
        ]
        if entity.closed:
            lines.append(
                _line(
                    *_transform_xy(points[-1][0], points[-1][1], origin_x, origin_y),
                    *_transform_xy(points[0][0], points[0][1], origin_x, origin_y),
                    0.12,
                    layer_name,
                )
            )
        return lines
    return []


def _bounds_for_entities(entities: Iterable) -> tuple[float, float, float, float] | None:
    entity_list = list(entities)
    if not entity_list:
        return None
    try:
        extents = bbox.extents(entity_list)
    except Exception:
        return None
    if not extents.has_data:
        return None
    return extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y


def _normalize_layers(source_layers: Sequence[str]) -> set[str]:
    return {str(layer).strip() for layer in source_layers if str(layer).strip()}


def _matches_pad_name(name: str, pattern: str) -> bool:
    return re.search(pattern, name, re.IGNORECASE) is not None


def _iter_virtual_entities(insert):
    for entity in insert.virtual_entities():
        yield entity


def _circle_radius(entity) -> float:
    return float(entity.dxf.radius)


def convert_dxf_file(source_path: str | Path, output_dir: str | Path | None = None, options: ConverterOptions | None = None) -> ConversionResult:
    options = options or ConverterOptions()
    source_path = Path(source_path)
    document = ezdxf.readfile(str(source_path))
    modelspace = document.modelspace()
    pad_layers = _normalize_layers(options.pad_layers)

    footprint_name = options.footprint_name or source_path.stem
    output_dir_path = Path(output_dir) if output_dir is not None else source_path.parent
    output_path = output_dir_path / f"{footprint_name}.kicad_mod"

    pad_entries: list[dict[str, float | str]] = []
    graphic_entities: list[object] = []
    graphic_primitive_count = 0

    for entity in modelspace:
        if entity.dxftype() == "INSERT":
            block_name = entity.dxf.name
            virtual_entities = list(_iter_virtual_entities(entity))
            is_pad = _matches_pad_name(block_name, options.pad_name_regex)
            if is_pad:
                circle_entities = [item for item in virtual_entities if item.dxftype() == "CIRCLE"]
                hole_entity = min(circle_entities, key=_circle_radius, default=None)
                if hole_entity is None:
                    continue
                pad_geometry = [
                    item
                    for item in virtual_entities
                    if str(item.dxf.layer) in pad_layers and item is not hole_entity
                ]
                bounds = _bounds_for_entities(pad_geometry)
                if bounds is None:
                    continue
                min_x, min_y, max_x, max_y = bounds
                pad_entries.append(
                    {
                        "name": block_name,
                        "x": float(hole_entity.dxf.center.x),
                        "y": float(hole_entity.dxf.center.y),
                        "drill": float(hole_entity.dxf.radius) * 2.0,
                        "width": max_x - min_x,
                        "height": max_y - min_y,
                    }
                )
            else:
                for virtual in virtual_entities:
                    if str(virtual.dxf.layer) in pad_layers:
                        graphic_entities.append(virtual)
        else:
            if str(entity.dxf.layer) in pad_layers:
                graphic_entities.append(entity)

    pad_entries.sort(key=lambda pad: (round(float(pad["y"]), 3), float(pad["x"])))

    all_bounds: list[tuple[float, float, float, float]] = []
    for pad in pad_entries:
        half_w = float(pad["width"]) / 2.0
        half_h = float(pad["height"]) / 2.0
        all_bounds.append((float(pad["x"]) - half_w, float(pad["y"]) - half_h, float(pad["x"]) + half_w, float(pad["y"]) + half_h))
    for entity in graphic_entities:
        if entity.dxftype() == "LINE":
            xs = [float(entity.dxf.start.x), float(entity.dxf.end.x)]
            ys = [float(entity.dxf.start.y), float(entity.dxf.end.y)]
            all_bounds.append((min(xs), min(ys), max(xs), max(ys)))
        elif entity.dxftype() == "ARC":
            center = (float(entity.dxf.center.x), float(entity.dxf.center.y))
            radius = float(entity.dxf.radius)
            all_bounds.append((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius))
        elif entity.dxftype() == "CIRCLE":
            center = (float(entity.dxf.center.x), float(entity.dxf.center.y))
            radius = float(entity.dxf.radius)
            all_bounds.append((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius))
        elif entity.dxftype() == "LWPOLYLINE":
            points = list(entity.get_points("xy"))
            if points:
                xs = [float(x) for x, _ in points]
                ys = [float(y) for _, y in points]
                all_bounds.append((min(xs), min(ys), max(xs), max(ys)))

    if all_bounds:
        min_x = min(item[0] for item in all_bounds)
        min_y = min(item[1] for item in all_bounds)
        max_x = max(item[2] for item in all_bounds)
        max_y = max(item[3] for item in all_bounds)
    else:
        min_x = min_y = -5.0
        max_x = max_y = 5.0

    origin_x = (min_x + max_x) / 2.0
    origin_y = (min_y + max_y) / 2.0

    for pad in pad_entries:
        pad["x"], pad["y"] = _transform_xy(float(pad["x"]), float(pad["y"]), origin_x, origin_y)

    transformed_min_x, transformed_top_y = _transform_xy(min_x, max_y, origin_x, origin_y)
    transformed_max_x, transformed_bottom_y = _transform_xy(max_x, min_y, origin_x, origin_y)

    courtyard_margin = 0.5
    fab_margin = 0.35
    courtyard = (
        transformed_min_x - courtyard_margin,
        transformed_top_y - courtyard_margin,
        transformed_max_x + courtyard_margin,
        transformed_bottom_y + courtyard_margin,
    )
    fab = (
        transformed_min_x - fab_margin,
        transformed_top_y - fab_margin,
        transformed_max_x + fab_margin,
        transformed_bottom_y + fab_margin,
    )
    ref_y = courtyard[1] - 0.8
    value_y = courtyard[3] + 0.8
    center_x = (courtyard[0] + courtyard[2]) / 2.0

    lines: list[str] = []
    lines.append(f'(footprint "{footprint_name}"')
    lines.append("\t(version 20260206)")
    lines.append('\t(generator "dxf-to-kicad-pyqt")')
    lines.append('\t(layer "F.Cu")')
    lines.append(f'\t(descr "Converted from DXF: {source_path.name}")')
    lines.append('\t(tags "DXF converted footprint")')
    lines.append('\t(property "Reference" "REF**"')
    lines.append(f'\t\t(at 0 {_fmt(ref_y)} 0)')
    lines.append('\t\t(layer "F.SilkS")')
    lines.append('\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append('\t)')
    lines.append(f'\t(property "Value" "{footprint_name}"')
    lines.append(f'\t\t(at 0 {_fmt(value_y)} 0)')
    lines.append('\t\t(layer "F.Fab")')
    lines.append('\t\t(effects (font (size 1 1) (thickness 0.15)))')
    lines.append('\t)')
    lines.append('\t(attr through_hole)')
    lines.append('\t(duplicate_pad_numbers_are_jumpers no)')

    lines.append(_rect(courtyard[0], courtyard[1], courtyard[2], courtyard[3], 0.05, options.courtyard_layer))
    lines.append(_rect(fab[0], fab[1], fab[2], fab[3], 0.1, options.fab_layer))
    triangle = 0.45
    lines.append(_line(center_x - triangle, fab[1], center_x, fab[1] + triangle, 0.1, options.fab_layer))
    lines.append(_line(center_x, fab[1] + triangle, center_x + triangle, fab[1], 0.1, options.fab_layer))

    if pad_entries:
        first_pad = pad_entries[0]
        p1_x = float(first_pad["x"])
        p1_y = float(first_pad["y"])
        lines.append(_line(p1_x - 0.4, p1_y - 0.4, p1_x + 0.4, p1_y - 0.4, 0.1, options.fab_layer))
        lines.append(_line(p1_x - 0.4, p1_y - 0.4, p1_x, p1_y + 0.4, 0.1, options.fab_layer))
        lines.append(_line(p1_x + 0.4, p1_y - 0.4, p1_x, p1_y + 0.4, 0.1, options.fab_layer))

    for entity in graphic_entities:
        primitive = _entity_lines(entity, options.graphic_layer, origin_x, origin_y)
        if primitive:
            graphic_primitive_count += len(primitive)
            lines.extend(primitive)

    for index, pad in enumerate(pad_entries, start=1):
        pad_x = float(pad["x"])
        pad_y = float(pad["y"])
        width = max(float(pad["width"]), 0.01)
        height = max(float(pad["height"]), 0.01)
        drill = max(float(pad["drill"]), 0.01)

        if index == 1 and options.pin1_roundrect:
            ratio = min(width, height) / max(width, height)
            roundrect_rratio = max(min(ratio * 0.5, 0.49), 0.05)
            lines.append(f'\t(pad "{index}" thru_hole roundrect')
            lines.append(f'\t\t(at {_fmt(pad_x)} {_fmt(pad_y)} 0)')
            lines.append(f'\t\t(size {_fmt(width)} {_fmt(height)})')
            lines.append(f'\t\t(drill {_fmt(drill)})')
            lines.append('\t\t(layers "*.Cu" "*.Mask")')
            lines.append('\t\t(remove_unused_layers no)')
            lines.append(f'\t\t(roundrect_rratio {_fmt(roundrect_rratio)})')
            lines.append('\t)')
            continue

        pad_kind = "circle" if abs(width - height) < 0.01 else "oval"
        if pad_kind == "circle":
            height = width

        lines.append(f'\t(pad "{index}" thru_hole {pad_kind}')
        lines.append(f'\t\t(at {_fmt(pad_x)} {_fmt(pad_y)} 0)')
        lines.append(f'\t\t(size {_fmt(width)} {_fmt(height)})')
        lines.append(f'\t\t(drill {_fmt(drill)})')
        lines.append('\t\t(layers "*.Cu" "*.Mask")')
        lines.append('\t\t(remove_unused_layers no)')
        lines.append('\t)')

    lines.append('\t(embedded_fonts no)')
    lines.append(')')

    footprint_text = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(footprint_text)

    return ConversionResult(
        source_path=source_path,
        output_path=output_path,
        footprint_text=footprint_text,
        pad_count=len(pad_entries),
        graphic_count=len(graphic_entities),
        graphic_primitive_count=graphic_primitive_count,
    )
