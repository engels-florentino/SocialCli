"""Escaped server-rendered account views using the approved SocialCli shell."""
from html import escape
from socialctl.saas import consent
from socialctl.connection_service.landing import _page, connection_symbols, PROVIDERS


def page(title,content,*,platform=None):
    if platform in PROVIDERS:
        content = connection_symbols(platform) + content
    return _page(title,content,platform=platform).replace('ACCOUNT CONNECTION','YOUR SOCIALCLI ACCOUNT').replace(
        '<main id="main-content">','<a class="skip-link" href="#main-content">Skip to content</a><main id="main-content">')


def hidden(name,value):
    return f'<input type="hidden" name="{escape(name,quote=True)}" value="{escape(value,quote=True)}">'


def notice(message, *, field=None):
    identifier = f' id="{escape(field, quote=True)}-error"' if field else ''
    return f'<p{identifier} class="status-notice" role="alert">{escape(message)}</p>' if message else ''


def login(csrf,next_path,message='',*,input_error=False):
    invalid = ' aria-invalid="true" aria-describedby="email-error"' if input_error and message else ''
    return page('Sign in',f'''<p class="eyebrow">WELCOME TO SOCIALCLI</p>
<h1 id="page-title">Your social accounts.<br>Your AI assistants.</h1>
<p class="description">Sign in with a code sent to your email. No password needed.</p>{notice(message, field="email" if invalid else None)}
<form method="post" action="/account/login" class="form-stack">{hidden('csrf',csrf)}{hidden('next',next_path)}
<div><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" maxlength="254" required{invalid}></div>
{privacy_choice_html(required=False)}
<button class="primary-button" type="submit">Send sign-in code</button></form>
<p class="expiry-note">14 days free from your first connection. Then USD 10/month.</p>''')


def verify(csrf,attempt,email,message='',*,input_error=False):
    invalid = ' aria-invalid="true" aria-describedby="code-error"' if input_error and message else ''
    return page('Check your email',f'''<p class="eyebrow">CHECK YOUR EMAIL</p><h1 id="page-title">Enter your sign-in code</h1>
<p class="description">We sent a code to {escape(email)}. It expires in 10 minutes.</p>{notice(message, field="code" if invalid else None)}
<form method="post" action="/account/verify" class="form-stack">{hidden('csrf',csrf)}{hidden('attempt',attempt)}
<div><label for="code">One-time code</label><input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6,8}}" minlength="6" maxlength="8" required{invalid}></div>
<button class="primary-button" type="submit">Sign in</button></form>
<a class="text-link" href="/account/login">Send a new code</a>''')


def account(user,csrf,details=''):
    return page('Your account',f'''<p class="eyebrow">YOUR ACCOUNT</p><h1 id="page-title">Welcome to SocialCli</h1>
<p class="description">{escape(user.email)}</p>{details}
<p class="quiet-notice">Connecting an account never approves publishing. You stay in control.</p>
<form method="post" action="/account/logout" class="form-stack">{hidden('csrf',csrf)}<button class="secondary-button" type="submit">Sign out</button></form>''')


def error(message):
    return page('Please try again',f'<h1 id="page-title">Let\'s try that again</h1>{notice(message)}<a class="secondary-button" href="/account/login">Back to sign in</a>')


def utc_date(value):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value,timezone.utc).strftime('%b %d, %Y at %H:%M UTC')


def subscription(decision,entitlement,billing,csrf,*,enabled,processing=False):
    reason=decision.reason
    action='';message='';heading='Your plan'
    if reason=='trial_not_started':
        message='Your 14 free days start when you connect your first social account. No card required.'
    elif reason=='trial':
        message='Your free trial ends '+utc_date(entitlement['trial_end'])+'.'
        ongoing=entitlement['subscription_status'] not in ('','canceled','incomplete_expired')
        if ongoing:
            message+=' Your subscription is arranged. The first USD 10 monthly payment is due when your trial ends.'
            if billing and billing['cancel_at_period_end']:
                message+=' Cancellation is scheduled; check Manage billing for the final date.'
        elif enabled:
            message+=' Subscribe now to keep access. Your first USD 10 payment is due at the end of your trial, then monthly. Cancel anytime.'
            action=f'<form method="post" action="/account/billing/checkout" class="form-stack">{hidden("csrf",csrf)}<button type="submit" class="primary-button">Subscribe — USD 10/month</button></form>'
    elif reason=='paid':
        heading='Subscription active'
        ending=billing and billing['cancel_at_period_end'] or entitlement['subscription_status']=='canceled'
        message=('Will not renew. Access ends ' if ending else 'Your paid access ends ')+utc_date(decision.access_until)+'.'
        if not ending:message+=' Renews monthly at USD 10.'
    elif reason in {'suspended','account_unavailable'}:
        heading='Account unavailable';message='Contact support for help with access.'
        if enabled and billing and billing['customer_id']:message+=' You can still manage your billing.'
    else:
        heading='Your free trial has ended' if reason=='trial_expired' else 'Subscription required'
        message='Subscribe to continue using your connected accounts. USD 10 is charged when you confirm, then monthly. Cancel anytime.'
        ongoing=entitlement and entitlement['subscription_status'] not in ('','canceled','incomplete_expired')
        if ongoing:
            heading='Subscription needs attention'
            message='Your subscription needs attention. Open Manage billing to check your payment and restore access.'
        elif enabled:
            action=f'<form method="post" action="/account/billing/checkout" class="form-stack">{hidden("csrf",csrf)}<button type="submit" class="primary-button">Subscribe — USD 10/month</button></form>'
    checking=processing and enabled and reason in {'trial','trial_expired','subscription_required'}
    if checking:
        heading='Checking your payment'
        message='Payment confirmation can take a few minutes. Check your account status before trying to subscribe again. If access is still unavailable, check Manage billing when available or contact support.'
        action='<a class="secondary-button" href="/account/">Check account status</a>'
    if enabled and billing and billing['customer_id']:
        action+=f'<form method="post" action="/account/billing/portal" class="form-stack">{hidden("csrf",csrf)}<button type="submit" class="secondary-button">Manage billing</button></form>'
    if not enabled:message+=' Subscription checkout is not available yet.'
    status=' role="status"' if checking else ''
    return f'<section aria-label="Subscription"><h2>{escape(heading)}</h2><p class="description">USD 10/month · One brand · Four social platforms</p><p class="status-notice"{status}>{escape(message)}</p>{action}</section>'


def privacy_choice_html(*, required=True):
    needed=' required' if required else ''
    return f'''{hidden('privacy_version',consent.PRIVACY_VERSION)}
<label for="privacy-accept"><input id="privacy-accept" type="checkbox" name="privacy_accept" value="1"{needed}> I have read and agree to the <a href="https://socialcli.app/privacy.html" target="_blank" rel="noopener">Privacy Policy</a>.</label>
<p class="expiry-note">Only the account owner may accept. Acceptance is required to enable features. You can sign in to delete your account or sign out without accepting.</p>'''


def privacy_review(csrf,next_path,account_csrf):
    return page('Review the Privacy Policy',f'''<p class="eyebrow">PRIVACY REVIEW</p><h1 id="page-title">Review the Privacy Policy</h1>
<p class="description">Before using SocialCli features, review how we access, use, share and retain your account data. A material policy change requires a new owner acceptance.</p>
<form method="post" action="/account/privacy" class="form-stack">{hidden('csrf',csrf)}{hidden('next',next_path)}{privacy_choice_html()}<button class="primary-button" type="submit">Accept and continue</button></form>
<a class="text-link" href="/account/delete">Delete my account</a>
<form method="post" action="/account/logout">{hidden('csrf',account_csrf)}<button class="secondary-button" type="submit">Sign out</button></form>''')


def assistant_choice_html():
    return f'''<fieldset class="permission-options"><legend>Assistant data access</legend>
<p>This installation receives SocialCli output and may obtain provider access tokens for the selected accounts. Output can include Google/YouTube account details, videos, comments and analytics. Tokens may allow direct provider access with all granted permissions.</p>
<p>Customer-controlled software runs under your control. If you use a third-party AI service, data you give it also goes to that provider under its terms and settings. Use Google data only for the features you explicitly request. Do not use Google data for model training or advertising. Human access requires specific consent permitted by the applicable Google policy.</p>
<p>Before continuing, choose a provider and configuration that restrict use and transfer to your requested features, disables training and advertising, and controls retention and human access. If these settings cannot be verified, do not link this installation. SocialCli does not certify every advertised assistant or provider configuration and cannot enforce third-party settings after data or tokens are delivered.</p>
{hidden('assistant_version',consent.ASSISTANT_VERSION)}
<label for="assistant-accept"><input id="assistant-accept" type="checkbox" name="assistant_accept" value="1" required> I am the account owner, understand this transfer, and have selected suitable software or provider settings for my requested features.</label></fieldset>'''
