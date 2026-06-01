from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence

import ezdxf


def _fmt(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in {"-0", ""} else "0"


def _point(x: float, y: float, scale: float, offset_x: float, offset_y: float) -> tuple[float, float]:
    return x * scale + offset_x, y * scale + offset_y


def _stroke(layer: str, stroke_width: float) -> str:
    return f'(stroke (width {_fmt(stroke_width)}) (type solid)) (layer {layer})'


def _line(start: tuple[float, float], end: tuple[float, float], layer: str, stroke_width: float) -> str:
    return (
        f'  (fp_line (start {_fmt(start[0])} {_fmt(start[1])}) '
        f'(end {_fmt(end[0])} {_fmt(end[1])}) {_stroke(layer, stroke_width)})'
    )


def _circle(center: tuple[float, float], radius: float, layer: str, stroke_width: float) -> str:
    end = (center[0] + radius, center[1])
    return (
        f'  (fp_circle (center {_fmt(center[0])} {_fmt(center[1])}) '
        f'(end {_fmt(end[0])} {_fmt(end[1])}) {_stroke(layer, stroke_width)})'
    )


def _arc_points(cx: float, cy: float, radius: float, start_angle: float, end_angle: float) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    while end_angle <= start_angle:
        end_angle += 360.0
    mid_angle = start_angle + (end_angle - start_angle) / 2.0

    def polar(angle_deg: float) -> tuple[float, float]:
        angle = math.radians(angle_deg)
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    return polar(start_angle), polar(mid_angle), polar(end_angle)


def _arc(center_x: float, center_y: float, radius: float, start_angle: float, end_angle: float, scale: float, offset_x: float, offset_y: float, layer: str, stroke_width: float) -> str:
    start, mid, end = _arc_points(center_x, center_y, radius, start_angle, end_angle)
    start = _point(start[0], start[1], scale, offset_x, offset_y)
    mid = _point(mid[0], mid[1], scale, offset_x, offset_y)
    end = _point(end[0], end[1], scale, offset_x, offset_y)
    return (
        f'  (fp_arc (start {_fmt(start[0])} {_fmt(start[1])}) '
        f'(mid {_fmt(mid[0])} {_fmt(mid[1])}) '
        f'(end {_fmt(end[0])} {_fmt(end[1])}) {_stroke(layer, stroke_width)})'
    )


def _find_oda_converter() -> str | None:
    candidates = [os.environ.get("ODA_FILE_CONVERTER"), "ODAFileConverter", "ODAFileConverter.exe", "TeighaFileConverter", "TeighaFileConverter.exe"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate) if Path(candidate).name == candidate else candidate
        if resolved and Path(resolved).exists():
            return resolved
    return None


def _convert_dwg_to_dxf(input_path: Path, temp_dir: Path) -> Path:
    converter = _find_oda_converter()
    if not converter:
        raise RuntimeError(
            "DWG input requires an ODA File Converter. Install ODAFileConverter "
            "or set ODA_FILE_CONVERTER to its executable path."
        )

    input_dir = temp_dir / "input"
    output_dir = temp_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    staged_input = input_dir / input_path.name
    staged_input.write_bytes(input_path.read_bytes())

    command = [
        converter,
        str(input_dir),
        str(output_dir),
        "ACAD2018",
        "DXF",
        "0",
        "1",
        "*.dwg",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "ODA File Converter failed.")

    converted = output_dir / f"{input_path.stem}.dxf"
    if not converted.exists():
        raise RuntimeError("ODA File Converter did not produce a DXF output file.")
    return converted


def _iter_entities(entity) -> Iterator:
    entity_type = entity.dxftype()
    if entity_type in {"INSERT", "LWPOLYLINE", "POLYLINE"}:
        yield from entity.virtual_entities()
        return
    yield entity


def convert_file(input_path: Path, output_path: Path, module_name: str, layer: str = "F.SilkS", stroke_width: float = 0.15, scale: float = 1.0, offset_x: float = 0.0, offset_y: float = 0.0, description: str | None = None) -> List[str]:
    warnings: List[str] = []
    graphics: List[str] = []

    with tempfile.TemporaryDirectory(prefix="dwg2kicadfoot-") as temp_name:
        resolved_input = input_path
        if input_path.suffix.lower() == ".dwg":
            resolved_input = _convert_dwg_to_dxf(input_path, Path(temp_name))

        document = ezdxf.readfile(resolved_input)
        modelspace = document.modelspace()

        for source_entity in modelspace:
            for entity in _iter_entities(source_entity):
                entity_type = entity.dxftype()
                if entity_type == "LINE":
                    start = _point(entity.dxf.start.x, entity.dxf.start.y, scale, offset_x, offset_y)
                    end = _point(entity.dxf.end.x, entity.dxf.end.y, scale, offset_x, offset_y)
                    graphics.append(_line(start, end, layer, stroke_width))
                elif entity_type == "CIRCLE":
                    center = _point(entity.dxf.center.x, entity.dxf.center.y, scale, offset_x, offset_y)
                    graphics.append(_circle(center, entity.dxf.radius * scale, layer, stroke_width))
                elif entity_type == "ARC":
                    graphics.append(
                        _arc(
                            entity.dxf.center.x,
                            entity.dxf.center.y,
                            entity.dxf.radius,
                            entity.dxf.start_angle,
                            entity.dxf.end_angle,
                            scale,
                            offset_x,
                            offset_y,
                            layer,
                            stroke_width,
                        )
                    )
                else:
                    warnings.append(f"Skipped unsupported entity type: {entity_type}")

    if not graphics:
        raise RuntimeError("No supported entities were found to convert.")

    lines = [f"(footprint \"{module_name}\"", "  (version 20240108)", "  (generator dwg2kicadfoot)"]
    if description:
        lines.append(f'  (descr "{description}")')
    lines.extend(graphics)
    lines.append(")")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert DWG/DXF geometry into a KiCad footprint.")
    parser.add_argument("input", type=Path, help="Input DWG or DXF file")
    parser.add_argument("output", type=Path, help="Output .kicad_mod file")
    parser.add_argument("--module-name", default="DWG2KicadFoot", help="KiCad footprint name")
    parser.add_argument("--layer", default="F.SilkS", help="KiCad graphics layer")
    parser.add_argument("--stroke-width", type=float, default=0.15, help="KiCad graphic stroke width")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale factor applied to coordinates")
    parser.add_argument("--offset-x", type=float, default=0.0, help="X offset applied after scaling")
    parser.add_argument("--offset-y", type=float, default=0.0, help="Y offset applied after scaling")
    parser.add_argument("--description", default=None, help="Optional footprint description")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.input.exists():
        parser.error(f"input file does not exist: {args.input}")

    try:
        warnings = convert_file(
            input_path=args.input,
            output_path=args.output,
            module_name=args.module_name,
            layer=args.layer,
            stroke_width=args.stroke_width,
            scale=args.scale,
            offset_x=args.offset_x,
            offset_y=args.offset_y,
            description=args.description,
        )
    except Exception as exc:  # pragma: no cover - CLI error handling
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
