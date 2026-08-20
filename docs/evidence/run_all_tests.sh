#!/bin/bash
set -o pipefail
tests=(
tests/test_scoring.py
tests/test_secrets.py
tests/test_drivers_license.py
tests/test_health_cards.py
tests/test_ip_url.py
tests/test_keyword_negation.py
tests/test_passports.py
tests/test_phone.py
tests/test_gliner_filter.py
tests/test_gliner_backend.py
tests/test_mrz.py
tests/test_image_extractor_confidence.py
tests/test_llm_verifier.py
tests/test_masking.py
tests/test_mismatch_alarm.py
tests/test_orchestration_audit.py
tests/test_scan_boundaries.py
tests/test_extraction_status.py
tests/test_pdf_native_text.py
tests/test_multiframe_tiff.py
tests/test_face_detector.py
tests/test_cancel_scan.py
tests/test_binary_content_guard.py
tests/test_gui_logic.py
tests/test_backend_faults.py
tests/test_eml_extractor.py
tests/test_pptx_extractor.py
tests/test_financial_identifier_tiers.py
)
pass=0
fail=0
failed_list=()
for t in "${tests[@]}"; do
  echo "=== $t ==="
  if python "$t" > /tmp/out_$$.log 2>&1; then
    echo "PASS"
    pass=$((pass+1))
  else
    echo "FAIL"
    fail=$((fail+1))
    failed_list+=("$t")
    tail -30 /tmp/out_$$.log
  fi
  rm -f /tmp/out_$$.log
done
echo "===================="
echo "TOTAL: $pass passed, $fail failed"
if [ $fail -gt 0 ]; then
  echo "FAILED: ${failed_list[@]}"
fi
