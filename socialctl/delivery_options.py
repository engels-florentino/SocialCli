"""Product delivery policy, separate from proof of live provider permissions."""


def delivery_options(brand) -> dict:
    account = brand.cuentas.get('tiktok') or {}
    mode = account.get('mode', 'inbox')
    from socialctl.connections.temporary_media import available
    temporary=available(brand) and not (brand.cuentas.get('instagram') or {}).get('media_url_base')
    return {
        'version': 1, 'brand': brand.nombre, 'server_media_storage': bool(temporary),
        'temporary_media': {'instagram_enabled_for_connection':bool(temporary),
            'max_lifetime_seconds':900,'backed_up':False,'uploads_at_dispatch':True,
            'server_availability_verified':False},
        'platforms': {
            'youtube': {
                'selected_delivery': 'upload',
                'platform_native_scheduling': True,
                'automatic_native_schedule': False,
                'native_schedule_status': 'pilot_required',
                'native_schedule_authorization_verified': False,
                'authorization_verified': False,
                'authorization_verified_scope': 'native_scheduling_only',
                'instruction': 'Native API submission requires verified project/account eligibility and a pilot. '
                    'Use native-schedule with explicit approval; only verified remote scheduling allows the agent to stop.',
            },
            'facebook': {
                'selected_delivery': 'publish_now',
                'platform_native_scheduling': True,
                'automatic_native_schedule': False,
                'native_schedule_status': 'pilot_required',
                'native_schedule_authorization_verified': False,
                'authorization_verified': False,
                'authorization_verified_scope': 'native_scheduling_only',
                'instruction': 'Meta supports scheduling; SocialCli API eligibility remains closed until the '
                    'account/format pilot is verified. Use gated native-schedule submit-api for Reels, or an approved Business Suite handoff.',
            },
            'instagram': {
                'selected_delivery': 'publish_now',
                'automatic_native_schedule': False,
                'native_schedule_status': 'not_supported_by_posting_api',
                'native_schedule_authorization_verified': False,
                'authorization_verified': False,
                'authorization_verified_scope': 'native_scheduling_only',
                'instruction': 'Publish now. Legacy local scheduling requires the original file and a running '
                    'executor at delivery time; it is not unattended SaaS scheduling.',
            },
            'tiktok': {
                'selected_delivery': {'inbox': 'creator_review', 'direct': 'excluded_direct_post'}.get(mode, 'invalid_configuration'),
                'direct_post_enabled': False,
                'delivery_status': {'inbox': 'creator_completion_required', 'direct': 'excluded'}.get(mode, 'invalid_configuration'),
                'automatic_native_schedule': False,
                'native_schedule_status': 'not_supported_by_posting_api',
                'native_schedule_authorization_verified': False,
                'authorization_verified': False,
                'authorization_verified_scope': 'native_scheduling_only',
                'instruction': 'Inbox uploads for creator review; the creator finishes in TikTok. Direct Post '
                    'is disabled and excluded from the current offer regardless of auditada configuration. '
                    'Creator-reviewed inbox upload is supported; it does not schedule a future post.',
            },
        },
        'native_schedule_intent': {
            'exact_time': True, 'explicit_timezone_required': True,
            'multiple_times_per_day': True, 'moves_conflicting_exact_time': False,
            'legacy_daily_reservations_preserved': True, 'provider_pilot_required': True,
        },
        'local_queue': {
            'requires_original_file': True, 'requires_running_executor': True,
            'ephemeral_agent_is_not_a_scheduler': True,
        },
    }
