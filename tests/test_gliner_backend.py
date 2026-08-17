#!/usr/bin/env python3
"""Backend-selection regressions for the GLiNER ONNX FP32 migration."""

import contextlib
import gc
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detectors import gliner_detector


class FakeModel:
    def predict_entities(self, text, labels, threshold):
        return [
            {
                "text": "Alice Example",
                "label": "person",
                "score": 0.97,
                "start": 0,
                "end": 13,
            }
        ]


class FakeExportModel:
    def export_to_onnx(
        self, save_dir, onnx_filename, quantize=False, **_kwargs
    ):
        path = Path(save_dir) / onnx_filename
        path.write_bytes(b"fake-fp32-onnx")
        (Path(save_dir) / "tokenizer.json").write_text(
            "{}", encoding="utf-8"
        )
        (Path(save_dir) / "tokenizer_config.json").write_text(
            "{}", encoding="utf-8"
        )
        return {"onnx_path": str(path), "quantized_path": None}


class FakeSessionOptions:
    intra_op_num_threads = None
    inter_op_num_threads = None


def _reset():
    gliner_detector._GLOBAL_MODEL = None
    gliner_detector._GLOBAL_BACKEND = None


def _run_cases():
    rows = []

    _reset()
    with (
        patch.object(gliner_detector.config, "GLINER_BACKEND", "onnx"),
        patch.object(
            gliner_detector, "_load_onnx_model", return_value=FakeModel()
        ) as onnx_load,
        patch.object(
            gliner_detector,
            "_load_torch_model",
            side_effect=AssertionError("torch path should not load"),
        ),
    ):
        findings = gliner_detector.detect_entities_gliner(
            "Alice Example works locally."
        )
    passed = (
        onnx_load.call_count == 1
        and gliner_detector._GLOBAL_BACKEND == "onnx"
        and findings.get("person", [("", 0)])[0][0] == "Alice Example"
    )
    rows.append(("ONNX default loads and produces findings", passed, findings))

    _reset()
    warning = io.StringIO()
    with (
        patch.object(gliner_detector.config, "GLINER_BACKEND", "onnx"),
        patch.object(gliner_detector, "ONNX_RUNTIME_AVAILABLE", False),
        patch.object(
            gliner_detector, "_load_torch_model", return_value=FakeModel()
        ) as torch_load,
        contextlib.redirect_stdout(warning),
    ):
        findings = gliner_detector.detect_entities_gliner(
            "Alice Example works locally."
        )
    output = warning.getvalue()
    passed = (
        torch_load.call_count == 1
        and gliner_detector._GLOBAL_BACKEND == "torch"
        and "falling back to PyTorch" in output
        and bool(findings.get("person"))
    )
    rows.append(("missing ONNX runtime warns and falls back", passed, output.strip()))

    _reset()
    with (
        patch.object(gliner_detector.config, "GLINER_BACKEND", "torch"),
        patch.object(
            gliner_detector, "_load_torch_model", return_value=FakeModel()
        ) as torch_load,
        patch.object(
            gliner_detector,
            "_load_onnx_model",
            side_effect=AssertionError("ONNX path should not load"),
        ),
    ):
        gliner_detector._get_model()
    passed = (
        torch_load.call_count == 1
        and gliner_detector._GLOBAL_BACKEND == "torch"
    )
    rows.append(("torch backend flag is honored", passed, gliner_detector._GLOBAL_BACKEND))

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot"
        cache = Path(tmp) / "onnx-cache"
        snapshot.mkdir()
        cache.mkdir()
        (snapshot / "gliner_config.json").write_text(
            "original", encoding="utf-8"
        )
        (cache / "gliner_config.json").write_text(
            "expanded-config", encoding="utf-8"
        )
        (cache / "tokenizer.json").write_text("tokenizer", encoding="utf-8")
        (cache / "tokenizer_config.json").write_text("config", encoding="utf-8")
        loaded = FakeModel()
        with (
            patch(
                "huggingface_hub.snapshot_download",
                return_value=str(snapshot),
            ),
            patch.object(gliner_detector, "_onnx_cache_dir", return_value=cache),
            patch.object(
                gliner_detector.GLiNER,
                "from_pretrained",
                return_value=loaded,
            ) as from_pretrained,
        ):
            actual = gliner_detector._load_torch_model()
        passed = (
            actual is loaded
            and (snapshot / "gliner_config.json").read_text()
            == "expanded-config"
            and (snapshot / "tokenizer.json").read_text() == "tokenizer"
            and (snapshot / "tokenizer_config.json").read_text() == "config"
            and from_pretrained.call_args.args == (str(snapshot),)
            and from_pretrained.call_args.kwargs == {"local_files_only": True}
        )
        rows.append(
            (
                "PyTorch fallback reuses exported offline tokenizer",
                passed,
                str(from_pretrained.call_args),
            )
        )

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        for filename in gliner_detector.ONNX_CACHE_FILENAMES:
            (cache / filename).write_text("fixture", encoding="utf-8")
        loaded = FakeModel()
        with (
            patch.object(
                gliner_detector.config, "GLINER_ONNX_THREADS", 4
            ),
            patch.object(
                gliner_detector, "_onnx_cache_dir", return_value=cache
            ),
            patch.object(
                gliner_detector.onnxruntime,
                "SessionOptions",
                return_value=FakeSessionOptions(),
            ),
            patch.object(
                gliner_detector.GLiNER,
                "from_pretrained",
                return_value=loaded,
            ) as from_pretrained,
        ):
            actual = gliner_detector._load_onnx_model()
        options = from_pretrained.call_args.kwargs["session_options"]
        passed = (
            actual is loaded
            and options.intra_op_num_threads == 4
            and options.inter_op_num_threads == 4
        )
        rows.append(
            (
                "ONNX session honors configured intra/inter thread caps",
                passed,
                (
                    options.intra_op_num_threads,
                    options.inter_op_num_threads,
                ),
            )
        )

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "onnx-cache"
        snapshot = Path(tmp) / "snapshot"
        snapshot.mkdir()
        (snapshot / "gliner_config.json").write_text("{}", encoding="utf-8")
        first_output = io.StringIO()
        with (
            patch(
                "huggingface_hub.snapshot_download",
                return_value=str(snapshot),
            ),
            # Not _load_torch_model: _prepare_onnx_model deliberately loads
            # straight from the freshly resolved snapshot via
            # GLiNER.from_pretrained() for this initial cold export, since
            # _load_torch_model() itself requires the ONNX cache's own
            # tokenizer files to already exist -- which, on a genuinely
            # empty cache dir, is exactly what this call is building.
            patch.object(
                gliner_detector.GLiNER,
                "from_pretrained",
                return_value=FakeExportModel(),
            ),
            contextlib.redirect_stdout(first_output),
        ):
            first = gliner_detector._prepare_onnx_model(cache)
        second_output = io.StringIO()
        with contextlib.redirect_stdout(second_output):
            second = gliner_detector._prepare_onnx_model(cache)
        passed = (
            first == second
            and first.read_bytes() == b"fake-fp32-onnx"
            and all(
                (cache / filename).is_file()
                for filename in gliner_detector.ONNX_CACHE_FILENAMES
            )
            and first_output.getvalue().count(
                "[*] Preparing ONNX model (one-time)..."
            )
            == 1
            and "Preparing ONNX" not in second_output.getvalue()
        )
        rows.append(("first-use export logs once; cached load is silent", passed, str(first)))

        (cache / "tokenizer.json").unlink()
        repaired_output = io.StringIO()
        with (
            patch(
                "huggingface_hub.snapshot_download",
                return_value=str(snapshot),
            ),
            patch.object(
                gliner_detector.GLiNER,
                "from_pretrained",
                return_value=FakeExportModel(),
            ),
            contextlib.redirect_stdout(repaired_output),
        ):
            gliner_detector._prepare_onnx_model(cache)
        passed = (
            repaired_output.getvalue().count(
                "[*] Preparing ONNX model (one-time)..."
            )
            == 1
            and all(
                (cache / filename).is_file()
                for filename in gliner_detector.ONNX_CACHE_FILENAMES
            )
        )
        rows.append(("partial cache forces a complete re-export", passed, str(cache)))

    _reset()
    return rows


def _raw_finding_sets(model, texts):
    return [
        {
            (
                entity["label"],
                entity["text"],
                entity["start"],
                entity["end"],
            )
            for entity in model.predict_entities(
                text,
                gliner_detector.LABELS,
                threshold=gliner_detector.MIN_CONFIDENCE,
            )
        }
        for text in texts
    ]


def test_real_onnx_fixture_parity():
    texts = [
        "Alice Johnson joined Microsoft in Toronto on March 14, 2024.",
        (
            "Dr. Robert Chen met Sarah Example at Alberta Health Services "
            "in Sampletown on 2025-06-11."
        ),
        (
            "The numbered invoice contains ordinary product descriptions "
            "and no named people."
        ),
    ]
    torch_model = gliner_detector._load_torch_model()
    torch_findings = _raw_finding_sets(torch_model, texts)
    del torch_model
    gc.collect()

    onnx_model = gliner_detector._load_onnx_model()
    onnx_findings = _raw_finding_sets(onnx_model, texts)

    assert sum(len(found) for found in torch_findings[:2]) == 9
    assert onnx_findings == torch_findings


def _run_real_parity_case():
    try:
        test_real_onnx_fixture_parity()
        return ("real PyTorch/ONNX fixture findings are identical", True, "9/9")
    except Exception as exc:
        return (
            "real PyTorch/ONNX fixture findings are identical",
            False,
            f"{type(exc).__name__}: {exc}",
        )


def main():
    rows = _run_cases() + [_run_real_parity_case()]
    print(f"{'CASE':58} {'RESULT':7} DETAIL")
    print("-" * 96)
    for name, passed, detail in rows:
        print(f"{name:58} {'PASS' if passed else 'FAIL':7} {detail}")
    passed_count = sum(passed for _name, passed, _detail in rows)
    print("-" * 96)
    print(f"SUMMARY: {passed_count}/{len(rows)} passed")
    return 0 if passed_count == len(rows) else 1


def test_gliner_backends():
    failed = [row for row in _run_cases() if not row[1]]
    assert not failed, failed


if __name__ == "__main__":
    raise SystemExit(main())
