"""Report contracts distinguish partial observations and redact diagnostics."""
import importlib.util
import json


def reports_module():
    assert importlib.util.find_spec('socialctl.read_reports') is not None, 'structured read reports are missing'
    from socialctl import read_reports
    return read_reports


def test_partial_report_emits_one_json_document_and_nonzero_exit(capsys):
    module = reports_module()
    report = module.ReadReport(status='partial', coverage=[{'platform':'youtube','complete':False}], data={'items':[]})
    assert module.emit_report(report) == 1
    value = json.loads(capsys.readouterr().out)
    assert value['version'] == 1 and value['status'] == 'partial'
    assert value['coverage'][0]['complete'] is False
    assert value['observed_at']


def test_diagnostics_keep_context_without_credentials():
    module = reports_module()
    error = module.safe_error('permission failure code=200 access_token=fictional-secret&code=oauth-code Authorization: Bearer another-secret', secrets=('fictional-secret',))
    assert 'permission failure' in error and 'code=200' in error
    for secret in ('fictional-secret','oauth-code','another-secret'):
        assert secret not in error
