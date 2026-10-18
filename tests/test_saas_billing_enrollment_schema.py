"""Closed fictional enrollment artifact contracts; no provider or secret input."""
import json

import pytest


def api():
    from socialctl.saas import billing_enrollment_schema as schema
    return schema


def approval():
    return {
        'schema': 'socialcli-legacy-enrollment-approval/v1',
        'approval_id': 'approval_' + '1' * 32,
        'principal_id': 'fictional_owner', 'role': 'owner',
        'scope': 'documentary_review', 'subject_sha256': 'a' * 64,
        'policy_sha256': 'b' * 64, 'created_at': 100, 'expires_at': 200,
        'decision': 'approve',
    }


def test_approval_exact_contract_roundtrip_and_draft_schema():
    parsed = api().parse_artifact(json.dumps(approval()).encode(), api().Approval)
    assert parsed.model_dump(by_alias=True) == approval()
    schema = api().json_schema(api().Approval)
    assert schema['$schema'] == 'https://json-schema.org/draft/2020-12/schema'
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == set(approval())


@pytest.mark.parametrize('change', [
    {'schema': 'socialcli-legacy-enrollment-approval/v2'},
    {'created_at': True}, {'created_at': 1.0}, {'created_at': '1'},
    {'expires_at': -1}, {'role': 'administrator'}, {'decision': False},
    {'approval_id': 'arbitrary'}, {'principal_id': 'email@example.test'},
    {'extra': 'unknown'}, {'subject_sha256': 'A' * 64},
])
def test_wrong_scalars_versions_fields_and_ids_rejected(change):
    obj = approval() | change
    with pytest.raises(ValueError):
        api().parse_artifact(json.dumps(obj).encode(), api().Approval)


@pytest.mark.parametrize('raw', [
    b'{"schema":"x","schema":"y"}', b'{"x":NaN}', b'{"x":Infinity}',
    b'\xef\xbb\xbf{}', b'{} {}', b'{"nested":{"x":1,"x":2}}', b'\xff',
])
def test_ambiguous_json_rejected_before_model_validation(raw):
    with pytest.raises(ValueError):
        api().parse_artifact(raw, api().Approval)


def test_all_artifact_schemas_are_closed_recursively():
    for name in ('Evidence', 'Manifest', 'Approval', 'Receipt', 'Verification'):
        schema = api().json_schema(getattr(api(), name))
        for definition in [schema, *schema.get('$defs', {}).values()]:
            if definition.get('type') == 'object':
                assert definition['additionalProperties'] is False
                assert set(definition['required']) == set(definition['properties'])


def test_boolean_does_not_satisfy_literal_integer_or_preserved_flag():
    for model, value in [(api().Binding, {'kind': 'saas-billing-environment', 'id': 'active',
                            'payload': {'account_id': 'acct_fictional', 'mode': 'test'},
                            'expires': 253402300799.0, 'credential_hash': ''}),
                         (api().Uncertainty, {'id': 'unknown', 'row_refs': [], 'record_refs': [],
                            'reason_code': 'unknown', 'provenance_lineage_ids': [], 'preserved': 1})]:
        with pytest.raises(ValueError):
            api().parse_artifact(json.dumps(value).encode(), model)
