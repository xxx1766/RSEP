"""Owned namespace manifests. Shared HPA controller flags are never changed."""
from issue30_transport import *
import os
NS='tide-fig7-issue30'
LABEL={'app':'issue30-inference'}
NAMES=json.loads(os.environ.get('RSEP_ELASTICITY_NODE_NAMES','["vm-11-0-4-47-debian","vm-11-0-4-48-debian","vm-11-0-4-49-debian"]'))
def init_namespace():
 existing=json.loads(kubectl(['get','ns','-o','json']))
 assert NS not in [v['metadata']['name'] for v in existing['items']],'preserve any existing namespace'
 obj={'apiVersion':'v1','kind':'Namespace','metadata':{'name':NS,'labels':{'experiment':'issue30-fig7'}}}
 save(OUT/'config/namespace.json',obj);created=json.loads(kubectl(['create','-f','-','-o','json'],json.dumps(obj)));save(OUT/'preflight/namespace-created.json',created)
 cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'issue30-source','namespace':NS},'data':{'issue30_http_worker.py':(OUT/'source/issue30_http_worker.py').read_text()}}
 save(OUT/'config/workload-source-configmap.json',cm);kubectl(['create','-f','-'],json.dumps(cm))
 service={'apiVersion':'v1','kind':'Service','metadata':{'name':'fig7-inference','namespace':NS},'spec':{'selector':LABEL,'ports':[{'name':'work','port':19384,'targetPort':19384}]}}
 save(OUT/'config/service.json',service);kubectl(['create','-f','-'],json.dumps(service))
def deployment(replicas,run):
 env=[{'name':k,'valueFrom':{'fieldRef':{'fieldPath':v}}} for k,v in [('POD_UID','metadata.uid'),('POD_NAME','metadata.name'),('NODE_NAME','spec.nodeName')]]
 return {'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'fig7-inference','namespace':NS,'labels':{'run':run}},'spec':{'replicas':replicas,'selector':{'matchLabels':LABEL},'template':{'metadata':{'labels':dict(LABEL,run=run)},'spec':{
 'automountServiceAccountToken':False,'terminationGracePeriodSeconds':180,
 'tolerations':[{'key':'node-role.kubernetes.io/control-plane','operator':'Exists','effect':'NoSchedule'}],
 'affinity':{'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':NAMES}]}]}}},
 'topologySpreadConstraints':[{'maxSkew':1,'minDomains':3,'topologyKey':'kubernetes.io/hostname','whenUnsatisfiable':'DoNotSchedule','labelSelector':{'matchLabels':LABEL},'nodeTaintsPolicy':'Honor'}],
 'containers':[{'name':'inference','image':IMAGE,'imagePullPolicy':'Never','command':['python3','/issue30/issue30_http_worker.py'],'env':env,
 'resources':{'requests':{'cpu':'2','memory':'512Mi'},'limits':{'cpu':'2','memory':'1Gi'}},
 'readinessProbe':{'httpGet':{'path':'/health','port':19385},'periodSeconds':1,'timeoutSeconds':1,'failureThreshold':3},
 'volumeMounts':[{'name':'source','mountPath':'/issue30','readOnly':True},{'name':'evidence','mountPath':'/evidence'}]}],
 'volumes':[{'name':'source','configMap':{'name':'issue30-source'}},{'name':'evidence','hostPath':{'path':REMOTE+'/hpa-requests','type':'Directory'}}]}}}}
def hpa():return {'apiVersion':'autoscaling/v2','kind':'HorizontalPodAutoscaler','metadata':{'name':'fig7-inference','namespace':NS},'spec':{'scaleTargetRef':{'apiVersion':'apps/v1','kind':'Deployment','name':'fig7-inference'},'minReplicas':1,'maxReplicas':18,'metrics':[{'type':'Resource','resource':{'name':'cpu','target':{'type':'Utilization','averageUtilization':60}}}]}}
def ensure_image_cache(run):
 import concurrent.futures
 def one(host):
  code=f'''import subprocess,json
rows=[v.split() for v in subprocess.check_output(['ctr','-n','k8s.io','images','ls'],text=True).splitlines() if v.startswith({IMAGE!r}+' ')]
reimported=False
if not rows:
 subprocess.run(['ctr','-n','k8s.io','images','import',{REMOTE!r}+'/runtime/inference-image.tar'],capture_output=True,text=True,check=True,timeout=90);reimported=True
 rows=[v.split() for v in subprocess.check_output(['ctr','-n','k8s.io','images','ls'],text=True).splitlines() if v.startswith({IMAGE!r}+' ')]
assert len(rows)==1 and rows[0][2]=={DIGEST!r}
print(json.dumps(dict(reimported=reimported,manifest=rows[0][2])))
'''
  return dict(host=host,result=json.loads(py(host,code,timeout=110)))
 with concurrent.futures.ThreadPoolExecutor(3) as pool:results=list(pool.map(one,HOSTS))
 save(OUT/'preflight'/('image-cache-'+run+'.json'),results)
def create_deployment(n,run):
 ensure_image_cache(run)
 obj=deployment(n,run);save(OUT/'config'/f'deployment-{run}.json',obj);return json.loads(kubectl(['create','-f','-','-o','json'],json.dumps(obj)))
def create_hpa(run):
 obj=hpa();save(OUT/'config'/f'hpa-{run}.json',obj);return json.loads(kubectl(['create','-f','-','-o','json'],json.dumps(obj)))
def pods():return json.loads(kubectl(['get','pods','-n',NS,'-o','json']))
def ready(p):return p.get('status',{}).get('phase')=='Running' and any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[])) and not p['metadata'].get('deletionTimestamp')
def wait_ready(n,timeout=180):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  p=pods();items=p['items']
  if len(items)==n and all(ready(v) for v in items):return p
  time.sleep(2)
 save(OUT/'preflight'/f'readiness-failure-{time.time_ns()}.json',p);raise RuntimeError('owned inference pods not Ready')
def remove_deployment(uid,hpa_uid=None):
 if hpa_uid:
  h=json.loads(kubectl(['get','hpa','fig7-inference','-n',NS,'-o','json']));assert h['metadata']['uid']==hpa_uid
  kubectl(['delete','hpa','fig7-inference','-n',NS,'--wait=true'])
 d=json.loads(kubectl(['get','deploy','fig7-inference','-n',NS,'-o','json']));assert d['metadata']['uid']==uid
 kubectl(['delete','deploy','fig7-inference','-n',NS,'--wait=false'])
 end=time.monotonic()+220
 while time.monotonic()<end:
  p=pods()
  if not p['items']:return
  time.sleep(2)
 raise RuntimeError('owned inference Pods failed to terminate')
if __name__=='__main__':init_namespace();print('namespace initialized',flush=True)
