from chordatlas.cli import main


def test_render_outputs_markdown(tmp_path, capsys) -> None:
    chart_path = tmp_path / "song.yaml"
    chart_path.write_text(
        "\n".join(
            [
                "title: Test",
                "sections:",
                "  - name: Intro",
                "    bars:",
                "      - [G]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["render", str(chart_path), "--format", "md"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("# Test\n")
    assert "## Chord Reference" in captured.out
    assert "### G" in captured.out
    assert captured.err == ""


def test_render_reports_malformed_yaml(tmp_path, capsys) -> None:
    chart_path = tmp_path / "bad.yaml"
    chart_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert main(["render", str(chart_path), "--format", "md"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid chart: Expected YAML mapping" in captured.err


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
