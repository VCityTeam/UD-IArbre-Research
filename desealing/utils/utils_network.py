import numpy as np
from rasterio.features import shapes
from shapely.geometry import LineString, Point
from rasterio import transform


def compute_flow_direction(dem):
    """Compute flow direction using only 8 directions
    Args:
        - dem (np.array): Raster DEM
    """
    rows, cols = dem.shape
    flow_dir = np.zeros((rows, cols), dtype=int)

    # 8 directions: 1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE
    directions = [
        (0, 1, 1),
        (1, 1, 2),
        (1, 0, 4),
        (1, -1, 8),
        (0, -1, 16),
        (-1, -1, 32),
        (-1, 0, 64),
        (-1, 1, 128),
    ]

    for i in range(1, rows - 1):
        for j in range(1, cols - 1):
            if np.isnan(dem[i, j]):
                continue

            max_slope = 0
            steepest_dir = 0

            for di, dj, dir_code in directions:
                ni, nj = i + di, j + dj
                if 0 <= ni < rows and 0 <= nj < cols and not np.isnan(dem[ni, nj]):
                    slope_val = (dem[i, j] - dem[ni, nj]) / np.sqrt(di * di + dj * dj)
                    if slope_val > max_slope:
                        max_slope = slope_val
                        steepest_dir = dir_code

            flow_dir[i, j] = steepest_dir

    return flow_dir


def compute_flow_accumulation(flow_dir):
    """Compute flow accumulation - number of cells that flow into each cell"""
    rows, cols = flow_dir.shape
    flow_acc = np.ones_like(flow_dir, dtype=np.float32)

    # Direction mappings for the 8 directions
    dir_map = {
        1: (0, 1),
        2: (1, 1),
        4: (1, 0),
        8: (1, -1),
        16: (0, -1),
        32: (-1, -1),
        64: (-1, 0),
        128: (-1, 1),
    }

    # Multiple passes to ensure all flow is accumulated
    for iteration in range(max(rows, cols)):
        changed = False
        for i in range(rows):
            for j in range(cols):
                if flow_dir[i, j] == 0 or np.isnan(flow_dir[i, j]):
                    continue

                # Find where this cell flows to
                if flow_dir[i, j] in dir_map:
                    di, dj = dir_map[flow_dir[i, j]]
                    ni, nj = i + di, j + dj

                    # Check if target cell is valid
                    if (
                        0 <= ni < rows
                        and 0 <= nj < cols
                        and not np.isnan(flow_dir[ni, nj])
                    ):
                        # Add this cell's accumulation to the downstream cell
                        old_acc = flow_acc[ni, nj]
                        flow_acc[ni, nj] += flow_acc[i, j]
                        if flow_acc[ni, nj] != old_acc:
                            changed = True

        if not changed:
            break

    return flow_acc


def compute_accumulation_threshold(flow_acc, min_accumulation=None):
    if min_accumulation is not None:
        return min_accumulation

    valid_acc = flow_acc[~np.isnan(flow_acc) & (flow_acc > 1)]
    if len(valid_acc) > 0:
        return np.percentile(valid_acc, 50)
    else:
        return 10


def get_flow_direction_map():
    return {
        1: (0, 1),
        2: (1, 1),
        4: (1, 0),
        8: (1, -1),
        16: (0, -1),
        32: (-1, -1),
        64: (-1, 0),
        128: (-1, 1),
    }


def trace_single_stream(
    start_i, start_j, stream_mask, flow_dir, dem_transform, visited
):
    rows, cols = stream_mask.shape
    dir_map = get_flow_direction_map()
    line_coords = []
    current_i, current_j = start_i, start_j

    while (
        0 <= current_i < rows
        and 0 <= current_j < cols
        and stream_mask[current_i, current_j]
        and not visited[current_i, current_j]
    ):
        visited[current_i, current_j] = True
        x, y = transform.xy(dem_transform, current_i, current_j)
        line_coords.append((x, y))

        flow_val = flow_dir[current_i, current_j]
        if flow_val in dir_map:
            di, dj = dir_map[flow_val]
            current_i += di
            current_j += dj
        else:
            break

    return line_coords


def trace_all_streams(stream_mask, flow_dir, dem_transform):
    rows, cols = stream_mask.shape
    stream_network = []
    visited = np.zeros_like(stream_mask, dtype=bool)

    for i in range(rows):
        for j in range(cols):
            if stream_mask[i, j] and not visited[i, j]:
                line_coords = trace_single_stream(
                    i, j, stream_mask, flow_dir, dem_transform, visited
                )

                if len(line_coords) >= 2:
                    try:
                        line = LineString(line_coords)
                        stream_network.append(line)
                    except Exception as e:
                        print(e)
                        pass

    return stream_network


def extract_stream_network(
    flow_acc, dem, dem_transform, flow_dir, min_accumulation=None
):
    threshold = compute_accumulation_threshold(flow_acc, min_accumulation)
    print(f"Using flow accumulation threshold: {threshold:.1f}")

    stream_mask = (flow_acc >= threshold) & (~np.isnan(flow_acc))
    stream_network = trace_all_streams(stream_mask, flow_dir, dem_transform)

    return stream_network, stream_mask, threshold
