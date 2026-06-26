from chordatlas.cli import main


def test_validate_accepts_valid_chart(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text("title: Test\nsections: []\n", encoding="utf-8")

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart:" in captured.err
    assert captured.out == ""


def test_validate_reports_provenance_warnings(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "provenance:",
                "  source_type: unknown",
                "  claim_origin: unknown",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["validate", str(chart_path)]) == 0

    captured = capsys.readouterr()
    assert "Valid chart with 1 provenance warning(s):" in captured.err
    assert "- chart has unknown provenance" in captured.err
    assert captured.out == ""


def test_validate_rejects_invalid_chart(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["validate", str(chart_path)]) == 1

    captured = capsys.readouterr()
    assert "Invalid chart: Expected YAML mapping" in captured.err
    assert captured.out == ""
