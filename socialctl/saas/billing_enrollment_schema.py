"""Closed v1 private artifact contracts; parsing never establishes authority.

Artifact hashes cover the original bytes, not a model serialization. Structural
validation is deliberately separate from custody and semantic eligibility.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import billing_inventory as inventory

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Timestamp = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9_:-]{1,512}$')]
Account = Annotated[str, Field(pattern=r'^acct_[A-Za-z0-9_]+$')]
Selector = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$', max_length=512)]
Role = Literal['inventory', 'provider', 'configuration', 'deployment', 'initial_empty',
               'owner_instruction', 'authority', 'auth_authority', 'backup', 'freeze',
               'source', 'image', 'policy', 'rehearsal', 'compatibility', 'documentary_review']


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    @field_validator('*', mode='before')
    @classmethod
    def strict_literals(cls, value, info):
        annotation = cls.model_fields[info.field_name].annotation
        if get_origin(annotation) is Literal:
            if not any(type(value) is type(item) and value == item for item in get_args(annotation)):
                raise ValueError('Invalid artifact.')
        return value

    @field_validator('*')
    @classmethod
    def no_credential_identifiers(cls, value):
        if isinstance(value, str) and value.startswith(('sk_', 'rk_', 'whsec_')):
            raise ValueError('Invalid artifact.')
        return value


class Artifact(Closed):
    id: Identifier
    sha256: Digest
    role: Role


class Ref(Closed):
    artifact_id: Identifier
    selector: Selector


class Row(Closed):
    table: Identifier
    key_sha256: Digest
    row_sha256: Digest

    @field_validator('table')
    @classmethod
    def known_table(cls, value):
        if value not in inventory._SOURCE_METADATA_ROOTS:
            raise ValueError('Invalid artifact.')
        return value


class RecordRef(Closed):
    kind: Identifier
    id: Identifier


class Transition(Closed):
    id: Identifier
    from_id: Identifier
    to_id: Identifier
    kind: Literal['initial', 'deployment', 'migration', 'restore', 'import']
    account_id: Account
    mode: Literal['test']
    receipts: list[Ref]
    # Reviewed explanatory text stays in the source receipt, not this artifact.
    # This reference/code cannot accidentally export an email or a raw payload.
    limitation: Annotated[str, Field(pattern=r'^[A-Za-z0-9_:-]{0,512}$')]


class Lineage(Closed):
    id: Identifier
    category: Literal['billing', 'event', 'paid_provider', 'paid_manual', 'intent']
    rows: list[Row]
    record_refs: list[RecordRef]
    user_id: Identifier
    account_id: Account | None
    mode: Literal['test'] | None
    origin_transition_id: Identifier
    historical_refs: list[Ref]
    provider_refs: list[Ref]
    owner_instruction_refs: list[Ref]


class Uncertainty(Closed):
    id: Identifier
    row_refs: list[Row]
    record_refs: list[RecordRef]
    reason_code: Identifier
    provenance_lineage_ids: list[Identifier]
    preserved: Literal[True]


class TableRoot(Closed):
    name: Identifier
    count: Timestamp
    sha256: Digest

    @field_validator('name')
    @classmethod
    def known_table(cls, value):
        return Row.known_table(value)


class Root(Closed):
    algorithm: Literal['inventory-typed-root-v1']
    schema_variant: Literal['schema10_deployed_rc15', 'schema10_candidate_receiver']
    schema_sha256: Digest
    state_sha256: Digest
    tables: list[TableRoot]


class Target(Closed):
    host_authority_id: Identifier
    service_id: Identifier
    mount_id: Identifier
    database_path: str
    device: Timestamp
    inode: Timestamp
    account_id: Account
    mode: Literal['test']

    @field_validator('database_path')
    @classmethod
    def absolute_path(cls, value):
        # Existing-file/custody checks are performed at use time, not parsing.
        from pathlib import PurePosixPath
        path = PurePosixPath(value)
        if (not path.is_absolute() or '..' in path.parts or str(path) != value
                or any(c in value for c in '?#') or any(ord(c) < 32 for c in value)):
            raise ValueError('Invalid artifact.')
        return value


class Evidence(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-evidence/v1'] = Field(alias='schema')
    bundle_id: Annotated[str, Field(pattern=r'^bundle_[0-9a-f]{32}$')]
    created_at: Timestamp
    artifacts: list[Artifact]
    transitions: list[Transition]
    lineages: list[Lineage]
    uncertainties: list[Uncertainty]
    documentary_review_ref: Ref


class BindingPayload(Closed):
    account_id: Account
    mode: Literal['test']


class Binding(Closed):
    kind: Literal['saas-billing-environment']
    id: Literal['active']
    payload: BindingPayload
    expires: Literal[253402300799]
    credential_hash: Literal['']


class Manifest(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-manifest/v1'] = Field(alias='schema')
    nonce: Annotated[str, Field(pattern=r'^enrollment_[0-9a-f]{32}$')]
    created_at: Timestamp
    expires_at: Timestamp
    target: Target
    baseline: Root
    evidence_sha256: Digest
    documentary_approval_sha256: list[Digest]
    source_sha256: Digest
    tool_sha256: Digest
    image_sha256: Digest
    config_sha256: Digest
    policy_sha256: Digest
    inventory_sha256: Digest
    authority_ref: Ref
    authority_observed_at: Timestamp
    auth_authority_ref: Ref
    backup_ref: Ref
    freeze_ref: Ref
    freeze_started_at: Timestamp
    provider_ref: Ref
    provider_observed_at: Timestamp
    rehearsal_ref: Ref
    compatibility_ref: Ref
    binding: Binding


class Approval(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-approval/v1'] = Field(alias='schema')
    approval_id: Annotated[str, Field(pattern=r'^approval_[0-9a-f]{32}$')]
    principal_id: Identifier
    role: Literal['owner', 'independent_reviewer']
    scope: Literal['documentary_review', 'enrollment_manifest']
    subject_sha256: Digest
    policy_sha256: Digest
    created_at: Timestamp
    expires_at: Timestamp
    decision: Literal['approve']


class Receipt(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-receipt/v1'] = Field(alias='schema')
    nonce: Annotated[str, Field(pattern=r'^enrollment_[0-9a-f]{32}$')]
    manifest_sha256: Digest
    evidence_sha256: Digest
    approval_sha256: list[Digest]
    target: Target
    committed_at: Timestamp
    baseline: Root
    post: Root
    post_excluding_binding: Root
    binding_ciphertext_sha256: Digest
    result: Literal['one_binding_insert_verified']


class Verification(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-verification/v1'] = Field(alias='schema')
    manifest_sha256: Digest
    receipt_sha256: Digest
    verified_at: Timestamp
    result: Literal['exact_delta_verified']
    post: Root
    post_excluding_binding: Root
    binding_ciphertext_sha256: Digest


Model = TypeVar('Model', bound=Closed)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Invalid artifact.')
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError('Invalid artifact.')


def parse_artifact(raw: bytes, model: type[Model]) -> Model:
    """Reject duplicate keys and ambiguous JSON before strict model validation."""
    try:
        if type(raw) is not bytes or raw.startswith(b'\xef\xbb\xbf'):
            raise ValueError()
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                           parse_constant=_nonfinite)
        return model.model_validate(value)
    except Exception:
        raise ValueError('Invalid artifact.') from None


def json_schema(model: type[Closed]) -> dict:
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            **model.model_json_schema(by_alias=True)}

# Implementation contract extension: protected typed assertions. They are
# documentary facts subject to independent review, never provider/human proofs.
class Principal(Closed):
    id: Identifier
    role: Literal['owner', 'independent_reviewer']


class Policy(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-policy/v1'] = Field(alias='schema')
    custody_directory: str
    registry_path: str
    target: Target
    principals: list[Principal]
    source_sha256: Digest
    tool_sha256: Digest
    image_sha256: Digest
    config_sha256: Digest
    collector_sha256: Digest
    app_id: Identifier
    provider_max_age: Literal[900]
    authority_max_age: Literal[300]
    manifest_max_age: Literal[300]


class RegistryEntry(Closed):
    id: Identifier
    filename: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]+\.json$')]
    sha256: Digest
    role: Role | Literal['evidence', 'manifest', 'approval', 'receipt']


class Registry(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-registry/v1'] = Field(alias='schema')
    entries: list[RegistryEntry]


class InitialEmptyFacts(Closed):
    source_receipt_sha256: Digest
    endpoint_id: Identifier
    account_id: Account
    mode: Literal['test']
    observed_at: Timestamp
    billing_rows: Literal[0]
    event_rows: Literal[0]
    paid_state_rows: Literal[0]
    encrypted_records: Literal[0]


class ContinuityFacts(Closed):
    source_receipt_sha256: Digest
    transition_id: Identifier
    from_id: Identifier
    to_id: Identifier
    kind: Literal['initial', 'deployment', 'migration', 'restore', 'import']
    account_id: Account
    mode: Literal['test']
    observed_at: Timestamp
    finding: Literal['continuous', 'unresolved', 'contradictory']


class ProviderObject(Closed):
    request_id: Identifier
    response_sha256: Digest
    reference_id: Identifier
    requested_id: Identifier
    returned_id: Identifier
    object_type: Literal['account', 'customer', 'subscription', 'checkout', 'invoice',
                         'event', 'price', 'portal', 'line_item']
    account_id: Account
    mode: Literal['test']
    user_id: Identifier | None
    app_id: Identifier
    customer_id: Identifier | None
    subscription_id: Identifier | None
    checkout_id: Identifier | None
    invoice_id: Identifier | None
    price_id: Identifier | None
    observed_at: Timestamp
    outcome: Literal['present', 'retained_terminal']
    retained_refs: list[Ref]


class ProviderCollection(Closed):
    collection: Literal['subscriptions', 'sessions', 'invoices', 'line_items']
    parent_id: Identifier
    object_ids: list[Identifier]
    pagination: Literal['complete', 'partial']
    observed_at: Timestamp


class ProviderFacts(Closed):
    objects: list[ProviderObject]
    collections: list[ProviderCollection]


class DocumentaryFacts(Closed):
    account_id: Account
    mode: Literal['test']
    original_empty_id: Identifier
    current_authority_id: Identifier
    transition_ids: list[Identifier]
    lineage_ids: list[Identifier]
    rows: list[Row]
    record_refs: list[RecordRef]
    uncertainty_ids: list[Identifier]
    evidence_sha256: list[Digest]
    findings: list[Literal['unresolved_provenance', 'contradictory_history',
                         'missing_relationship', 'incomplete_mapping']]


class AuthorityFacts(Closed):
    target: Target
    endpoint_id: Identifier
    observed_at: Timestamp
    expires_at: Timestamp
    state: Literal['authoritative']
    competing_authority_ids: list[Identifier]


class FreezeWriter(Closed):
    name: Literal['ingress', 'webhooks', 'workers', 'schedulers', 'deletion', 'revocation', 'reminders']
    state: Literal['drained', 'active', 'uncertain']


class FreezeFacts(Closed):
    target: Target
    freeze_id: Identifier
    started_at: Timestamp
    observed_at: Timestamp
    writers: list[FreezeWriter]
    arrivals: Literal['durable_external', 'reject_for_retry']
    network: Literal['deny_all']


class ConfigurationFacts(Closed):
    account_id: Account
    mode: Literal['test']
    app_id: Identifier
    price_id: Identifier
    portal_id: Identifier
    observed_at: Timestamp


class OwnerInstructionFacts(Closed):
    user_id: Identifier
    rows: list[Row]
    conversation_receipt_sha256: Digest
    observed_at: Timestamp


class BackupFacts(Closed):
    target: Target
    authority_id: Identifier
    auth_authority_id: Identifier
    sqlite_snapshot_sha256: Digest
    auth_snapshot_sha256: Digest
    verified_at: Timestamp


class RehearsalFacts(Closed):
    source_sha256: Digest
    image_sha256: Digest
    previous_image_sha256: Digest
    baseline_sha256: Digest
    exact_delta_sha256: Digest
    matching_test_startup: Literal['accepted']
    foreign_account_startup: Literal['rejected_before_provider']
    live_startup: Literal['rejected_before_provider']
    network: Literal['deny_all']
    restore: Literal['ready']
    uncertainty_ids: list[Identifier]


class CompatibilityFacts(Closed):
    previous_image_sha256: Digest
    binding: Literal['preserved']
    baseline: Literal['preserved']


class SourceFile(Closed):
    name: Literal['billing_enrollment.py', 'billing_enrollment_schema.py',
                  'billing_inventory.py', 'enroll_legacy_test_billing.py']
    sha256: Digest


class SourceFacts(Closed):
    files: list[SourceFile]


class ImageFacts(Closed):
    image_digest: Digest
    source_sha256: Digest


class FactReport(Closed):
    schema_version: Literal['socialcli-legacy-enrollment-facts/v1'] = Field(alias='schema')
    collector_sha256: Digest
    # Role is pinned separately by the protected registry. No caller-selected
    # discriminator can substitute provider facts for a documentary review.
    facts: InitialEmptyFacts | ContinuityFacts | ProviderFacts | DocumentaryFacts | AuthorityFacts | FreezeFacts | ConfigurationFacts | OwnerInstructionFacts | BackupFacts | RehearsalFacts | CompatibilityFacts | SourceFacts | ImageFacts
