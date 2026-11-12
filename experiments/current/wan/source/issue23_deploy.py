"""Public, reproducible manifests for actual VM Nodes and native Services."""
import argparse
import json
import issue23_common as p
from issue29_config import LEGACY_GUEST_ROOT

NAMESPACE='issue23'


def service(name,selector,prefer=False):
    spec=dict(type='ClusterIP',internalTrafficPolicy='Cluster',sessionAffinity='None',selector=selector,
        ports=[dict(name='infer',port=8080,targetPort=19383)])
    if prefer:spec['trafficDistribution']='PreferClose'
    return dict(apiVersion='v1',kind='Service',metadata=dict(name=name,namespace=NAMESPACE),spec=spec)


def manifests(vms,include_services):
    items=[]
    if include_services:items.append(dict(apiVersion='v1',kind='Namespace',metadata=dict(name=NAMESPACE)))
    for vm in vms:
        container=dict(name='infer',image='docker.io/tide/workload:amd64',imagePullPolicy='Never',
            command=['python3','/experiment/issue23_image_server.py','--input','/experiment/dog.jpg','--output','/output','--port','19383'],
            env=[dict(name=k,value=v) for k,v in dict(PYTHONPATH='/app',PYTHONUNBUFFERED='1',TIDE_MODEL='/app/yolox_nano.onnx',ORT_INTRA_THREADS='1',ORT_INTER_THREADS='1').items()],
            resources=dict(requests=dict(cpu='900m',memory='512Mi'),limits=dict(cpu='1',memory='1Gi')),
            readinessProbe=dict(httpGet=dict(path='/health',port=19383),periodSeconds=5,timeoutSeconds=2),
            volumeMounts=[dict(name='code',mountPath='/experiment',readOnly=True),dict(name='output',mountPath='/output')])
        items.append(dict(apiVersion='v1',kind='Pod',metadata=dict(name=vm,namespace=NAMESPACE,
            labels=dict(experiment='issue23',active='true',worker=vm)),
            spec=dict(nodeName=vm,hostNetwork=True,dnsPolicy='ClusterFirstWithHostNet',containers=[container],
            volumes=[dict(name='code',hostPath=dict(path=LEGACY_GUEST_ROOT+'/code',type='Directory')),
                dict(name='output',hostPath=dict(path=LEGACY_GUEST_ROOT+'/pod-output',type='DirectoryOrCreate'))])))
        items.append(service('witness-'+vm,dict(experiment='issue23',worker=vm)))
    if include_services:
        selector=dict(experiment='issue23',active='true')
        items.extend([service('wan-default',selector),service('wan-prefer-close',selector,True)])
    return dict(apiVersion='v1',kind='List',items=items)


def main(vms,initial):
    path=p.OUT/'validation/cluster'/('deployment-'+str(len(vms))+'-'+vms[0]+'.json')
    if path.exists():raise RuntimeError('manifest output exists')
    p.save(path,manifests(vms,initial));p.guest_copy('i23-c01',path,LEGACY_GUEST_ROOT+'/'+path.name)
    print(p.guest('i23-c01','KUBECONFIG=/etc/kubernetes/admin.conf kubectl create -f '+LEGACY_GUEST_ROOT+'/'+path.name),flush=True)
    print(p.guest('i23-c01',f'KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n {NAMESPACE} wait --for=condition=Ready pod -l experiment=issue23 --timeout=180s',timeout=200),flush=True)
    for resource in ['nodes','pods -A','services -n issue23','endpointslices -n issue23']:
        row=json.loads(p.guest('i23-c01','KUBECONFIG=/etc/kubernetes/admin.conf kubectl get '+resource+' -o json'))
        p.save(p.OUT/'validation/cluster'/(resource.replace(' ','_')+'-after-'+vms[0]+'.json'),row)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--initial',action='store_true');parser.add_argument('vms',nargs='+');a=parser.parse_args();main(a.vms,a.initial)
