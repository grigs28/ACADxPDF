import ezdxf

doc = ezdxf.readfile('C:/opt/ACADxPDF/output/1.dxf')
msp = doc.modelspace()

# Get tk block bounding box
print('=== "tk" block analysis ===')
tk_block = doc.blocks.get('tk')
if tk_block:
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    for e in tk_block:
        try:
            for p in e.virtual_entities():
                try:
                    bb = list(p.points())
                    for pt in bb:
                        if hasattr(pt, 'x'):
                            min_x = min(min_x, pt.x)
                            max_x = max(max_x, pt.x)
                            min_y = min(min_y, pt.y)
                            max_y = max(max_y, pt.y)
                except:
                    pass
        except:
            pass

    # Also try direct entity points
    for e in tk_block:
        try:
            if e.dxftype() == 'LINE':
                min_x = min(min_x, e.dxf.start.x, e.dxf.end.x)
                max_x = max(max_x, e.dxf.start.x, e.dxf.end.x)
                min_y = min(min_y, e.dxf.start.y, e.dxf.end.y)
                max_y = max(max_y, e.dxf.start.y, e.dxf.end.y)
            elif e.dxftype() == 'LWPOLYLINE':
                for pt in e.get_points(format='xy'):
                    min_x = min(min_x, pt[0])
                    max_x = max(max_x, pt[0])
                    min_y = min(miny, pt[1]) if 'miny' in dir() else min(min_y, pt[1])
                    max_y = max(max_y, pt[1])
                    min_y = min(min_y, pt[1])
        except Exception as ex:
            pass

    w = max_x - min_x
    h = max_y - min_y
    print(f'  Block bbox: ({min_x:.0f},{min_y:.0f})-({max_x:.0f},{max_y:.0f})')
    print(f'  Width={w:.0f} Height={h:.0f}')
    print(f'  Paper size: h/100 = {h/100:.0f}mm, w/100 = {w/100:.0f}mm')

# Get all INSERT bounding boxes
print('\n=== All INSERT positions ===')
for ins in msp.query('INSERT'):
    name = ins.dxf.name
    ix = ins.dxf.insert.x
    iy = ins.dxf.insert.y
    xs = ins.dxf.get('xscale', 1)
    ys = ins.dxf.get('yscale', 1)
    block = doc.blocks.get(name)
    if block:
        bx0 = by0 = float('inf')
        bx1 = by1 = float('-inf')
        for e in block:
            try:
                if e.dxftype() == 'LINE':
                    bx0 = min(bx0, e.dxf.start.x, e.dxf.end.x)
                    bx1 = max(bx1, e.dxf.start.x, e.dxf.end.x)
                    by0 = min(by0, e.dxf.start.y, e.dxf.end.y)
                    by1 = max(by1, e.dxf.start.y, e.dxf.end.y)
                elif e.dxftype() == 'LWPOLYLINE':
                    for pt in e.get_points(format='xy'):
                        bx0 = min(bx0, pt[0])
                        bx1 = max(bx1, pt[0])
                        by0 = min(by0, pt[1])
                        by1 = max(by1, pt[1])
            except:
                pass
        if bx0 < float('inf'):
            bw = (bx1 - bx0) * abs(xs)
            bh = (by1 - by0) * abs(ys)
            print(f'  {name}: insert({ix:.0f},{iy:.0f}) block_size=({bw:.0f},{bh:.0f}) -> world_size=({bw:.0f}x{bh:.0f})')
            if bh > 50000:  # likely a border
                print(f'    ** BORDER CANDIDATE: h/100={bh/100:.0f}mm w/100={bw/100:.0f}mm')
        else:
            print(f'  {name}: insert({ix:.0f},{iy:.0f}) [no bbox]')

# Extents
extmin = doc.header.get('$EXTMIN', (0,0,0))
extmax = doc.header.get('$EXTMAX', (0,0,0))
print(f'\n=== Drawing extents ===')
print(f'  MIN: {extmin}')
print(f'  MAX: {extmax}')
if hasattr(extmin, '__getitem__'):
    dw = extmax[0] - extmin[0]
    dh = extmax[1] - extmin[1]
    print(f'  Size: {dw:.0f} x {dh:.0f}')
