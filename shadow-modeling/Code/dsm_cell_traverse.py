import numpy as np

from shadow_calculation import *

def build_dsm(points, resolution=1.0):
    print("begin building DSM")
    xmin, ymin = points[:, 0].min(), points[:, 1].min()
    xmax, ymax = points[:, 0].max(), points[:, 1].max()
    nx = int((xmax - xmin) / resolution) + 1
    ny = int((ymax - ymin) / resolution) + 1

    DSM = np.full((ny, nx), -np.inf)

    for x, y, z, _ in points:
        ix = int((x - xmin) / resolution)
        iy = int((y - ymin) / resolution)

        DSM[iy, ix] = max(DSM[iy, ix], z)

    return DSM, xmin, ymin, resolution

def dsm_to_cells(DSM):
    cells = []

    for iy in range(DSM.shape[0]):
        for ix in range(DSM.shape[1]):

            z = DSM[iy, ix]

            if z > -np.inf:
                cells.append([ix, iy, z])

    return np.asarray(cells)

class BVHNode:
    def __init__(self, cells, leaf_size=512):
        self.left = None
        self.right = None
        self.cells = None

        xy = cells[:, :2]

        self.min = xy.min(axis=0)
        self.max = xy.max(axis=0)

        if len(cells) <= leaf_size:
            self.cells = cells
            return

        extent = self.max - self.min
        axis = np.argmax(extent)

        order = np.argsort(cells[:, axis])
        cells = cells[order]

        mid = len(cells) // 2

        self.left = BVHNode(cells[:mid], leaf_size)
        self.right = BVHNode(cells[mid:], leaf_size)

def traverse(node, alpha, psi, DSM, shadow):
    if node is None:
        return

    if node.cells is not None:

        for ix, iy, z in node.cells:

            process_cell(ix, iy, z, alpha, psi, DSM, shadow)

        return

    traverse(node.left, alpha, psi, DSM, shadow)
    traverse(node.right, alpha, psi, DSM, shadow)

def traverse_vegetation(node, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm):
    if node is None:
        return

    if node.cells is not None:

        for ix, iy, z in node.cells:

            process_cell_vegetation(ix, iy, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm)

        return

    traverse_vegetation(node.left, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm)
    traverse_vegetation(node.right, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, dtm)
