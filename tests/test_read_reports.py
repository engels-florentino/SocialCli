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
    assert module.emit_report(report) == 3
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


def test_read_errors_gain_actions_without_losing_partial_data():
    module=reports_module()
    report=module.ReadReport(status='partial',data={'items':[{'id':'kept'}]},
        errors=[{'platform':'youtube','message':'page incomplete'}])
    assert report.data['items']==[{'id':'kept'}]
    assert report.errors[0]['action']=='inspect'
    assert report.errors[0]['retryable'] is False
    assert report.errors[0]['code']=='read_partial'
    assert report.errors[0]['requires_user_action'] is False


def test_legacy_partial_dictionary_retains_cursor_items_and_exit_indicators():
    module=reports_module()
    original={'items':[{'id':'kept'}],'cursor':'next-page','complete':False,'timed_out':True,'error':'timeout'}
    result=module.annotate_partial_read(original)
    assert all(result[k]==v for k,v in original.items())
    assert result['error_detail']['action']=='adjust_read'
    assert result['error_detail']['retryable'] is False


def test_unknown_stored_machine_fields_cannot_request_reconnect_or_retry():
    module=reports_module()
    report=module.ReadReport(status='partial',errors=[{'code':'private-provider-secret','action':'reconnect','retryable':True}])
    row=report.errors[0]
    assert row['action']=='inspect' and row['retryable'] is False
    assert 'private-provider-secret' not in report.model_dump_json()


def test_read_envelope_cannot_turn_uncertain_token_refresh_into_retry():
    import httpx
    from socialctl.agent_errors import error_document
    module=reports_module()
    error=httpx.ReadTimeout('private',request=httpx.Request('POST','https://example.test/token'))
    report=module.ReadReport(status='error',errors=[error_document(error,read_only=True)])
    assert report.errors[0]['action']=='reconcile'
    assert report.errors[0]['retryable'] is False
