"""Local training API. Restarts update example data, not real workloads."""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from threading import RLock
from urllib.parse import urlsplit


def create_server(host='127.0.0.1',port=9010,label='Training'):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self,status,value,head=False):
            data=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers()
            if not head:self.wfile.write(data)
        def do_HEAD(self):self.get(head=True)
        def do_GET(self):self.get()
        def get(self,head=False):
            parts=urlsplit(self.path).path.strip('/').split('/')
            with self.server.state_lock:
                apps=deepcopy(self.server.apps)
                if parts==['']:value={'name':self.server.label,'training':True}
                elif parts==['health']:value={'status':'ok','label':self.server.label}
                elif parts==['apps']:value={'items':apps}
                elif len(parts)==2 and parts[0]=='apps':
                    value=next((app for app in apps if app['id']==parts[1]),None)
                    if value is None:return self.send_json(404,{'error':'Unknown application'},head)
                elif parts==['teams']:value={'items':[{'id':'platform','name':'Platform'},{'id':'product','name':'Product'}]}
                elif len(parts)==3 and parts[0]=='teams' and parts[2]=='apps':value={'items':[app for app in apps if app['team']==parts[1]]}
                else:return self.send_json(404,{'error':'Unknown endpoint'},head)
            self.send_json(200,value,head)
        def do_POST(self):
            parts=urlsplit(self.path).path.strip('/').split('/')
            if len(parts)!=3 or parts[0]!='apps' or parts[2]!='restart':return self.send_json(404,{'error':'Unknown endpoint'})
            try:size=int(self.headers.get('Content-Length','0'))
            except ValueError:return self.send_json(400,{'error':'Invalid body length'})
            if not 0<=size<=4096:return self.send_json(413,{'error':'Body too large'})
            self.rfile.read(size)
            with self.server.state_lock:
                app=next((app for app in self.server.apps if app['id']==parts[1]),None)
                if app is None:return self.send_json(404,{'error':'Unknown application'})
                app['restarts']+=1;result=deepcopy(app)
            self.send_json(200,result)
    server=ThreadingHTTPServer((host,port),Handler)
    server.state_lock=RLock();server.label=label
    server.apps=[{'id':'api','name':'Catalog API','team':'platform','status':'running','restarts':0},
                 {'id':'worker','name':'Queue worker','team':'platform','status':'stopped','restarts':0},
                 {'id':'portal','name':'Customer portal','team':'product','status':'running','restarts':0}]
    return server

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=9010);parser.add_argument('--label',default='Training');args=parser.parse_args()
    server=create_server(port=args.port,label=args.label)
    print(f'{args.label} training API: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
