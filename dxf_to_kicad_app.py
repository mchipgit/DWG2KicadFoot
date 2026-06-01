"""
2026.05.28 CYJ / AI: GitHub Copilot (GPT-5.3-Codex)
DXF to KiCad Footprint 변환 GUI.

용도:
- DXF 파일에서 pad/그래픽 정보를 추출해 KiCad .kicad_mod 파일로 변환한다.
- 단일 파일 변환과 폴더 단위 일괄 변환을 지원한다.

기본 사용법:
1) DXF 파일 또는 DXF가 들어있는 폴더를 선택한다.
2) 출력 폴더를 지정한다. 비우면 소스 폴더를 사용한다.
3) 필요하면 Pad 블록 정규식과 그래픽 소스 레이어를 조정한다.
4) 단일 변환은 "변환", 일괄 변환은 "일괄 변환 후 종료"를 누른다.
5) 우측 미리보기와 하단 결과(출력 파일, Pad 수, 그래픽 수)를 확인한다.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from dxf_to_kicad_core import ConverterOptions, convert_dxf_file


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DXF to KiCad Footprint")
        self.resize(1200, 820)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.source_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.footprint_edit = QLineEdit()
        self.footprint_edit.setPlaceholderText("비워두면 DXF 파일명 사용")
        self.pad_regex_edit = QLineEdit(r"SIGLEPIN")
        self.layer_edit = QLineEdit("1")
        self.pin1_roundrect = QCheckBox("1번 패드를 roundrect로 생성")
        self.pin1_roundrect.setChecked(True)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.preview_view = QPlainTextEdit()
        self.preview_view.setReadOnly(True)
        self.preview_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        fixed_font = QFont("Consolas")
        fixed_font.setStyleHint(QFont.Monospace)
        self.log_view.setFont(fixed_font)
        self.preview_view.setFont(fixed_font)
        self.preview_view.setPlainText(self._build_startup_guide())

        self.pad_count_label = QLabel("0")
        self.graphic_count_label = QLabel("0")
        self.output_path_label = QLabel("-")

        form_box = QGroupBox("입력")
        form_layout = QFormLayout(form_box)
        form_layout.addRow("DXF 파일", self._path_row(self.source_edit, self.browse_source))
        form_layout.addRow("출력 폴더", self._path_row(self.output_edit, self.browse_output))
        form_layout.addRow("Footprint 이름", self.footprint_edit)
        form_layout.addRow("Pad 블록 정규식", self.pad_regex_edit)
        form_layout.addRow("그래픽 소스 레이어", self.layer_edit)
        form_layout.addRow("옵션", self.pin1_roundrect)

        button_row = QHBoxLayout()
        self.convert_button = QPushButton("변환")
        self.batch_convert_button = QPushButton("일괄 변환 후 종료")
        self.clear_button = QPushButton("로그 지우기")
        self.convert_button.clicked.connect(self.convert)
        self.batch_convert_button.clicked.connect(self.batch_convert_and_exit)
        self.clear_button.clicked.connect(self.log_view.clear)
        button_row.addWidget(self.convert_button)
        button_row.addWidget(self.batch_convert_button)
        button_row.addWidget(self.clear_button)
        button_row.addStretch(1)

        stats_box = QGroupBox("결과")
        stats_layout = QFormLayout(stats_box)
        stats_layout.addRow("출력 파일", self.output_path_label)
        stats_layout.addRow("Pad 수", self.pad_count_label)
        stats_layout.addRow("그래픽 엔티티 수", self.graphic_count_label)

        left_layout = QVBoxLayout()
        left_layout.addWidget(form_box)
        left_layout.addLayout(button_row)
        left_layout.addWidget(stats_box)
        left_layout.addWidget(QLabel("로그"))
        left_layout.addWidget(self.log_view, 1)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("미리보기"))
        right_layout.addWidget(self.preview_view, 1)

        main_layout = QHBoxLayout(central)
        main_layout.addLayout(left_layout, 1)
        main_layout.addLayout(right_layout, 1)

    def _build_startup_guide(self) -> str:
        today = date.today().strftime("%Y/%m/%d")
        return (
            "[프로그램 용도]\n"
            "- DXF 도면을 KiCad Footprint(.kicad_mod)로 변환합니다.\n"
            "- 단일 변환 및 폴더 일괄 변환을 지원합니다.\n\n"
            "[사용법]\n"
            "1. DXF 파일을 선택합니다.\n"
            "2. 출력 폴더를 선택합니다.\n"
            "3. 필요 시 Pad 블록 정규식/그래픽 소스 레이어를 조정합니다.\n"
            "4. 단일 변환은 [변환], 일괄 변환은 [일괄 변환 후 종료]를 누릅니다.\n"
            "5. 변환 후 미리보기와 결과(Pad 수, 그래픽 엔티티 수)를 확인합니다.\n\n"
            "[저작권]\n"
            "작성자 : 투에스텍 주식회사 / 최연준부장\n"
            f"작성일 : {today}\n"
            "Copyright (c) 투에스텍 주식회사. All rights reserved.\n"
        )

    def _path_row(self, edit: QLineEdit, browse_slot):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        button = QPushButton("찾기")
        button.clicked.connect(browse_slot)
        layout.addWidget(button)
        return row

    def _default_browse_dir(self) -> str:
        source_value = self.source_edit.text().strip()
        if source_value:
            source_parent = Path(source_value).parent
            if source_parent.exists():
                return str(source_parent)

        output_value = self.output_edit.text().strip()
        if output_value and Path(output_value).exists():
            return output_value

        if getattr(sys, "frozen", False):
            return str(Path(sys.executable).resolve().parent)

        return str(Path.cwd())

    def browse_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "DXF 파일 선택",
            self._default_browse_dir(),
            "DXF Files (*.dxf);;All Files (*)",
        )
        if path:
            self.source_edit.setText(path)
            if not self.footprint_edit.text().strip():
                self.footprint_edit.setText(Path(path).stem)
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(Path(path).parent))

    def browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "출력 폴더 선택",
            self._default_browse_dir(),
        )
        if path:
            self.output_edit.setText(path)

    def _build_options(self, footprint_name: str | None = None) -> ConverterOptions:
        source_layers = [item.strip() for item in self.layer_edit.text().split(",") if item.strip()]
        return ConverterOptions(
            footprint_name=footprint_name,
            pad_name_regex=self.pad_regex_edit.text().strip() or r"SIGLEPIN",
            pad_layers=tuple(source_layers or ["1"]),
            pin1_roundrect=self.pin1_roundrect.isChecked(),
        )

    def _convert_source(self, source_path: Path, footprint_name: str | None = None):
        output_dir = self.output_edit.text().strip() or None
        options = self._build_options(footprint_name=footprint_name)
        return convert_dxf_file(source_path, output_dir, options)

    def convert(self) -> None:
        source = self.source_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "입력 필요", "DXF 파일을 먼저 선택하세요.")
            return

        source_path = Path(source)
        if not source_path.exists():
            QMessageBox.warning(self, "파일 없음", f"파일을 찾을 수 없습니다.\n{source_path}")
            return

        try:
            result = self._convert_source(source_path, self.footprint_edit.text().strip() or None)
        except Exception as exc:
            QMessageBox.critical(self, "변환 실패", str(exc))
            self.log_view.appendPlainText(f"ERROR: {exc}")
            return

        self.output_path_label.setText(str(result.output_path))
        self.pad_count_label.setText(str(result.pad_count))
        self.graphic_count_label.setText(str(result.graphic_count))
        self.preview_view.setPlainText(result.footprint_text)
        self.log_view.appendPlainText(
            f"OK: {result.source_path.name} -> {result.output_path.name}\n"
            f"  pads: {result.pad_count}\n"
            f"  graphics: {result.graphic_count}\n"
            f"  graphic primitives: {result.graphic_primitive_count}\n"
        )

    def batch_convert_and_exit(self) -> None:
        current_dir = Path(self._default_browse_dir())
        dxf_files = sorted(current_dir.glob("*.dxf"))
        if not dxf_files:
            QMessageBox.warning(self, "파일 없음", f"현재 폴더에 DXF 파일이 없습니다.\n{current_dir}")
            return

        self.log_view.appendPlainText(f"BATCH START: {current_dir}")
        converted = 0
        failures: list[str] = []
        last_result = None

        for source_path in dxf_files:
            try:
                result = self._convert_source(source_path, footprint_name=None)
            except Exception as exc:
                failures.append(f"{source_path.name}: {exc}")
                self.log_view.appendPlainText(f"ERROR: {source_path.name}: {exc}")
                continue

            converted += 1
            last_result = result
            self.log_view.appendPlainText(
                f"OK: {result.source_path.name} -> {result.output_path.name}"
            )

        if last_result is not None:
            self.output_path_label.setText(str(last_result.output_path))
            self.pad_count_label.setText(str(last_result.pad_count))
            self.graphic_count_label.setText(str(last_result.graphic_count))
            self.preview_view.setPlainText(last_result.footprint_text)

        if failures:
            QMessageBox.critical(
                self,
                "일괄 변환 실패",
                "일부 파일 변환에 실패했습니다.\n" + "\n".join(failures[:10]),
            )
            return

        self.log_view.appendPlainText(f"BATCH DONE: {converted} file(s)")
        application = QApplication.instance()
        if application is not None:
            application.quit()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
