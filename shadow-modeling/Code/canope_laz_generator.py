import numpy as np
import laspy
from scipy.spatial import cKDTree
from scipy.stats import binned_statistic_2d
from skimage.feature import peak_local_max
from skimage.segmentation import watershed

def capone_laz_generate(laz_file):

    las = laspy.read(laz_file)

    x = np.asarray(las.x)
    y = np.asarray(las.y)
    z = np.asarray(las.z)
    cls = las.classification

    resolution = 1.0

    ground = cls == 2

    x_ground = x[ground]
    y_ground = y[ground]
    z_ground = z[ground]

    trees = np.isin(cls, [3, 4, 5, 8])

    x_tree = x[trees]
    y_tree = y[trees]
    z_tree = z[trees]

    xmin = x.min()
    xmax = x.max()

    ymin = y.min()
    ymax = y.max()

    bins_x = np.arange(
        xmin,
        xmax + resolution,
        resolution
    )

    bins_y = np.arange(
        ymin,
        ymax + resolution,
        resolution
    )

    dtm, _, _, _ = binned_statistic_2d(
        x_ground,
        y_ground,
        z_ground,
        statistic="min",
        bins=[bins_x, bins_y]
    )

    dtm = dtm.T

    dtm = np.nan_to_num(
        dtm,
        nan=np.nanmedian(dtm)
    )

    dsm, _, _, _ = binned_statistic_2d(
        x_tree,
        y_tree,
        z_tree,
        statistic="max",
        bins=[bins_x, bins_y]
    )

    dsm = dsm.T
    dsm = np.nan_to_num(dsm, nan=0)

    chm = dsm - dtm
    chm[chm < 0] = 0

    tops = peak_local_max(
        chm,
        min_distance=8,
        threshold_abs=2
    )

    markers = np.zeros(
        chm.shape,
        dtype=np.int32
    )

    for i, (r, c) in enumerate(tops):
        markers[r, c] = i + 1

    labels = watershed(
        -chm,
        markers,
        mask=chm > 0
    )

    col = ((x_tree - xmin) / resolution).astype(int)
    row = ((y_tree - ymin) / resolution).astype(int)

    row = np.clip(
        row,
        0,
        labels.shape[0] - 1
    )

    col = np.clip(
        col,
        0,
        labels.shape[1] - 1
    )

    tree_id = labels[row, col]

    tree_ground = cKDTree(
        np.column_stack((x_ground, y_ground))
    )

    _, idx = tree_ground.query(
        np.column_stack((x_tree, y_tree))
    )

    ground_z = z_ground[idx]

    height = z_tree - ground_z

    keep = np.zeros(
        len(z_tree),
        dtype=bool
    )

    for tree in np.unique(tree_id):

        if tree == 0:
            continue

        pts = tree_id == tree

        hmax = height[pts].max()

        seuil = 0.70 * hmax

        keep[pts] = height[pts] >= seuil

    x_keep = x_tree[keep]
    y_keep = y_tree[keep]
    z_keep = z_tree[keep]

    dsm_canope_top, _, _, _ = binned_statistic_2d(
        x_keep,
        y_keep,
        z_keep,
        statistic="max",
        bins=[bins_x, bins_y]
    )

    dsm_canope_top = dsm_canope_top.T

    dsm_canope_top = np.nan_to_num(
        dsm_canope_top,
        nan=-np.inf
    )

    dsm_canope_bottom, _, _, _ = binned_statistic_2d(
        x_keep,
        y_keep,
        z_keep,
        statistic="min",
        bins=[bins_x, bins_y]
    )

    dsm_canope_bottom = dsm_canope_bottom.T

    dsm_canope_bottom = np.nan_to_num(
        dsm_canope_bottom,
        nan=np.inf
    )

    new_las = laspy.LasData(las.header)

    new_las.points = las.points[trees][keep]

    output_file = "Data/canopee.laz"

    new_las.write(output_file)

    return (
        output_file,
        dsm_canope_top,
        dsm_canope_bottom,
        chm,
        dtm
    )