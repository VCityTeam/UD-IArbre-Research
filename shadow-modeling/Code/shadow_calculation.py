import math
import numpy as np

def bresenham(x_cell, y_cell, x_project_end, y_project_end):
    x_cell, y_cell = int(round(x_cell)), int(round(y_cell))
    x_project_end, y_project_end = int(round(x_project_end)), int(round(y_project_end))

    points = []

    dx = abs(x_project_end - x_cell)
    dy = abs(y_project_end - y_cell)

    sx = 1 if x_cell < x_project_end else -1
    sy = 1 if y_cell < y_project_end else -1

    err = dx - dy

    while True:
        points.append((x_cell, y_cell))

        if x_cell == x_project_end and y_cell == y_project_end:
            break

        e2 = 2 * err

        if e2 > -dy:
            err -= dy
            x_cell += sx

        if e2 < dx:
            err += dx
            y_cell += sy

    return points

# Def process_cell : The function takes various parameters and tests whether a point is in shadow; if so, it adds the point to the 'shadow' list.
# Parameters : ix (x position of the cell), iy (y position of the cell), z (z coordinate of the cell), alpha (solar elevation), psi (solar azimuth), DSM, shadow list
def process_cell(ix, iy, z, alpha, psi, DSM,  shadow):
    if alpha == 0:
        return

    theta = psi - math.pi

    D = min(z / math.tan(alpha), 2 * max(DSM.shape))

    dx = -D * math.sin(theta)
    dy = -D * math.cos(theta)

    x_top = ix - dx
    y_top = iy - dy

    line = bresenham(ix, iy, x_top, y_top)

    ix = int(ix)
    iy = int(iy)
    z_source = DSM[iy, ix]

    for x, y in line[1:]:

        if (
                x < 0 or x >= DSM.shape[1] or
                y < 0 or y >= DSM.shape[0]
        ):
            continue

        z_cell = DSM[y, x]

        if z_cell == -np.inf:
            continue

        d = np.hypot(x - ix, y - iy)
        beta = math.atan((z_source - z_cell) / d)

        if beta >= alpha:
            shadow.append((x, y, z_cell))

# Def process_cell_vegetation : (vegetation) The function takes various parameters and tests whether a point is in shadow; if so, it adds the point to the 'shadow_vegetation' list
# Parameters : ix (x position of the cell), iy (y position of the cell), alpha (solar elevation), psi (solar azimuth), DSM, shadow_vegetation list, DSM CANOPE TOP, DSM CANOPE BOTTOM, DTM
def process_cell_vegetation(ix, iy, alpha, psi, DSM, shadow_vegetation, dsm_canope_top, dsm_canope_bottom, DTM):

    if alpha <= 0:
        return

    ix = int(ix)
    iy = int(iy)

    z_source = dsm_canope_top[iy, ix]

    if z_source == -np.inf:
        return

    h = z_source - DTM[iy, ix]

    if h <= 0:
        return

    theta = psi - math.pi

    D = min(
        h / math.tan(alpha),
        2 * max(DSM.shape)
    )

    dx = -D * math.sin(theta)
    dy = -D * math.cos(theta)

    x_end = ix - dx
    y_end = iy - dy

    line = bresenham(ix, iy, x_end, y_end)

    ombre = False

    for x, y in line[1:]:

        if (
                x < 0 or x >= DSM.shape[1] or
                y < 0 or y >= DSM.shape[0]
        ):
            continue

        d = np.hypot(x - ix, y - iy)

        z_ray = z_source - d * math.tan(alpha)

        z_top = dsm_canope_top[y, x]
        z_bottom = dsm_canope_bottom[y, x]
        z_sol = DTM[y, x]

        if z_top != -np.inf:

            if z_bottom <= z_ray <= z_top:
                ombre = True

        if ombre and z_sol < z_ray < z_bottom:
            shadow_vegetation.append((x, y, z_sol))
