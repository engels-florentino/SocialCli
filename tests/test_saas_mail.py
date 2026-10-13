"""Exercise SMTP envelope/DATA boundaries with a fictional SMTP server."""
import smtplib
import ssl
import pytest


class Server:
    def __init__(self): self.events=[];self.result=250;self.error=None;self.rcpt_result=250;self.auth_error=False
    def ehlo(self):self.events.append('ehlo');return 250,b'ok'
    def starttls(self,*,context):
        assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
        self.events.append('tls')
    def login(self,*args):
        self.events.append('auth')
        if self.auth_error:raise smtplib.SMTPAuthenticationError(535,b'private password')
    def mail(self,sender):self.events.append('mail');return 250,b'ok'
    def rcpt(self,recipient):self.events.append('rcpt');return self.rcpt_result,b'private recipient'
    def data(self,message):
        self.events.append('data')
        assert b'Message-ID:' in message
        if self.error:raise self.error
        return self.result,b'private queue id'
    def close(self):self.events.append('close')


def mailer(monkeypatch,server):
    from socialctl.saas.mail import MailSettings,SMTPMailer
    from email.message import EmailMessage
    monkeypatch.setattr(smtplib,'SMTP',lambda host,port,timeout:server)
    settings=MailSettings('mail.example.test',587,'fictional-user','private-password')
    message=EmailMessage();message['Message-ID']='<fixture@example.test>';message.set_content('fictional body')
    return SMTPMailer(settings),message


def test_verified_tls_and_auth_precede_durable_send_marker(monkeypatch):
    server=Server();sender,message=mailer(monkeypatch,server)
    def fence():server.events.append('fence');return True
    assert sender.send('recipient@example.test',message,fence)=='accepted'
    assert server.events==['ehlo','tls','ehlo','auth','mail','rcpt','fence','data','close']


def test_cancel_at_fence_never_sends_body(monkeypatch):
    server=Server();sender,message=mailer(monkeypatch,server)
    assert sender.send('recipient@example.test',message,lambda:False)=='cancelled'
    assert 'data' not in server.events


@pytest.mark.parametrize('response,expected',[(451,'retry'),(550,'rejected'),(250,'accepted'),(300,'uncertain')])
def test_final_data_ack_controls_retry(monkeypatch,response,expected):
    server=Server();server.result=response;sender,message=mailer(monkeypatch,server)
    assert sender.send('recipient@example.test',message,lambda:True)==expected


def test_lost_data_response_is_uncertain_and_auth_failure_can_retry(monkeypatch):
    server=Server();server.error=smtplib.SMTPServerDisconnected('private detail');sender,message=mailer(monkeypatch,server)
    assert sender.send('recipient@example.test',message,lambda:True)=='uncertain'
    server.auth_error=True
    assert sender.send('recipient@example.test',message,lambda:True)=='retry'


def test_recipient_rejection_and_header_injection_never_send_data(monkeypatch):
    server=Server();server.rcpt_result=550;sender,message=mailer(monkeypatch,server)
    assert sender.send('recipient@example.test',message,lambda:True)=='rejected'
    assert sender.send('recipient@example.test\r\nBcc:other@example.test',message,lambda:True)=='rejected'
    assert 'data' not in server.events


def test_close_failure_does_not_change_accepted_ack(monkeypatch):
    server=Server();sender,message=mailer(monkeypatch,server)
    def fail():raise OSError('private close detail')
    server.close=fail
    assert sender.send('recipient@example.test',message,lambda:True)=='accepted'
