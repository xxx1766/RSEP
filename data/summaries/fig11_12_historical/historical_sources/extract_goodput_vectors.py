"""Read published vector geometry; retain discrepancies rather than changing data."""
import bisect,csv,hashlib,json,pathlib,re,xml.etree.ElementTree as ET
R=pathlib.Path(__file__).resolve().parent
NUMBER=r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?'
SOURCE_ROLES={'rgb(12.156677%, 46.665955%, 70.587158%)':'KubeEdge','rgb(17.254639%, 62.744141%, 17.254639%)':'Bare Metal','rgb(49.803162%, 49.803162%, 49.803162%)':'Ray-Actor-40','rgb(100%, 49.803162%, 5.490112%)':'Ray Task','rgb(83.920288%, 15.293884%, 15.686035%)':'Tide'}
TMC_ROLES=['Bare Metal','KubeEdge','KubeEdge-2C4C','KubeEdge-3C8C','Ray Task','Ray-Actor-15','Ray-Actor-20','Ray-Actor-25','Ray-Actor-40','Tide']
def paths(name):return [e for e in ET.parse(R/name).getroot().iter() if e.tag.endswith('path')]
def coords(e):return list(map(float,re.findall(NUMBER,e.get('d',''))))
def interp(points,x):
 xs=[p[0] for p in points];i=max(0,min(len(points)-2,bisect.bisect_right(xs,x)-1));a,b=points[i:i+2]
 return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
def world_y(e,y):
 v=list(map(float,re.findall(NUMBER,e.get('transform','matrix(1,0,0,1,0,0)'))));assert len(v)==6 and v[1]==v[2]==0
 return v[3]*y+v[5]
def world_coords(e):
 v=list(map(float,re.findall(NUMBER,e.get('transform','matrix(1,0,0,1,0,0)'))));assert len(v)==6 and v[1]==v[2]==0
 raw=coords(e)
 return [v[0]*n+v[4] if i%2==0 else v[3]*n+v[5] for i,n in enumerate(raw)]
def source():
 elements=paths('goodput-source-page71.svg');ideals={};curves={}
 for e in elements:
  v=coords(e)
  if e.get('fill')!='none' or e.get('stroke-width')!='1.5' or len(v)<8 or max(v[::2])-min(v[::2])<300:continue
  points=list(zip(v[::2],v[1::2]));qos=300 if v[0]<400 else 350
  if e.get('stroke-dasharray'):ideals[qos]=points
  elif e.get('stroke') in SOURCE_ROLES:curves[qos,SOURCE_ROLES[e.get('stroke')]]=points
 assert len(ideals)==2 and len(curves)==10
 rows=[]
 for (qos,role),points in curves.items():
  line=ideals[qos];scale=(line[-1][1]-line[0][1])/135;zero=line[0][1]-15*scale
  for j,rate in enumerate(range(5,51,5)):
   x=line[0][0]+(line[-1][0]-line[0][0])*j/9
   value=(interp(points,x)-zero)/scale
   rows.append(dict(platform=role,qos_ms=qos,rate_rps_per_client=rate,aggregate_ideal_rps=rate*3,goodput_rps_from_pdf_vector=value,goodput_over_aggregate_ideal=value/(rate*3),source_pdf_page=71,source_printed_page=55))
 return rows
def tmc():
 elements=paths('goodput-tmc.svg');bars=[];patterns=[];grids=[]
 for e in elements:
  v=world_coords(e)
  if len(v)<4:continue
  xs=v[::2];ys=v[1::2]
  if e.get('fill-opacity')=='0.9' and len(v)==10 and 14<max(xs)-min(xs)<17 and max(ys)>200:
   bars.append(dict(center=(min(xs)+max(xs))/2,bottom=max(ys),top=min(ys),element=e))
  if e.get('fill','').startswith('url(#pattern') and len(v) in [8,10] and 14<max(xs)-min(xs)<17:
   patterns.append(dict(center=(min(xs)+max(xs))/2,bottom=max(ys),top=min(ys)))
  if e.get('stroke')=='rgb(68.998718%, 68.998718%, 68.998718%)' and len(v)==4 and v[1]==v[3]:
   grids.append(dict(left=v[0],right=v[2],y=v[1]))
 bars.sort(key=lambda x:x['center']);assert len(bars)==50,len(bars)
 rows=[]
 for panel,rate in enumerate([10,20,30,40,50]):
  group=bars[10*panel:10*panel+10];base=group[0]['bottom'];center=group[0]['center']
  ys=[g['y'] for g in grids if g['left']<center<g['right'] and g['y']<base-.02]
  step=[10,20,20,25,50][panel];scale=(base-max(ys))/step
  assert all(abs((base-y)/scale/step-round((base-y)/scale/step))<.001 for y in ys)
  for role,b in zip(TMC_ROLES,group):
   match=[v for v in patterns if abs(v['center']-b['center'])<.02 and abs(v['bottom']-base)<.02];assert len(match)<=1
   good=(base-match[0]['top'])/scale if match else 0.
   rows.append(dict(platform=role,rate_rps_per_client=rate,throughput_rps_from_pdf_vector=(base-b['top'])/scale,goodput_rps_from_pdf_vector=good,pattern_patch_present=bool(match)))
 return rows
def savecsv(name,rows):
 with (R/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
 s=source();t=tmc();savecsv('figure_4_8_goodput_vector_values.csv',s);savecsv('tmc_goodput_vector_values.csv',t)
 comparisons=[]
 for row in t:
  if row['platform'] not in SOURCE_ROLES.values():continue
  other=next(x for x in s if x['platform']==row['platform'] and x['rate_rps_per_client']==row['rate_rps_per_client'] and x['qos_ms']==350)
  comparisons.append(dict(platform=row['platform'],rate_rps_per_client=row['rate_rps_per_client'],thesis350_goodput=other['goodput_rps_from_pdf_vector'],tmc_goodput=row['goodput_rps_from_pdf_vector'],absolute_rps_difference=abs(other['goodput_rps_from_pdf_vector']-row['goodput_rps_from_pdf_vector'])))
 report=dict(method='Native PDF->SVG path geometry, source zero/scale from the Ideal=3r curve; TMC zero from patch baseline and scale independently from neighboring grid-line tick spacing. No pixel digitization, fitting to desired outcomes or new experimental evidence.',source_rows=len(s),tmc_rows=len(t),shared_rows=len(comparisons),shared_rows_within_0_02_rps=sum(c['absolute_rps_difference']<=.02 for c in comparisons),maximum_rps_difference=max(c['absolute_rps_difference'] for c in comparisons),comparisons=comparisons,source_files_sha256={n:hashlib.sha256((R/n).read_bytes()).hexdigest() for n in ['goodput-source-page71.svg','goodput-tmc.svg']})
 (R/'goodput-cross-source-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in report.items() if k not in ['comparisons','source_files_sha256']},ensure_ascii=False))
 for role in SOURCE_ROLES.values():print(role,[(c['rate_rps_per_client'],round(c['thesis350_goodput'],3),round(c['tmc_goodput'],3)) for c in comparisons if c['platform']==role])
 tide=next(x for x in s if x['platform']=='Tide' and x['qos_ms']==350 and x['rate_rps_per_client']==50)
 print('Tide50',tide)
if __name__=='__main__':main()
