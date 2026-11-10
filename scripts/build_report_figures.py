"""Generate editable diagrams and plots from saved cloud evidence."""
from pathlib import Path
import json,base64,xml.etree.ElementTree as ET
from datetime import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
from matplotlib.offsetbox import OffsetImage,AnnotationBbox
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'docs/figures';OUT.mkdir(exist_ok=True)
ICONS=ROOT/'docs/assets/aws-icons'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'svg.fonttype':'none'})

def diagram(name,title,nodes,edges,notes):
 fig,ax=plt.subplots(figsize=(10,6.5));fig.subplots_adjust(left=.025,right=.975,top=.90,bottom=.09)
 ax.set_xlim(0,100);ax.set_ylim(0,65);ax.axis('off');fig.suptitle(title,fontsize=17,fontweight='bold')
 xml=ET.Element('mxfile',host='app.diagrams.net'); dia=ET.SubElement(xml,'diagram',name=title);model=ET.SubElement(dia,'mxGraphModel',pageWidth='1100',pageHeight='750');root=ET.SubElement(model,'root');ET.SubElement(root,'mxCell',id='0');ET.SubElement(root,'mxCell',id='1',parent='0')
 for key,(x,y,text,icon) in nodes.items():
  ax.add_patch(FancyBboxPatch((x-12,y-5),24,10,boxstyle='round,pad=.4',facecolor='#f1f5f9',edgecolor='#475569',linewidth=1.3,zorder=2))
  dx=0
  if icon and (ICONS/(icon+'.png')).exists():
   im=plt.imread(ICONS/(icon+'.png'));ax.add_artist(AnnotationBbox(OffsetImage(im,zoom=.4),(x-9,y),frameon=False,zorder=4));dx=2
  ax.text(x+dx,y,text,ha='center',va='center',fontsize=12,zorder=4)
  cell=ET.SubElement(root,'mxCell',id=key,value=text,style='rounded=1;whiteSpace=wrap;html=0;fontSize=16;fillColor=#f1f5f9;',vertex='1',parent='1');ET.SubElement(cell,'mxGeometry',x=str((x-12)*10),y=str((65-y-5)*10),width='240',height='100',**{'as':'geometry'})
  if icon and (ICONS/(icon+'.png')).exists():
   encoded=base64.b64encode((ICONS/(icon+'.png')).read_bytes()).decode()
   cell=ET.SubElement(root,'mxCell',id=key+'-icon',style='shape=image;image=data:image/png,'+encoded+';',vertex='1',parent='1');ET.SubElement(cell,'mxGeometry',x=str((x-11)*10),y=str((65-y-2.5)*10),width='45',height='45',**{'as':'geometry'})
 for i,(a,b,label,curve) in enumerate(edges):
  xa,ya=nodes[a][:2];xb,yb=nodes[b][:2]
  if abs(xb-xa)>abs(yb-ya):start=(xa+(12.5 if xb>xa else -12.5),ya);end=(xb+(-12.5 if xb>xa else 12.5),yb)
  else:start=(xa,ya+(5.5 if yb>ya else -5.5));end=(xb,yb+(-5.5 if yb>ya else 5.5))
  arrow=FancyArrowPatch(start,end,connectionstyle=f'arc3,rad={curve}',arrowstyle='-|>',mutation_scale=13,color='#334155',linewidth=1.25,zorder=1);ax.add_patch(arrow)
  if label:ax.text((start[0]+end[0])/2,(start[1]+end[1])/2+.8,label,fontsize=11,ha='center',va='bottom',bbox={'facecolor':'white','edgecolor':'none','pad':1},zorder=5)
  c=ET.SubElement(root,'mxCell',id='edge'+str(i),source=a,target=b,value=label,style='edgeStyle=orthogonalEdgeStyle;rounded=0;html=0;fontSize=14;endArrow=block;',edge='1',parent='1');ET.SubElement(c,'mxGeometry',relative='1',**{'as':'geometry'})
 fig.text(.04,.025,notes,fontsize=11,color='#334155')
 for ext in ['png','svg','pdf']:fig.savefig(OUT/(name+'.'+ext),dpi=180)
 plt.close(fig);ET.ElementTree(xml).write(OUT/(name+'.drawio'),encoding='utf-8',xml_declaration=True)

def main():
 n={'user':(15,57,'Browser\nclient',None),'id':(50,57,'Identity\nprovider',None),'cache':(85,57,'Process-local\nlist cache',None),'lb':(15,36,'HTTPS load\nbalancer',None),'api':(50,36,'Application\ncontainers',None),'db':(85,36,'Metadata\nstore',None),'q':(15,13,'Durable queue\nand DLQ',None),'worker':(50,13,'Pull worker\ncontainers',None),'objects':(85,13,'Private object\nstore',None)}
 diagram('vendor-agnostic','Stillday: vendor-independent request and processing design',n,[('user','id','login',0),('user','lb','HTTPS',0),('lb','api','requests',0),('api','cache','list reads',0),('api','db','state',0),('api','q','enqueue',0),('worker','q','pull / ack',0),('worker','db','commit',0),('worker','objects','read / write',0)],'Direct signed object transfers: browser to object store. API validates and snapshots uploads.\nQueue metrics control worker capacity; API CPU controls API capacity. Both emit operational logs.')
 n={'user':(15,57,'Browser\nHTTPS client',None),'id':(50,57,'Cognito\naccess tokens','Amazon-Cognito'),'cache':(85,57,'In-process cache\n5 s / 8 MiB',None),'lb':(15,36,'ALB\nACM TLS','Elastic-Load-Balancing'),'api':(50,36,'Fargate API\n1-2 tasks','Amazon-Elastic-Container-Service'),'db':(85,36,'DynamoDB\nfamily records','Amazon-DynamoDB'),'q':(15,13,'SQS queue\nand DLQ','Amazon-Simple-Queue-Service'),'worker':(50,13,'Fargate worker\n0-4 tasks','Amazon-Elastic-Container-Service'),'objects':(85,13,'Private S3\nimages','Amazon-Simple-Storage-Service')}
 diagram('aws-data-plane','Stillday: deployed AWS data plane (ap-southeast-2)',n,[('user','id','login',0),('user','lb','HTTPS',0),('lb','api','port 8080',0),('api','cache','photo lists',0),('api','db','state',0),('api','q','job',0),('worker','q','long poll',0),('worker','db','lease / commit',0),('worker','objects','images',0)],'Tasks use public subnets and shared CAB432SG; ALB-only task isolation is not enforced.\nBrowser transfers images directly using signed S3 requests. No CloudFront or verified AI path.')
 n={'dns':(15,54,'Route 53\nA3 hostname','Amazon-Route-53'),'cert':(50,54,'ACM\ncertificate','AWS-Certificate-Manager'),'ecr':(85,54,'ECR\nshared image','Amazon-Elastic-Container-Registry'),'alb':(15,32,'ALB\nHTTPS listener','Elastic-Load-Balancing'),'api':(50,32,'Fargate API\nCPU policy','Amazon-Elastic-Container-Service'),'worker':(85,32,'Fargate worker\nqueue policy','Amazon-Elastic-Container-Service'),'logs':(15,10,'CloudWatch\nlogs / dashboard','Amazon-CloudWatch'),'scale':(50,10,'Application\nAuto Scaling','AWS-Auto-Scaling'),'metrics':(85,10,'CloudWatch\nqueue alarms','Amazon-CloudWatch')}
 diagram('aws-operations','Stillday: deployment, observability and capacity control',n,[('dns','alb','alias',0),('cert','alb','TLS',0),('ecr','api','image',0),('ecr','worker','image',0),('api','logs','logs',0),('metrics','scale','alarm actions',0),('scale','api','1-2 tasks',0),('scale','worker','0-4 tasks',0)],'API: 0.25 vCPU / 0.5 GiB. Worker: 1 vCPU / 2 GiB. Shared course IAM roles.\nWorker logs also go to CloudWatch. Log expiry is not enabled under current course permissions.')
 d=json.loads((ROOT/'evidence/cloud-batch-scaling.json').read_text());t0=datetime.fromisoformat(d['startedAt']);samples=d['samples'];times=[(datetime.fromisoformat(x['time'])-t0).total_seconds()/60 for x in samples]
 fig,axes=plt.subplots(3,1,figsize=(9.8,6.6),sharex=True,layout='constrained')
 axes[0].step(times,[int(x['queue']['ApproximateNumberOfMessages']) for x in samples],where='post',label='Visible (approx.)');axes[0].plot(times,[x.get('statuses',{}).get('READY',0) for x in samples],label='READY photos');axes[0].set_ylabel('Photos / jobs');axes[0].legend(loc='center left',bbox_to_anchor=(1,0.5),fontsize=11)
 for field in ['desired','running']:axes[1].step(times,[x[field] for x in samples],where='post',label=field)
 axes[1].set_ylabel('Worker tasks');axes[1].set_yticks([0,1,2]);axes[1].legend(loc='center left',bbox_to_anchor=(1,0.5),fontsize=11)
 axes[2].step(times,[int(x['queue']['ApproximateNumberOfMessagesNotVisible']) for x in samples],where='post',color='#a16207');axes[2].set_ylabel('In flight\n(approx.)');axes[2].set_xlabel('Minutes since 2026-10-22 08:49:22 UTC')
 for ax in axes:ax.grid(alpha=.25)
 fig.suptitle('48-image batch: observed backlog, completion and worker capacity',fontsize=15)
 for ext in ['png','svg','pdf']:fig.savefig(OUT/('scaling-results.'+ext),dpi=180)
 plt.close(fig)
 print('Generated 3 editable architecture diagrams and measured scaling figure.')
if __name__=='__main__':main()
