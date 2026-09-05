// GSTile Q96: all 45 colour and 15 directional-opacity residuals retained.
struct Raw { uint data[24]; uint tile; };
struct Quant { float v[80]; };
struct Projected { float2 center; float2 axis1; float2 axis2; float4 color; float depth; uint source; };
cbuffer Frame : register(b0) {
  float4 eye; float4 cameraRight; float4 cameraUp; float4 cameraForward;
  float4 screen; // width, height, focal pixels, near distance
  uint count; uint groups; uint shift; uint shConfig; // color degree: bits 0-1, opacity: 2-3, enable: 4
  float4 pointer; // picking pixel xy
  float4 tone; // x: exposure, y: reciprocal edge opacity multiplier
};
StructuredBuffer<uint> activeIndices : register(t7);
StructuredBuffer<Raw> rawData : register(t0);
StructuredBuffer<Quant> quantData : register(t1);
StructuredBuffer<uint2> sortedIn : register(t2);
StructuredBuffer<Projected> projectedIn : register(t3);
StructuredBuffer<uint> histIn : register(t4);
StructuredBuffer<uint> prefixIn : register(t5);
StructuredBuffer<uint> totalsIn : register(t6);
RWStructuredBuffer<Projected> projected : register(u0);
RWStructuredBuffer<uint2> sortedOut : register(u1);
RWStructuredBuffer<uint> histogram : register(u2);
RWStructuredBuffer<uint> prefix : register(u3);
RWStructuredBuffer<uint> totals : register(u4);
RWStructuredBuffer<uint2> picked : register(u5);
uint read16(Raw r,uint offset) { return (r.data[offset/4]>>((offset%4)*8))&65535; }
int signed16(Raw r,uint offset) { return (int)(read16(r,offset)<<16)>>16; }
int signed8(Raw r,uint offset) { return (int)(((r.data[offset/4]>>((offset%4)*8))&255)<<24)>>24; }
float3 rotateQ(float3 v,float4 q) { return v+2*cross(q.yzw,cross(q.yzw,v)+q.x*v); }
void basis(float3 d,out float b[15]) {
  float x=d.x,y=d.y,z=d.z,xx=x*x,yy=y*y,zz=z*z;
  b[0]=-.4886025119029199*y;b[1]=.4886025119029199*z;b[2]=-.4886025119029199*x;
  b[3]=1.0925484305920792*x*y;b[4]=-1.0925484305920792*y*z;
  b[5]=.31539156525252005*(2*zz-xx-yy);b[6]=-1.0925484305920792*x*z;b[7]=.5462742152960396*(xx-yy);
  b[8]=-.5900435899266435*y*(3*xx-yy);b[9]=2.890611442640554*x*y*z;
  b[10]=-.4570457994644658*y*(4*zz-xx-yy);b[11]=.3731763325901154*z*(2*zz-3*xx-3*yy);
  b[12]=-.4570457994644658*x*(4*zz-xx-yy);b[13]=1.445305721320277*z*(xx-yy);b[14]=-.5900435899266435*x*(xx-3*yy);
}
#if COMPUTE_STAGE
[numthreads(256,1,1)]
void Project(uint id:SV_DispatchThreadID) {
  if(id>=count)return;
  Raw r=rawData[activeIndices[id]];Quant q=quantData[r.tile];
  float3 p;
  [unroll]for(uint i=0;i<3;i++)p[i]=q.v[i]+read16(r,2*i)*q.v[3+i];
  float3 delta=p-eye.xyz;
  float3 v=float3(dot(delta,cameraRight.xyz),dot(delta,cameraUp.xyz),dot(delta,cameraForward.xyz));
  Projected o=(Projected)0;o.source=id;sortedOut[id]=uint2(0xffffffff,id);
  if(v.z<=screen.w){projected[id]=o;return;}
  float3 scale;
  [unroll]for(uint s=0;s<3;s++)scale[s]=exp(q.v[6+s]+read16(r,6+2*s)*q.v[9+s]);
  float4 rotation=float4(signed16(r,12),signed16(r,14),signed16(r,16),signed16(r,18));
  rotation=normalize(rotation);
  // Jacobian in world coordinates; covariance = J R S^2 R^T J^T.
  float focal=screen.z;
  float3 jx=(cameraRight.xyz-cameraForward.xyz*clamp(v.x/v.z,-2.0*screen.x/screen.y,2.0*screen.x/screen.y))*(focal/v.z);
  float3 jy=(cameraUp.xyz-cameraForward.xyz*clamp(v.y/v.z,-2.0,2.0))*(focal/v.z);
  float3 a=rotateQ(float3(scale.x,0,0),rotation);
  float3 b=rotateQ(float3(0,scale.y,0),rotation);
  float3 c=rotateQ(float3(0,0,scale.z),rotation);
  float3 dx=float3(dot(jx,a),dot(jx,b),dot(jx,c)),dy=float3(dot(jy,a),dot(jy,b),dot(jy,c));
  float xx=dot(dx,dx)+.3,xy=dot(dx,dy),yy=dot(dy,dy)+.3;
  float mid=.5*(xx+yy),rad=length(float2(.5*(xx-yy),xy));
  float l1=max(mid+rad,.3),l2=max(mid-rad,.3);
  float2 axis=abs(xy)>1e-8?normalize(float2(xy,l1-xx)):(xx>=yy?float2(1,0):float2(0,1));
  o.center=float2(screen.x*.5+focal*v.x/v.z,screen.y*.5-focal*v.y/v.z);
  // 3 sigma support, rasterized as an oriented ellipse.
  o.axis1=float2(axis.x,-axis.y)*(3*sqrt(l1));
  o.axis2=float2(-axis.y,-axis.x)*(3*sqrt(l2));
  float2 extent=abs(o.axis1)+abs(o.axis2);
  if(any(o.center+extent<0)||any(o.center-extent>screen.xy)){projected[id]=(Projected)0;return;}
  uint colorDegree=shConfig&3,opacityDegree=(shConfig>>2)&3;
  uint colorTerms=(colorDegree+1)*(colorDegree+1)-1;
  uint opacityTerms=(shConfig&16)?(opacityDegree+1)*(opacityDegree+1)-1:0;
  float sh[15];basis(normalize(delta),sh);
  float3 rgb=.5+.28209479177387814*float3(signed16(r,22)*q.v[14],signed16(r,24)*q.v[15],signed16(r,26)*q.v[16]);
  float logit=q.v[12]+read16(r,20)*q.v[13];
  [unroll]for(uint k=0;k<15;k++){
    [branch]if(k<colorTerms)rgb+=sh[k]*float3(signed8(r,28+k)*q.v[17+k],signed8(r,43+k)*q.v[32+k],signed8(r,58+k)*q.v[47+k]);
    [branch]if(k<opacityTerms)logit+=sh[k]*signed8(r,73+k)*q.v[62+k];
  }
  o.color=float4(max(rgb,0)*tone.x,min(.999,1/(1+exp(-clamp(logit,-80.,80.)))));
  if(o.color.a<1./255){projected[id]=(Projected)0;return;}
  o.depth=v.z;projected[id]=o;sortedOut[id]=uint2(~asuint(v.z),id);
}
groupshared uint bins[16];
[numthreads(256,1,1)]
void Histogram(uint id:SV_DispatchThreadID,uint lane:SV_GroupIndex,uint3 group:SV_GroupID) {
  if(lane<16)bins[lane]=0;GroupMemoryBarrierWithGroupSync();
  if(id<count)InterlockedAdd(bins[(sortedIn[id].x>>shift)&15],1);
  GroupMemoryBarrierWithGroupSync();
  if(lane<16)histogram[lane*groups+group.x]=bins[lane];
}
groupshared uint sums[256];
[numthreads(256,1,1)]
void Prefix(uint lane:SV_GroupIndex,uint3 group:SV_GroupID) {
  uint bucket=group.x,chunk=(groups+255)/256,begin=lane*chunk,end=min(groups,begin+chunk);
  uint sum=0;for(uint i=begin;i<end;i++)sum+=histIn[bucket*groups+i];
  sums[lane]=sum;GroupMemoryBarrierWithGroupSync();
  [unroll]for(uint stride=1;stride<256;stride*=2) {
    uint value=lane>=stride?sums[lane-stride]:0;GroupMemoryBarrierWithGroupSync();
    sums[lane]+=value;GroupMemoryBarrierWithGroupSync();
  }
  uint running=lane==0?0:sums[lane-1];
  for(uint j=begin;j<end;j++){prefix[bucket*groups+j]=running;running+=histIn[bucket*groups+j];}
  if(lane==255)totals[bucket]=sums[255];
}
groupshared uint masks[128];
[numthreads(256,1,1)]
void Scatter(uint id:SV_DispatchThreadID,uint lane:SV_GroupIndex,uint3 group:SV_GroupID) {
  if(lane<128)masks[lane]=0;GroupMemoryBarrierWithGroupSync();
  uint2 item=id<count?sortedIn[id]:uint2(0xffffffff,0);uint digit=(item.x>>shift)&15;
  if(id<count)InterlockedOr(masks[digit*8+lane/32],1u<<(lane%32));
  GroupMemoryBarrierWithGroupSync();
  if(id>=count)return;
  uint rank=0;
  [unroll]for(uint word=0;word<8;word++) {
    uint bits=masks[digit*8+word];
    if(word>lane/32)bits=0;
    if(word==lane/32)bits&=(1u<<(lane%32))-1;
    rank+=countbits(bits);
  }
  uint base=0;[unroll]for(uint d=0;d<16;d++)if(d<digit)base+=totalsIn[d];
  sortedOut[base+prefixIn[digit*groups+group.x]+rank]=item;
}
#endif
struct Vertex { float4 position:SV_Position; float2 uv:TEXCOORD0; nointerpolation float4 color:COLOR0; };
Vertex VS(uint vertex:SV_VertexID,uint instance:SV_InstanceID) {
  const float2 corners[6]={float2(-1,-1),float2(1,-1),float2(1,1),float2(-1,-1),float2(1,1),float2(-1,1)};
  Projected p=projectedIn[sortedIn[instance].y];Vertex o;
  o.uv=corners[vertex];o.color=p.color;
  float2 pixel=p.center+p.axis1*o.uv.x+p.axis2*o.uv.y;
  o.position=float4(pixel/screen.xy*float2(2,-2)+float2(-1,1),0,1);
  if(p.color.a==0)o.position=float4(2,2,2,1);
  return o;
}
float4 PS(Vertex i):SV_Target {
  float power=-4.5*dot(i.uv,i.uv);if(power<-4.5)discard;
  float alpha=i.color.a*exp(power*tone.y);if(alpha<1./255)discard;
  return float4(i.color.rgb*alpha,alpha);
}
#if COMPUTE_STAGE
groupshared uint2 pickValues[256];
[numthreads(256,1,1)]
void Pick(uint id:SV_DispatchThreadID,uint lane:SV_GroupIndex,uint3 group:SV_GroupID) {
  uint2 best=uint2(0xffffffff,0xffffffff);
  if(id<count){
    Projected p=projectedIn[id];float2 d=pointer.xy-p.center;
    float det=p.axis1.x*p.axis2.y-p.axis1.y*p.axis2.x;
    if(abs(det)>1e-12&&p.color.a>0){
      float2 uv=float2(d.x*p.axis2.y-d.y*p.axis2.x,p.axis1.x*d.y-p.axis1.y*d.x)/det;
      float alpha=p.color.a*exp(-4.5*dot(uv,uv)*tone.y);
      if(dot(uv,uv)<=1&&alpha>=.1)best=uint2(asuint(p.depth),id);
    }
  }
  pickValues[lane]=best;GroupMemoryBarrierWithGroupSync();
  for(uint stride=128;stride>0;stride/=2){
    if(lane<stride&&pickValues[lane+stride].x<pickValues[lane].x)pickValues[lane]=pickValues[lane+stride];
    GroupMemoryBarrierWithGroupSync();
  }
  if(lane==0)picked[group.x]=pickValues[0];
}

#endif
