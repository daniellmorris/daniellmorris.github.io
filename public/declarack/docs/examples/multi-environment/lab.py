#!/usr/bin/env python3
"""Provision and verify real nested Swarms using Docker, SSH, and Python stdlib."""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
RUNTIME = ROOT / 'runtime'
PROJECT = 'declarack-lab'
ENGINE_SERVICES = ['swarm-a-manager', 'swarm-a-worker', 'swarm-b-manager', 'swarm-b-worker']
PORTS = {'declarack': ('LAB_DECLARACK_PORT', 9080), 'grafana': ('LAB_GRAFANA_PORT', 9300),
         'prometheus': ('LAB_PROMETHEUS_PORT', 9090), 'ssh_a': ('LAB_SSH_A_PORT', 2221),
         'ssh_b': ('LAB_SSH_B_PORT', 2222), 'web_a': ('LAB_WEB_A_PORT', 18081), 'web_b': ('LAB_WEB_B_PORT', 18082)}


def run(args, *, capture=False, input=None):
    result = subprocess.run([str(arg) for arg in args], input=input, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def compose(*args, capture=False):
    return run(['docker', 'compose', '-p', PROJECT, '-f', ROOT/'compose.yaml', *args], capture=capture)


def engine(service, *args, capture=True):
    return compose('exec', '-T', service, 'docker', *args, capture=capture)


def port(name):
    env, default = PORTS[name]
    return int(os.environ.get(env, default))


def prepare():
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    RUNTIME.chmod(0o700)
    if not (RUNTIME/'client_key').exists():
        run(['ssh-keygen','-q','-t','ed25519','-N','','-f',RUNTIME/'client_key'])
    for name in ('declarack_password','grafana_password'):
        file = RUNTIME/name
        if not file.exists():
            file.write_text(secrets.token_urlsafe(24)+'\n')
        file.chmod(0o644 if name == 'grafana_password' else 0o600)
    (RUNTIME/'client_key').chmod(0o600)


def wait(check, label, timeout=120):
    deadline = time.monotonic()+timeout
    last = ''
    while time.monotonic()<deadline:
        try:
            result = check()
            if result:
                return result
        except (URLError, OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            last = str(error)
        time.sleep(1)
    raise RuntimeError(f'Timed out waiting for {label}. {last}')


def known_hosts():
    entries = []
    for suffix in ('a','b'):
        manager=f'swarm-{suffix}-manager'
        public=compose('exec','-T',manager,'cat','/var/lib/lab-ssh/ssh_host_ed25519_key.pub',capture=True)
        key=' '.join(public.split()[:2])
        entries.append(f'{manager},[127.0.0.1]:{port("ssh_"+suffix)},[localhost]:{port("ssh_"+suffix)} {key}')
    file=RUNTIME/'known_hosts';file.write_text('\n'.join(entries)+'\n');file.chmod(0o600)


def ssh(suffix, *command):
    return run(['ssh','-F','/dev/null','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                '-o','ConnectTimeout=5','-o',f'UserKnownHostsFile={RUNTIME/"known_hosts"}',
                '-i',RUNTIME/'client_key','-p',str(port('ssh_'+suffix)),'root@127.0.0.1',*command],capture=True)


def ip(service, network):
    identifier=compose('ps','-q',service,capture=True)
    info=json.loads(run(['docker','inspect',identifier],capture=True))[0]
    return info['NetworkSettings']['Networks'][PROJECT+'_'+network]['IPAddress']


def initialize(suffix):
    manager=f'swarm-{suffix}-manager';worker=f'swarm-{suffix}-worker'
    address=ip(manager,'swarm_'+suffix)
    info=json.loads(engine(manager,'info','--format','{{json .Swarm}}'))
    if info['LocalNodeState']=='inactive':
        engine(manager,'swarm','init','--advertise-addr',address,'--default-addr-pool',f'10.{151 if suffix=="a" else 152}.0.0/16','--default-addr-pool-mask-length','24')
    elif not info.get('ControlAvailable'):
        raise RuntimeError(f'{manager} is already part of a different cluster. Refusing to reset it.')
    worker_info=json.loads(engine(worker,'info','--format','{{json .Swarm}}'))
    if worker_info['LocalNodeState']=='inactive':
        token=engine(manager,'swarm','join-token','-q','worker')
        # Tokens stay in memory and are never printed.
        engine(worker,'swarm','join','--token',token,'--advertise-addr',ip(worker,'swarm_'+suffix),address+':2377')
    def ready():
        rows=[json.loads(line) for line in engine(manager,'node','ls','--format','{{json .}}').splitlines()]
        return len(rows)==2 and all(row['Status']=='Ready' for row in rows)
    wait(ready,manager+' and worker')
    node_id=engine(worker,'info','--format','{{.Swarm.NodeID}}')
    node=json.loads(engine(manager,'node','inspect',node_id))[0]
    if node['Spec']['Role']!='worker':
        raise RuntimeError(f'{worker} is not this cluster\'s worker.')


def load_images(service):
    print(f'Loading workload and job images into {service}...',flush=True)
    save=subprocess.Popen(['docker','image','save','declarack-lab:local','declarack-lab-app:local'],stdout=subprocess.PIPE)
    try:
        subprocess.run(['docker','compose','-p',PROJECT,'-f',str(ROOT/'compose.yaml'),'exec','-T',service,'docker','image','load'],stdin=save.stdout,stdout=subprocess.DEVNULL,check=True)
    finally:
        save.stdout.close()
        code=save.wait()
    if code:raise RuntimeError('Image save failed.')


def deploy(suffix):
    manager=f'swarm-{suffix}-manager'
    for name,role,published,replicas in [('web','worker',8080,2),('heartbeat','manager',8081,1)]:
        service='lab-'+name
        if engine(manager,'service','ls','--filter','name='+service,'-q'):
            engine(manager,'service','update','--detach','--force','--image','declarack-lab-app:local','--no-resolve-image',service)
        else:
            engine(manager,'service','create','--detach','--name',service,'--no-resolve-image','--constraint','node.role=='+role,
                   '--replicas',str(replicas),'--publish',f'published={published},target=8000',
                   '--env','LAB_NAME=swarm-'+suffix,'--env','LAB_ROLE='+name,'--label','io.declarack.lab=1',
                   'declarack-lab-app:local')


def read_template():
    code='import json,yaml; print(json.dumps(yaml.safe_load(open("/template.yaml"))))'
    return json.loads(run(['docker','run','--rm','-v',f'{ROOT/"declarack.yaml"}:/template.yaml:ro','declarack-lab:local','python','-c',code],capture=True))


def write_yaml(file, data):
    text=run(['docker','run','--rm','-i','declarack-lab:local','python','-c',
              'import json,sys,yaml; print(yaml.safe_dump(json.load(sys.stdin),sort_keys=False),end="")'],input=json.dumps(data),capture=True)
    file.write_text(text+'\n');file.chmod(0o600)


def configure(mode):
    for style in ('ssh','direct'):
        config=read_template()
        if style=='direct':
            for name in ('grafana','prometheus'):config['targets'][name].pop('ssh_tunnel',None)
        write_yaml(RUNTIME/f'declarack.{style}.yaml',config)
    (RUNTIME/'declarack.yaml').write_text((RUNTIME/f'declarack.{mode}.yaml').read_text())
    (RUNTIME/'declarack.yaml').chmod(0o600)
    # A directory is just ordered YAML fragments of this same merged config.
    merged = read_template()
    if mode == 'direct':
        for name in ('grafana', 'prometheus'):
            merged['targets'][name].pop('ssh_tunnel', None)
    config_dir = RUNTIME/'config'
    config_dir.mkdir(mode=0o700, exist_ok=True)
    for filename, section in [('00-system.yaml','auth'), ('05-credentials.yaml','credentials'), ('10-targets.yaml','targets'),
                              ('20-operations.yaml','operations'), ('30-features.yaml','features'), ('40-pages.yaml','pages'), ('60-environments.yaml','environments'), ('70-checks.yaml','checks')]:
        write_yaml(config_dir/filename, {section: merged[section]})

    # Host-side variant, for running Python directly instead of the lab GUI container.
    config=read_template()
    config['credentials'][config['auth']['bootstrap_credential']]['password']={'file':str(RUNTIME/'declarack_password')}
    for profile in config['credentials'].values():
        if profile['type']=='ssh':
            profile['private_key']={'file':str(RUNTIME/'client_key')};profile['known_hosts']={'file':str(RUNTIME/'known_hosts')}
    for target in config['targets'].values():
        tunnel=target.get('ssh_tunnel')
        if tunnel:
            tunnel['port']=port('ssh_a' if tunnel['host']=='swarm-a-manager' else 'ssh_b')
            tunnel['host']='127.0.0.1'
    for name in ('grafana','prometheus'):
        config['targets'][name]['base_url']=f'http://127.0.0.1:{port(name)}'
        if mode=='direct':config['targets'][name].pop('ssh_tunnel',None)
    write_yaml(RUNTIME/'declarack.host.yaml',config)
    print(f'HTTP target transport: {mode}',flush=True)


_AUTH_CLIENTS = {}


def http(path, *, auth=False, body=None, _retry=True):
    from http.cookiejar import CookieJar
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    from urllib.request import HTTPCookieProcessor, build_opener
    headers={}
    if body is not None:headers.update({'Content-Type':'application/json','X-Declarack-Action':'1'})
    client=None
    if auth:
        parsed=urlsplit(path);origin=f'{parsed.scheme}://{parsed.netloc}'
        if origin not in _AUTH_CLIENTS:
            password=(RUNTIME/'declarack_password').read_text().strip()
            client=build_opener(HTTPCookieProcessor(CookieJar()))
            login=Request(origin+'/api/v1/auth/login',headers={'Content-Type':'application/json','X-Declarack-Action':'1'},data=json.dumps({'username':'admin','password':password}).encode())
            with client.open(login,timeout=20) as response:session=json.load(response)
            _AUTH_CLIENTS[origin]=(client,session['csrf_token'])
        client,csrf=_AUTH_CLIENTS[origin]
        headers['X-Declarack-CSRF']=csrf
    try:
        with (client.open if client else urlopen)(Request(path,headers=headers,data=None if body is None else json.dumps(body).encode()),timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        if auth and _retry and error.code==401:
            _AUTH_CLIENTS.pop(origin,None)
            return http(path,auth=auth,body=body,_retry=False)
        raise


def operation(target, name):
    return http(f'http://127.0.0.1:{port("declarack")}/api/v1/targets/{target}/operations/{name}/run',auth=True,body={})['result']


def verify():
    clusters=[]
    for suffix in ('a','b'):
        info=json.loads(ssh(suffix,'docker','info','--format',"'{{json .Swarm}}'"))
        if not info.get('ControlAvailable'):raise RuntimeError('SSH target is not a manager')
        clusters.append(info['Cluster']['ID'])
        nodes=operation('swarm_'+suffix,'swarm.nodes.list')
        if len(nodes)!=2 or sorted(node['Spec']['Role'] for node in nodes)!=['manager','worker']:
            raise RuntimeError('Expected exactly one manager and one worker')
        services=operation('swarm_'+suffix,'swarm.services.list')
        expected={'lab-web':2,'lab-heartbeat':1}
        for service in services:
            name=service['Spec']['Name']
            if name in expected and service.get('ServiceStatus',{}).get('RunningTasks')==expected[name]:expected.pop(name)
        if expected:raise RuntimeError('Sample services not converged: '+str(expected))
        app=http(f'http://127.0.0.1:{port("web_"+suffix)}/health')
        if app['swarm']!='swarm-'+suffix:raise RuntimeError('Workload response came from the wrong Swarm')
    if clusters[0]==clusters[1]:raise RuntimeError('The two managers share a Swarm ID')
    if not isinstance(operation('host','containers.list'),list):raise RuntimeError('Host engine target failed')
    targets=operation('prometheus','prometheus.targets.read')['data']['activeTargets']
    if len(targets)<7 or any(target['health']!='up' for target in targets):
        raise RuntimeError('Prometheus scrape targets are not all up')
    if not operation('grafana','grafana.dashboards.read'):raise RuntimeError('Grafana provisioned dashboard missing')
    print('Verified: two distinct Swarms, one manager + one worker each, real services, host Docker, 7 healthy scrapes, Grafana, and Declarack queries.',flush=True)


def up(mode):
    host_before=run(['docker','info','--format','{{json .Swarm}}'],capture=True)
    prepare()
    compose('build','swarm-a-manager','declarack')
    compose('up','-d','--wait',*ENGINE_SERVICES,'prometheus','grafana')
    known_hosts()
    for suffix in ('a','b'):initialize(suffix)
    run(['docker','build','-t','declarack-lab-app:local',ROOT/'app'])
    for service in ENGINE_SERVICES:load_images(service)
    for suffix in ('a','b'):deploy(suffix)
    configure(mode)
    compose('up','-d','--force-recreate','declarack')
    wait(lambda:http(f'http://127.0.0.1:{port("declarack")}/api/v1/targets',auth=True),'Declarack')
    wait(lambda:(verify() or True),'the full running lab',180)
    if run(['docker','info','--format','{{json .Swarm}}'],capture=True)!=host_before:
        raise RuntimeError('Host Swarm state changed unexpectedly; investigate before continuing.')
    print(f'Declarack: http://127.0.0.1:{port("declarack")}/#/~pages/lab\nGrafana: http://127.0.0.1:{port("grafana")}\nPrometheus: http://127.0.0.1:{port("prometheus")}\nPasswords: {RUNTIME}/declarack_password and grafana_password (username admin).',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['up','verify','configure','down','status'])
    parser.add_argument('--http-mode',choices=['ssh','direct'],default='ssh')
    args=parser.parse_args()
    if args.command=='up':up(args.http_mode)
    elif args.command=='verify':verify()
    elif args.command=='configure':
        configure(args.http_mode)
        compose('up','-d','--force-recreate','declarack')
        wait(lambda:http(f'http://127.0.0.1:{port("declarack")}/api/v1/targets',auth=True),'Declarack after configuration change')
    elif args.command=='down':compose('down')  # Volumes and credentials are preserved.
    else:compose('ps')

if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError,URLError) as error:
        message = f'Process exited with status {error.returncode}; inspect the preceding output.' if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f'Lab setup failed: {message}',file=sys.stderr)
        sys.exit(1)
