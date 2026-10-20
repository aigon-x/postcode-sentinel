import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ci'))
import quality_gates as qg


def test_secret_is_detected(tmp_path):
    (tmp_path / 'config.py').write_text('KEY = "AKIA1234567890ABCDEF"\n')
    findings = qg.run(str(tmp_path))
    assert any(f['gate'] == 'secret-scan' and not f['ok'] for f in findings)


def test_documented_example_key_is_not_a_finding(tmp_path):
    (tmp_path / 'doc.md').write_text('example AKIAIOSFODNN7EXAMPLE is public\n')
    findings = qg.run(str(tmp_path))
    assert not any(f['gate'] == 'secret-scan' for f in findings)


def test_absolute_claim_is_flagged_but_negation_is_not(tmp_path):
    (tmp_path / 'a.md').write_text('This detector gives zero false positives.\n')
    (tmp_path / 'b.md').write_text('Clean plates are not guaranteed false positive free.\n')
    findings = [f for f in qg.run(str(tmp_path)) if f['gate'] == 'honest-claims']
    assert len(findings) == 1
    assert findings[0]['path'] == 'a.md'


def test_codeclimate_report_shape(tmp_path):
    findings = [{'gate': 'license', 'ok': False, 'detail': 'LICENSE missing', 'path': 'LICENSE'}]
    issues = qg.codeclimate(findings)
    assert issues[0]['check_name'] == 'license'
    assert issues[0]['location']['path'] == 'LICENSE'
    assert issues[0]['fingerprint']
