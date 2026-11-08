import sys, zlib, struct
for nom in sys.argv[1:]:
    d = open(nom, 'rb').read()
    entete, reste = d.split(b'\n', 1)
    _, w, h, _ = entete.split()
    w, h = int(w), int(h)
    lignes = b''.join(b'\0' + reste[y*w*3:(y+1)*w*3] for y in range(h))
    def bloc(t, x): return struct.pack('>I', len(x)) + t + x + struct.pack('>I', zlib.crc32(t + x) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + bloc(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + bloc(b'IDAT', zlib.compress(lignes, 9)) + bloc(b'IEND', b'')
    open(nom[:-4] + '.png', 'wb').write(png)
