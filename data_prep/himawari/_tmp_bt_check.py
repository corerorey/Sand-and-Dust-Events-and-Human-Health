from pathlib import Path
import struct
import numpy as np

files=[
Path(r"C:\DOCUMENTO\himawari\HS_H08_20210316_0400_B11_FLDK_R20_S0110.DAT"),
Path(r"C:\DOCUMENTO\himawari\HS_H08_20210316_0400_B13_FLDK_R20_S0110.DAT"),
Path(r"C:\DOCUMENTO\himawari\HS_H08_20210316_0400_B15_FLDK_R20_S0110.DAT"),
]

def blocks(blob):
    n=blob[3]; off=0; out={}
    for _ in range(n):
        b=blob[off]; L=struct.unpack_from('<H',blob,off+1)[0]
        out[b]=blob[off:off+L]; off+=L
    return out,off

for p in files:
    blob=p.read_bytes(); blks,off=blocks(blob)
    b5=blks[5]
    band=struct.unpack_from('<H',b5,3)[0]
    wl=struct.unpack_from('<d',b5,5)[0]
    slope=struct.unpack_from('<d',b5,13)[0]
    intercept=struct.unpack_from('<d',b5,21)[0]
    c0=struct.unpack_from('<d',b5,29)[0]
    c1=struct.unpack_from('<d',b5,37)[0]
    c2=struct.unpack_from('<d',b5,45)[0]
    C0=struct.unpack_from('<d',b5,53)[0]
    C1=struct.unpack_from('<d',b5,61)[0]
    C2=struct.unpack_from('<d',b5,69)[0]
    c=struct.unpack_from('<d',b5,77)[0]
    h=struct.unpack_from('<d',b5,85)[0]
    k=struct.unpack_from('<d',b5,93)[0]
    # image sample
    arr=np.frombuffer(blob,dtype='<u2',offset=off,count=5500*550)
    valid=arr[(arr<65000)]
    radiance=slope*valid + intercept
    # Planck from doc page 13
    Te=(h*c)/(k*(wl*1e-6)) / np.log((2*h*c*c)/(radiance*1e6*((wl*1e-6)**5))+1.0)
    Tb=c0 + c1*Te + c2*(Te**2)
    print('\n',p.name)
    print('band,wl',band,wl)
    print('slope,intercept',slope,intercept)
    print('c0,c1,c2',c0,c1,c2)
    print('C0,C1,C2',C0,C1,C2)
    print('constants c,h,k',c,h,k)
    print('DN p1/50/99',np.percentile(valid,[1,50,99]))
    print('BT p1/50/99',np.percentile(Tb,[1,50,99]))
