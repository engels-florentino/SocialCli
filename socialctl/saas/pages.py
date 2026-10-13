"""Escaped server-rendered account views using the approved SocialCli shell."""
from html import escape
from socialctl.connection_service.landing import _page


def page(title,content):
    return _page(title,content).replace('ACCOUNT CONNECTION','YOUR SOCIALCLI ACCOUNT').replace(
        '<main id="main-content">','<a class="skip-link" href="#main-content">Skip to content</a><main id="main-content">')


def hidden(name,value):
    return f'<input type="hidden" name="{escape(name,quote=True)}" value="{escape(value,quote=True)}">'


def notice(message):
    return f'<p class="status-notice" role="alert">{escape(message)}</p>' if message else ''


def login(csrf,next_path,message=''):
    return page('Sign in',f'''<p class="eyebrow">WELCOME TO SOCIALCLI</p>
<h1 id="page-title">Your social accounts.<br>Your AI assistants.</h1>
<p class="description">Sign in with a code sent to your email. No password needed.</p>{notice(message)}
<form method="post" action="/account/login" class="form-stack">{hidden('csrf',csrf)}{hidden('next',next_path)}
<div><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" maxlength="254" required></div>
<button class="primary-button" type="submit">Send sign-in code</button></form>
<p class="expiry-note">14 days free from your first connection. Then USD 10/month.</p>''')


def verify(csrf,attempt,email,message=''):
    return page('Check your email',f'''<p class="eyebrow">CHECK YOUR EMAIL</p><h1 id="page-title">Enter your sign-in code</h1>
<p class="description">We sent a code to {escape(email)}. It expires in 10 minutes.</p>{notice(message)}
<form method="post" action="/account/verify" class="form-stack">{hidden('csrf',csrf)}{hidden('attempt',attempt)}
<div><label for="code">One-time code</label><input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6,8}}" minlength="6" maxlength="8" required></div>
<button class="primary-button" type="submit">Sign in</button></form>
<a class="text-link" href="/account/login">Send a new code</a>''')


def account(user,csrf,details=''):
    return page('Your account',f'''<p class="eyebrow">YOUR ACCOUNT</p><h1 id="page-title">Welcome to SocialCli</h1>
<p class="description">{escape(user.email)}</p>{details}
<p class="quiet-notice">Connecting an account never approves publishing. You stay in control.</p>
<form method="post" action="/account/logout" class="form-stack">{hidden('csrf',csrf)}<button class="secondary-button" type="submit">Sign out</button></form>''')


def error(message):
    return page('Please try again',f'<h1 id="page-title">Let\'s try that again</h1>{notice(message)}<a class="secondary-button" href="/account/login">Back to sign in</a>')
