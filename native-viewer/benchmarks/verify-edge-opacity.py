from pathlib import Path
import json,base64,sys
import numpy as np
from PIL import Image
e=Path(sys.argv[1])
def pixels(p):return np.array(Image.open(p).convert('RGB')).astype(np.int16)
native={v:pixels(e/f'native-edge-{v}.bmp') for v in (25,100,200)}
result={'nativeDefaultIdenticalToPrevious':bool(np.array_equal(native[100],pixels(e/'lod-carving-corrected.bmp'))),
 'nativeDifferenceFromDefault':{v:float(np.abs(a-native[100]).mean()) for v,a in native.items()}}
p=e/'webgpu-edge-opacity.json'
web=json.loads(p.read_text())
if 'error' in web:
 print(result);print(web);raise SystemExit(1)
images=[]
for i,c in enumerate(web['edgeOpacityCases']):
 path=e/f'webgpu-edge-{int(c["value"]*100)}-{i}.png'
 path.write_bytes(base64.b64decode(c['capture'].split(',')[1]));images.append(pixels(path))
result.update({'webErrors':web['errors'],'invalidEdgeOpacityRejected':web['invalidEdgeOpacityRejected'],
 'webDefaultResetIdentical':bool(np.array_equal(images[1],images[3])),
 'webDefaultResetMeanAbsoluteRgbError':float(np.abs(images[1]-images[3]).mean()),
 'webDifferenceFromDefault':{int(c['value']*100):float(np.abs(a-images[1]).mean()) for c,a in zip(web['edgeOpacityCases'],images)},
 'crossViewerMeanAbsoluteRgbError':{v:float(np.abs(native[v]-images[i]).mean()) for i,v in enumerate((25,100,200))},
 'residentGaussians':[c['snapshot']['resident']['gaussianCount'] for c in web['edgeOpacityCases']]})
(e/'edge-opacity-pixels.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))

assert result['nativeDefaultIdenticalToPrevious'], 'Native default reconstruction changed'
assert not result['webErrors'], result['webErrors']
assert result['webDefaultResetMeanAbsoluteRgbError'] < .01, 'WebGPU reset did not restore appearance'
assert len(set(result['residentGaussians'])) == 1, 'Resident cut changed during opacity adjustment'
for value in (25,200):
 assert result['nativeDifferenceFromDefault'][value] > 1, 'Native opacity uniform has no effect'
 assert result['webDifferenceFromDefault'][value] > 1, 'WebGPU opacity uniform has no effect'
for delta in result['crossViewerMeanAbsoluteRgbError'].values():
 assert delta < 3, 'Cross-viewer opacity mismatch on the qualified Saint-Etienne camera'
