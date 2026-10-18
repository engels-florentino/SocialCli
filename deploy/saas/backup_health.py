#!/usr/bin/env python3
"""Unprivileged, read-only health endpoint; no access to private backup records."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import stat
import time


def response(path,*,now=time.time):
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(descriptor,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError
            raw=stream.read(4097)
        if len(raw)>4096:raise ValueError
        value=json.loads(raw);current=now()
        if set(value)!={'check','evaluated_at','completed_at','healthy'}:raise ValueError
        if value['check']!='backup' or value['healthy'] is not True:raise ValueError
        for key in ('evaluated_at','completed_at'):
            stamp=value[key]
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=stamp<=current:
                raise ValueError
        if not value['completed_at']<=value['evaluated_at']:raise ValueError
        if current-value['evaluated_at']>600 or current-value['completed_at']>7200:raise ValueError
        return 200,b'backup-current\n'
    except Exception:
        return 503,b'backup-unavailable\n'


def handler(path):
    class Handler(BaseHTTPRequestHandler):
        server_version='SocialCliHealth'
        sys_version=''

        def setup(self):
            self.request.settimeout(3)
            super().setup()

        def do_GET(self):
            code,body=response(path) if self.path=='/monitoring/backup' else (404,b'not-found\n')
            self.send_response(code)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)

        def log_message(self,*args):
            pass
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt',type=Path)
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8790)
    args=parser.parse_args()
    HTTPServer((args.bind,args.port),handler(args.receipt)).serve_forever()


if __name__=='__main__':main()
