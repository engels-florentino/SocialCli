"""Verified STARTTLS SMTP, one recipient, with an explicit durable DATA fence."""
from dataclasses import dataclass,field
from email.headerregistry import Address
from email.policy import SMTP
import os
import re
import smtplib
import ssl


def mailbox(value):
    if not isinstance(value,str) or not value.isascii() or any(ord(c)<33 or ord(c)==127 for c in value):
        raise ValueError('Invalid mail recipient.')
    try: address=Address(addr_spec=value)
    except Exception: raise ValueError('Invalid mail recipient.') from None
    if not address.username or not address.domain or address.addr_spec!=value:
        raise ValueError('Invalid mail recipient.')
    return value


@dataclass(frozen=True)
class MailSettings:
    host:str
    port:int
    username:str=field(repr=False)
    password:str=field(repr=False)

    def __post_init__(self):
        if (not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',self.host) or not 1<=self.port<=65535
                or not self.username or not self.password):
            raise ValueError('Trial reminders require a valid private SMTP configuration.')

    @classmethod
    def from_environment(cls):
        if os.environ.get('SOCIALCLI_REMINDERS_ENABLED')!='1':return None
        return cls(os.environ.get('SOCIALCLI_SMTP_HOST',''),int(os.environ.get('SOCIALCLI_SMTP_PORT','587')),
                   os.environ.get('SOCIALCLI_SMTP_USER',''),os.environ.get('SOCIALCLI_SMTP_PASSWORD',''))


def rejection(code):
    return 'retry' if 400<=code<500 else 'rejected' if 500<=code<600 else 'uncertain'


class SMTPMailer:
    def __init__(self,settings):
        self.settings=settings
        self.budget=None

    def constrain(self,server):
        if self.budget:
            server.sock.settimeout(self.budget.timeout(20))

    def send(self,recipient,message,before_send):
        try:mailbox(recipient)
        except ValueError:return 'rejected'
        server=None;sending=False
        try:
            raw=message.as_bytes(policy=SMTP)
            server=smtplib.SMTP(self.settings.host,self.settings.port,
                timeout=self.budget.timeout(20) if self.budget else 20)
            self.constrain(server)
            code,_=server.ehlo()
            if code!=250:return 'retry'
            self.constrain(server)
            server.starttls(context=ssl.create_default_context())
            self.constrain(server)
            code,_=server.ehlo()
            if code!=250:return 'retry'
            self.constrain(server)
            server.login(self.settings.username,self.settings.password)
            self.constrain(server)
            code,_=server.mail('access@socialcli.app')
            if code!=250:return 'retry'  # sender configuration must be fixed by operator
            self.constrain(server)
            code,_=server.rcpt(recipient)
            if code not in {250,251}:return rejection(code)
            self.constrain(server)
            if not before_send():return 'cancelled'
            self.constrain(server)
            sending=True
            try:code,_=server.data(raw)
            except smtplib.SMTPDataError as error:return rejection(error.smtp_code)
            return 'accepted' if code==250 else rejection(code)
        except Exception:
            return 'uncertain' if sending else 'retry'
        finally:
            if server is not None:
                try:server.close()
                except Exception:pass  # close cannot undo a received SMTP acceptance
