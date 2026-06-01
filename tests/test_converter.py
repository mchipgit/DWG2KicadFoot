import tempfile
import unittest
from pathlib import Path

import ezdxf

from dwg2kicadfoot import convert_file


class ConverterTests(unittest.TestCase):
    def test_convert_basic_entities_to_kicad_footprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / "sample.dxf"
            output_path = temp_dir / "sample.kicad_mod"

            document = ezdxf.new()
            modelspace = document.modelspace()
            modelspace.add_line((0, 0), (1, 2))
            modelspace.add_circle((5, 5), radius=2)
            modelspace.add_arc((10, 10), radius=3, start_angle=0, end_angle=90)
            document.saveas(input_path)

            warnings = convert_file(
                input_path=input_path,
                output_path=output_path,
                module_name="ExampleFootprint",
                scale=2.0,
                offset_x=1.0,
                offset_y=-1.0,
                description="Imported from test",
            )

            self.assertEqual(warnings, [])
            contents = output_path.read_text(encoding="utf-8")
            self.assertIn('(footprint "ExampleFootprint"', contents)
            self.assertIn('(descr "Imported from test")', contents)
            self.assertIn(
                '(fp_line (start 1 -1) (end 3 3) (stroke (width 0.15) (type solid)) (layer "F.SilkS"))',
                contents,
            )
            self.assertIn(
                '(fp_circle (center 11 9) (end 15 9) (stroke (width 0.15) (type solid)) (layer "F.SilkS"))',
                contents,
            )
            self.assertIn(
                '(stroke (width 0.15) (type solid)) (layer "F.SilkS"))',
                contents,
            )

    def test_dwg_requires_converter_when_not_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            input_path = temp_dir / "sample.dwg"
            input_path.write_bytes(b"not-a-real-dwg")
            output_path = temp_dir / "sample.kicad_mod"

            with self.assertRaisesRegex(RuntimeError, "ODA File Converter"):
                convert_file(
                    input_path=input_path,
                    output_path=output_path,
                    module_name="ExampleFootprint",
                )


if __name__ == "__main__":
    unittest.main()
