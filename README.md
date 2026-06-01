# DWG2KicadFoot

Convert simple DWG/DXF geometry into a KiCad footprint (`.kicad_mod`).

## Features

- Reads `.dxf` files directly with `ezdxf`
- Accepts `.dwg` input by converting it to DXF with an installed ODA File Converter
- Converts common drawing entities into KiCad graphics:
  - `LINE`
  - `CIRCLE`
  - `ARC`
  - `LWPOLYLINE`
  - `POLYLINE`
  - `INSERT`
- Writes standard KiCad footprint S-expressions

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Usage

```bash
python3 dwg2kicadfoot.py input.dxf output.kicad_mod --module-name MyFootprint
python3 dwg2kicadfoot.py input.dwg output.kicad_mod --module-name MyFootprint
```

### Useful options

- `--layer F.SilkS` - output KiCad layer
- `--stroke-width 0.15` - graphic stroke width in millimeters
- `--scale 1.0` - scale factor applied to input coordinates
- `--offset-x 0 --offset-y 0` - coordinate offsets applied after scaling
- `--description "Imported from CAD"` - footprint description text

### DWG support

DWG parsing is handled by first converting the input file to DXF with an ODA
File Converter executable available on `PATH`, or configured with
`ODA_FILE_CONVERTER=/path/to/ODAFileConverter`.
